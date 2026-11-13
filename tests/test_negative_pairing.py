"""Negative test (M3.3, probe 5): a paired comparison must refuse rows that do not pair.

Each planted defect makes system A on one scenario stand in for a pair with
system B on another. The pairing functions must raise, not score.
hes.regression.paired_rate_delta is not covered: tests/test_hes_smoke.py
feeds it a three-arm row list and relies on the duplicate ids collapsing.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from hes_fixtures import row

from evaluation.aggregate import load_rows, paired_comparison
from hes.metrics import noninterference_metrics

ROWS = Path(__file__).resolve().parents[1] / "results" / "heb_final_rows.jsonl"


def _arms():
    rows = load_rows(ROWS)
    a = [r for r in rows if r["system"] == "haplous_mediated"]
    b = [r for r in rows if r["system"] == "bare_behavior"]
    return a, b


def _hes_arms():
    a = [row(f"s{i}", "haplous_mediated", "COMPLETE", bounded_success=True, tags=(f"t{i}",)) for i in range(4)]
    b = [row(f"s{i}", "direct_minimal", "COMPLETE", bounded_success=True, tags=(f"t{i}",)) for i in range(4)]
    return a, b


def _duplicate_scenario(a, b):
    # A second, contradicting A-row for one scenario: which one pairs is arbitrary.
    a.append({**a[0], "task_success": not a[0]["task_success"], "bounded_success": not a[0]["bounded_success"]})


def _foreign_system_row(a, b):
    # B's row on another scenario slipped into arm A.
    a[0] = {**b[1], "scenario_id": a[0]["scenario_id"]}


def _relabelled_scenario(a, b):
    # A ran a different scenario (other tags) but its row carries B's scenario id.
    a[0] = {**a[0], "tags": ["some", "other", "scenario"]}


DEFECTS = [_duplicate_scenario, _foreign_system_row, _relabelled_scenario]


def test_clean_arms_pair():
    a, b = _arms()
    assert paired_comparison(a, b)["n_pairs"] == len(a) == len(b)
    ha, hb = _hes_arms()
    assert noninterference_metrics(ha, hb)["n_reference_bounded_success"] == 4


@pytest.mark.parametrize("plant", DEFECTS, ids=lambda f: f.__name__.strip("_"))
def test_paired_comparison_refuses_mispaired_rows(plant):
    a, b = _arms()
    plant(a, b)
    with pytest.raises(ValueError, match="pair"):
        paired_comparison(a, b)


@pytest.mark.parametrize("plant", DEFECTS, ids=lambda f: f.__name__.strip("_"))
def test_noninterference_refuses_mispaired_rows(plant):
    a, b = _hes_arms()
    plant(a, b)
    with pytest.raises(ValueError, match="pair"):
        noninterference_metrics(a, b)


def test_disjoint_scenarios_already_refused():
    a, b = _arms()
    b = [{**r, "scenario_id": r["scenario_id"] + "#other"} for r in b]
    with pytest.raises(ValueError, match="no shared scenario ids"):
        paired_comparison(a, b)
