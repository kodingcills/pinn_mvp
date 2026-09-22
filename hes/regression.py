"""Candidate vs reference regression gates and slice gates.

Two layers:

1. metric-vector comparison with explicit per-metric direction and
   tolerance, plus a paired scenario-level bootstrap for rate metrics;
2. slice gates: a global improvement that hides a regression in one task
   family or offset stratum is rejected.

Nothing here reruns physics or rewrites history; it compares already
computed HES analysis outputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evaluation import stats as st

# metric path -> (direction, default tolerance in absolute units)
METRIC_CONTRACT: dict[str, tuple[str, float]] = {
    "system.bounded_success": ("higher", 0.0),
    "system.coverage": ("higher", 0.0),
    "authority.execution_capture": ("higher", 0.0),
    "authority.authorization_precision": ("higher", 0.0),
    "authority.authorization_recall": ("higher", 0.0),
    "authority.hazard_rejection": ("higher", 0.0),
    "authority.false_refusal_rate": ("lower", 0.0),
    "authority.false_abort_rate": ("lower", 0.0),
    "authority.violation_given_acceptance": ("lower", 0.0),
    "non_interference.preservation_rate": ("higher", 0.0),
    "non_interference.bounded_success_regression_rate": ("lower", 0.0),
    "non_interference.task_success_regression_rate": ("lower", 0.0),
    "non_interference.unnecessary_intervention_rate": ("lower", 0.0),
}


def _dig(tree: Mapping[str, Any], path: str) -> Any:
    node: Any = tree
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _value(tree: Mapping[str, Any], path: str) -> float | None:
    node = _dig(tree, path)
    if isinstance(node, Mapping):
        value = node.get("value")
        return float(value) if isinstance(value, (int, float)) else None
    if isinstance(node, (int, float)):
        return float(node)
    return None


@dataclass(frozen=True)
class GateResult:
    metric: str
    candidate: float | None
    reference: float | None
    delta: float | None
    direction: str
    tolerance: float
    passed: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "candidate": self.candidate,
            "reference": self.reference,
            "delta": self.delta,
            "direction": self.direction,
            "tolerance": self.tolerance,
            "passed": self.passed,
            "reason": self.reason,
        }


def compare_metric_vectors(
    candidate: Mapping[str, Any],
    reference: Mapping[str, Any],
    *,
    tolerances: Mapping[str, float] | None = None,
    metrics: Sequence[str] = tuple(METRIC_CONTRACT),
) -> dict[str, Any]:
    tolerances = tolerances or {}
    gates: list[GateResult] = []
    for metric in metrics:
        direction, default_tol = METRIC_CONTRACT[metric]
        tolerance = tolerances.get(metric, default_tol)
        cand = _value(candidate, metric)
        ref = _value(reference, metric)
        if cand is None or ref is None:
            gates.append(
                GateResult(
                    metric, cand, ref, None, direction, tolerance, True,
                    "metric not available in both arms; gate not applied",
                )
            )
            continue
        delta = cand - ref
        if direction == "higher":
            passed = delta >= -tolerance
        else:
            passed = delta <= tolerance
        gates.append(
            GateResult(
                metric, cand, ref, delta, direction, tolerance, passed,
                "within tolerance" if passed else f"{direction}-is-better gate violated",
            )
        )
    return {
        "passed": all(g.passed for g in gates),
        "gates": [g.as_dict() for g in gates],
        "n_violations": sum(1 for g in gates if not g.passed),
    }


def paired_rate_delta(
    candidate_rows: Sequence[Mapping[str, Any]],
    reference_rows: Sequence[Mapping[str, Any]],
    *,
    field: str = "bounded_success",
    resamples: int = 5000,
    seed: int = 12345,
) -> dict[str, Any]:
    """Scenario-paired bootstrap of a rate difference."""
    cand = {str(r.get("scenario_id") or r.get("episode_id")): r for r in candidate_rows}
    ref = {str(r.get("scenario_id") or r.get("episode_id")): r for r in reference_rows}
    shared = sorted(set(cand) & set(ref))
    deltas = [
        float(bool(cand[s].get(field))) - float(bool(ref[s].get(field))) for s in shared
    ]
    if not deltas:
        return {"n": 0, "reason": "no paired scenarios"}
    # paired_bootstrap_mean(a, b) reports statistic(b) - statistic(a); pass the
    # zero baseline first so the reported delta is candidate - reference.
    result = st.paired_bootstrap_mean([0.0] * len(deltas), deltas, n_resamples=resamples, seed=seed)
    return {"field": field, "n_pairs": len(deltas), **result}


def slice_gates(
    candidate_slices: Mapping[str, Mapping[str, Any]],
    reference_slices: Mapping[str, Mapping[str, Any]],
    *,
    arm: str,
    metric: str = "bounded_success",
    max_regression: float = 0.0,
    min_slice_n: int = 30,
) -> dict[str, Any]:
    """Per-slice, per-arm gate. Slices below ``min_slice_n`` are reported as
    under-powered rather than passed."""
    violations: list[dict[str, Any]] = []
    underpowered: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []
    for dimension, groups in candidate_slices.items():
        ref_groups = reference_slices.get(dimension, {})
        for name, candidate_slice in groups.items():
            reference_slice = ref_groups.get(name)
            if reference_slice is None:
                continue
            n = candidate_slice.get("n", 0)
            if n < min_slice_n:
                underpowered.append({"dimension": dimension, "slice": name, "n": n})
                continue
            cand = _slice_value(candidate_slice, arm, metric)
            ref = _slice_value(reference_slice, arm, metric)
            if cand is None or ref is None:
                continue
            delta = cand - ref
            entry = {
                "dimension": dimension,
                "slice": name,
                "n": n,
                "candidate": cand,
                "reference": ref,
                "delta": delta,
            }
            checked.append(entry)
            if delta < -max_regression:
                violations.append(entry)
    return {
        "passed": not violations,
        "n_checked": len(checked),
        "violations": violations,
        "underpowered": underpowered,
        "checked": checked,
    }


def _slice_value(slice_data: Mapping[str, Any], arm: str, metric: str) -> float | None:
    arms = slice_data.get("arms") or {}
    arm_data = arms.get(arm)
    if not isinstance(arm_data, Mapping):
        return None
    node = arm_data.get(metric)
    if isinstance(node, Mapping):
        value = node.get("value")
        return float(value) if isinstance(value, (int, float)) else None
    return float(node) if isinstance(node, (int, float)) else None