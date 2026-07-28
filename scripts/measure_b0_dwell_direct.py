"""Disposable: B0 dwell check, direct version. Rather than waiting for the
natural spiral timeline to happen to pass near the hole (it didn't, within
6s, in measure_b0_lag_dwell.py), directly command a linear sweep through
the hole at the search tangential speed v_t and measure z-response during
the crossing -- isolates the same physical question (does dwell time allow
enough engagement) from spiral-alignment luck.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

from pegsim.control import ImpedanceGains, assert_gains_stable, compute_and_apply_qfrc
from pegsim.rollout import InsertionProfile
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
GAINS = ImpedanceGains(k_xy=500.0, k_rx=5.0, k_ry=5.0, k_rz=0.05)
INSERTION = InsertionProfile()
V_T = 0.005  # m/s, default spiral tangential speed

model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, SOLVER))
data = mujoco.MjData(model)
start_x = -0.003
data.qpos[0] = start_x
data.qpos[2] = 0.005
mujoco.mj_forward(model, data)
assert_gains_stable(model, data, GAINS, SOLVER.timestep)

site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
dt = SOLVER.timestep
z_search_target = -INSERTION.f_search / GAINS.k_z

# Settle onto the table first (z-only), holding x fixed, before starting the sweep.
z_des_raw = 0.005
for i in range(4000):
    mujoco.mj_forward(model, data)
    tip_now = data.site_xpos[site_id]
    z_des_raw = max(z_des_raw - INSERTION.v_descent * dt, z_search_target)
    z_des = max(z_des_raw, tip_now[2] - INSERTION.f_target / GAINS.k_z)
    compute_and_apply_qfrc(model, data, GAINS, np.array([start_x, 0.0, z_des]))
    mujoco.mj_step(model, data)

print(f"settled tip z before sweep: {data.site_xpos[site_id][2]*1000:.4f}mm")

# Now sweep x_des linearly through 0 at V_T, holding the settled z reference.
n_steps = int(2.0 * abs(start_x) / V_T / dt) + 200
tip_z = np.zeros(n_steps)
x_des_log = np.zeros(n_steps)
tip_x_log = np.zeros(n_steps)
for i in range(n_steps):
    mujoco.mj_forward(model, data)
    tip_now = data.site_xpos[site_id]
    x_des = start_x + V_T * i * dt
    z_des_raw = max(z_des_raw - INSERTION.v_descent * dt, z_search_target)
    z_des = max(z_des_raw, tip_now[2] - INSERTION.f_target / GAINS.k_z)
    compute_and_apply_qfrc(model, data, GAINS, np.array([x_des, 0.0, z_des]))
    mujoco.mj_step(model, data)
    tip_z[i] = data.site_xpos[site_id][2]
    x_des_log[i] = x_des
    tip_x_log[i] = data.site_xpos[site_id][0]

capture_radius = TASK.capture_radius
within_capture = np.abs(tip_x_log) <= capture_radius
if within_capture.any():
    idx = np.where(within_capture)[0]
    dwell_s = (idx[-1] - idx[0] + 1) * dt
    z_at_entry = tip_z[idx[0]]
    z_min_during = tip_z[idx].min()
    print(f"dwell within capture_radius: {dwell_s*1000:.3f}ms ({len(idx)} steps)")
    print(f"z at entry: {z_at_entry*1000:.5f}mm, min z during dwell: {z_min_during*1000:.5f}mm, "
          f"drop: {(z_at_entry-z_min_during)*1000:.5f}mm")
else:
    print("tip never registered within capture_radius of the sweep path (unexpected)")

print(f"predicted dwell = 2*R_capture/v_t = {2*capture_radius/V_T*1000:.3f}ms")
