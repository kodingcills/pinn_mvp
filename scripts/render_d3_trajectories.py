"""Disposable: D3 -- plot actual peg XY trajectory vs commanded setpoint path,
for one capturing angle (270deg) and one failing angle (90deg) at 1.8mm
offset, same axes/scale, hole mouth + bore overlaid. Directly answers whether
the peg is tracking the spiral setpoint or getting stuck."""
from __future__ import annotations

import math

import matplotlib
import mujoco
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

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


def run_trial(offset_theta: float):
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

    tip_xy = np.zeros((n_steps, 2))
    setpoint_xy_log = np.zeros((n_steps, 2))

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

        tip_xy[i] = data.site_xpos[site_id][:2]
        setpoint_xy_log[i] = xy_des

        if data.site_xpos[site_id][2] <= -TASK.plate_thickness + 0.001:
            tip_xy = tip_xy[: i + 1]
            setpoint_xy_log = setpoint_xy_log[: i + 1]
            break

    return tip_xy, setpoint_xy_log


fig, axes = plt.subplots(1, 2, figsize=(12, 6))
for ax, (deg, label) in zip(axes, [(270.0, "capturing (270deg)"), (90.0, "failing (90deg)")]):
    tip_xy, setpoint_xy_log = run_trial(math.radians(deg))
    ax.plot(setpoint_xy_log[:, 0] * 1000, setpoint_xy_log[:, 1] * 1000, "--", color="gray",
            alpha=0.6, label="commanded setpoint", linewidth=0.8)
    ax.plot(tip_xy[:, 0] * 1000, tip_xy[:, 1] * 1000, "-", color="C0", label="actual tip path")
    ax.plot([tip_xy[0, 0] * 1000], [tip_xy[0, 1] * 1000], "go", label="start")
    ax.plot([tip_xy[-1, 0] * 1000], [tip_xy[-1, 1] * 1000], "rs", label="end")
    hole_circle = plt.Circle((0, 0), TASK.hole_radius * 1000, fill=False, color="black", linewidth=1.5)
    clearance_circle = plt.Circle((0, 0), TASK.radial_clearance * 1000, fill=False, color="red",
                                   linestyle=":", linewidth=1)
    ax.add_patch(hole_circle)
    ax.add_patch(clearance_circle)
    ax.set_title(f"offset_theta={label}")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_aspect("equal")
    ax.set_xlim(-3.5, 3.5)
    ax.set_ylim(-3.5, 3.5)
    ax.legend(fontsize=7, loc="upper right")
    ax.grid(True, alpha=0.3)

fig.suptitle("D3: commanded spiral setpoint vs actual peg XY path, 1.8mm offset")
fig.tight_layout()
fig.savefig("docs/d3_trajectory_capture_vs_fail.png", dpi=150)
print("wrote docs/d3_trajectory_capture_vs_fail.png")
