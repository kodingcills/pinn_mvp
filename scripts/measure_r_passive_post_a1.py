"""Disposable: re-measure R_passive after the A1 (two-phase insertion) fix,
with A2's measured pen_allow and A3's final-state+persistence criterion.
Confirms angle-independence at the new boundary specifically, not the whole
range (already confirmed angle-independent pre-fix; checking whether the
fix changes that)."""
from __future__ import annotations

import math

from pegsim.characterize import find_r_passive, run_offset_sweep, success_rate_by_offset
from pegsim.control import ImpedanceGains
from pegsim.rollout import InsertionProfile
from pegsim.scene import SolverSpec, TaskSpec

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
SOLVER = SolverSpec(timestep=0.00025)
GAINS = ImpedanceGains(k_xy=500.0, k_z=2000.0, k_rx=5.0, k_ry=5.0, k_rz=0.05)
INSERTION = InsertionProfile(k_z_search=2000.0, f_search=4.0)
PEN_ALLOW = 1.5 * 8e-6  # 1.5x measured seated lateral interpenetration

offsets_mm = [1.0, 1.25, 1.5, 1.6, 1.75, 2.0]
angles_deg = [0, 45, 90, 135, 180, 225, 270, 315]

results = run_offset_sweep(
    TASK, SOLVER, GAINS, INSERTION,
    offsets_m=[mm / 1000.0 for mm in offsets_mm],
    angles_rad=[math.radians(a) for a in angles_deg],
    z_start=0.005, max_seconds=8.0, pen_allow=PEN_ALLOW,
)
rates = success_rate_by_offset(results)
r_passive = find_r_passive(rates)

print(f"pen_allow = {PEN_ALLOW} m ({PEN_ALLOW*1000:.5f} mm)")
print(f"{'offset_mm':>10s} {'success_rate':>13s}")
for mm in offsets_mm:
    print(f"{mm:10.2f} {rates[mm/1000.0]:13.2f}")
print(f"\nR_passive (post A1 fix): {r_passive*1000 if r_passive else None} mm")
print(f"R_passive / (clearance+chamfer) = {r_passive/TASK.capture_radius if r_passive else None}")
