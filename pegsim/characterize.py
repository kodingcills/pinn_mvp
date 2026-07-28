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
from pegsim.rollout import EpisodeConfig, run_episode
from pegsim.scene import SolverSpec, TaskSpec
from pegsim.search import SpiralSpec


def run_offset_sweep(
    task: TaskSpec,
    solver: SolverSpec,
    gains: ImpedanceGains,
    offsets_m: list[float],
    angles_rad: list[float],
    z_start: float,
    z_end: float,
    z_rate: float,
    max_seconds: float,
    spiral_spec: SpiralSpec | None = None,
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
                task=task, solver=solver, gains=gains,
                z_start=z_start, z_end=z_end, z_rate=z_rate,
                initial_offset_r=offset, initial_offset_theta=angle,
                max_seconds=max_seconds, spiral_spec=spiral_spec,
            )
            try:
                log = run_episode(cfg)
                results.append({
                    "offset_m": offset,
                    "angle_rad": angle,
                    "seated": log["seated"],
                    "time_to_seat_s": log["time_to_seat_s"],
                    "max_tilt_deg": log["max_tilt_deg"],
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
