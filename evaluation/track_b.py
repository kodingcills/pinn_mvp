"""Track B: deterministic execution-contract decision engine + G0 minimal-guards ablation.

Software-semantics prototype for the v1.1 fixtures (evaluation/spec/haplous_eval_v1_1/).
It is NOT a safety function, a protective stop, or a controller. TERMINATE on
`oem_stop_active` only reports an externally authoritative stop state.

Precedence (BASELINE_SPEC.md "Track B independent expected-decision precedence"):
  0 external stop  1 authority/lifecycle  2 capability/preconditions  3 clock validity
  4 command deadline  5 observation freshness  6 mode handover  7 admit
Every predicate is evaluated; all fired faults are recorded in precedence order and the
first one decides. Lower-precedence faults remain visible in the trace as shadowed.

Engine extensions where the fixtures are silent (fail-closed; reason codes outside the
fixture vocabulary are marked EXT):
  * INPUT_INVALID (EXT): missing, unknown, or ill-typed input keys -> REFUSE (after stop).
  * AUTHORITY_EPOCH_INVALID (EXT): command_epoch newer than active_epoch -> REFUSE.
  * REVOKE / LEASE_EXPIRED events while IDLE -> REFUSE with the authority reason.
  * lease_valid == false while ACTIVE (any event) -> TERMINATE / AUTHORITY_LEASE_EXPIRED.
  * command_age_ms below -future_skew_tolerance_ms -> REFUSE / CLOCK_INVALID (the spec
    names observation age; the same skew bound is applied to the command clock).
  * No clock conversion is ever "explicitly proven" in v1.1 inputs, so any domain
    mismatch (observation or command) -> REFUSE / CLOCK_DOMAIN_MISMATCH.
  * Several verified + authorized substitutes: the first in allowed_mode_equivalents order.

CLI: python -m evaluation.track_b --fixtures FILE --out DIR
"""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluation.run_registry import canonical_bytes, strict_loads

DECISIONS = ("ADMIT", "REFUSE", "SUSPEND", "TERMINATE")
G0_LABELS = ("ALLOW", "BLOCK", "STOP")
EVENTS = ("COMMAND", "ACK", "LEASE_EXPIRED", "REVOKE")
STATES = ("IDLE", "ACTIVE")

_STR = ("clock_domain", "observation_clock_domain", "command_clock_domain", "required_mode", "resource_id",
        "command_resource_id", "authority_owner", "command_owner", "event", "execution_state")
_INT = ("future_skew_tolerance_ms", "contract_max_obs_age_ms", "command_deadline_ms", "active_epoch",
        "command_epoch", "ack_epoch", "observation_age_ms", "command_age_ms")
_NONNEG = ("future_skew_tolerance_ms", "contract_max_obs_age_ms", "command_deadline_ms")
_BOOL = ("lease_valid", "calibration_valid", "mode_switch_confirmed", "oem_stop_active")
_STR_LIST = ("available_modes", "required_sensors", "available_sensors", "non_substitutable_modes")
_MODE_MAP = ("allowed_mode_equivalents", "verified_mode_equivalents")
INPUT_KEYS = frozenset(_STR + _INT + _BOOL + _STR_LIST + _MODE_MAP + ("mode_specific_max_obs_age_ms",))


@dataclass(frozen=True)
class Fault:
    level: int
    decision: str
    reason: str
    detail: str


