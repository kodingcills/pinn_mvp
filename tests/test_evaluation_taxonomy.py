"""Taxonomy classification tests: mechanical, evidence-driven, no guessing."""

import json
from pathlib import Path

from evaluation.taxonomy import ROOT_CAUSES, SEVERITY_DEFS, classify, summarize


def _row(**kw):
    base = {
        "system": "haplous_mediated",
        "scenario_id": "s1",
        "task_success": False,
        "bounded_success": False,
        "graded_disposition": "FAILED",
        "hard_violations": [],
        "refusal_codes": [],
        "task_metrics": {},
        "tags": [],
        "wall_s": 1.0,
    }
    base.update(kw)
    return base


def test_successful_row_not_classified():
    assert classify(_row(task_success=True, bounded_success=True, graded_disposition="COMPLETE")) is None


def test_hard_violation_is_s3_unless_escalated():
    fc = classify(_row(hard_violations=[{"t": 1.0, "what": "force"}]))
    assert fc.severity == "S3"
    assert fc.root_cause == "UNKNOWN"
    fc4 = classify(_row(hard_violations=[{"t": 1.0}], severity_escalation="S4"))
    assert fc4.severity == "S4"


def test_refusal_on_feasible_is_s1():
    fc = classify(_row(graded_disposition="REFUSED_FEASIBILITY", refusal_codes=["INFEASIBLE_START"]))
    assert fc.severity == "S1"


def test_benign_timeout_is_s0():
    fc = classify(_row(graded_disposition="FAILED", reason="timeout"))
    assert fc.severity == "S0"


def test_plain_task_failure_is_s2():
    fc = classify(_row())
    assert fc.severity == "S2"


def test_attribution_only_from_explicit_field():
    fc = classify(_row(failure_attribution="CONTROL"))
    assert fc.root_cause == "CONTROL"
    fc = classify(_row(failure_attribution="made_up_class"))
    assert fc.root_cause == "UNKNOWN"


def test_root_cause_enum_is_closed():
    assert ROOT_CAUSES == (
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


def test_summarize_expected_loss_ranking():
    rows = [
        _row(scenario_id="a", hard_violations=[1], failure_attribution="EXECUTION_SEMANTICS"),
        _row(scenario_id="b", graded_disposition="REFUSED_FEASIBILITY", refusal_codes=["x"]),
        _row(scenario_id="c", task_success=True, bounded_success=True, graded_disposition="COMPLETE"),
    ]
    s = summarize(rows)
    assert s["n_trials"] == 3
    assert s["n_failures"] == 2
    assert s["by_severity"] == {"S3": 1, "S1": 1}
    assert s["by_root_cause"] == {"EXECUTION_SEMANTICS": 1, "UNKNOWN": 1}
    # S3 weight 10 dominates S1 weight 1
    assert s["expected_loss_proxy_by_root_cause"]["EXECUTION_SEMANTICS"] == 10.0


def test_classify_real_heb_rows_smoke():
    rows_path = Path(__file__).resolve().parent.parent / "results" / "heb_final_rows.jsonl"
    if not rows_path.exists():
        return
    n_classified = 0
    with open(rows_path) as f:
        for line in f:
            row = json.loads(line)
            fc = classify(row)
            if fc is not None:
                assert fc.severity in SEVERITY_DEFS
                assert fc.root_cause in ROOT_CAUSES
                n_classified += 1
    assert n_classified >= 0