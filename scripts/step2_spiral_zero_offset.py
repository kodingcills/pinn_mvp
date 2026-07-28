"""Milestone 4 sanity check: with zero initial offset, adding the spiral
search must not prevent seating or destabilize contact -- confirms the
search overlay itself before Milestone 4's real test (fixed offsets).

Uses the stable gains config (K_rx=K_ry=5.0, dt=0.0005 -- see Stage A item
A5: dt=0.001 is unstable for this peg at K_rot=5, dt=0.0005 is verified
stable and is 2x cheaper than the 0.00025 used during diagnosis) and
InsertionProfile's two-phase axial strategy (Stage A item A1/A1b/A1c).
pen_allow measured from a zero-offset run, see PHASE2.md / commit history.
"""
from __future__ import annotations

import sys

from pegsim.control import ImpedanceGains
from pegsim.rollout import EpisodeConfig, InsertionProfile, run_episode
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
GAINS = ImpedanceGains(k_rx=5.0, k_ry=5.0, k_rz=0.05)
INSERTION = InsertionProfile()
Z_START = 0.005
MAX_SECONDS = 10.0
PEN_ALLOW = 3.7035e-05  # 1.5x measured max interpenetration near the floor


def main() -> int:
    solver = SolverSpec(timestep=0.0005)
    spiral_spec = SpiralSpec(task=TASK, delta_max=1.25 * TASK.capture_radius)

    cfg = EpisodeConfig(
        task=TASK, solver=solver, gains=GAINS, insertion=INSERTION,
        z_start=Z_START,
        initial_offset_r=0.0, initial_offset_theta=0.0,
        max_seconds=MAX_SECONDS, spiral_spec=spiral_spec,
    )
    log = run_episode(cfg, pen_allow=PEN_ALLOW)

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
