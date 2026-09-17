from __future__ import annotations

import json
import webbrowser
from argparse import Namespace
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from haplous.cli.commands import _table
from haplous.cli.registry import RunRegistry

# Evidence event kind -> normalized human timeline label. The runtime's
# event vocabulary is the source of truth; unknown kinds fall back to the
# uppercased kind so a new event is visible, never hidden.
_TIMELINE_LABELS: dict[str, str] = {
    "feasibility": "CAPABILITY DECISION",
    "degraded_requirements": "DEGRADED",
    "execution_authorized": "AUTHORIZED",
    "motion_started": "EXECUTING",
    "temporal_hold_entered": "HOLD",
    "temporal_revalidation_resumed": "RESUME",
    "temporal_hold_budget_exhausted": "HOLD BUDGET EXHAUSTED",
    "constraint_violation": "VIOLATION",
    "recovery_decision": "RECOVERY",
    "command_rejected": "COMMAND REJECTED",
    "measurement_loss": "OBSERVATION LOSS",
    "pre_execution_state_invalid": "STATE INVALID",
    "outcome": "TERMINAL",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_command(args: Namespace) -> int:
    registry = RunRegistry()
    left = _load_json(registry.resolve(args.run_a).summary).get("systems", {})
    right = _load_json(registry.resolve(args.run_b).summary).get("systems", {})
    rows = []
    for system in sorted(set(left) | set(right)):
        left_metrics = left.get(system, {})
        right_metrics = right.get(system, {})
        for metric in sorted(set(left_metrics) | set(right_metrics)):
            left_value = left_metrics.get(metric, "-")
            right_value = right_metrics.get(metric, "-")
            if isinstance(left_value, (str, int, float, bool)) or left_value is None:
                rows.append((system, metric, str(left_value), str(right_value)))
    _table(("system", "metric", args.run_a, args.run_b), rows)
    return 0


def report_command(args: Namespace) -> int:
    summary = RunRegistry().resolve(args.run_id).summary
    print(summary)
    if args.open:
        try:
            opened = webbrowser.open(summary.resolve().as_uri())
        except webbrowser.Error:
            opened = False
        if not opened:
            print("browser: unavailable")
    return 0


def _load_trials(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _timeline_rows(evidence: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for event in evidence.get("events", []):
        kind = str(event.get("kind", ""))
        label = _TIMELINE_LABELS.get(kind, kind.upper())
        payload = event.get("payload", {})
        if kind == "feasibility":
            outcome = str(payload.get("outcome", ""))
            codes = ", ".join(r.get("code", "") for r in payload.get("reasons", []))
            detail = f"{outcome} {codes}".strip()
        elif kind == "state_transition":
            detail = str(payload.get("to", payload.get("state", "")))
        elif kind == "outcome":
            detail = str(payload.get("result", "")) + (
                f" ({payload['reason']})" if payload.get("reason") else ""
            )
        elif kind == "constraint_violation":
            detail = f"{payload.get('code', '')} value={payload.get('value', '')} limit={payload.get('limit', '')}"
        else:
            detail = json.dumps(payload, sort_keys=True) if payload else ""
        rows.append((f"{float(event.get('t_s', 0.0)):.3f}s", label, detail))
    return rows


def _inspections(run) -> list[dict[str, Any]]:
    records = _load_trials(run.trials)
    out: list[dict[str, Any]] = []
    for record in records:
        detail = record.get("detail") or {}
        evidence = detail.get("evidence")
        entry: dict[str, Any] = {
            "execution_id": record.get("scenario_id"),
            "system": record.get("system"),
            "disposition": record.get("reported_disposition") or record.get("disposition"),
            "reason": record.get("reason"),
        }
        if evidence:
            entry["request"] = evidence.get("intent_summary")
            entry["timeline"] = _timeline_rows(evidence)
        out.append(entry)
    return out


def inspect_command(args: Namespace) -> int:
    run = RunRegistry().resolve(args.run_id)
    inspections = _inspections(run)
    if args.json:
        print(json.dumps(inspections, indent=2, sort_keys=True))
        return 0
    print(f"execution run: {run.run_id}")
    print(f"trials: {len(inspections)}")
    for entry in inspections:
        print(
            f"\n[{entry['execution_id']}] {entry['system']}: "
            f"{entry['disposition']}"
            + (f" ({entry['reason']})" if entry.get("reason") else "")
        )
        request = entry.get("request")
        if request:
            print(f"  behavior: {request.get('behavior')} goal={request.get('goal_position_m')}")
            for req in request.get("requirements", []):
                print(f"  requirement: {req.get('kind')} hard={req.get('hard')} limit={req.get('limit')}")
        if entry.get("timeline"):
            print("  timeline:")
            for t_s, label, detail in entry["timeline"]:
                print(f"    {t_s:>8}  {label:<22} {detail}")
        elif not request:
            print("  (no evidence events recorded for this execution)")
    return 0


def timeline_command(args: Namespace) -> int:
    path = Path(args.source)
    bundles: list[dict[str, Any]]
    if path.is_file():
        evidence = json.loads(path.read_text(encoding="utf-8"))
        bundles = [{"execution_id": path.name, "request": evidence.get("intent_summary"), "timeline": _timeline_rows(evidence)}]
    else:
        bundles = _inspections(RunRegistry().resolve(args.source))
    if args.json:
        print(json.dumps(bundles, indent=2, sort_keys=True))
        return 0
    for bundle in bundles:
        print(f"[{bundle['execution_id']}]")
        for t_s, label, detail in bundle.get("timeline", []):
            print(f"  {t_s:>8}  {label:<22} {detail}")
    return 0


def replay_command(args: Namespace) -> int:
    rows = _load_trials(RunRegistry().resolve(args.run_id).trials)
    selected = [row for row in rows if not row.get("bounded_success")] if args.failures else rows
    _table(
        ("scenario_id", "system", "reason"),
        [
            (str(row.get("scenario_id", "")), str(row.get("system", "")), str(row.get("reason") or "-"))
            for row in selected
        ],
    )
    if args.ui:
        print("viewer: not wired")
    return 0


def envelope_command(args: Namespace) -> int:
    rows = _load_trials(RunRegistry().resolve(args.run_id).trials)
    grouped: dict[tuple[str, str], tuple[int, int]] = {}
    for row in rows:
        for dimension, value in row.get("scenario_axes", {}).items():
            key = (str(dimension), str(value))
            successes, total = grouped.get(key, (0, 0))
            grouped[key] = (successes + int(bool(row.get("bounded_success"))), total + 1)
    table_rows = [
        (dimension, value, f"{successes}/{total}", f"{successes / total:.3f}")
        for (dimension, value), (successes, total) in sorted(grouped.items())
    ]
    _table(("dimension", "value", "bounded", "rate"), table_rows)
    return 0
