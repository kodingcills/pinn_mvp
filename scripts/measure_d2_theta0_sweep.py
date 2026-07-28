"""Disposable: D2 discriminator. Re-run the 16-angle x 1.8mm-offset capture
sweep at spiral start phase theta_0 = 0, 90, 180, 270 deg. If the capturing
block ROTATES with theta_0, the mechanism is spiral geometry/phase. If it
stays PINNED to the same offset_theta range regardless of theta_0, the
capture is being decided by something independent of the spiral (world-frame
asymmetry or, per the D3 trajectory plot + force-budget check, the spiral
being too weak to ever move the peg -- i.e., passive tip-and-lever capture,
not active search)."""
from __future__ import annotations

import math

import mujoco
import numpy as np

from pegsim.control import ImpedanceGains, assert_gains_stable, compute_and_apply_qfrc
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
OFFSET = 0.0018
V_T = 0.005
spiral_spec = SpiralSpec(task=TASK, delta_max=DELTA_MAX_SPEC_M, v_t=V_T)
max_seconds = spiral_spec.r_spiral**2 / (2 * spiral_spec.b * V_T) + 3.0


def run_trial(offset_theta: float, theta0: float) -> bool:
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
    spiral.theta = theta0  # nonzero start phase for the discriminator

    dt = SOLVER.timestep
    n_steps = int(max_seconds / dt)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
    z_seating_target = -TASK.plate_thickness - INSERTION.f_target / GAINS.k_z
    z_search_target = -INSERTION.f_search / GAINS.k_z

    xy_des = nominal_xy
    search_done = False
    z_des_raw = 0.005
    seated = False

    for i in range(n_steps):
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        axis_dir_now = data.site_xmat[site_id].reshape(3, 3)[:, 2]
        mouth_now = axis_xy_at_z(tip_now, axis_dir_now, 0.0)
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

        x_des = np.array([xy_des[0], xy_des[1], z_des])
        compute_and_apply_qfrc(model, data, GAINS, x_des)
        mujoco.mj_step(model, data)

        if data.site_xpos[site_id][2] <= -TASK.plate_thickness + 0.001:
            seated = True
            break

    return seated


angles = [i * 22.5 for i in range(16)]
for theta0_deg in (0.0, 90.0, 180.0, 270.0):
    row = []
    for deg in angles:
        seated = run_trial(math.radians(deg), math.radians(theta0_deg))
        row.append("1" if seated else "0")
    print(f"theta0={theta0_deg:5.1f}deg: " + " ".join(row) +
          f"   ({sum(int(c) for c in row)}/16)")
print("offset_theta grid: " + " ".join(f"{d:g}" for d in angles))
