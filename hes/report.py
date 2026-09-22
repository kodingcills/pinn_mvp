"""Canonical HES report rendering and drift checking.

The report is generated from the analysis bundle, never hand-edited. A
machine digest is embedded and ``check_report`` fails when the committed
markdown no longer matches the evidence it claims to describe.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from hes.intervention import meaning

DIGEST_PREFIX = "<!-- hes-report-digest:"
DIGEST_SUFFIX = "-->"


def bundle_digest(bundle: Mapping[str, Any]) -> str:
    payload = json.dumps(bundle, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _fmt(value: Any) -> str:
    if value is None:
        return "NOT AVAILABLE"
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, Mapping):
        if "value" in value:
            return _fmt(value.get("value")) + (
                f" (n={value['n']})" if value.get("n") is not None else ""
            )
        return ", ".join(f"{k}={_fmt(v)}" for k, v in value.items())
    return str(value)


def _metric_row(analysis: Mapping[str, Any], arm: str, path: str) -> str:
    node: Any = (analysis.get("metric_arm_vector") or {}).get(arm, {})
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return "NOT AVAILABLE"
        node = node[part]
    return _fmt(node)


def render_markdown(bundle: Mapping[str, Any]) -> str:
    analysis = bundle["analysis"]
    arms = list(analysis.get("arms") or ())
    runtime_arm = bundle.get("runtime_arm") or ("haplous_mediated" if "haplous_mediated" in arms else arms[0])
    baseline = analysis.get("baseline_arm")
    lines: list[str] = []
    lines.append(f"{DIGEST_PREFIX} {bundle_digest(bundle)} {DIGEST_SUFFIX}")
    lines.append("")
    lines.append(f"# HES v0 report: {analysis.get('experiment_id')}")
    lines.append("")
    lines.append(
        f"{analysis.get('n_scenarios')} scenarios, {analysis.get('n_episodes')} episodes, "
        f"arms {arms}, unmediated baseline `{baseline}`."
    )
    lines.append("")

    lines.append("## Execution authority")
    lines.append("")
    lines.append("| arm | bounded success | coverage | execution capture | false refusal | violation given acceptance |")
    lines.append("|---|---|---|---|---|---|")
    for arm in arms:
        lines.append(
            "| {arm} | {bsr} | {cov} | {cap} | {fr} | {viol} |".format(
                arm=arm,
                bsr=_metric_row(analysis, arm, "system.bounded_success"),
                cov=_metric_row(analysis, arm, "system.coverage"),
                cap=_metric_row(analysis, arm, "authority.execution_capture"),
                fr=_metric_row(analysis, arm, "authority.false_refusal_rate"),
                viol=_metric_row(analysis, arm, "authority.violation_given_acceptance"),
            )
        )
    lines.append("")

    attain = analysis.get("attainability") or {}
    lines.append("## Attainability")
    lines.append("")
    lines.append(f"counts: {_fmt(attain.get('counts'))}")
    lines.append(f"provenance: {_fmt(attain.get('provenance'))}")
    lines.append("")
    lines.append(
        "Attainment-relative metrics (execution capture, false refusal) are bounded "
        "by the demonstrated-attainable mass. `UNKNOWN` is not `non-attainable`."
    )
    lines.append("")

    intervention = analysis.get("intervention") or {}
    lines.append("## Intervention quality")
    lines.append("")
    lines.append(f"counts: {_fmt(intervention.get('counts'))}")
    for classification in ("BENEFICIAL", "HARMFUL", "UNNECESSARY"):
        lines.append(f"- `{classification}`: {meaning(classification)}")
    lines.append("")
    lines.append(
        "| metric | value |\n|---|---|\n"
        f"| intervention precision | {_fmt(intervention.get('intervention_precision'))} |\n"
        f"| harmful intervention rate | {_fmt(intervention.get('harmful_intervention_rate'))} |\n"
        f"| unnecessary intervention rate | {_fmt(intervention.get('unnecessary_intervention_rate'))} |\n"
        f"| bounded successes recovered | {_fmt(intervention.get('bounded_successes_recovered'))} |\n"
        f"| bounded successes destroyed | {_fmt(intervention.get('bounded_successes_destroyed'))} |\n"
    )
    lines.append("")

    lines.append("## Non-interference")
    lines.append("")
    lines.append("| arm | preservation | BSR regression | task regression | unnecessary intervention | completion-time tax |")
    lines.append("|---|---|---|---|---|---|")
    for arm in arms:
        if arm == baseline:
            continue
        lines.append(
            "| {arm} | {p} | {b} | {t} | {u} | {c} |".format(
                arm=arm,
                p=_metric_row(analysis, arm, "non_interference.preservation_rate"),
                b=_metric_row(analysis, arm, "non_interference.bounded_success_regression_rate"),
                t=_metric_row(analysis, arm, "non_interference.task_success_regression_rate"),
                u=_metric_row(analysis, arm, "non_interference.unnecessary_intervention_rate"),
                c=_metric_row(analysis, arm, "non_interference.completion_time_tax_s"),
            )
        )
    lines.append("")

    attribution = (analysis.get("metric_arm_vector") or {}).get(runtime_arm, {}).get("attribution") or {}
    lines.append("## Failure attribution (run arm)")
    lines.append("")
    lines.append(f"by layer: {_fmt(attribution.get('by_layer'))}")
    lines.append(f"by runtime subcause: {_fmt(attribution.get('by_runtime_subcause'))}")
    lines.append(f"unknown: {attribution.get('n_unknown')} of {attribution.get('n_failures')}")
    lines.append("")

    responsibility = (analysis.get("metric_arm_vector") or {}).get(runtime_arm, {}).get("responsibility")
    lines.append("## Responsibility decomposition")
    lines.append("")
    lines.append("Refusals are charged as a caused loss only when the scenario was ")
    lines.append("demonstrated attainable; UNKNOWN-attainability refusals are tracked separately.")
    lines.append("")
    lines.append(_fmt(responsibility))
    lines.append("")

    pareto = (analysis.get("metric_arm_vector") or {}).get(runtime_arm, {}).get("failure_pareto") or []
    lines.append("## Failure Pareto (run arm, top 10)")
    lines.append("")
    lines.append("| cause | count | severity | haplous responsibility | addressability | priority |")
    lines.append("|---|---|---|---|---|---|")
    for row in pareto[:10]:
        lines.append(
            "| {k} | {c} | {s} | {r} | {a} | {p} |".format(
                k=row["cause_key"],
                c=row["count"],
                s=_fmt(row["severity"]),
                r=_fmt(row["haplous_responsibility"]),
                a=row["addressability"],
                p=_fmt(row["priority"]),
            )
        )
    lines.append("")

    gates = bundle.get("hard_gates") or {}
    lines.append("## Hard gates")
    lines.append("")
    lines.append(f"qualified: **{gates.get('qualified')}**")
    for gate in gates.get("gates") or ():
        mark = "PASS" if gate["passed"] else "FAIL"
        lines.append(
            f"- `{gate['gate_id']}` {gate['name']}: {mark} "
            f"(observed {_fmt(gate['observed'])}, threshold {_fmt(gate['threshold'])})"
        )
    lines.append("")

    integrity = bundle.get("integrity") or {}
    lines.append("## Integrity")
    lines.append("")
    lines.append(f"passed: **{integrity.get('passed')}**")
    for check in integrity.get("checks") or ():
        lines.append(f"- {check['name']}: {'PASS' if check['passed'] else 'FAIL'} - {check['detail']}")
    lines.append("")

    health = bundle.get("benchmark_health") or {}
    lines.append("## Benchmark health")
    lines.append("")
    lines.append(f"healthy: **{health.get('healthy')}**")
    for finding in health.get("findings") or ():
        lines.append(
            f"- {finding['name']}: {'PASS' if finding['healthy'] else 'FAIL'} - "
            f"{finding['detail']}"
        )
    lines.append("")

    ledger = bundle.get("claim_ledger") or {}
    lines.append("## Claim ledger")
    lines.append("")
    lines.append("| claim | registry | HES | executable | notes |")
    lines.append("|---|---|---|---|---|")
    for claim in ledger.get("claims") or ():
        note = (claim.get("notes") or [""])[0]
        lines.append(
            f"| {claim['claim_id']} {claim['name']} | {claim['registry_status']} | "
            f"{claim['hes_status']} | {claim['executable']} | {note} |"
        )
    lines.append("")

    lines.append("## Recommended next experiments")
    lines.append("")
    for rec in bundle.get("recommendations") or ():
        lines.append(
            f"- **{rec['experiment']}** (priority {_fmt(rec['priority'])}): "
            f"removes uncertainty about {rec['removes_uncertainty']}. {rec['rationale']} "
            f"Cost: {rec['estimated_cost']}."
        )
    lines.append("")

    not_available = analysis.get("not_available") or {}
    lines.append("## Caveats")
    lines.append("")
    caveats: list[str] = []
    for arm_name in arms:
        caveats.extend((analysis.get("metric_arm_vector") or {}).get(arm_name, {}).get("caveats") or [])
    for caveat in dict.fromkeys(caveats):
        lines.append(f"- {caveat}")
    lines.append("")

    lines.append("## Not available from this evidence")
    lines.append("")
    for name, reason in not_available.items():
        lines.append(f"- `{name}`: {reason}")
    lines.append("")

    corpus = bundle.get("corpus") or {}
    if corpus:
        lines.append("## Historical failure corpus")
        lines.append("")
        lines.append(f"{corpus.get('n')} entries, {corpus.get('n_without_regression_test')} without a regression test.")
        lines.append("")

    return "\n".join(lines) + "\n"


def check_report(path: str | Path, bundle: Mapping[str, Any]) -> tuple[bool, str]:
    """True when the committed report matches the current evidence digest."""
    path = Path(path)
    if not path.is_file():
        return False, f"report {path} does not exist"
    text = path.read_text(encoding="utf-8")
    expected = bundle_digest(bundle)
    marker = f"{DIGEST_PREFIX} {expected} {DIGEST_SUFFIX}"
    if marker in text:
        return True, "report matches the evidence digest"
    return False, f"report drift: expected {marker!r} not found (upstream evidence changed)"