@dataclass(frozen=True)
class Decision:
    decision: str
    reason: str
    selected_mode: str | None
    faults: tuple[Fault, ...]
    trace: tuple[dict[str, Any], ...]


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def input_problems(x: Mapping[str, Any]) -> list[str]:
    """Every way the input fails to be a complete, well-typed scenario."""
    problems = [f"missing:{k}" for k in sorted(INPUT_KEYS - x.keys())]
    problems += [f"unknown:{k}" for k in sorted(x.keys() - INPUT_KEYS)]
    for k in _STR:
        if k in x and not (isinstance(x[k], str) and x[k]):
            problems.append(f"type:{k}")
    for k in _INT:
        if k in x and not _is_int(x[k]):
            problems.append(f"type:{k}")
    for k in _NONNEG:
        if k in x and _is_int(x[k]) and x[k] < 0:
            problems.append(f"range:{k}")
    for k in _BOOL:
        if k in x and not isinstance(x[k], bool):
            problems.append(f"type:{k}")
    for k in _STR_LIST:
        if k in x and not (isinstance(x[k], list) and all(isinstance(i, str) for i in x[k])):
            problems.append(f"type:{k}")
    for k in _MODE_MAP:
        v = x.get(k)
        if k in x and not (isinstance(v, dict) and all(
                isinstance(m, list) and all(isinstance(i, str) for i in m) for m in v.values())):
            problems.append(f"type:{k}")
    v = x.get("mode_specific_max_obs_age_ms")
    if "mode_specific_max_obs_age_ms" in x and not (
            isinstance(v, dict) and all(_is_int(i) and i >= 0 for i in v.values())):
        problems.append("type:mode_specific_max_obs_age_ms")
    if isinstance(x.get("event"), str) and x["event"] not in EVENTS:
        problems.append("enum:event")
    if isinstance(x.get("execution_state"), str) and x["execution_state"] not in STATES:
        problems.append("enum:execution_state")
    return problems


def _resolve_mode(x: Mapping[str, Any]) -> tuple[str | None, Fault | None, dict[str, Any] | None]:
    """Exact mode, or a substitute that is BOTH authorized and verified; never a silent downgrade."""
    req = x["required_mode"]
    if req in x["available_modes"]:
        return req, None, None
    allowed = x["allowed_mode_equivalents"].get(req, [])
    verified = set(x["verified_mode_equivalents"].get(req, []))
    candidates = [m for m in allowed if m in verified and m in x["available_modes"]]
    if req in x["non_substitutable_modes"]:
        reason = "CONTROL_AUTHORITY_NOT_EQUIVALENT" if candidates else "CAPABILITY_UNSUPPORTED"
        return None, Fault(2, "REFUSE", reason, f"{req} requires exact control authority"), None
    if candidates:
        sub = {"check": "BEHAVIORAL_SUBSTITUTION", "required_mode": req, "selected_mode": candidates[0],
               "authorized": True, "verified": True}
        return candidates[0], None, sub
    missing = [w for w, ok in (("authorization", bool(set(allowed) & set(x["available_modes"]))),
                               ("verification", bool(verified & set(x["available_modes"])))) if not ok]
    return None, Fault(2, "REFUSE", "CAPABILITY_UNSUPPORTED",
                       f"{req} unavailable; substitute lacks {'+'.join(missing) or 'availability'}"), None


