"""Adaptive one-dimensional boundary search.

Boundary regions matter more than average case: the interesting runtime
behavior (marginal feasibility, marginal safety, marginal timing) lives at
transitions. This module finds the transition of a boolean probe along a
continuous scenario parameter with a bounded evaluation budget, reports the
bracket, and refuses to claim a boundary when the response is non-monotone.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from benchmarks.heb.protocol import Scenario, SystemRun

Runner = Callable[[Scenario], SystemRun]
ScalarProbe = Callable[[float], float | bool | str]


@dataclass(frozen=True)
class BoundaryResult:
    parameter: str
    low: float
    high: float
    n_evals: int
    monotone: bool
    direction: str
    boundary_estimate: float | None
    bracket_width: float | None
    evaluations: list[tuple[float, float | bool | str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "parameter": self.parameter,
            "low": self.low,
            "high": self.high,
            "n_evals": self.n_evals,
            "monotone": self.monotone,
            "direction": self.direction,
            "boundary_estimate": self.boundary_estimate,
            "bracket_width": self.bracket_width,
            "evaluations": [
                {"parameter": p, "value": v} for p, v in self.evaluations
            ],
        }


def _truthy(value: float | bool | str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.upper() in ("COMPLETE", "PASS", "TRUE")
    return value != 0.0


def find_boundary(
    probe: ScalarProbe,
    *,
    parameter: str,
    low: float,
    high: float,
    max_evals: int = 24,
    tolerance: float = 1e-4,
) -> BoundaryResult:
    """Bisect a monotone boolean transition on ``[low, high]``.

    ``probe`` must be deterministic. Raised (unexpected) exceptions are not
    swallowed: a boundary search that cannot evaluate is a failed search,
    not a boundary at zero.
    """
    evaluations: list[tuple[float, float | bool | str]] = []

    def evaluate(x: float) -> bool:
        value = probe(x)
        evaluations.append((x, value))
        return _truthy(value)

    lo_true = evaluate(low)
    hi_true = evaluate(high)
    if lo_true == hi_true:
        return BoundaryResult(
            parameter, low, high, len(evaluations), True, "none", None, None, evaluations
        )
    direction = "increasing" if hi_true else "decreasing"
    a, b = low, high
    n = len(evaluations)
    while n < max_evals and (b - a) > tolerance:
        mid = 0.5 * (a + b)
        if evaluate(mid) == lo_true:
            a = mid
        else:
            b = mid
        n = len(evaluations)
    # Monotonicity check: the final bracket endpoints must differ and all
    # evaluated points must respect the direction.
    monotone = _check_monotone(evaluations, direction)
    estimate = 0.5 * (a + b) if monotone else None
    return BoundaryResult(
        parameter=parameter,
        low=low,
        high=high,
        n_evals=len(evaluations),
        monotone=monotone,
        direction=direction,
        boundary_estimate=estimate,
        bracket_width=(b - a),
        evaluations=evaluations,
    )


def _check_monotone(
    evaluations: Sequence[tuple[float, float | bool | str]], direction: str
) -> bool:
    points = sorted(evaluations, key=lambda item: item[0])
    flags = [_truthy(v) for _, v in points]
    if direction == "increasing":
        return all(not flags[i] or flags[i + 1] for i in range(len(flags) - 1))
    return all(flags[i] or not flags[i + 1] for i in range(len(flags) - 1))


def find_decision_boundary(
    scenario: Scenario,
    runner: Runner,
    *,
    parameter: str,
    low: float,
    high: float,
    predicate: Callable[[SystemRun], bool] | None = None,
    max_evals: int = 24,
    tolerance: float = 1e-4,
) -> BoundaryResult:
    """Search a scenario parameter for a transition in the runtime decision.

    ``predicate`` defaults to "the runtime accepts the request".
    """
    decide = predicate or (lambda run: run.disposition != "REFUSED")

    def probe(value: float) -> bool:
        variant = dataclasses.replace(scenario, **{parameter: value})
        return decide(runner(variant))

    return find_boundary(
        probe, parameter=parameter, low=low, high=high, max_evals=max_evals, tolerance=tolerance
    )


def boundary_uncertainty(
    scenario: Scenario,
    runner: Runner,
    *,
    parameter: str,
    low: float,
    high: float,
    predicated_around: float,
    repeats: int = 5,
) -> dict[str, Any]:
    """Boundary uncertainty from repeated search in a shrinking window
    around an observed boundary. Deterministic runs return identical
    estimates; the spread is reported as zero rather than invented."""
    estimates: list[float] = []
    window = max(abs(high - low) * 0.1, 1e-6)
    for _ in range(repeats):
        result = find_decision_boundary(
            scenario,
            runner,
            parameter=parameter,
            low=max(low, predicated_around - window),
            high=min(high, predicated_around + window),
        )
        if result.boundary_estimate is not None:
            estimates.append(result.boundary_estimate)
    if not estimates:
        return {"n": 0, "reason": "no boundary found in window"}
    return {
        "n": len(estimates),
        "estimate": sum(estimates) / len(estimates),
        "min": min(estimates),
        "max": max(estimates),
        "spread": max(estimates) - min(estimates),
    }