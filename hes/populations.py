"""Versioned evaluation populations.

Populations are named and versioned so that development, regression, and
qualification claims cannot be silently pooled. A new scenario generator
produces a new version; old versions remain reproducible by seed.

The scenarios themselves come from the existing HEB generator
(``benchmarks.heb.population.population``); HES only fixes the sampling
contract per class.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from benchmarks.heb.population import population
from benchmarks.heb.protocol import Scenario

Generator = Callable[[int, int], list[Scenario]]


@dataclass(frozen=True)
class PopulationClass:
    name: str
    version: str
    purpose: str
    seed: int
    n: int
    slide_fraction: float = 0.28
    generator: Generator = population
    notes: str = ""

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"

    def generate(self) -> list[Scenario]:
        return self.generator(self.n, self.seed, slide_fraction=self.slide_fraction)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "purpose": self.purpose,
            "seed": self.seed,
            "n": self.n,
            "slide_fraction": self.slide_fraction,
            "notes": self.notes,
        }


# The v3 qualification package was generated with population(1000, seed) over
# the internal insert+slide family. The exact original seed is recorded in the
# committed manifest; this class reproduces the sampling contract, not a
# claim about the historical draw.
POPULATIONS: dict[str, PopulationClass] = {
    "dev": PopulationClass(
        name="dev",
        version="v1",
        purpose="iteration and debugging; never cited as qualification evidence",
        seed=7,
        n=40,
        notes="small internal insert+slide sample",
    ),
    "regression": PopulationClass(
        name="regression",
        version="v1",
        purpose="candidate-vs-reference gating on a frozen slice",
        seed=101,
        n=300,
        notes="paired across arms; unchanged between candidates",
    ),
    "qualification": PopulationClass(
        name="qualification",
        version="v1",
        purpose="claim-bearing evidence; frozen before the run",
        seed=2024,
        n=1000,
        notes="one draw per qualification campaign; do not repool across versions",
    ),
}


def list_populations() -> list[dict[str, Any]]:
    return [p.as_dict() for p in POPULATIONS.values()]


def get_population(name: str) -> PopulationClass:
    try:
        return POPULATIONS[name]
    except KeyError as exc:
        raise KeyError(f"unknown population {name!r}; known: {sorted(POPULATIONS)}") from exc