def decide(x: Mapping[str, Any]) -> Decision:
    """Pure function of the merged scenario input."""
    faults: list[Fault] = []
    trace: list[dict[str, Any]] = []
    if x.get("oem_stop_active") is True:
        faults.append(Fault(0, "TERMINATE", "OEM_STOP_ACTIVE", "external authoritative stop state"))
    problems = input_problems(x)
    if problems:
        faults.append(Fault(0, "REFUSE", "INPUT_INVALID", ",".join(problems)))
        return _finish(faults, trace, None)

    active = x["execution_state"] == "ACTIVE"
    event = x["event"]
    # 1. authority and lifecycle
    if active and event == "REVOKE":
        faults.append(Fault(1, "TERMINATE", "AUTHORITY_REVOKED", "revoked during ACTIVE"))
    if active and (event == "LEASE_EXPIRED" or not x["lease_valid"]):
        faults.append(Fault(1, "TERMINATE", "AUTHORITY_LEASE_EXPIRED", "lease lost during ACTIVE"))
    # Refusals in BASELINE_SPEC's listed order: owner, resource, epoch, lease, ACK epoch.
    if x["command_owner"] != x["authority_owner"]:
        faults.append(Fault(1, "REFUSE", "AUTHORITY_CONFLICT", f"{x['command_owner']} != {x['authority_owner']}"))
    if x["command_resource_id"] != x["resource_id"]:
        faults.append(Fault(1, "REFUSE", "AUTHORITY_RESOURCE_MISMATCH",
                            f"{x['command_resource_id']} != {x['resource_id']}"))
    if x["command_epoch"] < x["active_epoch"]:
        faults.append(Fault(1, "REFUSE", "AUTHORITY_REVOKED", f"epoch {x['command_epoch']} < {x['active_epoch']}"))
    if x["command_epoch"] > x["active_epoch"]:
        faults.append(Fault(1, "REFUSE", "AUTHORITY_EPOCH_INVALID",
                            f"epoch {x['command_epoch']} > {x['active_epoch']}"))
    if not active and event == "REVOKE":
        faults.append(Fault(1, "REFUSE", "AUTHORITY_REVOKED", "revoke while not ACTIVE"))
    if not active and (event == "LEASE_EXPIRED" or not x["lease_valid"]):
        faults.append(Fault(1, "REFUSE", "AUTHORITY_LEASE_EXPIRED", "no valid lease"))
    if event == "ACK" and x["ack_epoch"] != x["active_epoch"]:
        faults.append(Fault(1, "REFUSE", "STALE_ACK", f"ack epoch {x['ack_epoch']} != {x['active_epoch']}"))
    # 2. capability and preconditions
    selected, mode_fault, substitution = _resolve_mode(x)
    if mode_fault:
        faults.append(mode_fault)
    if substitution:
        trace.append(substitution)
    missing_sensors = sorted(set(x["required_sensors"]) - set(x["available_sensors"]))
    if missing_sensors:
        faults.append(Fault(2, "REFUSE", "OBSERVATION_UNAVAILABLE", f"missing {missing_sensors}"))
    if not x["calibration_valid"]:
        faults.append(Fault(2, "REFUSE", "CALIBRATION_INVALID", "calibration invalid"))
    # 3. clock validity (domain first: ages across unmapped domains are meaningless)
    for side in ("observation", "command"):
        if x[f"{side}_clock_domain"] != x["clock_domain"]:
            faults.append(Fault(3, "REFUSE", "CLOCK_DOMAIN_MISMATCH",
                                f"{side} {x[f'{side}_clock_domain']} != {x['clock_domain']} (no proven conversion)"))
    for side in ("observation", "command"):
        if x[f"{side}_age_ms"] < -x["future_skew_tolerance_ms"]:
            faults.append(Fault(3, "REFUSE", "CLOCK_INVALID",
                                f"{side} age {x[f'{side}_age_ms']} < -{x['future_skew_tolerance_ms']}"))
    # 4. command deadline
    if x["command_age_ms"] > x["command_deadline_ms"]:
        faults.append(Fault(4, "REFUSE", "COMMAND_EXPIRED", f"{x['command_age_ms']} > {x['command_deadline_ms']}"))
    # 5. observation freshness against the SELECTED mode's bound
    mode_limits = x["mode_specific_max_obs_age_ms"]
    if selected is not None and selected in mode_limits:
        limit, reason = mode_limits[selected], "STATE_STALE_FOR_MODE"
    else:
        limit, reason = x["contract_max_obs_age_ms"], "STATE_STALE"
    if x["observation_age_ms"] > limit:
        faults.append(Fault(5, "SUSPEND", reason, f"{x['observation_age_ms']} > {limit}"))
    # 6. mode handover
    if not x["mode_switch_confirmed"]:
        faults.append(Fault(6, "SUSPEND", "MODE_SWITCH_UNCONFIRMED", "mode switch not confirmed"))
    return _finish(faults, trace, selected)


