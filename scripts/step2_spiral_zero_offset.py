"""Milestone 4 sanity check: with zero initial offset, adding the spiral
search must not prevent seating or destabilize contact -- confirms the
search overlay itself before Milestone 4's real test (fixed offsets)."""
from __future__ import annotations

import sys

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
MAX_SECONDS = 10.0


def main() -> int:
    solver = SolverSpec()
    spiral_spec = SpiralSpec(task=TASK, delta_max=1.25 * TASK.capture_radius)

    cfg = EpisodeConfig(
        task=TASK, solver=solver, gains=GAINS,
        z_start=Z_START, z_end=Z_END, z_rate=Z_RATE,
        initial_offset_r=0.0, initial_offset_theta=0.0,
        max_seconds=MAX_SECONDS, spiral_spec=spiral_spec,
    )
    log = run_episode(cfg)

    finite_dist = log["min_contact_dist"][log["min_contact_dist"] < 1e30]
    max_interpenetration_mm = -finite_dist[finite_dist < 0].min() * 1000.0 if (finite_dist < 0).any() else 0.0

    print(f"seated: {log['seated']}")
    print(f"time_to_seat_s: {log['time_to_seat_s']:.4f}")
    print(f"max_interpenetration_mm: {max_interpenetration_mm:.5f}")
    print(f"final tip: {log['tip_pos'][-1]}")

    ok = log["seated"] and max_interpenetration_mm < TASK.radial_clearance * 1000.0
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
