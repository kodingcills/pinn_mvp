"""Hierarchical failure attribution.

The old campaign taxonomy stopped at a single coarse ``EXECUTION_SEMANTICS``
bucket. HES keeps severity (from ``evaluation.taxonomy``) but splits
root cause into a top-level layer plus an execution-runtime subcategory,
records contributing causes, the first causal divergence, an explicit
confidence, and evidence references.

Deterministic rules first; a heuristic is used only where the reason string
is unambiguous; everything else is UNKNOWN. No LLM assigns ground truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hes.model import Attainability, AttainabilityAxis, _reported


class CauseLayer(StrEnum):
    UPSTREAM_POLICY = "UPSTREAM_POLICY"
    EXECUTION_RUNTIME = "EXECUTION_RUNTIME"
    CONTROLLER = "CONTROLLER"
    MACHINE_PHYSICS = "MACHINE_PHYSICS"
    ENVIRONMENT = "ENVIRONMENT"
    BENCHMARK_EVALUATOR = "BENCHMARK_EVALUATOR"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    UNKNOWN = "UNKNOWN"


class RuntimeSubcause(StrEnum):
    CORRECT_INFEASIBLE_REFUSAL = "correct_infeasible_refusal"
    FALSE_REFUSAL = "false_refusal"
    CAPABILITY_REJECTION = "capability_rejection"
    DEADLINE_REJECTION = "deadline_rejection"
    FRESHNESS_REJECTION = "freshness_rejection"
    REACH_REJECTION = "reach_rejection"
    HOLD_TIMEOUT = "hold_timeout"
    UNSAFE_HOLD = "unsafe_hold"
    REVALIDATION_FAILURE = "revalidation_failure"
    RESUME_FAILURE = "resume_failure"
    CONSTRAINT_ABORT = "constraint_abort"
    FALSE_ABORT = "false_abort"
    RECOVERY_FAILURE = "recovery_failure"
    RECOVERY_UNAVAILABLE = "recovery_unavailable"
    EVIDENCE_INTERNAL_FAILURE = "evidence_internal_failure"
    OTHER = "other"


_ADDRESSABILITY = {
    CauseLayer.UPSTREAM_POLICY: "upstream",
    CauseLayer.EXECUTION_RUNTIME: "generically_addressable",
    CauseLayer.CONTROLLER: "technically_addressable",
    CauseLayer.MACHINE_PHYSICS: "technically_addressable",
    CauseLayer.ENVIRONMENT: "task_specific",
    CauseLayer.BENCHMARK_EVALUATOR: "benchmark_fix",
    CauseLayer.INFRASTRUCTURE: "technically_addressable",
    CauseLayer.UNKNOWN: "unknown",
}

# Reason code -> (layer, subcause). Deterministic mappings only.
_REASON_MAP: dict[str, tuple[CauseLayer, RuntimeSubcause | None]] = {
    "negotiation_refusal": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.OTHER),
    "hard_constraint_process_force": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.CONSTRAINT_ABORT),
    "hard_constraint_tilt_deg": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.CONSTRAINT_ABORT),
    "hard_constraint_state_freshness": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.FRESHNESS_REJECTION),
    "condition_not_permitted_process_force": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.RECOVERY_UNAVAILABLE),
    "condition_not_permitted_state_freshness": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.RECOVERY_UNAVAILABLE),
    "recovery_budget_exhausted": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.RECOVERY_FAILURE),
    "recovery_timeout": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.RECOVERY_FAILURE),
    "temporal_hold_budget_exhausted": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.HOLD_TIMEOUT),
    "command_rejection_persistent": (CauseLayer.INFRASTRUCTURE, RuntimeSubcause.EVIDENCE_INTERNAL_FAILURE),
    "state_unavailable": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.FRESHNESS_REJECTION),
    "state_unavailable_at_start": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.FRESHNESS_REJECTION),
    "state_too_stale": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.FRESHNESS_REJECTION),
    "deadline_exceeded": (CauseLayer.UPSTREAM_POLICY, None),
    "success_condition_unmet": (CauseLayer.UPSTREAM_POLICY, None),
    "horizon_exhausted": (CauseLayer.UPSTREAM_POLICY, None),
    "machine_fault_tilt_guard": (CauseLayer.CONTROLLER, None),
    "machine_fault_machine_fault": (CauseLayer.MACHINE_PHYSICS, None),
    "supervisor_force_limit": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.CONSTRAINT_ABORT),
    "supervisor_tilt_limit": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.CONSTRAINT_ABORT),
    "supervisor_state_freshness": (CauseLayer.EXECUTION_RUNTIME, RuntimeSubcause.FRESHNESS_REJECTION),
    "supervisor_deadline": (CauseLayer.UPSTREAM_POLICY, None),
}

_REFUSAL_CODE_SUBCAUSE = {
    "deadline_unachievable": RuntimeSubcause.DEADLINE_REJECTION,
    "deadline_at_risk": RuntimeSubcause.DEADLINE_REJECTION,
    "state_too_stale": RuntimeSubcause.FRESHNESS_REJECTION,
    "state_age_marginal": RuntimeSubcause.FRESHNESS_REJECTION,
    "unreachable_goal": RuntimeSubcause.REACH_REJECTION,
    "goal_near_reach_limit": RuntimeSubcause.REACH_REJECTION,
    "missing_force_measurement": RuntimeSubcause.CAPABILITY_REJECTION,
    "missing_tilt_observation": RuntimeSubcause.CAPABILITY_REJECTION,
    "missing_pose_observation": RuntimeSubcause.CAPABILITY_REJECTION,
    "requested_tolerance_unachievable": RuntimeSubcause.CAPABILITY_REJECTION,
    "unsupported_control_mode": RuntimeSubcause.CAPABILITY_REJECTION,
    "missing_success_condition": RuntimeSubcause.CAPABILITY_REJECTION,
    "force_floor_unsensed": RuntimeSubcause.CAPABILITY_REJECTION,
    "tilt_requirement_unsensed": RuntimeSubcause.CAPABILITY_REJECTION,
}


@dataclass(frozen=True)
class Attribution:
    primary: CauseLayer
    subcause: RuntimeSubcause | None
    contributing: tuple[CauseLayer, ...] = ()
    first_divergence: str | None = None
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    addressability: str = "unknown"
    severity: str = "S2"

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary_cause": self.primary.value,
            "runtime_subcause": self.subcause.value if self.subcause else None,
            "contributing_causes": [c.value for c in self.contributing],
            "first_causal_divergence": self.first_divergence,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "addressability": self.addressability,
            "severity": self.severity,
        }


def _severity(row: Mapping[str, Any]) -> str:
    if int(row.get("n_hard_violations") or 0) > 0 or row.get("hard_violations"):
        return "S3"
    reported = _reported(row)
    if reported == "REFUSED":
        return "S1"
    if str(row.get("reason") or "") == "horizon_exhausted":
        return "S0"
    return "S2"


def attribute(
    row: Mapping[str, Any],
    *,
    attainability: Attainability | None = None,
) -> Attribution:
    """Deterministic attribution of one failed/refused episode row."""
    reported = _reported(row)
    reason = str(row.get("reason") or "")
    codes = tuple(str(c) for c in row.get("refusal_codes") or ())
    n_viol = int(row.get("n_hard_violations") or 0)
    severity = _severity(row)

    # A refusal is only correctly attributed after attainability is known.
    if reported == "REFUSED":
        if attainability is not None and attainability.axis is AttainabilityAxis.ATTAINABLE:
            return _finish(
                CauseLayer.EXECUTION_RUNTIME,
                RuntimeSubcause.FALSE_REFUSAL,
                severity,
                0.9,
                "refusal:scenario_demonstrated_attainable",
                addressability="generically_addressable",
                first_divergence="refuse_before_attempt",
            )
        if attainability is not None and attainability.axis is AttainabilityAxis.NON_ATTAINABLE:
            return _finish(
                CauseLayer.EXECUTION_RUNTIME,
                RuntimeSubcause.CORRECT_INFEASIBLE_REFUSAL,
                severity,
                0.9,
                "refusal:scenario_marked_infeasible",
                addressability="non_addressable",
                first_divergence="refuse_before_attempt",
            )
        for code in codes:
            sub = _REFUSAL_CODE_SUBCAUSE.get(code)
            if sub is not None:
                return _finish(
                    CauseLayer.EXECUTION_RUNTIME,
                    sub,
                    severity,
                    0.8,
                    f"refusal_code:{code}",
                    addressability="generically_addressable",
                    first_divergence="refuse_before_attempt",
                )
        return _finish(
            CauseLayer.EXECUTION_RUNTIME,
            RuntimeSubcause.OTHER,
            severity,
            0.4,
            f"refusal_codes:{codes}",
            first_divergence="refuse_before_attempt",
        )

    mapped = _REASON_MAP.get(reason)
    if mapped is not None:
        layer, sub = mapped
        contributing: tuple[CauseLayer, ...] = ()
        if n_viol > 0 and layer is CauseLayer.UPSTREAM_POLICY:
            contributing = (CauseLayer.EXECUTION_RUNTIME,)
        return _finish(
            layer,
            sub,
            severity,
            0.8 if reason else 0.4,
            f"reason:{reason}" if reason else "reason_missing",
            contributing=contributing,
            first_divergence=reason or None,
        )
    if reason.startswith("machine_error_"):
        return _finish(
            CauseLayer.INFRASTRUCTURE,
            None,
            severity,
            0.7,
            f"reason:{reason}",
            first_divergence=reason,
        )
    if not reason and n_viol == 0:
        # Failed with no reason and no violation: policy simply did not finish.
        return _finish(
            CauseLayer.UPSTREAM_POLICY,
            None,
            severity,
            0.5,
            "no_reason_no_violation",
            first_divergence="task_not_completed",
        )
    return _finish(
        CauseLayer.UNKNOWN,
        None,
        severity,
        0.0,
        f"unmapped_reason:{reason}",
    )


def _finish(
    layer: CauseLayer,
    sub: RuntimeSubcause | None,
    severity: str,
    confidence: float,
    evidence: str,
    *,
    contributing: tuple[CauseLayer, ...] = (),
    first_divergence: str | None = None,
    addressability: str | None = None,
) -> Attribution:
    return Attribution(
        primary=layer,
        subcause=sub,
        contributing=contributing,
        first_divergence=first_divergence,
        confidence=confidence,
        evidence=(evidence,),
        addressability=addressability or _ADDRESSABILITY[layer],
        severity=severity,
    )


def haplous_responsibility(attr: Attribution) -> float:
    """How much of the failure plausibly belongs to Haplous itself, in
    [0, 1]. Only execution-runtime causes are addressable by the runtime;
    everything else is reported as not-Haplous rather than silently folded
    into a runtime bucket."""
    if attr.primary is not CauseLayer.EXECUTION_RUNTIME:
        return 0.0
    if attr.subcause is RuntimeSubcause.CORRECT_INFEASIBLE_REFUSAL:
        return 0.0
    return 1.0


@dataclass
class AttributionSummary:
    by_layer: dict[str, int] = field(default_factory=dict)
    by_subcause: dict[str, int] = field(default_factory=dict)
    by_confidence_band: dict[str, int] = field(default_factory=dict)
    n_failures: int = 0
    n_unknown: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_failures": self.n_failures,
            "n_unknown": self.n_unknown,
            "by_layer": self.by_layer,
            "by_runtime_subcause": self.by_subcause,
            "by_confidence_band": self.by_confidence_band,
        }


def _band(confidence: float) -> str:
    if confidence >= 0.7:
        return "high"
    if confidence >= 0.4:
        return "medium"
    return "low"


def summarize_attributions(attributions: list[Attribution]) -> AttributionSummary:
    summary = AttributionSummary(n_failures=len(attributions))
    for attr in attributions:
        summary.by_layer[attr.primary.value] = summary.by_layer.get(attr.primary.value, 0) + 1
        if attr.subcause is not None:
            summary.by_subcause[attr.subcause.value] = summary.by_subcause.get(attr.subcause.value, 0) + 1
        band = _band(attr.confidence)
        summary.by_confidence_band[band] = summary.by_confidence_band.get(band, 0) + 1
        if attr.primary is CauseLayer.UNKNOWN:
            summary.n_unknown += 1
    return summary