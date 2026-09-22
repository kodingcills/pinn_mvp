"""Execution-runtime metric vector.

Vector-valued by construction: HES never emits a single weighted Haplous
score. Every metric that cannot be computed from the evidence actually
present is returned as ``NOT_AVAILABLE`` rather than fabricated.

Groups implemented here:

- whole-system: task success, bounded success, system BSR, conditional BSR,
  coverage, completion time;
- execution authority: authorization precision/recall, execution capture,
  hazard rejection, false refusal/abort, violation given acceptance;
- non-interference: preservation, task/bounded regression, unnecessary
  intervention, completion-time tax;
- physical consequence: peak force, exceedance, severity distribution;
- intervention quality: classification rates, recovered/destroyed;
- timing: P50/P90/P95 extremes only when the sample supports them;
- risk-coverage points.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from evaluation import stats as st
from hes.attribution import Attribution, attribute
from hes.model import (
    Attainability,
    AttainabilityAxis,
    _reported,
    classify_episode,
)

NOT_AVAILABLE = "NOT_AVAILABLE"
# Minimum sample before an extreme percentile is reported at all.
_P99_MIN_SAMPLES = 100
_P95_MIN_SAMPLES = 40


def _k(rows: Sequence[Mapping[str, Any]], predicate) -> int:
    return sum(1 for r in rows if predicate(r))


def _prop(k: int, n: int) -> dict[str, Any]:
    if n <= 0:
        return {"value": None, "n": 0, "reason": NOT_AVAILABLE}
    return st.proportion_ci(k, n).as_dict()


def _accepted(row: Mapping[str, Any]) -> bool:
    return _reported(row) != "REFUSED"


def _viol(row: Mapping[str, Any]) -> bool:
    return int(row.get("n_hard_violations") or 0) > 0 or bool(row.get("hard_violations"))


def system_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    accepted = [r for r in rows if _accepted(r)]
    outcomes = [classify_episode(r) for r in rows]
    return {
        "n": n,
        "task_success": _prop(_k(rows, lambda r: r.get("task_success")), n),
        "bounded_success": _prop(_k(rows, lambda r: r.get("bounded_success")), n),
        "system_bsr": _prop(_k(rows, lambda r: r.get("bounded_success")), n),
        "conditional_bsr": _prop(
            _k(accepted, lambda r: r.get("bounded_success")), len(accepted)
        ),
        "coverage": _prop(len(accepted), n),
        "task_axis_counts": _counts(outcomes, lambda o: o.task.value),
        "contract_axis_counts": _counts(outcomes, lambda o: o.contract.value),
        "authority_axis_counts": _counts(outcomes, lambda o: o.authority.value),
    }


def _counts(outcomes: Sequence[Any], key) -> dict[str, int]:
    counts: dict[str, int] = {}
    for outcome in outcomes:
        value = key(outcome)
        counts[value] = counts.get(value, 0) + 1
    return counts


def authority_metrics(
    rows: Sequence[Mapping[str, Any]],
    attainability: Mapping[str, Attainability],
) -> dict[str, Any]:
    accepted = [r for r in rows if _accepted(r)]
    refused = [r for r in rows if not _accepted(r)]
    attainable_ids = {s for s, a in attainability.items() if a.axis is AttainabilityAxis.ATTAINABLE}
    non_attainable_ids = {
        s for s, a in attainability.items() if a.axis is AttainabilityAxis.NON_ATTAINABLE
    }

    def sid(row: Mapping[str, Any]) -> str:
        return str(row.get("scenario_id") or row.get("episode_id"))

    auth_attainable = _k(accepted, lambda r: sid(r) in attainable_ids)
    authorized_recall_denom = _k(rows, lambda r: sid(r) in attainable_ids)
    refused_attainable = _k(refused, lambda r: sid(r) in attainable_ids)

    capture_num = _k(
        rows, lambda r: r.get("bounded_success") and sid(r) in attainable_ids
    )

    hazard = _k(refused, lambda r: sid(r) in non_attainable_ids)
    non_attainable_presented = _k(rows, lambda r: sid(r) in non_attainable_ids)

    # False abort: accepted, then aborted, on a scenario demonstrated attainable.
    false_abort = _k(
        rows,
        lambda r: _reported(r) == "ABORTED"
        and sid(r) in attainable_ids
        and not r.get("bounded_success"),
    )

    return {
        "n_presented": len(rows),
        "n_accepted": len(accepted),
        "n_refused": len(refused),
        "n_demonstrated_attainable": len(attainable_ids),
        "authorization_precision": _prop(auth_attainable, len(accepted)),
        "authorization_recall": _prop(
            _k(accepted, lambda r: sid(r) in attainable_ids), authorized_recall_denom
        ),
        "execution_capture": _prop(capture_num, len(attainable_ids)),
        "hazard_rejection": (
            _prop(hazard, non_attainable_presented)
            if non_attainable_presented
            else {
                "value": None,
                "n": 0,
                "reason": "no_scenario_demonstrated_non_attainable",
            }
        ),
        "false_refusal_rate": (
            _prop(refused_attainable, authorized_recall_denom)
            if authorized_recall_denom
            else {"value": None, "n": 0, "reason": NOT_AVAILABLE}
        ),
        "false_abort_rate": (
            _prop(false_abort, authorized_recall_denom)
            if authorized_recall_denom
            else {"value": None, "n": 0, "reason": NOT_AVAILABLE}
        ),
        "violation_given_acceptance": _prop(
            _k(accepted, _viol), len(accepted)
        ),
    }


def noninterference_metrics(
    rows: Sequence[Mapping[str, Any]],
    reference_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reference arm (the unmediated baseline) defines already-good
    execution. Preservation is measured only on reference bounded
    successes, paired by scenario."""
    ref = {
        str(r.get("scenario_id") or r.get("episode_id")): r for r in reference_rows
    }
    arm = {str(r.get("scenario_id") or r.get("episode_id")): r for r in rows}
    shared = sorted(set(ref) & set(arm))
    ref_ok = [s for s in shared if ref[s].get("bounded_success")]
    if not ref_ok:
        return {
            "n_reference_bounded_success": 0,
            "preservation_rate": {"value": None, "n": 0, "reason": NOT_AVAILABLE},
            "bounded_success_regression_rate": {"value": None, "n": 0, "reason": NOT_AVAILABLE},
            "task_success_regression_rate": {"value": None, "n": 0, "reason": NOT_AVAILABLE},
            "unnecessary_intervention_rate": {"value": None, "n": 0, "reason": NOT_AVAILABLE},
            "completion_time_tax_s": {"value": None, "n": 0, "reason": NOT_AVAILABLE},
        }
    preserved = [s for s in ref_ok if arm[s].get("bounded_success")]
    task_regress = [s for s in ref_ok if ref[s].get("task_success") and not arm[s].get("task_success")]
    unnecessary = [s for s in ref_ok if not _accepted(arm[s])]
    taxes = [
        arm[s]["wall_s"] - ref[s]["wall_s"]
        for s in preserved
        if "wall_s" in arm[s] and "wall_s" in ref[s]
    ]
    return {
        "n_reference_bounded_success": len(ref_ok),
        "preservation_rate": _prop(len(preserved), len(ref_ok)),
        "bounded_success_regression_rate": _prop(len(ref_ok) - len(preserved), len(ref_ok)),
        "task_success_regression_rate": _prop(len(task_regress), len(ref_ok)),
        "unnecessary_intervention_rate": _prop(len(unnecessary), len(ref_ok)),
        "completion_time_tax_s": (
            {"value": st.tail_percentiles(taxes)["p50"], "n": len(taxes), "unit": "s"}
            if taxes
            else {"value": None, "n": 0, "reason": NOT_AVAILABLE}
        ),
    }


