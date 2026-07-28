"""Disposable: B0 -- measure whether spiral lateral tracking during table
sliding is a steady lag or genuine oscillation, and verify the dwell-time
margin empirically (does the peg drop enough during a pass over the hole).
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

from pegsim.control import ImpedanceGains, assert_gains_stable, compute_and_apply_qfrc, min_contact_dist
from pegsim.rollout import InsertionProfile
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf
from pegsim.search import DELTA_MAX_SPEC_M, ArchimedeanSpiral, SpiralSpec

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
SOLVER = SolverSpec(timestep=0.0005)  # A5: stable, 2x cheaper than 0.00025
GAINS = ImpedanceGains(k_rx=5.0, k_ry=5.0, k_rz=0.05)  # K_xy stays 500 (default), untouched
INSERTION = InsertionProfile()
OFFSET = 0.00175  # 1.75mm, just outside passive R_passive=1.5mm -- forces real sliding search

spiral_spec = SpiralSpec(task=TASK, delta_max=DELTA_MAX_SPEC_M)
model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, SOLVER))
data = mujoco.MjData(model)
data.qpos[0] = OFFSET
data.qpos[2] = 0.005
mujoco.mj_forward(model, data)
assert_gains_stable(model, data, GAINS, SOLVER.timestep)

nominal_xy = np.array([OFFSET, 0.0])
spiral = ArchimedeanSpiral(spiral_spec)
site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")

dt = SOLVER.timestep
n_steps = int(6.0 / dt)
tip_xy = np.zeros((n_steps, 2))
setpoint_xy = np.zeros((n_steps, 2))
tip_z = np.zeros(n_steps)
z_des_raw = 0.005

z_search_target = -INSERTION.f_search / GAINS.k_z
for i in range(n_steps):
    mujoco.mj_forward(model, data)
    tip_now = data.site_xpos[site_id]
    in_contact = math.isfinite(min_contact_dist(data))
    if tip_now[2] <= 0.001 and not in_contact:
        xy_des = nominal_xy + spiral.step(dt)
    else:
        xy_des = nominal_xy + np.array([spiral.radius * math.cos(spiral.theta), spiral.radius * math.sin(spiral.theta)])
    z_des_raw = max(z_des_raw - INSERTION.v_descent * dt, z_search_target)
    z_des = max(z_des_raw, tip_now[2] - INSERTION.f_target / GAINS.k_z)
    compute_and_apply_qfrc(model, data, GAINS, np.array([xy_des[0], xy_des[1], z_des]))
    mujoco.mj_step(model, data)

    tip_xy[i] = data.site_xpos[site_id][:2]
    setpoint_xy[i] = xy_des
    tip_z[i] = data.site_xpos[site_id][2]

lag = np.linalg.norm(tip_xy - setpoint_xy, axis=1)
# Steady-state window: skip the initial transient (first 1s), look at sliding phase
steady = lag[int(1.0 / dt):int(5.0 / dt)]
print(f"lag steady-state: mean={steady.mean()*1000:.4f}mm std={steady.std()*1000:.4f}mm "
      f"min={steady.min()*1000:.4f}mm max={steady.max()*1000:.4f}mm")
print(f"lag coefficient of variation (std/mean): {steady.std()/steady.mean():.4f}")

# Dwell check: find the closest approach of the tip to world origin (a pass
# over the hole), and measure z-drop during that window.
dist_to_hole = np.linalg.norm(tip_xy, axis=1)
closest_idx = int(np.argmin(dist_to_hole))
print(f"closest approach to hole: dist={dist_to_hole[closest_idx]*1000:.4f}mm at t={closest_idx*dt:.4f}s")
window_radius_steps = int(0.03 / dt)  # +/- 30ms window around closest approach
w = slice(max(0, closest_idx - window_radius_steps), min(n_steps, closest_idx + window_radius_steps))
print(f"z during window around closest approach: start={tip_z[w][0]*1000:.4f}mm "
      f"min={tip_z[w].min()*1000:.4f}mm end={tip_z[w][-1]*1000:.4f}mm")
print(f"z drop during window: {(tip_z[w][0]-tip_z[w].min())*1000:.5f}mm")
