"""Gate 1 -- capture reliability at the demo shift magnitude. Decides
whether the recovery demo is buildable at all, and whether it can honestly
use all 16 directions or only the favorable (cardinal) ones.

VALIDATED_LOOSE_V1, search ON. offsets 1.0-2.25mm, 16 angles each. Reports
the full grid, cardinal vs diagonal capture rate separately (F6 found clean
capture at 0/90/180/270 and failure at the diagonals at 1.8mm -- if that
persists, a cardinal-only demo would cherry-pick), and demo_shift_max both
across all 16 angles and cardinals-only.
"""
from __future__ import annotations

import math

from pegsim.characterize import run_offset_sweep
from pegsim.rollout import VALIDATED_LOOSE_V1
cfg = VALIDATED_LOOSE_V1
spiral_spec = cfg.spiral
max_seconds = cfg.max_seconds

OFFSETS_MM = [1.0, 1.25, 1.5, 1.75, 2.0, 2.25]
ANGLES_DEG = [i * 22.5 for i in range(16)]
CARDINAL_DEG = {0.0, 90.0, 180.0, 270.0}
DIAGONAL_DEG = {45.0, 135.0, 225.0, 315.0}

results = run_offset_sweep(
    cfg.task, cfg.solver, cfg.gains, cfg.insertion,
    offsets_m=[mm / 1000.0 for mm in OFFSETS_MM],
    angles_rad=[math.radians(d) for d in ANGLES_DEG],
    z_start=cfg.z_start, max_seconds=max_seconds, spiral_spec=spiral_spec,
    pen_allow=cfg.pen_allow,
)

print(f"config=VALIDATED_LOOSE_V1 ({cfg.validated_by})")
print(f"max_seconds={max_seconds:.2f}s\n")

print("Full (offset, angle) grid: 1=seated, 0=not")
header = "offset_mm\\deg " + " ".join(f"{d:5.1f}" for d in ANGLES_DEG)
print(header)
by_offset_angle = {}
for r in results:
    by_offset_angle[(round(r["offset_m"] * 1000, 2), round(math.degrees(r["angle_rad"]), 1))] = r
for offset_mm in OFFSETS_MM:
    row = []
    for deg in ANGLES_DEG:
        r = by_offset_angle[(offset_mm, deg)]
        row.append("  1  " if r["seated"] else "  0  ")
    print(f"{offset_mm:13.2f} " + " ".join(row))

print(f"\n{'offset_mm':>10s} {'total':>8s} {'cardinal':>10s} {'diagonal':>10s} "
      f"{'other12':>9s} {'tts_mean_s':>11s} {'tts_max_s':>10s}")
demo_shift_max_all = None
demo_shift_max_cardinal = None
for offset_mm in OFFSETS_MM:
    rows = [by_offset_angle[(offset_mm, d)] for d in ANGLES_DEG]
    n_seated = sum(r["seated"] for r in rows)
    cardinal_rows = [by_offset_angle[(offset_mm, d)] for d in CARDINAL_DEG]
    diagonal_rows = [by_offset_angle[(offset_mm, d)] for d in DIAGONAL_DEG]
    other_rows = [r for r in rows if round(math.degrees(r["angle_rad"]), 1) not in CARDINAL_DEG
                  and round(math.degrees(r["angle_rad"]), 1) not in DIAGONAL_DEG]
    n_cardinal = sum(r["seated"] for r in cardinal_rows)
    n_diagonal = sum(r["seated"] for r in diagonal_rows)
    n_other = sum(r["seated"] for r in other_rows)
    tts = [r["time_to_seat_s"] for r in rows if r["seated"]]
    tts_mean = sum(tts) / len(tts) if tts else float("nan")
    tts_max = max(tts) if tts else float("nan")
    print(f"{offset_mm:10.2f} {n_seated:5d}/16 {n_cardinal:7d}/4 {n_diagonal:7d}/4 "
          f"{n_other:6d}/8 {tts_mean:11.3f} {tts_max:10.3f}")

    if n_seated / 16 >= 0.95 and demo_shift_max_all is None:
        pass  # tracked via last-passing below, see loop after
    if n_cardinal / 4 >= 0.95:
        demo_shift_max_cardinal = offset_mm
    if n_seated / 16 >= 0.95:
        demo_shift_max_all = offset_mm

print(f"\ndemo_shift_max (>=95% across all 16 angles): "
      f"{demo_shift_max_all if demo_shift_max_all is not None else 'NONE FOUND'} mm")
print(f"demo_shift_max (>=95% on cardinals only):     "
      f"{demo_shift_max_cardinal if demo_shift_max_cardinal is not None else 'NONE FOUND'} mm")
if demo_shift_max_all != demo_shift_max_cardinal:
    print("\n*** WARNING: cardinal-only and all-angle demo_shift_max DIFFER "
          "-- cherry-picking risk is real and quantified above. ***")

print("\nFailure-pattern check: does the cardinal/diagonal split still show "
      "the 0/90/180/270-favorable pattern at these (larger) offsets?")
for offset_mm in OFFSETS_MM:
    n_cardinal = sum(by_offset_angle[(offset_mm, d)]["seated"] for d in CARDINAL_DEG)
    n_diagonal = sum(by_offset_angle[(offset_mm, d)]["seated"] for d in DIAGONAL_DEG)
    print(f"  {offset_mm:.2f}mm: cardinal {n_cardinal}/4, diagonal {n_diagonal}/4")