def intervention_metrics(forks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {"BENEFICIAL": 0, "HARMFUL": 0, "UNNECESSARY": 0, "INSUFFICIENT": 0, "AMBIGUOUS": 0}
    beneficial_kinds: dict[str, int] = {}
    recovered = destroyed = 0
    for fork in forks:
        cf = (fork.get("counterfactuals") or {}).get("intervention")
        if not cf:
            continue
        classification = cf.get("classification", "AMBIGUOUS")
        counts[classification] = counts.get(classification, 0) + 1
        kind = cf.get("beneficial_kind")
        if kind:
            beneficial_kinds[kind] = beneficial_kinds.get(kind, 0) + 1
        haplous = ((fork.get("arms") or {}).get("haplous") or {})
        if classification == "BENEFICIAL" and haplous.get("bounded_success"):
            recovered += 1
        if classification == "HARMFUL":
            destroyed += 1
    attributable = (
        counts["BENEFICIAL"] + counts["HARMFUL"] + counts["UNNECESSARY"] + counts["INSUFFICIENT"]
    )
    return {
        "counts": counts,
        "beneficial_kinds": beneficial_kinds,
        "n_attributable": attributable,
        "intervention_precision": (
            _prop(counts["BENEFICIAL"], attributable)
            if attributable
            else {"value": None, "n": 0, "reason": NOT_AVAILABLE}
        ),
        "harmful_intervention_rate": (
            _prop(counts["HARMFUL"], attributable)
            if attributable
            else {"value": None, "n": 0, "reason": NOT_AVAILABLE}
        ),
        "unnecessary_intervention_rate": (
            _prop(counts["UNNECESSARY"], attributable)
            if attributable
            else {"value": None, "n": 0, "reason": NOT_AVAILABLE}
        ),
        "bounded_successes_recovered": recovered,
        "bounded_successes_destroyed": destroyed,
        "ambiguous_rate": (
            _prop(counts["AMBIGUOUS"], sum(counts.values()))
            if sum(counts.values())
            else {"value": None, "n": 0, "reason": NOT_AVAILABLE}
        ),
    }


def physical_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    forces = [
        float(r["task_metrics"]["peak_abs_force_n"])
        for r in rows
        if isinstance(r.get("task_metrics"), Mapping)
        and r["task_metrics"].get("peak_abs_force_n") is not None
    ]
    tilts = [
        float(r["task_metrics"]["max_tilt_deg"])
        for r in rows
        if isinstance(r.get("task_metrics"), Mapping)
        and r["task_metrics"].get("max_tilt_deg") is not None
    ]
    exceedances: list[float] = []
    for row in rows:
        for violation in row.get("hard_violations") or ():
            if not isinstance(violation, Mapping):
                continue
            if violation.get("code") != "process_force":
                continue
            try:
                exceedances.append(max(0.0, float(violation["value"]) - float(violation["limit"])))
            except (KeyError, TypeError, ValueError):
                continue
    return {
        "peak_force_n": st.tail_percentiles(forces) if forces else NOT_AVAILABLE,
        "max_tilt_deg": st.tail_percentiles(tilts) if tilts else NOT_AVAILABLE,
        "force_exceedance_n": st.tail_percentiles(exceedances) if exceedances else NOT_AVAILABLE,
        "integrated_constraint_excursion": NOT_AVAILABLE,
        "collision_count": NOT_AVAILABLE,
        "collision_severity": NOT_AVAILABLE,
    }


def timing_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    walls = [float(r["wall_s"]) for r in rows if r.get("wall_s") is not None]
    if not walls:
        return {"wall_s": NOT_AVAILABLE}
    tails = st.tail_percentiles(walls)
    out: dict[str, Any] = {
        "wall_s": {
            "n": tails["n"],
            "p50": tails["p50"],
            "p90": tails["p90"],
            "p99": tails["p99"] if tails["n"] >= _P99_MIN_SAMPLES else NOT_AVAILABLE,
            "max": tails["max"],
        },
        "decision_latency": NOT_AVAILABLE,
        "fault_to_intervention_latency": NOT_AVAILABLE,
        "state_age_at_authorization": NOT_AVAILABLE,
    }
    if tails["n"] >= _P95_MIN_SAMPLES:
        out["wall_s"]["p95"] = tails["p95"]
    return out


def risk_coverage_points(
    arm_to_rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """coverage -> violation risk among accepted, per arm. Underlying points
    are stored; no area summary is treated as the result."""
    points: dict[str, list[dict[str, Any]]] = {}
    for arm, rows in arm_to_rows.items():
        n = len(rows)
        accepted = [r for r in rows if _accepted(r)]
        coverage = len(accepted) / n if n else None
        violation = (
            _k(accepted, _viol) / len(accepted) if accepted else None
        )
        points[arm] = [
            {
                "coverage": coverage,
                "violation_given_acceptance": violation,
                "n_accepted": len(accepted),
                "n_presented": n,
            }
        ]
    return points


def failure_rows(
    rows: Sequence[Mapping[str, Any]],
    attainability: Mapping[str, Attainability],
) -> list[tuple[Mapping[str, Any], Attribution]]:
    out: list[tuple[Mapping[str, Any], Attribution]] = []
    for row in rows:
        if row.get("bounded_success"):
            continue
        sid = str(row.get("scenario_id") or row.get("episode_id"))
        attribution = attribute(row, attainability=attainability.get(sid))
        out.append((row, attribution))
    return out