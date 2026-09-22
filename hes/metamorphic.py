"""Metamorphic relations for the runtime's decision surface.

These do not test numeric optimum; they test required invariances of any
correct runtime. A run that violates one is a semantic defect even if its
headline success rate is high.

Relations:

- requirement relaxation: relaxing a hard requirement (larger force cap,
  longer deadline, looser tolerance, fresher-state allowance) can never
  turn an ACCEPT into a REFUSE;
- requirement tightening: tightening can never turn a REFUSE-by-capability
  into an ACCEPT (it may turn an ACCEPT into a REFUSE);
- decision-irrelevant perturbation: changing only evaluator metadata
  (scenario id, tags) must not change the disposition;
- re-parameterization: the disposition must be stable when a continuous
  clearance value is represented at a different but equivalent precision.

The transforms are pure; the driver takes a runner so the relations can be
checked with a stub in unit tests and with the real benchmark in
qualification runs.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from benchmarks.heb.protocol import Scenario, SystemRun

Runner = Callable[[Scenario], SystemRun]

_RELAX = {
    "max_force_n": 1.05,
    "deadline_s": 1.25,
    "state_max_age_s": 1.1,
    "position_tolerance_m": 1.1,
    "max_tilt_deg": 1.1,
}


def relax_requirements(scenario: Scenario) -> Scenario:
    changes: dict[str, Any] = {}
    for field_name, factor in _RELAX.items():
        value = getattr(scenario, field_name)
        if value is not None and value > 0:
            changes[field_name] = value * factor
    return dataclasses.replace(scenario, **changes) if changes else scenario


def tighten_requirements(scenario: Scenario) -> Scenario:
    changes: dict[str, Any] = {}
    for field_name, factor in _RELAX.items():
        value = getattr(scenario, field_name)
        if value is not None and value > 0:
            changes[field_name] = value / factor
    return dataclasses.replace(scenario, **changes) if changes else scenario


def irrelevant_perturbation(scenario: Scenario) -> Scenario:
    return dataclasses.replace(
        scenario,
        id=f"{scenario.id}#meta",
        tags=tuple(scenario.tags) + ("irrelevant_metadata",),
    )


def reprerametrize(scenario: Scenario) -> Scenario:
    """Clearance re-parameterization: identical physical clearance expressed
    with a different float representation."""
    if scenario.clearance_delta_m is None:
        return scenario
    value = scenario.clearance_delta_m
    return dataclasses.replace(scenario, clearance_delta_m=value + 1e-12 - 1e-12 + 0.0)


@dataclass(frozen=True)
class MetamorphicResult:
    relation: str
    scenario_id: str
    passed: bool
    detail: str
    left: str
    right: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "detail": self.detail,
            "left_disposition": self.left,
            "right_disposition": self.right,
        }


def _disposition(run: SystemRun) -> str:
    return run.disposition


def check_relaxation(scenario: Scenario, runner: Runner) -> MetamorphicResult:
    original = _disposition(runner(scenario))
    relaxed = _disposition(runner(relax_requirements(scenario)))
    ok = not (original == "COMPLETE" and relaxed == "REFUSED")
    return MetamorphicResult(
        "relaxation_monotone",
        scenario.id,
        ok,
        "relaxing requirements never converts ACCEPT to REFUSE",
        original,
        relaxed,
    )


def check_tightening(scenario: Scenario, runner: Runner) -> MetamorphicResult:
    original = _disposition(runner(scenario))
    tightened = _disposition(runner(tighten_requirements(scenario)))
    ok = not (original == "REFUSED" and tightened == "COMPLETE")
    return MetamorphicResult(
        "tightening_monotone",
        scenario.id,
        ok,
        "tightening requirements never converts REFUSE to ACCEPT",
        original,
        tightened,
    )


def check_irrelevant(scenario: Scenario, runner: Runner) -> MetamorphicResult:
    original = _disposition(runner(scenario))
    perturbed = _disposition(runner(irrelevant_perturbation(scenario)))
    ok = original == perturbed
    return MetamorphicResult(
        "irrelevant_invariance",
        scenario.id,
        ok,
        "evaluator metadata must not change the decision",
        original,
        perturbed,
    )


def check_reparameterization(scenario: Scenario, runner: Runner) -> MetamorphicResult:
    original = _disposition(runner(scenario))
    changed = _disposition(runner(reprerametrize(scenario)))
    ok = original == changed
    return MetamorphicResult(
        "reparameterization_invariance",
        scenario.id,
        ok,
        "equivalent continuous representation must not change the decision",
        original,
        changed,
    )


RELATIONS = {
    "relaxation_monotone": check_relaxation,
    "tightening_monotone": check_tightening,
    "irrelevant_invariance": check_irrelevant,
    "reparameterization_invariance": check_reparameterization,
}


def run_metamorphic(
    scenarios: Sequence[Scenario],
    runner: Runner,
    *,
    relations: Sequence[str] = tuple(RELATIONS),
) -> dict[str, Any]:
    results: list[MetamorphicResult] = []
    for scenario in scenarios:
        for relation in relations:
            results.append(RELATIONS[relation](scenario, runner))
    violations = [r.as_dict() for r in results if not r.passed]
    return {
        "n_checks": len(results),
        "n_violations": len(violations),
        "passed": not violations,
        "violations": violations[:50],
        "by_relation": {
            relation: {
                "n": sum(1 for r in results if r.relation == relation),
                "violations": sum(
                    1 for r in results if r.relation == relation and not r.passed
                ),
            }
            for relation in relations
        },
    }