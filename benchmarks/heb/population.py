"""HEB randomized scenario population: seeded, stratified, data-only.

The development/qualification/final sets are hand-designed. This module
generates LARGE randomized populations over the same scenario axes the
hand sets already exercise, for Phase A (broad) and Phase C (boundary)
of the simulation fleet. Scenario generation is benchmark-owned truth;
nothing here touches runtime semantics.

Strata are recorded in tags so discovery (biased) and estimation
(unbiased) samples can be separated downstream.
"""
from __future__ import annotations

import math
import random

from benchmarks.heb.metrics import PEN_ALLOW_M
from benchmarks.heb.protocol import Scenario
from benchmarks.heb.sliding import _R_PATH_M, _SLIDE_MIN_FORCE_N

# Insertion: measured capture boundary is 3.0-3.5 mm (results/
# heb_sweeps_dev.json offset sweep). Stratify around it.
_INSERT_TOL_BASE_M = 0.0005 + PEN_ALLOW_M
_INSERT_TOL_TIGHT_M = 0.0003 + PEN_ALLOW_M

_FACETS = (16, 24, 32, 48, 64)
_CLEARANCE_DELTAS_M = (-0.0002, -0.0001, 0.0, 0.0001, 0.0002)
_PLATE_THICKNESS_M = (0.010, 0.015, 0.020)
_INSERT_FORCE_LIMITS_N = (12.0, 15.0, 20.0, 25.0)
_INSERT_DEADLINES_S = (1.8, 2.0, 2.5, 3.0, 5.0)
_STATE_MAX_AGES_S = (0.01, 0.02, 0.05)
_RECEIVE_DELAYS_S = ((0.0, 0.5), (0.005, 0.2), (0.01, 0.2), (0.05, 0.1))

_SLIDE_FORCE_LIMITS_N = (1.0, 1.25, 1.5, 2.0)
_SLIDE_TOLERANCE_M = 6e-4
_SLIDE_PERSISTENCE_S = 0.2
_SLIDE_MIN_FORCE_N = 0.06
_SLIDE_ARC_HALF = math.radians(60.0) / 2.0
_MID_SLIDE_S = 1.1


def _slide_path() -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    start = (
        _R_PATH_M * math.cos(-_SLIDE_ARC_HALF),
        _R_PATH_M * math.sin(-_SLIDE_ARC_HALF),
        0.0,
    )
    end = (
        _R_PATH_M * math.cos(_SLIDE_ARC_HALF),
        _R_PATH_M * math.sin(_SLIDE_ARC_HALF),
        0.0,
    )
    return start, end


def _sample_offset_m(rng: random.Random) -> tuple[float, str]:
    """Offset strata: interior / boundary / beyond the measured capture
    boundary. Boundary stratum is oversampled relative to uniform so the
    refusal/capture boundary is resolved; stratum tags keep discovery and
    estimation separable."""
    u = rng.random()
    if u < 0.35:
        return rng.uniform(0.0, 0.0025), "interior"
    if u < 0.75:
        return rng.uniform(0.0025, 0.0035), "boundary"
    return rng.uniform(0.0035, 0.005), "beyond"


def _window(
    rng: random.Random, *, t_lo: float, t_hi: float, dur_choices: tuple[float, ...]
) -> tuple[float, float]:
    return rng.uniform(t_lo, t_hi), rng.choice(dur_choices)


def insertion_scenario(index: int, rng: random.Random) -> Scenario:
    offset, stratum = _sample_offset_m(rng)
    bearing = rng.uniform(0.0, 2.0 * math.pi)
    facet_count = rng.choice(_FACETS)
    clearance_delta = rng.choice(_CLEARANCE_DELTAS_M)
    plate = rng.choice(_PLATE_THICKNESS_M)
    tight = clearance_delta < 0.0

    max_force_n: float | None = None
    u = rng.random()
    if u < 0.05:
        max_force_n = 2.0  # guaranteed-abort class: exercises stop forks
    elif u < 0.60:
        max_force_n = rng.choice(_INSERT_FORCE_LIMITS_N)

    deadline_s: float | None = None
    if rng.random() < 0.40:
        deadline_s = rng.choice(_INSERT_DEADLINES_S)

    state_max_age_s = rng.choice(_STATE_MAX_AGES_S)
    delays = _RECEIVE_DELAYS_S
    receive_delay_s = rng.choices([d for d, _ in delays], [w for _, w in delays])[0]

    dropout_windows_s: tuple[tuple[float, float], ...] = ()
    if rng.random() < 0.30:
        t0, dur = _window(rng, t_lo=0.2, t_hi=1.2, dur_choices=(0.005, 0.02, 0.2))
        dropout_windows_s = ((t0, t0 + dur),)

    stale_windows_s: tuple[tuple[float, float, float], ...] = ()
    if rng.random() < 0.30:
        t0 = rng.uniform(0.2, 1.2)
        dur = rng.uniform(0.1, 0.2)
        stale_windows_s = ((t0, t0 + dur, 0.05),)

    command_rejection_windows_s: tuple[tuple[float, float], ...] = ()
    if rng.random() < 0.20:
        t0 = rng.uniform(0.2, 1.2)
        dur = rng.choice((0.0005, 0.02))
        command_rejection_windows_s = ((t0, t0 + dur),)

    fault_at_s: float | None = None
    if rng.random() < 0.08:
        fault_at_s = rng.uniform(0.4, 2.0)

    disturbances: tuple[tuple[float, tuple[float, float, float, float, float, float]], ...] = ()
    if rng.random() < 0.12:
        t = rng.uniform(0.3, 1.5)
        dv = rng.uniform(0.05, 0.4) * rng.choice((-1.0, 1.0))
        disturbances = ((t, (0.0, 0.0, dv, 0.0, 0.0, 0.0)),)

    tags = ("task_a", "population", f"offset:{stratum}")
    return Scenario(
        id=f"fleet_insert_{index:06d}",
        task_family="peg_insertion",
        facet_count=facet_count,
        clearance_delta_m=clearance_delta,
        plate_thickness_m=plate,
        initial_offset_m=offset,
        initial_bearing_rad=bearing,
        max_force_n=max_force_n,
        deadline_s=deadline_s,
        state_max_age_s=state_max_age_s,
        position_tolerance_m=_INSERT_TOL_TIGHT_M if tight else _INSERT_TOL_BASE_M,
        receive_delay_s=receive_delay_s,
        dropout_windows_s=dropout_windows_s,
        stale_windows_s=stale_windows_s,
        command_rejection_windows_s=command_rejection_windows_s,
        fault_at_s=fault_at_s,
        disturbances=disturbances,
        tags=tags,
    )


