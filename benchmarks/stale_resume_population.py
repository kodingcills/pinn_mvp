"""E19 development population (``e19_stale_resume_dev@v1``): temporal-hold
windows crossed with the contact phase at hold entry, for issue #19.

Development only (docs/engineering/experiments/E19_stale_resume_dev.md). It is
not ``m4_stress@v1``: new seed, new ids, and no scenario shares a fault window
with the frozen M4 population (tests/test_stale_resume_population.py). Scenario
construction reuses ``benchmarks.stress_population._insertion`` so geometry,
tolerance, the 25 N ceiling and the 0.02 s state-age bound match M4.

Full factorial, assigned round-robin by index (allocation is exact when n is a
multiple of len(CELLS)): fault kind x intended phase at hold entry x window
duration. The intended phase only sets the window-start range; the analysis
stratifies by the phase observed at window start (protocol section 3).
"""
from __future__ import annotations

import itertools
import random
from dataclasses import replace

from benchmarks.heb.protocol import Scenario
from benchmarks.stress_population import STALE_AGE_S, _insertion

POPULATION_ID = "e19_stale_resume_dev@v1"
SEED = 19019
N = 64
FAULTS = ("stale", "rejection")
# Window-start ranges (s). Nominal approach reaches the hole surface near 0.52 s
# (results/e19/diagnosis/summary.json); fastest M4-control seating is 1.65 s.
PHASE_START_S = {
    "free_approach": (0.15, 0.35),
    "contact_onset": (0.45, 0.55),
    "in_hole": (0.80, 1.30),
    "near_seat": (1.45, 1.60),
}
DURATIONS_S = (0.10, 0.25, 0.40, 0.60)
CELLS = tuple(itertools.product(FAULTS, PHASE_START_S, DURATIONS_S))


def e19_scenario(index: int, rng: random.Random) -> Scenario:
    fault, phase, duration = CELLS[index % len(CELLS)]
    t0 = rng.uniform(*PHASE_START_S[phase])
    window = (
        {"stale_windows_s": ((t0, t0 + duration, STALE_AGE_S),)}
        if fault == "stale"
        else {"command_rejection_windows_s": ((t0, t0 + duration),)}
    )
    base = _insertion(index, f"{fault}_long", rng, **window)
    return replace(
        base,
        id=f"e19_{fault}_{phase}_{int(duration * 1000):03d}ms_{index:04d}",
        tags=("task_a", "e19_stale_resume_dev", f"fault:{fault}", f"phase:{phase}", f"duration_ms:{int(duration * 1000)}"),
    )


def e19_population(n: int = N, seed: int = SEED, *, slide_fraction: float = 0.0) -> list[Scenario]:
    """Deterministic per (n, seed); insertion only, so slide_fraction is unused
    (kept for the HES PopulationClass generator signature)."""
    rng = random.Random(seed)
    return [e19_scenario(i, rng) for i in range(n)]
