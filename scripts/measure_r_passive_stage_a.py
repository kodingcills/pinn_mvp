"""Disposable: Item 2's R_passive re-measurement for the Stage A report.
Search OFF, 0.25mm steps, 8 angles, blind-hole floor, ground-truth seat
check (tip+mouth+wedge), gains verified stable at this dt (K_rx=K_ry=5.0,
dt=0.00025 -- see the Stage A report for why)."""
from __future__ import annotations

import math

from pegsim.characterize import find_r_passive, run_offset_sweep, success_rate_by_offset
from pegsim.control import ImpedanceGains
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
GAINS = ImpedanceGains(k_rx=5.0, k_ry=5.0, k_rz=0.05)

offsets_mm = [i * 0.25 for i in range(1, 21)]  # 0.25 .. 5.0mm
angles_deg = [0, 45, 90, 135, 180, 225, 270, 315]

results = run_offset_sweep(
    TASK, SOLVER, GAINS,
    offsets_m=[mm / 1000.0 for mm in offsets_mm],
    angles_rad=[math.radians(a) for a in angles_deg],
    z_start=0.005, z_end=-0.045, z_rate=0.01, max_seconds=8.0,
)

rates = success_rate_by_offset(results)
r_passive = find_r_passive(rates)

print(f"{'offset_mm':>10s} {'success_rate':>13s} {'max_tilt_deg':>13s} {'n_unstable':>11s}")
for mm in offsets_mm:
    offset = mm / 1000.0
    rows = [r for r in results if r["offset_m"] == offset]
    n_unstable = sum(r["unstable"] for r in rows)
    tilts = [r["max_tilt_deg"] for r in rows if r["max_tilt_deg"] is not None]
    max_tilt = max(tilts) if tilts else float("nan")
    print(f"{mm:10.2f} {rates[offset]:13.2f} {max_tilt:13.3f} {n_unstable:11d}")

print(f"\nR_passive (largest offset with success rate >= 50%): {r_passive*1000 if r_passive else None} mm")
print(f"R_passive / (clearance+chamfer) = {r_passive/TASK.capture_radius if r_passive else None}")
