"""Disposable: E6 -- lift-and-step probe search vs the Archimedean spiral.
The friction force budget (D1/D3/E1) exists only because the spiral drags
the peg across the table while searching. Lift-and-step sidesteps it
entirely: lift clear of the table (zero friction), translate in free space,
lower to check capture, repeat over a hex-covering probe pattern. Compares
16-angle capture fraction, time-to-capture, and wall-clock against the best
retuned spiral config from measure_e3_search_phase_sweep.py, at the loose
(1.8mm offset) config, then projects probe count at a tight-tolerance
config.
"""
from __future__ import annotations

import math
import time

import mujoco
import numpy as np

from pegsim.control import ImpedanceGains, assert_gains_stable, compute_and_apply_qfrc, peg_contact_force_world
from pegsim.rollout import (
    DELTA_Z_TRIGGER,
    DEPTH_ENGAGE_MARGIN,
    InsertionProfile,
    _rate_limited_advance,
    axis_xy_at_z,
    assert_seating_trigger_reachable,
    ground_truth_seated,
)
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf
from pegsim.search import DELTA_MAX_SPEC_M, ProbeSpec

TASK = TaskSpec(
    peg_radius=0.005, radial_clearance=0.0005, chamfer_width=0.001,
    plate_thickness=0.01, peg_length=0.05, peg_mass=0.05, n_segments=16, friction=0.4,
)
SOLVER = SolverSpec(timestep=0.0005)
PEN_ALLOW = 3.7035e-05
R_CAP_DYN = 0.0016
OFFSET = 0.0018
LIFT_RATE = 0.01  # m/s, reuses InsertionProfile.v_descent's rate-limit convention
TRANSLATE_RATE = 0.005  # m/s, reuses the spiral's v_t convention
TOL_Z = 0.00005  # 0.05mm
TOL_XY = 0.0001  # 0.1mm
MAX_STEPS_PER_PHASE = 20000


def run_probe_trial(offset_theta: float, gains: ImpedanceGains, insertion: InsertionProfile,
                     probe_spec: ProbeSpec, target_depth: float) -> dict:
    model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, SOLVER))
    data = mujoco.MjData(model)
    x0 = OFFSET * math.cos(offset_theta)
    y0 = OFFSET * math.sin(offset_theta)
    data.qpos[0] = x0
    data.qpos[1] = y0
    data.qpos[2] = 0.005
    mujoco.mj_forward(model, data)
    assert_gains_stable(model, data, gains, SOLVER.timestep)
    assert_seating_trigger_reachable(insertion, gains)
    nominal_xy = np.array([x0, y0])
    dt = SOLVER.timestep
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
    z_search_target = -insertion.f_search / gains.k_z
    z_seating_target = -TASK.plate_thickness - insertion.f_target / gains.k_z

    z_des_raw = 0.005
    xy_des = nominal_xy.copy()
    total_steps = 0
    captured = False
    capture_step = None
    seated_step = None
    required_consecutive = max(1, int(round(0.1 / dt)))
    consecutive_ok = 0
    z_flat = None
    Z_FLAT_EMA_ALPHA = 0.001

    def apply_and_step(xy_target, z_target_raw, in_seating_phase_override=None):
        nonlocal z_des_raw, total_steps, z_flat
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        engaging_slope = tip_now[2] < -DEPTH_ENGAGE_MARGIN
        if tip_now[2] <= 0.001 and not engaging_slope:
            z_flat = tip_now[2] if z_flat is None else (
                (1.0 - Z_FLAT_EMA_ALPHA) * z_flat + Z_FLAT_EMA_ALPHA * tip_now[2]
            )
        in_seating_phase = (
            in_seating_phase_override if in_seating_phase_override is not None
            else (z_flat is not None and (z_flat - tip_now[2]) > DELTA_Z_TRIGGER)
        )
        phase_target = z_seating_target if in_seating_phase else z_search_target
        z_des_raw = _rate_limited_advance(z_des_raw, phase_target, insertion.v_descent * dt)
        z_des = max(z_des_raw, tip_now[2] - insertion.f_target / gains.k_z)
        z_cmd = z_target_raw if z_target_raw is not None else z_des
        compute_and_apply_qfrc(model, data, gains, np.array([xy_target[0], xy_target[1], z_cmd]))
        mujoco.mj_step(model, data)
        total_steps += 1
        return data.site_xpos[site_id].copy()

    def mouth_lateral_now() -> float:
        tip_now = data.site_xpos[site_id]
        axis_dir_now = data.site_xmat[site_id].reshape(3, 3)[:, 2]
        return float(np.linalg.norm(axis_xy_at_z(tip_now, axis_dir_now, 0.0)))

    for point in probe_spec.points:
        probe_xy = nominal_xy + point

        # LIFT: rate-limit z up to lift_height, holding current xy, capped by MAX_STEPS_PER_PHASE.
        for _ in range(MAX_STEPS_PER_PHASE):
            tip = apply_and_step(xy_des, min(data.site_xpos[site_id][2] + LIFT_RATE * dt, probe_spec.lift_height))
            if abs(tip[2] - probe_spec.lift_height) <= TOL_Z:
                break

        # TRANSLATE: rate-limit xy toward probe_xy at lift height, no friction (peg is airborne).
        for _ in range(MAX_STEPS_PER_PHASE):
            delta = probe_xy - xy_des
            dist = np.linalg.norm(delta)
            if dist <= TOL_XY:
                break
            step = min(TRANSLATE_RATE * dt, dist)
            xy_des = xy_des + delta / dist * step
            apply_and_step(xy_des, probe_spec.lift_height)

        # LOWER: rate-limit z back down to the search reference, holding xy at the probe point.
        for _ in range(MAX_STEPS_PER_PHASE):
            tip = apply_and_step(xy_des, None, in_seating_phase_override=False)
            if mouth_lateral_now() <= TASK.radial_clearance:
                captured = True
                capture_step = total_steps
                break
            if tip[2] <= z_search_target + TOL_Z:
                break
        if captured:
            break

    # Once captured (or probes exhausted), keep running the normal two-phase
    # z governor (search/seating) to bottom out, same as the spiral path.
    max_extra_steps = int(6.0 / dt)
    for _ in range(max_extra_steps):
        tip_now_pre = data.site_xpos[site_id]
        if not captured:
            xy_des = nominal_xy  # exhausted: hold last nominal center, no more search authority
        tip = apply_and_step(xy_des, None)
        axis_dir = data.site_xmat[site_id].reshape(3, 3)[:, 2]
        force = peg_contact_force_world(model, data)
        if ground_truth_seated(tip, axis_dir, force, TASK, target_depth, PEN_ALLOW):
            consecutive_ok += 1
        else:
            consecutive_ok = 0
        if seated_step is None and consecutive_ok >= required_consecutive:
            seated_step = total_steps - required_consecutive + 1
        if seated_step is not None:
            break

    return {
        "seated": seated_step is not None,
        "capture_step": capture_step,
        "time_to_seat_s": seated_step * dt if seated_step is not None else math.inf,
        "total_steps": total_steps,
    }


