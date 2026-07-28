"""Disposable: D1 diagnostic. Mirrors rollout.run_episode's loop exactly
(same pause/stop logic, same two-phase z) but additionally instruments the
spiral's own theta trajectory: max spiral radius actually reached, total
sim-time spent paused, and whether the spiral ever completed (theta reaching
theta_end) within max_seconds. This is the direct check for D1's hypothesis:
does the spiral run out of TIME (via repeated pausing) before covering the
angle needed for a 1.8mm-offset hole, even though R_spiral itself (2.625mm)
comfortably exceeds 1.8mm on paper.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

from pegsim.control import ImpedanceGains, assert_gains_stable, compute_and_apply_qfrc, peg_contact_force_world
from pegsim.rollout import DEPTH_ENGAGE_MARGIN, InsertionProfile, _rate_limited_advance, axis_xy_at_z
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf
from pegsim.search import DELTA_MAX_SPEC_M, ArchimedeanSpiral, SpiralSpec

TASK = TaskSpec(
    peg_radius=0.005, radial_clearance=0.0005, chamfer_width=0.001,
    plate_thickness=0.01, peg_length=0.05, peg_mass=0.05, n_segments=16, friction=0.4,
)
SOLVER = SolverSpec(timestep=0.0005)
GAINS = ImpedanceGains(k_xy=500.0, k_rx=5.0, k_ry=5.0, k_rz=0.05)
INSERTION = InsertionProfile()
PEN_ALLOW = 3.7035e-05
OFFSET = 0.0018
V_T = 0.005

spiral_spec = SpiralSpec(task=TASK, delta_max=DELTA_MAX_SPEC_M, v_t=V_T)
a, b = spiral_spec.a, spiral_spec.b
pitch = 2 * math.pi * b
r_spiral = spiral_spec.r_spiral
theta_end = (r_spiral - a) / b
max_seconds = r_spiral**2 / (2 * b * V_T) + 3.0

print(f"a=b={a*1000:.6f}mm pitch={pitch*1000:.6f}mm r_spiral={r_spiral*1000:.6f}mm")
print(f"theta_end={theta_end:.4f}rad ({theta_end/(2*math.pi):.4f} rev, "
      f"{math.degrees(theta_end)%360:.2f}deg mod 360)")
print(f"chirality: theta increases monotonically (theta_dot=v_t/radius > 0), theta_0=0.0")
print(f"max_seconds budget={max_seconds:.3f}s, dt={SOLVER.timestep}")
print()


def run_trial(offset_theta: float) -> dict:
    model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, SOLVER))
    data = mujoco.MjData(model)
    x0 = OFFSET * math.cos(offset_theta)
    y0 = OFFSET * math.sin(offset_theta)
    data.qpos[0] = x0
    data.qpos[1] = y0
    data.qpos[2] = 0.005
    mujoco.mj_forward(model, data)
    assert_gains_stable(model, data, GAINS, SOLVER.timestep)
    nominal_xy = np.array([x0, y0])
    spiral = ArchimedeanSpiral(spiral_spec)

    dt = SOLVER.timestep
    n_steps = int(max_seconds / dt)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")

    z_seating_target = -TASK.plate_thickness - INSERTION.f_target / GAINS.k_z
    z_search_target = -INSERTION.f_search / GAINS.k_z

    xy_des = nominal_xy
    search_done = False
    z_des_raw = 0.005
    paused_steps = 0
    advancing_steps = 0
    min_tip_lateral = math.inf
    max_spiral_radius = 0.0
    seated = False

    for i in range(n_steps):
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        axis_dir_now = data.site_xmat[site_id].reshape(3, 3)[:, 2]
        mouth_now = axis_xy_at_z(tip_now, axis_dir_now, 0.0)
        min_tip_lateral = min(min_tip_lateral, float(np.linalg.norm(tip_now[:2])))
        if not search_done and np.linalg.norm(mouth_now) <= TASK.radial_clearance:
            search_done = True
            xy_des = tip_now[:2].copy()
        engaging_slope = tip_now[2] < -DEPTH_ENGAGE_MARGIN
        in_seating_phase = tip_now[2] < -TASK.chamfer_width
        phase_target = z_seating_target if in_seating_phase else z_search_target
        z_des_raw = _rate_limited_advance(z_des_raw, phase_target, INSERTION.v_descent * dt)
        z_des = max(z_des_raw, tip_now[2] - INSERTION.f_target / GAINS.k_z)

        if not search_done and tip_now[2] <= 0.001 and not engaging_slope:
            xy_des = nominal_xy + spiral.step(dt)
            advancing_steps += 1
            max_spiral_radius = max(max_spiral_radius, spiral.radius)
        elif not search_done:
            paused_steps += 1

        x_des = np.array([xy_des[0], xy_des[1], z_des])
        compute_and_apply_qfrc(model, data, GAINS, x_des)
        mujoco.mj_step(model, data)

        tip = data.site_xpos[site_id]
        if tip[2] <= -TASK.plate_thickness + 0.001:
            seated = True

    return {
        "offset_theta_deg": math.degrees(offset_theta),
        "min_tip_lateral_mm": min_tip_lateral * 1000,
        "max_spiral_radius_mm": max_spiral_radius * 1000,
        "final_theta_rev": spiral.theta / (2 * math.pi),
        "spiral_exhausted": spiral.exhausted,
        "advancing_steps": advancing_steps,
        "paused_steps": paused_steps,
        "seated_bottomed_out": seated,
    }


angles = [i * 22.5 for i in range(16)]
print(f"{'theta_deg':>10s} {'min_dist_mm':>12s} {'max_r_mm':>10s} {'final_rev':>10s} "
      f"{'exhausted':>10s} {'adv_steps':>10s} {'pause_steps':>12s} {'bottomed':>9s}")
for deg in angles:
    r = run_trial(math.radians(deg))
    print(f"{r['offset_theta_deg']:10.1f} {r['min_tip_lateral_mm']:12.4f} "
          f"{r['max_spiral_radius_mm']:10.4f} {r['final_theta_rev']:10.4f} "
          f"{str(r['spiral_exhausted']):>10s} {r['advancing_steps']:10d} "
          f"{r['paused_steps']:12d} {str(r['seated_bottomed_out']):>9s}")
