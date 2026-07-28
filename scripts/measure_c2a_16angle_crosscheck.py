"""Disposable: C2a cross-check -- 16 angles at 1.8mm offset, spiral search ON,
same v_t=0.005 m/s used to measure R_cap_dyn. Predicted f = 2*R_cap_dyn/pitch,
capped at 1.0. R_cap_dyn=1.6mm (measured), pitch=pitch_safety*capture_radius
=0.5*1.5mm=0.75mm -> f=2*1.6/0.75=4.27 -> predicted f=1.0 (full capture,
every start angle should seat)."""
from __future__ import annotations

import math

from pegsim.characterize import run_offset_sweep
from pegsim.control import ImpedanceGains
from pegsim.rollout import InsertionProfile
from pegsim.scene import SolverSpec, TaskSpec
from pegsim.search import DELTA_MAX_SPEC_M, SpiralSpec

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
PEN_ALLOW = 3.7035e-05
OFFSET = 0.0018  # 1.8mm
V_T = 0.005
R_CAP_DYN = 0.0016  # measured via measure_c2a_dynamic_capture_radius.py

spiral_spec = SpiralSpec(task=TASK, delta_max=DELTA_MAX_SPEC_M, v_t=V_T)
pitch = 2.0 * math.pi * spiral_spec.b
f_predicted = min(1.0, 2.0 * R_CAP_DYN / pitch)

angles = [i * 2.0 * math.pi / 16 for i in range(16)]
max_seconds = spiral_spec.r_spiral**2 / (2 * spiral_spec.b * V_T) + 3.0

results = run_offset_sweep(
    TASK, SOLVER, GAINS, INSERTION,
    offsets_m=[OFFSET], angles_rad=angles,
    z_start=0.005, max_seconds=max_seconds, spiral_spec=spiral_spec, pen_allow=PEN_ALLOW,
)

n_seated = sum(r["seated"] for r in results)
print(f"pitch={pitch*1000:.4f}mm, R_cap_dyn={R_CAP_DYN*1000:.2f}mm, "
      f"predicted f={f_predicted:.3f}")
print(f"measured: {n_seated}/16 seated at offset={OFFSET*1000}mm, v_t={V_T*1000}mm/s")
for angle, r in zip(angles, results):
    print(f"  angle={math.degrees(angle):6.1f}deg  seated={r['seated']}  "
          f"time_to_seat={r.get('time_to_seat_s')}")