def main() -> None:
    probe_spec = ProbeSpec(task=TASK, delta_max=DELTA_MAX_SPEC_M, r_cap_dyn=R_CAP_DYN)
    print(f"probe pattern: {len(probe_spec.points)} points, spacing={probe_spec.spacing*1000:.4f}mm, "
          f"delta_max={probe_spec.delta_max*1000:.1f}mm, r_cap_dyn={R_CAP_DYN*1000:.1f}mm")

    gains = ImpedanceGains(k_xy=1000.0, k_rx=5.0, k_ry=5.0, k_rz=0.05)
    insertion = InsertionProfile(f_search=1.0)
    target_depth = TASK.plate_thickness - 0.001

    angles = [i * 2.0 * math.pi / 16 for i in range(16)]
    print(f"\n{'theta_deg':>10s} {'seated':>7s} {'time_to_seat_s':>15s} {'total_steps':>12s}")
    t0 = time.time()
    n_seated = 0
    times = []
    for deg in angles:
        r = run_probe_trial(math.radians(deg), gains, insertion, probe_spec, target_depth)
        n_seated += int(r["seated"])
        if r["seated"]:
            times.append(r["time_to_seat_s"])
        print(f"{deg:10.1f} {str(r['seated']):>7s} {r['time_to_seat_s']:15.4f} {r['total_steps']:12d}")
    wall = time.time() - t0
    print(f"\nprobe search: {n_seated}/16 seated, wall_clock={wall:.2f}s "
          f"({wall/16:.3f}s/trial), time_to_seat={times}")

    # Tight-config projection: R_cap ~ 0.1mm over a 2mm delta_max.
    tight_probe = ProbeSpec(task=TASK, delta_max=0.002, r_cap_dyn=0.0001)
    print(f"\ntight-config projection (r_cap_dyn=0.1mm, delta_max=2mm): "
          f"{len(tight_probe.points)} probe points")


if __name__ == "__main__":
    main()
