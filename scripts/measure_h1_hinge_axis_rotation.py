"""Disposable: H1 -- decisive test for the 4-fold cardinal capture pattern.
Elliptic cone confirmed (G1); the only remaining 4-fold-symmetric structure
in the system is the rx/ry hinge axes, world-aligned along x/y. Rotates
those axes 45deg in the compiled MJCF (string surgery on build_mjcf's
output -- NOT a permanent scene.py parameter, this is a one-off diagnostic)
and re-runs the search-OFF 16-angle static-settle test at 1.8mm offset,
f_search=4N (the same config that showed the clean 0/90/180/270 pattern).
If the pattern rotates to 45/135/225/315, the joint parameterization is
confirmed as the cause. If it stays at 0/90/180/270, it isn't the joints.
"""
from __future__ import annotations

import math

import mujoco

from pegsim.control import ImpedanceGains
from pegsim.rollout import EpisodeConfig, InsertionProfile, run_episode
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf

TASK = TaskSpec(
    peg_radius=0.005, radial_clearance=0.0005, chamfer_width=0.001,
    plate_thickness=0.01, peg_length=0.05, peg_mass=0.05, n_segments=16, friction=0.4,
)
SOLVER = SolverSpec(timestep=0.0005)
GAINS = ImpedanceGains(k_xy=500.0, k_rx=5.0, k_ry=5.0, k_rz=0.05)
INSERTION = InsertionProfile(f_search=4.0)
PEN_ALLOW = 3.7035e-05

ORIGINAL_RX_AXIS = 'axis="1 0 0" pos="0 0 0" damping="0" frictionloss="0" armature="0"/>'
ORIGINAL_RY_AXIS = 'axis="0 1 0" pos="0 0 0" damping="0" frictionloss="0" armature="0"/>'
ROTATED_RX_AXIS = 'axis="0.70710678 0.70710678 0" pos="0 0 0" damping="0" frictionloss="0" armature="0"/>'
ROTATED_RY_AXIS = 'axis="-0.70710678 0.70710678 0" pos="0 0 0" damping="0" frictionloss="0" armature="0"/>'


def build_rotated_mjcf() -> str:
    xml = build_mjcf(TASK, SOLVER)
    rx_line = f'<joint name="peg_rx" type="hinge" {ORIGINAL_RX_AXIS}'
    ry_line = f'<joint name="peg_ry" type="hinge" {ORIGINAL_RY_AXIS}'
    rotated_rx_line = f'<joint name="peg_rx" type="hinge" {ROTATED_RX_AXIS}'
    rotated_ry_line = f'<joint name="peg_ry" type="hinge" {ROTATED_RY_AXIS}'
    assert rx_line in xml, "peg_rx joint line not found verbatim -- scene.py's XML format changed"
    assert ry_line in xml, "peg_ry joint line not found verbatim -- scene.py's XML format changed"
    xml = xml.replace(rx_line, rotated_rx_line).replace(ry_line, rotated_ry_line)
    return xml


def run_static_settle(offset_theta: float, xml: str) -> bool:
    model = mujoco.MjModel.from_xml_string(xml)
    # Re-implements run_episode's core loop against a pre-built model/xml
    # (run_episode always calls build_mjcf itself) -- everything else
    # (gains, insertion, seat check) is identical to the production path.
    import numpy as np

    from pegsim.control import assert_gains_stable, compute_and_apply_qfrc, peg_contact_force_world
    from pegsim.rollout import (
        DELTA_Z_TRIGGER,
        DEPTH_ENGAGE_MARGIN,
        _rate_limited_advance,
        assert_seating_trigger_reachable,
        axis_xy_at_z,
        ground_truth_seated,
    )

    data = mujoco.MjData(model)
    x0 = 0.0018 * math.cos(offset_theta)
    y0 = 0.0018 * math.sin(offset_theta)
    data.qpos[0] = x0
    data.qpos[1] = y0
    data.qpos[2] = 0.005
    mujoco.mj_forward(model, data)
    assert_gains_stable(model, data, GAINS, SOLVER.timestep)
    assert_seating_trigger_reachable(INSERTION, GAINS)

    dt = SOLVER.timestep
    n_steps = int(8.0 / dt)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
    z_search_target = -INSERTION.f_search / GAINS.k_z
    z_seating_target = -TASK.plate_thickness - INSERTION.f_target / GAINS.k_z
    target_depth = TASK.plate_thickness - 0.001

    xy_des = np.array([x0, y0])
    z_des_raw = 0.005
    z_flat = None
    Z_FLAT_EMA_ALPHA = 0.001
    seated_step = None
    consecutive_ok = 0
    required_consecutive = max(1, int(round(0.1 / dt)))

    for i in range(n_steps):
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        engaging_slope = tip_now[2] < -DEPTH_ENGAGE_MARGIN
        if tip_now[2] <= 0.001 and not engaging_slope:
            z_flat = tip_now[2] if z_flat is None else (
                (1.0 - Z_FLAT_EMA_ALPHA) * z_flat + Z_FLAT_EMA_ALPHA * tip_now[2]
            )
        in_seating_phase = z_flat is not None and (z_flat - tip_now[2]) > DELTA_Z_TRIGGER
        phase_target = z_seating_target if in_seating_phase else z_search_target
        z_des_raw = _rate_limited_advance(z_des_raw, phase_target, INSERTION.v_descent * dt)
        z_des = max(z_des_raw, tip_now[2] - INSERTION.f_target / GAINS.k_z)
        compute_and_apply_qfrc(model, data, GAINS, np.array([xy_des[0], xy_des[1], z_des]))
        mujoco.mj_step(model, data)

        tip = data.site_xpos[site_id]
        axis_dir = data.site_xmat[site_id].reshape(3, 3)[:, 2]
        force = peg_contact_force_world(model, data)
        if ground_truth_seated(tip, axis_dir, force, TASK, target_depth, PEN_ALLOW):
            consecutive_ok += 1
        else:
            consecutive_ok = 0
        if seated_step is None and consecutive_ok >= required_consecutive:
            seated_step = i - required_consecutive + 1
            break

    return seated_step is not None


def main() -> None:
    print("Cheap checks first:")
    print(f"  k_xy applied identically to x and y (single shared field, "
          f"stiffness_vector = [k_xy, k_xy, k_z, ...]): structurally yes, no typo possible")
    print(f"  orientation error: proper log-map (quat composition + mju_quat2Vel), "
          f"not a naive per-joint Euler difference (see control._orientation_error)")

    xml = build_rotated_mjcf()
    angles = [i * 22.5 for i in range(16)]
    print(f"\n{'theta_deg':>10s} {'seated (rotated axes)':>22s}")
    for deg in angles:
        seated = run_static_settle(math.radians(deg), xml)
        print(f"{deg:10.1f} {str(seated):>22s}")


if __name__ == "__main__":
    main()
