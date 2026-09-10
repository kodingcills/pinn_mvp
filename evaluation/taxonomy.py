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

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


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

    attribution = str(row.get("failure_attribution") or "").strip().upper()
    root = attribution if attribution in ROOT_CAUSES else "UNKNOWN"

    return FailureClass(severity=severity.value, root_cause=root, rationale=rationale)


def summarize(rows: list[Mapping[str, Any]]) -> dict:
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
