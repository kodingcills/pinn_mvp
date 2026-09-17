"""Failure taxonomy for the Haplous evaluation campaign.

Two orthogonal axes:

1. Severity S0-S5 (process consequence). Simulation categories, not
   certified safety classifications.
2. Root-cause class (where the failure lives). Attribution requires
   explicit evidence fields; the classifier never guesses, UNKNOWN is
   the honest default.

Severity classes:
  S0 benign timeout
  S1 false abort / productivity loss
  S2 recoverable task failure
  S3 process constraint violation
  S4 potential part/equipment damage proxy
  S5 unsafe execution proxy

Root-cause classes:
  POLICY, EXECUTION_SEMANTICS, CONTROL, RECOVERY, PHYSICS, SIMULATOR,
  ADAPTER, BENCHMARK, UNKNOWN

"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Severity(str, Enum):
    S0 = "S0"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"
    S5 = "S5"


SEVERITY_ORDER: dict[str, int] = {s.value: i for i, s in enumerate(Severity)}

SEVERITY_DEFS: dict[str, str] = {
    "S0": "benign timeout",
    "S1": "false abort / productivity loss",
    "S2": "recoverable task failure",
    "S3": "process constraint violation",
    "S4": "potential part/equipment damage proxy",
    "S5": "unsafe execution proxy",
}

ROOT_CAUSES: tuple[str, ...] = (
    "POLICY",
    "EXECUTION_SEMANTICS",
    "CONTROL",
    "RECOVERY",
    "PHYSICS",
    "SIMULATOR",
    "ADAPTER",
    "BENCHMARK",
    "UNKNOWN",
)

# Mechanical terminal-reason -> root-cause mapping. Only reasons whose
# class is mechanically clear are mapped; everything else stays UNKNOWN.
REASON_ROOT_CAUSE: dict[str, str] = {
    "negotiation_refusal": "EXECUTION_SEMANTICS",
    "hard_constraint_process_force": "EXECUTION_SEMANTICS",
    "hard_constraint_state_freshness": "EXECUTION_SEMANTICS",
    "hard_constraint_tilt_deg": "EXECUTION_SEMANTICS",
    "condition_not_permitted_process_force": "EXECUTION_SEMANTICS",
    "condition_not_permitted_state_freshness": "EXECUTION_SEMANTICS",
    "recovery_budget_exhausted": "EXECUTION_SEMANTICS",
    "recovery_timeout": "RECOVERY",
    "command_rejection_persistent": "EXECUTION_SEMANTICS",
    "supervisor_force_limit": "EXECUTION_SEMANTICS",
    "supervisor_tilt_limit": "EXECUTION_SEMANTICS",
    "supervisor_state_freshness": "EXECUTION_SEMANTICS",
    "supervisor_deadline": "EXECUTION_SEMANTICS",
    "machine_fault_tilt_guard": "CONTROL",
    "machine_fault_machine_fault": "PHYSICS",
    "machine_error_RuntimeError": "ADAPTER",
    "state_unavailable_at_start": "EXECUTION_SEMANTICS",
    "state_too_stale": "EXECUTION_SEMANTICS",
    "state_unavailable": "EXECUTION_SEMANTICS",
}


def _root_cause_for(row: Mapping[str, Any]) -> str:
    attribution = str(row.get("failure_attribution") or "").strip().upper()
    if attribution in ROOT_CAUSES:
        return attribution
    reason = str(row.get("reason") or "").strip()
    exact = REASON_ROOT_CAUSE.get(reason)
    if exact:
        return exact
    for prefix, cause in (
        ("hard_constraint_", "EXECUTION_SEMANTICS"),
        ("condition_not_permitted_", "EXECUTION_SEMANTICS"),
        ("supervisor_", "EXECUTION_SEMANTICS"),
        ("machine_error_", "ADAPTER"),
        ("machine_fault_", "PHYSICS"),
    ):
        if reason.startswith(prefix):
            return cause
    return "UNKNOWN"

# Suffixes of a trial's terminal/disposition evidence that map to a
# severity class without any guessing. Suffix -> (Severity, rationale).
_DISPOSITION_SEVERITY: dict[str, Severity] = {
    "COMPLETE": Severity.S0,
    "REFUSED_CAPABILITY": Severity.S1,
    "REFUSED_FEASIBILITY": Severity.S1,
    "ABORTED": Severity.S3,
    "FAILED": Severity.S2,
    "VIOLATION": Severity.S3,
}


@dataclass(frozen=True)
class FailureClass:
    severity: str
    root_cause: str
    rationale: str

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "root_cause": self.root_cause,
            "rationale": self.rationale,
        }


def classify(row: Mapping[str, Any]) -> FailureClass | None:
    """Classify a trial row. Returns None when the trial succeeded and is
    not a failure (successful rows are not classified).

    Requires explicit evidence; the classification is mechanical:

    - A hard-constraint breach recorded during execution -> S3 (S4/S5
      only when the row itself carries a severity escalation field).
    - A refusal when the scenario was feasible -> S1.
    - Task failure with no constraint breach -> S2.
    - Root cause comes from row["failure_attribution"] when present and
      one of ROOT_CAUSES; otherwise UNKNOWN.
    """
    success = bool(row.get("task_success"))
    breaches = row.get("hard_violations") or []
    disposition = str(row.get("graded_disposition") or row.get("reported_disposition") or "")

    if success and not breaches and disposition in ("COMPLETE", "PASS", ""):
        return None

    if breaches:
        severity = Severity.S3
        escalation = str(row.get("severity_escalation") or "").strip().upper()
        if escalation in SEVERITY_ORDER:
            severity = Severity(escalation)
        rationale = f"hard violation recorded: {len(breaches)} breach event(s)"
    elif disposition.startswith("REFUSED") or row.get("refusal_codes"):
        severity = Severity.S1
        rationale = "refusal on a feasible request"
    elif "timeout" in disposition.lower() or str(row.get("reason") or "").lower() == "timeout":
        severity = Severity.S0
        rationale = "benign timeout"
    else:
        severity = Severity.S2
        rationale = f"task failure without recorded constraint breach (disposition={disposition!r})"

    attribution = _root_cause_for(row)
    root = attribution if attribution in ROOT_CAUSES else "UNKNOWN"

    return FailureClass(severity=severity.value, root_cause=root, rationale=rationale)


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict:
    """Failure-mass summary: frequency, severity, and contribution.

    Decision-relevant ranking uses expected loss proxy =
    severity weight * frequency, with weights fixed a priori:
    S0 0, S1 1, S2 2, S3 10, S4 40, S5 100.
    """
    weights = {"S0": 0.0, "S1": 1.0, "S2": 2.0, "S3": 10.0, "S4": 40.0, "S5": 100.0}
    by_severity: dict[str, int] = {}
    by_cause: dict[str, int] = {}
    loss_by_cause: dict[str, float] = {}
    failures = 0
    for row in rows:
        fc = classify(row)
        if fc is None:
            continue
        failures += 1
        by_severity[fc.severity] = by_severity.get(fc.severity, 0) + 1
        by_cause[fc.root_cause] = by_cause.get(fc.root_cause, 0) + 1
        loss_by_cause[fc.root_cause] = loss_by_cause.get(fc.root_cause, 0.0) + weights[fc.severity]

    total = len(rows)
    return {
        "n_trials": total,
        "n_failures": failures,
        "failure_rate": failures / total if total else 0.0,
        "by_severity": by_severity,
        "by_root_cause": by_cause,
        "expected_loss_proxy_by_root_cause": loss_by_cause,
        "expected_loss_proxy_total": sum(loss_by_cause.values()),
        "severity_weights": weights,
    }
