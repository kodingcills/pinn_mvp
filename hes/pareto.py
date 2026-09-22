"""Failure-Pareto analysis with an explicit responsibility decomposition.

Ranking is a transparent product of raw components, which are always
reported alongside it:

    priority = frequency * severity_weight * haplous_responsibility * addressability_weight

Raw counts, responsibility, and addressability are never hidden behind the
ranking. ``UNKNOWN`` is tracked as a first-class row.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from hes.attribution import Attribution, haplous_responsibility

SEVERITY_WEIGHTS = {"S0": 0.0, "S1": 1.0, "S2": 2.0, "S3": 10.0, "S4": 40.0, "S5": 100.0}
ADDRESSABILITY_WEIGHTS = {
    "generically_addressable": 1.0,
    "technically_addressable": 0.8,
    "benchmark_fix": 0.6,
    "task_specific": 0.4,
    "upstream": 0.2,
    "non_addressable": 0.0,
    "unknown": 0.0,
}


@dataclass
class ParetoRow:
    cause_key: str
    primary_cause: str
    runtime_subcause: str | None
    count: int
    fraction_of_all: float
    fraction_of_failures: float
    severity: dict[str, int]
    haplous_responsibility: float
    addressability: str
    confidence: str
    priority: float
    example_scenarios: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cause_key": self.cause_key,
            "primary_cause": self.primary_cause,
            "runtime_subcause": self.runtime_subcause,
            "count": self.count,
            "fraction_of_all_episodes": self.fraction_of_all,
            "fraction_of_failures": self.fraction_of_failures,
            "severity": self.severity,
            "haplous_responsibility": self.haplous_responsibility,
            "addressability": self.addressability,
            "confidence": self.confidence,
            "priority": self.priority,
            "example_scenarios": self.example_scenarios,
        }


def _band(confidence: float) -> str:
    if confidence >= 0.7:
        return "high"
    if confidence >= 0.4:
        return "medium"
    return "low"


def build_pareto(
    failures: Sequence[tuple[Mapping[str, Any], Attribution]],
    *,
    n_all_episodes: int,
) -> list[dict[str, Any]]:
    """Group attributed failures, rank by priority, and always retain the
    raw components."""
    groups: dict[str, dict[str, Any]] = {}
    for row, attr in failures:
        key = f"{attr.primary.value}/{attr.subcause.value if attr.subcause else '-'}"
        group = groups.setdefault(
            key,
            {
                "primary": attr.primary.value,
                "subcause": attr.subcause.value if attr.subcause else None,
                "count": 0,
                "severity": {},
                "responsibility_sum": 0.0,
                "addressability": attr.addressability,
                "confidence_sum": 0.0,
                "examples": [],
            },
        )
        group["count"] += 1
        group["severity"][attr.severity] = group["severity"].get(attr.severity, 0) + 1
        group["responsibility_sum"] += haplous_responsibility(attr)
        group["confidence_sum"] += attr.confidence
        if len(group["examples"]) < 5:
            group["examples"].append(str(row.get("scenario_id") or row.get("episode_id")))

    n_failures = len(failures) or 1
    rows: list[ParetoRow] = []
    for key, group in groups.items():
        count = group["count"]
        responsibility = group["responsibility_sum"] / count
        severity_max = max(
            (SEVERITY_WEIGHTS[s] for s in group["severity"]), default=0.0
        )
        address_weight = ADDRESSABILITY_WEIGHTS.get(group["addressability"], 0.0)
        priority = (count / n_all_episodes if n_all_episodes else 0.0) * severity_max * responsibility * address_weight
        rows.append(
            ParetoRow(
                cause_key=key,
                primary_cause=group["primary"],
                runtime_subcause=group["subcause"],
                count=count,
                fraction_of_all=count / n_all_episodes if n_all_episodes else 0.0,
                fraction_of_failures=count / n_failures,
                severity=group["severity"],
                haplous_responsibility=responsibility,
                addressability=group["addressability"],
                confidence=_band(group["confidence_sum"] / count),
                priority=priority,
                example_scenarios=group["examples"],
            )
        )
    rows.sort(key=lambda r: (-r.priority, -r.count))
    return [row.as_dict() for row in rows]


def responsibility_decomposition(
    failures: Sequence[tuple[Mapping[str, Any], Attribution]],
    attainability: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """Coarse responsibility buckets required by the completion criteria.

    A runtime refusal is only charged as a caused loss when the scenario was
    *demonstrated* attainable. Refusals whose attainability is UNKNOWN are
    tracked separately: charging them as losses would assert a productivity
    loss we cannot show.
    """
    from hes.model import AttainabilityAxis

    buckets: dict[str, int] = {
        "correct_refusal": 0,
        "false_refusal_loss": 0,
        "haplous_caused_loss": 0,
        "haplous_unprevented_violation": 0,
        "runtime_refusal_attainability_unknown": 0,
        "upstream_controller_physics_limit": 0,
        "benchmark_evaluator": 0,
        "infrastructure": 0,
        "unknown": 0,
    }
    for row, attr in failures:
        sid = str(row.get("scenario_id") or row.get("episode_id"))
        axis = None
        if attainability is not None:
            entry = attainability.get(sid)
            axis = entry.axis if entry is not None else None
        primary = attr.primary.value
        is_refusal = str(row.get("reported_disposition")) == "REFUSED"
        if attr.subcause is not None and attr.subcause.value == "correct_infeasible_refusal":
            buckets["correct_refusal"] += 1
            continue
        if primary == "EXECUTION_RUNTIME" and is_refusal:
            if attr.subcause is not None and attr.subcause.value == "false_refusal":
                buckets["false_refusal_loss"] += 1
            elif axis is not None and axis is AttainabilityAxis.ATTAINABLE:
                buckets["haplous_caused_loss"] += 1
            elif axis is not None and axis is AttainabilityAxis.NON_ATTAINABLE:
                buckets["correct_refusal"] += 1
            else:
                buckets["runtime_refusal_attainability_unknown"] += 1
            continue
        if primary == "EXECUTION_RUNTIME":
            if int(row.get("n_hard_violations") or 0) > 0:
                buckets["haplous_unprevented_violation"] += 1
            else:
                buckets["haplous_caused_loss"] += 1
        elif primary in ("UPSTREAM_POLICY", "CONTROLLER", "MACHINE_PHYSICS", "ENVIRONMENT"):
            buckets["upstream_controller_physics_limit"] += 1
        elif primary == "BENCHMARK_EVALUATOR":
            buckets["benchmark_evaluator"] += 1
        elif primary == "INFRASTRUCTURE":
            buckets["infrastructure"] += 1
        else:
            buckets["unknown"] += 1
    return buckets