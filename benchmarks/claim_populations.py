"""Development populations for the claim-to-experiment compiler (issue #20).

DEV only: never cited as qualification evidence, and disjoint from every sealed
population by scenario id and content (enforced by ``hes.claim_compiler``).
Scenario construction reuses ``benchmarks.stress_population._insertion`` so
geometry, tolerance, the 25 N ceiling and the 0.02 s state-age bound match M4.

- ``temporal_hold_dev@v1``: one dropout or stale window per insertion, its
  duration below or above the machine's 0.5 s compliant-hold budget. Cells
  (fault x budget class) are assigned round-robin by index.
- ``admission_dev@v1``: fault-free insertions, for command-mode admission.
"""
from __future__ import annotations

import itertools
import random
from dataclasses import replace

from benchmarks.heb.protocol import Scenario
from benchmarks.stress_population import STALE_AGE_S, WINDOW_START_S, _insertion

HOLD_BUDGET_S = 0.5  # MachineCapabilities.max_hold_unobserved_s default
DURATION_S = {"below_budget": (0.15, 0.40), "above_budget": (0.60, 0.90)}
CELLS = tuple(itertools.product(("dropout", "stale"), DURATION_S))


def temporal_hold_population(n: int, seed: int, *, slide_fraction: float = 0.0) -> list[Scenario]:
    """Deterministic per (n, seed); insertion only (slide_fraction unused)."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        fault, budget = CELLS[i % len(CELLS)]
        t0 = rng.uniform(*WINDOW_START_S)
        t1 = t0 + rng.uniform(*DURATION_S[budget])
        window = ({"dropout_windows_s": ((t0, t1),)} if fault == "dropout"
                  else {"stale_windows_s": ((t0, t1, STALE_AGE_S),)})
        base = _insertion(i, f"{fault}_long", rng, **window)
        out.append(replace(base, id=f"cth_{fault}_{budget}_{i:04d}",
                           tags=("task_a", "claims_dev", f"fault:{fault}", f"stratum:{budget}")))
    return out


def admission_population(n: int, seed: int, *, slide_fraction: float = 0.0) -> list[Scenario]:
    """Fault-free insertions; deterministic per (n, seed)."""
    rng = random.Random(seed)
    return [replace(_insertion(i, "nominal", rng), id=f"cadm_{i:04d}",
                    tags=("task_a", "claims_dev", "stratum:nominal")) for i in range(n)]