def slide_scenario(index: int, rng: random.Random) -> Scenario:
    start, end = _slide_path()
    max_force_n = rng.choice(_SLIDE_FORCE_LIMITS_N)

    disturbances: tuple[tuple[float, tuple[float, float, float, float, float, float]], ...] = ()
    recovery_conditions: tuple[str, ...] = ()
    severe = rng.random() < 0.05
    if severe:
        # Measured unrecoverable class (rotational impulse past the
        # machine tilt guard): rare-failure exposure, no recovery grant.
        disturbances = ((_MID_SLIDE_S, (0.0, 0.0, 0.0, 100.0, 0.0, 0.0)),)
    elif rng.random() < 0.45:
        dv = rng.choice((0.05, -0.25, -0.15, 0.15))
        disturbances = (
            (_MID_SLIDE_S + rng.uniform(-0.2, 0.2), (0.0, 0.0, dv, 0.0, 0.0, 0.0)),
        )
        # Recovery authorization only below the facet-tap peak band,
        # where it is the demonstrated value mechanism.
        if max_force_n <= 1.5 and rng.random() < 0.6:
            recovery_conditions = ("process_force",)

    deadline_s: float | None = None
    u = rng.random()
    if u < 0.10:
        deadline_s = 1.0  # measured infeasible (slide completes 1.95-6.1 s)
    elif u < 0.30:
        deadline_s = rng.choice((2.0, 3.0))

    stale_windows_s: tuple[tuple[float, float, float], ...] = ()
    if rng.random() < 0.20:
        t0 = _MID_SLIDE_S + rng.uniform(-0.3, 0.3)
        stale_windows_s = ((t0, t0 + 0.2, 0.05),)

    dropout_windows_s: tuple[tuple[float, float], ...] = ()
    if rng.random() < 0.15:
        t0 = rng.uniform(0.3, 1.3)
        dur = rng.choice((0.005, 0.02))
        dropout_windows_s = ((t0, t0 + dur),)

    command_rejection_windows_s: tuple[tuple[float, float], ...] = ()
    if rng.random() < 0.15:
        t0 = _MID_SLIDE_S + rng.uniform(-0.3, 0.3)
        command_rejection_windows_s = ((t0, t0 + 0.02),)

    tags = ("task_b", "population", "severe" if severe else "normal")
    return Scenario(
        id=f"fleet_slide_{index:06d}",
        task_family="constrained_slide",
        path_start_m=start,
        path_end_m=end,
        start_position_m=start,
        min_force_n=_SLIDE_MIN_FORCE_N,
        max_force_n=max_force_n,
        position_tolerance_m=_SLIDE_TOLERANCE_M,
        persistence_s=_SLIDE_PERSISTENCE_S,
        deadline_s=deadline_s,
        state_max_age_s=0.02,
        recovery_permitted_conditions=recovery_conditions,
        max_recovery_actions=6 if recovery_conditions else 0,
        recovery_settle_s=0.3 if recovery_conditions else 0.0,
        disturbances=disturbances,
        stale_windows_s=stale_windows_s,
        dropout_windows_s=dropout_windows_s,
        command_rejection_windows_s=command_rejection_windows_s,
        tags=tags,
    )


def population(n: int, seed: int, *, slide_fraction: float = 0.28) -> list[Scenario]:
    """A seeded randomized scenario population. Deterministic per (n, seed)."""
    rng = random.Random(seed)
    scenarios: list[Scenario] = []
    for i in range(n):
        if rng.random() < slide_fraction:
            scenarios.append(slide_scenario(i, rng))
        else:
            scenarios.append(insertion_scenario(i, rng))
    return scenarios