def _finish(faults: list[Fault], trace: list[dict[str, Any]], selected: str | None) -> Decision:
    faults.sort(key=lambda f: f.level)  # stable: within a level, evaluation order is precedence
    if faults:
        top = faults[0]
        decision, reason, selected = top.decision, top.reason, None
    elif any(t["check"] == "BEHAVIORAL_SUBSTITUTION" for t in trace):
        decision, reason = "ADMIT", "AUTHORIZED_EQUIVALENCE"
    else:
        decision, reason = "ADMIT", "OK"
    steps = [{"check": f"L{f.level}:{f.reason}", "decision": f.decision, "detail": f.detail,
              "role": "decisive" if i == 0 else "shadowed"} for i, f in enumerate(faults)]
    full = tuple(trace + steps + [{"check": "DECISION", "decision": decision, "reason": reason,
                                   "selected_mode": selected}])
    return Decision(decision, reason, selected, tuple(faults), full)


def g0_decision(x: Mapping[str, Any]) -> str:
    """baseline_g0_minimal_guards, exactly as BASELINE_SPEC.md defines it. A deliberately
    limited component-ablation reference, NOT an industrial incumbent."""
    if x["oem_stop_active"]:
        return "STOP"
    if x["required_mode"] not in x["available_modes"]:
        return "BLOCK"
    if x["command_age_ms"] > x["command_deadline_ms"]:
        return "BLOCK"
    return "ALLOW"


# Checks the candidate performs, and whether G0's three rules cover them.
CHECK_COVERAGE = {
    "external_stop_state": True, "exact_mode_availability": True, "command_deadline": True,
    "lifecycle_revoke_terminate": False, "lifecycle_lease_terminate": False, "authority_owner": False,
    "authority_resource": False, "authority_epoch": False, "lease_validity": False, "ack_epoch": False,
    "verified_and_authorized_substitution": False, "non_substitutable_control_authority": False,
    "required_sensors": False, "calibration": False, "clock_domain": False, "future_skew": False,
    "observation_freshness": False, "mode_specific_freshness": False, "mode_switch_confirmation": False,
    "input_completeness": False,
}
TRUTH_TO_G0 = {"ADMIT": "ALLOW", "REFUSE": "BLOCK", "SUSPEND": "BLOCK", "TERMINATE": "STOP"}


