"""Disposable: C2a -- measure the DYNAMIC capture radius directly. A linear
sweep across the hole at search speed (v_t), at a fixed perpendicular
offset d from the true center, either captures (drops to the floor) or
doesn't. The largest d that still captures is R_cap_dyn -- this is what the
pitch bound should actually be sized against, not the static funnel radius
(capture_radius = clearance+chamfer), which is what the spiral currently
uses and produced the B0b angle-dependence."""
from __future__ import annotations

import math

import mujoco
import numpy as np

from pegsim.control import ImpedanceGains, assert_gains_stable, compute_and_apply_qfrc
from pegsim.rollout import DEPTH_ENGAGE_MARGIN, InsertionProfile, _rate_limited_advance, axis_xy_at_z
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf

TASK = TaskSpec(
    peg_radius=0.005,
    radial_clearance=0.0005,
    chamfer_width=0.001,
    plate_thickness=0.01,
    peg_length=0.05,
    peg_mass=0.05,
    n_segments=16,
    friction=0.4,
)
SOLVER = SolverSpec(timestep=0.0005)
GAINS = ImpedanceGains(k_rx=5.0, k_ry=5.0, k_rz=0.05)
INSERTION = InsertionProfile()
V_T = 0.005  # m/s, default spiral tangential speed
START_X = -0.003


def sweep_captures(d: float) -> bool:
    model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, SOLVER))
    data = mujoco.MjData(model)
    data.qpos[0] = START_X
    data.qpos[1] = d
    data.qpos[2] = 0.005
    mujoco.mj_forward(model, data)
    assert_gains_stable(model, data, GAINS, SOLVER.timestep)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
    dt = SOLVER.timestep
    z_search_target = -INSERTION.f_search / GAINS.k_z
    z_seating_target = -TASK.plate_thickness - INSERTION.f_target / GAINS.k_z

    def z_des_for(tip_now, z_des_raw):
        # MUST match rollout.py's two-phase logic exactly (reusing its own
        # _rate_limited_advance): an earlier version of this script only
        # ever referenced z_search_target, so once past the chamfer it
        # plateaued at the search depth (-2.0mm, exactly -f_search/K_z)
        # instead of continuing to the floor -- not a rollout.py bug, an
        # incomplete port in this standalone script.
        in_seating_phase = tip_now[2] < -TASK.chamfer_width
        phase_target = z_seating_target if in_seating_phase else z_search_target
        z_des_raw = _rate_limited_advance(z_des_raw, phase_target, INSERTION.v_descent * dt)
        z_des = max(z_des_raw, tip_now[2] - INSERTION.f_target / GAINS.k_z)
        return z_des, z_des_raw

    # Settle onto the table first, holding x,y fixed.
    z_des_raw = 0.005
    for i in range(4000):
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        z_des, z_des_raw = z_des_for(tip_now, z_des_raw)
        compute_and_apply_qfrc(model, data, GAINS, np.array([START_X, d, z_des]))
        mujoco.mj_step(model, data)

    # Sweep x_des through 0 at V_T, holding y_des = d fixed, with the SAME
    # two governors rollout.py's real search uses (see rollout.py's fixed
    # pause logic): pause gated on FK depth past DEPTH_ENGAGE_MARGIN below
    # the flat table, NOT lateral force -- a force-based gate
    # (lateral_force > mu*axial_force) was tried first and is wrong: kinetic
    # friction during genuine sliding sits right at mu*N, so the gate
    # flickered at the threshold and froze x_des just as badly as gating on
    # any contact did (verified directly: a dead-center sweep still failed
    # to capture with the force gate).
    n_cross_steps = int(2.0 * abs(START_X) / V_T / dt)
    settle_extra_steps = int(2.0 / dt)  # up to 2s to finish bottoming out once stopped
    floor_z = -TASK.plate_thickness
    x_des = START_X
    stopped = False
    for i in range(n_cross_steps + settle_extra_steps):
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        axis_dir_now = data.site_xmat[site_id].reshape(3, 3)[:, 2]
        mouth_now = axis_xy_at_z(tip_now, axis_dir_now, 0.0)
        if not stopped and np.linalg.norm(mouth_now) <= TASK.radial_clearance:
            stopped = True
            x_des = tip_now[0]  # snap to actual position, not the lagged commanded value
        engaging_slope = tip_now[2] < -DEPTH_ENGAGE_MARGIN
        if not stopped and not engaging_slope and i < n_cross_steps:
            x_des = START_X + V_T * i * dt
        z_des, z_des_raw = z_des_for(tip_now, z_des_raw)
        compute_and_apply_qfrc(model, data, GAINS, np.array([x_des, d, z_des]))
        mujoco.mj_step(model, data)
        if tip_now[2] <= floor_z + 0.001:
            return True
    return False


print(f"v_t={V_T*1000}mm/s, static capture_radius={TASK.capture_radius*1000:.4f}mm")
print(f"{'d_mm':>8s} {'captured':>10s}")
last_captured = None
for d_mm in np.arange(0.0, 3.5, 0.1):
    captured = sweep_captures(d_mm / 1000.0)
    print(f"{d_mm:8.2f} {str(captured):>10s}")
    if captured:
        last_captured = d_mm
    elif last_captured is not None:
        break
print(f"\nR_cap_dyn (largest d that still captured): {last_captured} mm")
