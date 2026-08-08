"""Reusable offset-sweep characterization routine.

This is a preview of the product's identification stage (measuring what a
given peg/hole/gain/solver configuration can actually capture), so it lives
here as a parameterized function, not a one-off script. The actual parameter
sweeps that CALL this stay in scripts/ as a list of dicts and a loop -- no
config framework.
"""
from __future__ import annotations

import math

from pegsim.control import ImpedanceGains
from pegsim.rollout import EpisodeConfig, InsertionProfile, run_episode
from pegsim.scene import SolverSpec, TaskSpec
from pegsim.search import SpiralSpec


def run_offset_sweep(
    task: TaskSpec,
    solver: SolverSpec,
    gains: ImpedanceGains,
    insertion: InsertionProfile,
    offsets_m: list[float],
    angles_rad: list[float],
    z_start: float,
    max_seconds: float,
    spiral_spec: SpiralSpec | None = None,
    pen_allow: float = 0.0,
) -> list[dict]:
    """Run one episode per (offset, angle) pair. Returns a flat list of
    per-trial result dicts -- one row per trial, easy to turn into a table
    or a success-rate-vs-offset curve. A tilt-instability assert during a
    trial is recorded as a non-seated result with unstable=True, not raised
    -- one bad trial should not abort the sweep.
    """
    results = []
    for offset in offsets_m:
        for angle in angles_rad:
            cfg = EpisodeConfig(
                task=task, solver=solver, gains=gains, insertion=insertion,
                z_start=z_start,
                initial_offset_r=offset, initial_offset_theta=angle,
                max_seconds=max_seconds, spiral_spec=spiral_spec,
            )
            try:
                log = run_episode(cfg, pen_allow=pen_allow)
                results.append({
                    "offset_m": offset,
                    "angle_rad": angle,
                    "seated": log.success,
                    "time_to_seat_s": log.time_to_seat_s,
                    "max_tilt_deg": log.max_tilt_deg,
                    "unstable": False,
                })
            except AssertionError:
                results.append({
                    "offset_m": offset,
                    "angle_rad": angle,
                    "seated": False,
                    "time_to_seat_s": math.inf,
                    "max_tilt_deg": None,
                    "unstable": True,
                })
    return results


def success_rate_by_offset(results: list[dict]) -> dict[float, float]:
    """Aggregate run_offset_sweep's flat trial list into {offset_m: success_rate}."""
    by_offset: dict[float, list[bool]] = {}
    for r in results:
        by_offset.setdefault(r["offset_m"], []).append(r["seated"])
    return {offset: sum(flags) / len(flags) for offset, flags in by_offset.items()}


def find_r_passive(success_by_offset: dict[float, float]) -> float | None:
    """Largest offset (in the sorted input) at which success rate is still
    >= 50%. Returns None if no offset reaches 50%."""
    passing = [offset for offset, rate in success_by_offset.items() if rate >= 0.5]
    return max(passing) if passing else None


def measure_settling_depths(
    task: TaskSpec,
    solver: SolverSpec,
    gains: ImpedanceGains,
    insertion: InsertionProfile,
    offsets_m: list[float],
    z_start: float = 0.005,
    seconds: float = 2.0,
) -> dict[float, float]:
    """How deep the tip actually rests below the flat table at each lateral
    offset, with the search running but the spiral OFF.

    This is the measured lower bound on DEPTH_COMMIT (see
    pegsim.rollout.assert_commit_gate_reachable). It exists because the H1
    failure was a threshold placed at 0.05mm inside a settling band that
    turned out to be 0.06-0.10mm -- a number nobody had measured. Hardcoding
    0.10mm here would just move the same mistake one level up.

    Near the hole the peg's rim partly overhangs the chamfer, so it settles
    deeper than it does further out; this returns the whole per-offset curve
    rather than one number, and callers take the max over the offsets their
    search will actually visit.

    Non-circular by construction: the returned depth is only meaningful if
    the episode never committed (a committed episode is descending into the
    hole, not resting on it), and that is asserted. If it fires, DEPTH_COMMIT
    is genuinely too shallow for this config -- which is the finding, not a
    measurement error.
    """
    depths = {}
    for offset in offsets_m:
        cfg = EpisodeConfig(
            task=task, solver=solver, gains=gains, insertion=insertion,
            z_start=z_start, initial_offset_r=offset, initial_offset_theta=0.0,
            max_seconds=seconds, spiral_spec=None,
        )
        log = run_episode(cfg)
        assert not log.committed.any(), (
            f"peg committed at offset {offset * 1000:.3f}mm with the spiral "
            "off -- the resting depth there already exceeds DEPTH_COMMIT, so "
            "there is no settling measurement to take and the gate cannot "
            "distinguish resting from committing at this config"
        )
        # Last 20% of the episode: past the ramp, at rest on the table.
        tail = log.tip_pos[int(0.8 * len(log.tip_pos)):, 2]
        depths[offset] = float(-tail.mean())
    return depths
