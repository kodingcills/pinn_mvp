"""Milestone 4: capture-radius curve for the base controller alone
(benchmark condition B). Sweeps fixed offsets at 0.25R, 0.5R, 0.75R, R,
1.25R (R = capture_radius) at several angles each, reporting success rate
and time-to-seat per offset.

Because the friction cone is elliptic, results should be angle-independent
-- systematic variation with angle is a bug to investigate, not noise to
average over.
"""
from __future__ import annotations

import math
import sys

import numpy as np

from pegsim.control import ImpedanceGains
from pegsim.rollout import EpisodeConfig, run_episode
from pegsim.scene import SolverSpec, TaskSpec
from pegsim.search import SpiralSpec

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
GAINS = ImpedanceGains()
Z_START = 0.005
Z_END = -0.045
Z_RATE = 0.01
MAX_SECONDS = 12.0

OFFSET_FRACTIONS = (0.25, 0.5, 0.75, 1.0, 1.25)
ANGLES_DEG = (0, 45, 90, 135, 180, 225, 270, 315)


def main() -> int:
    solver = SolverSpec()
    spiral_spec = SpiralSpec(task=TASK, delta_max=1.25 * TASK.capture_radius)

    print(f"{'offset/R':10s} {'offset_mm':10s} {'success_rate':12s} {'mean_tts_s':11s} {'std_tts_s':10s} {'min_tts_s':10s} {'max_tts_s':10s}")
    print("-" * 75)

    for frac in OFFSET_FRACTIONS:
        offset_r = frac * TASK.capture_radius
        seated_flags = []
        times_to_seat = []
        per_angle = []
        for angle_deg in ANGLES_DEG:
            cfg = EpisodeConfig(
                task=TASK, solver=solver, gains=GAINS,
                z_start=Z_START, z_end=Z_END, z_rate=Z_RATE,
                initial_offset_r=offset_r, initial_offset_theta=math.radians(angle_deg),
                max_seconds=MAX_SECONDS, spiral_spec=spiral_spec,
            )
            log = run_episode(cfg)
            seated_flags.append(log["seated"])
            if log["seated"]:
                times_to_seat.append(log["time_to_seat_s"])
            per_angle.append((angle_deg, log["seated"], log["time_to_seat_s"]))

        success_rate = float(np.mean(seated_flags))
        tts = np.array(times_to_seat) if times_to_seat else np.array([math.nan])
        print(
            f"{frac:<10.2f} {offset_r*1000:<10.4f} {success_rate:<12.2f} "
            f"{np.nanmean(tts):<11.4f} {np.nanstd(tts):<10.4f} {np.nanmin(tts):<10.4f} {np.nanmax(tts):<10.4f}"
        )
        for angle_deg, seated, tts_val in per_angle:
            flag = "seated" if seated else "FAILED"
            tts_str = f"{tts_val:.4f}s" if seated else "--"
            print(f"    angle={angle_deg:4d}deg  {flag:7s} time_to_seat={tts_str}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