# --------------------------------------------------------------------------
# Judging against independent fixture truth (never against the candidate itself)
# --------------------------------------------------------------------------


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def evaluate_fixtures(fixtures: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    for case in fixtures["cases"]:
        x = {**fixtures["defaults"], **case["overrides"]}
        out = decide(x)
        details = case.get("expected_details", {})
        trace_checks = {t["check"] for t in out.trace}
        checks = {
            "decision": out.decision == case["expected"],
            "reason": out.reason == case["reason"],
            "selected_mode": details.get("selected_mode", out.selected_mode) == out.selected_mode,
            "trace_contains": set(details.get("trace_contains", [])) <= trace_checks,
        }
        g0 = g0_decision(x)
        row = {
            "id": case["id"],
            "input_sha256": sha256_hex(canonical_bytes(x)),
            "expected": {"decision": case["expected"], "reason": case["reason"], "details": details},
            "observed": {"decision": out.decision, "reason": out.reason, "selected_mode": out.selected_mode},
            "checks": checks,
            "pass": all(checks.values()),
            "mismatch_class": _mismatch_class(case["expected"], out.decision, checks),
            "faults": [f.reason for f in out.faults],
            "interaction": len({f.reason for f in out.faults}) >= 2,
            "trace": list(out.trace),
            "g0": {"observed": g0, "fixture_expected": case["expected_baseline_g0"],
                   "matches_g0_spec": g0 == case["expected_baseline_g0"],
                   "truth_mapped": TRUTH_TO_G0[case["expected"]],
                   "aligned_with_truth": g0 == TRUTH_TO_G0[case["expected"]]},
        }
        row["trace_id"] = sha256_hex(canonical_bytes({k: row[k] for k in ("id", "input_sha256", "observed", "trace")}))
        rows.append(row)
    return rows, summarize(rows)


def _mismatch_class(expected: str, observed: str, checks: Mapping[str, bool]) -> str | None:
    if all(checks.values()):
        return None
    if expected != "ADMIT" and observed == "ADMIT":
        return "false_admission"
    if expected == "TERMINATE" and observed != "TERMINATE":
        return "failed_terminal_transition"
    if expected == "ADMIT" and observed != "ADMIT":
        return "false_refusal"
    if expected != observed:
        return "wrong_decision"
    if not checks["reason"]:
        return "wrong_reason"
    return "wrong_details"


def _matrix(pairs: Sequence[tuple[str, str]], rows: Sequence[str], cols: Sequence[str]) -> dict[str, dict[str, int]]:
    c = Counter(pairs)
    return {r: {k: c[(r, k)] for k in cols} for r in rows}


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    classes = Counter(r["mismatch_class"] for r in rows if r["mismatch_class"])
    g0_ok = [r for r in rows if r["g0"]["aligned_with_truth"]]
    inter = [r for r in rows if r["interaction"]]
    return {
        "population": {"cases": n, "unit": "fixture case"},
        "correctness": {
            "passed": sum(r["pass"] for r in rows), "denominator": n,
            "decision_correct": sum(r["checks"]["decision"] for r in rows),
            "reason_correct": sum(r["checks"]["reason"] for r in rows),
            "false_admission": classes["false_admission"], "false_refusal": classes["false_refusal"],
            "failed_terminal_transition": classes["failed_terminal_transition"],
            "wrong_decision": classes["wrong_decision"], "wrong_reason": classes["wrong_reason"],
            "wrong_details": classes["wrong_details"],
            "failed_case_ids": sorted(r["id"] for r in rows if not r["pass"]),
            "failed_trace_ids": {r["id"]: r["trace_id"] for r in rows if not r["pass"]},
        },
        "confusion_expected_vs_observed": _matrix(
            [(r["expected"]["decision"], r["observed"]["decision"]) for r in rows], DECISIONS, DECISIONS),
        "reason_pairs_expected_vs_observed": dict(sorted(Counter(
            f"{r['expected']['reason']} -> {r['observed']['reason']}" for r in rows).items())),
        "interactions": {
            "cases_with_multiple_faults": len(inter), "denominator": n,
            "correct": sum(r["pass"] for r in inter),
            "by_case": {r["id"]: r["faults"] for r in inter},
        },
        "g0_mechanism_isolation": {
            "label": "baseline_g0_minimal_guards: deliberately limited component ablation, NOT an industrial incumbent",
            "g0_matches_its_own_spec": sum(r["g0"]["matches_g0_spec"] for r in rows), "denominator": n,
            "aligned_with_truth": len(g0_ok),
            "mapping": TRUTH_TO_G0,
            "confusion_truth_vs_g0": _matrix(
                [(r["expected"]["decision"], r["g0"]["observed"]) for r in rows], DECISIONS, G0_LABELS),
            "g0_allow_where_truth_not_admit": sorted(
                r["id"] for r in rows if r["g0"]["observed"] == "ALLOW" and r["expected"]["decision"] != "ADMIT"),
            "g0_block_where_truth_admit": sorted(
                r["id"] for r in rows if r["g0"]["observed"] == "BLOCK" and r["expected"]["decision"] == "ADMIT"),
            "checks_implemented_by_g0": sorted(k for k, v in CHECK_COVERAGE.items() if v),
            "checks_not_implemented_by_g0": sorted(k for k, v in CHECK_COVERAGE.items() if not v),
        },
    }


def render_report(summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], provenance: Mapping[str, str]) -> str:
    """Markdown generated only from summary + rows; no other inputs."""
    c, g = summary["correctness"], summary["g0_mechanism_isolation"]
    lines = [
        "# Track B v1.1 results (generated)",
        "",
        "Evidence class: **software-semantics correctness against fixture truth**. Not physical safety, "
        "not a protective stop, not OEM portability, not VLA performance.",
        "",
        *[f"- {k}: `{v}`" for k, v in sorted(provenance.items())],
        "",
        "## A. Candidate correctness vs independent fixture truth",
        "",
        f"Passed **{c['passed']} / {c['denominator']}** cases (decision + reason + required details).",
        f"Decision correct {c['decision_correct']}/{c['denominator']}; reason correct "
        f"{c['reason_correct']}/{c['denominator']}.",
        "",
        "| mismatch class | count |", "|---|---:|",
        *[f"| {k} | {c[k]} |" for k in ("false_admission", "false_refusal", "failed_terminal_transition",
                                       "wrong_decision", "wrong_reason", "wrong_details")],
        "",
        f"Failed case ids: {', '.join(c['failed_case_ids']) or 'none'}",
        "",
        "Confusion matrix (rows expected, columns observed):",
        "",
        "| expected \\ observed | " + " | ".join(DECISIONS) + " |",
        "|---|" + "---:|" * len(DECISIONS),
        *[f"| {r} | " + " | ".join(str(summary['confusion_expected_vs_observed'][r][k]) for k in DECISIONS) + " |"
          for r in DECISIONS],
        "",
        f"Interactions: {summary['interactions']['correct']} / {summary['interactions']['cases_with_multiple_faults']} "
        "multi-fault cases correct.",
        "",
        "## B. G0 mechanism-isolation comparison (NOT a competitive comparison)",
        "",
        g["label"] + ".",
        "",
        f"G0 implementation matches its own spec (`expected_baseline_g0`): {g['g0_matches_its_own_spec']}/{g['denominator']}.",
        f"G0 aligned with truth under mapping {g['mapping']}: {g['aligned_with_truth']}/{g['denominator']}.",
        "",
        f"G0 ALLOW where truth is not ADMIT (omissions by construction): {', '.join(g['g0_allow_where_truth_not_admit']) or 'none'}",
        "",
        f"G0 BLOCK where truth is ADMIT: {', '.join(g['g0_block_where_truth_admit']) or 'none'}",
        "",
        f"Checks G0 implements ({len(g['checks_implemented_by_g0'])}): {', '.join(g['checks_implemented_by_g0'])}",
        "",
        f"Checks G0 does not implement ({len(g['checks_not_implemented_by_g0'])}): "
        f"{', '.join(g['checks_not_implemented_by_g0'])}",
        "",
        "## Per-case",
        "",
        "| case | expected | observed | pass | G0 | faults (decisive first) | trace id |",
        "|---|---|---|---|---|---|---|",
        *[f"| {r['id']} | {r['expected']['decision']}/{r['expected']['reason']} | "
          f"{r['observed']['decision']}/{r['observed']['reason']} | {'yes' if r['pass'] else '**NO**'} | "
          f"{r['g0']['observed']} | {', '.join(r['faults']) or '-'} | `{r['trace_id'][:12]}` |" for r in rows],
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evaluation.track_b")
    ap.add_argument("--fixtures", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    raw = args.fixtures.read_bytes()
    fixtures = strict_loads(raw)
    rows, summary = evaluate_fixtures(fixtures)
    provenance = {"fixture_sha256": sha256_hex(raw),
                  "engine_sha256": sha256_hex(Path(__file__).read_bytes()),
                  "fixture_version": str(fixtures["fixture_version"])}
    summary = {"provenance": provenance, **summary}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.jsonl").write_bytes(b"".join(canonical_bytes(r) + b"\n" for r in rows))
    (args.out / "summary.json").write_bytes(canonical_bytes(summary) + b"\n")
    (args.out / "report.md").write_text(render_report(summary, rows, provenance))
    c = summary["correctness"]
    print(f"track_b: {c['passed']}/{c['denominator']} passed; failed={c['failed_case_ids']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
