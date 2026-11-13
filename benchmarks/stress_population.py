"""M4 stress population (``m4_stress@v1``): seeded scenarios in the regions where
the HEB ``population()`` never goes and competent guards can disagree.

Registered as an HES population class in ``hes/populations.py``. The frozen HEB
generator (``benchmarks/heb/population.py``) is reused for geometry constants
only and is not modified. Each scenario carries exactly one stress factor, so
a disposition difference is attributable to that factor. Parameter ranges are
the ones fixed in ``docs/eval_program/designs/M4_preregistration.md``; any
change is a new version (``v2``), never an edit of ``v1``.

Strata (assigned round-robin by index, so allocation is exact for any n):

- ``dropout_long``: one observation dropout window, start U(0.3, 1.0) s,
  duration U(0.25, 1.0) s. Straddles the machine's 0.5 s compliant-hold budget.
- ``stale_long``: one stale window (sample age 0.05 s > the 0.02 s bound),
  start U(0.3, 1.0) s, duration U(0.25, 1.0) s.
- ``rejection_long``: one command-rejection window, start U(0.3, 1.0) s,
  duration U(0.25, 1.0) s.
- ``tilt_limit``: insertion with a hard tilt limit, log-uniform on
  [1e-5, 1e-3] deg (nominal peak tilt is 8.9e-5 deg, G1_incumbent.md section 7).
- ``ceiling_retry``: insertion with a hard force ceiling U(1.0, 4.0) N and a
  declared process-force recovery permission (budget 1, 2, 4 or 6 retries,
  settle 0.1, 0.3 or 0.5 s).

Exclusions are designed in, not filtered after the fact: no slide carries a
translational disturbance (mesh artifact, KNOWN_ISSUES), every force limit is
below the 100 N sensed ceiling (G1 asymmetry 2), every tilt limit is below the
45 deg sensed range and every tolerance and state-age bound is above the
machine's resolution and state period (G1 asymmetry 3).
"""
from __future__ import annotations

import math
import random

from benchmarks.heb.metrics import PEN_ALLOW_M
from benchmarks.heb.population import (
    _SLIDE_MIN_FORCE_N,
    _SLIDE_PERSISTENCE_S,
    _SLIDE_TOLERANCE_M,
    _slide_path,
)
from benchmarks.heb.protocol import Scenario

VERSION = "v1"
STRATA = ("dropout_long", "stale_long", "rejection_long", "tilt_limit", "ceiling_retry")
TEMPORAL_STRATA = frozenset({"dropout_long", "stale_long", "rejection_long"})

WINDOW_START_S = (0.3, 1.0)
WINDOW_DURATION_S = (0.25, 1.0)
STALE_AGE_S = 0.05
STATE_MAX_AGE_S = 0.02
TILT_LIMIT_DEG_LOG10 = (-5.0, -3.0)
CEILING_N = (1.0, 4.0)
RECOVERY_BUDGETS = (1, 2, 4, 6)
RECOVERY_SETTLE_S = (0.1, 0.3, 0.5)
INTERIOR_OFFSET_M = (0.0, 0.0025)
NON_BINDING_INSERT_FORCE_N = 25.0
SLIDE_FORCE_N = 2.0


def _insertion(index: int, stratum: str, rng: random.Random, **fields) -> Scenario:
    return Scenario(
        id=f"m4s_{stratum}_{index:05d}",
        task_family="peg_insertion",
        initial_offset_m=rng.uniform(*INTERIOR_OFFSET_M),
        initial_bearing_rad=rng.uniform(0.0, 2.0 * math.pi),
        position_tolerance_m=0.0005 + PEN_ALLOW_M,
        state_max_age_s=STATE_MAX_AGE_S,
        tags=("task_a", "m4_stress", f"stratum:{stratum}")
        + (("recovery_authorized",) if fields.get("recovery_permitted_conditions") else ()),
        **{"max_force_n": NON_BINDING_INSERT_FORCE_N, **fields},
    )


def _slide(index: int, stratum: str, **fields) -> Scenario:
    start, end = _slide_path()
    return Scenario(
        id=f"m4s_{stratum}_{index:05d}",
        task_family="constrained_slide",
        path_start_m=start,
        path_end_m=end,
        start_position_m=start,
        min_force_n=_SLIDE_MIN_FORCE_N,
        max_force_n=SLIDE_FORCE_N,
        position_tolerance_m=_SLIDE_TOLERANCE_M,
        persistence_s=_SLIDE_PERSISTENCE_S,
        state_max_age_s=STATE_MAX_AGE_S,
        tags=("task_b", "m4_stress", f"stratum:{stratum}"),
        **fields,
    )


def stress_scenario(index: int, rng: random.Random, *, slide_fraction: float) -> Scenario:
    stratum = STRATA[index % len(STRATA)]
    if stratum in TEMPORAL_STRATA:
        slide = rng.random() < slide_fraction
        t0 = rng.uniform(*WINDOW_START_S)
        t1 = t0 + rng.uniform(*WINDOW_DURATION_S)
        window = {
            "dropout_long": {"dropout_windows_s": ((t0, t1),)},
            "stale_long": {"stale_windows_s": ((t0, t1, STALE_AGE_S),)},
            "rejection_long": {"command_rejection_windows_s": ((t0, t1),)},
        }[stratum]
        return _slide(index, stratum, **window) if slide else _insertion(index, stratum, rng, **window)
    if stratum == "tilt_limit":
        return _insertion(index, stratum, rng, max_tilt_deg=10.0 ** rng.uniform(*TILT_LIMIT_DEG_LOG10))
    return _insertion(
        index,
        stratum,
        rng,
        max_force_n=rng.uniform(*CEILING_N),
        recovery_permitted_conditions=("process_force",),
        max_recovery_actions=rng.choice(RECOVERY_BUDGETS),
        recovery_settle_s=rng.choice(RECOVERY_SETTLE_S),
    )


def stress_population(n: int, seed: int, *, slide_fraction: float = 0.28) -> list[Scenario]:
    """Deterministic per (n, seed); population(n) is a prefix of population(n + k)."""
    rng = random.Random(seed)
    return [stress_scenario(i, rng, slide_fraction=slide_fraction) for i in range(n)]

