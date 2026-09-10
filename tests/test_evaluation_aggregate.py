"""Aggregation tests against committed HEB artifacts (Phase B reproduction)."""

import json
from pathlib import Path

import pytest

from evaluation.aggregate import arm_summary, campaign_summary, load_rows, paired_comparison
from evaluation.stats import mcnemar_exact

RESULTS = Path(__file__).resolve().parent.parent / "results"


def _load(name: str) -> list[dict]:
    return load_rows(RESULTS / name)


@pytest.mark.skipif(not (RESULTS / "heb_final_rows.jsonl").exists(), reason="artifact missing")
def test_heb_final_reproduces_committed_headlines():
    rows = _load("heb_final_rows.jsonl")
    by_system = {}
    for r in rows:
        by_system.setdefault(r["system"], []).append(r)

    hap = arm_summary(by_system["haplous_mediated"])
    bare = arm_summary(by_system["bare_behavior"])

    # Committed headlines (heb_final_summary.json, HEB 0.2.0 regrade):
    # mediated coverage 32/38=0.842, BSR among accepted 27/32=0.84375,
    # breach_trials 3; bare BSR among accepted 30/38=0.789, breach 6.
    assert hap["coverage"]["value"] == pytest.approx(0.8421, abs=1e-3)
    assert hap["bounded_success_among_accepted"]["value"] == pytest.approx(0.84375, abs=1e-4)
    assert bare["bounded_success_among_accepted"]["value"] == pytest.approx(0.78947, abs=1e-4)
    assert round(hap["any_violation"]["value"] * hap["n"]) == 3
    assert round(bare["any_violation"]["value"] * bare["n"]) == 6

    paired = paired_comparison(by_system["haplous_mediated"], by_system["bare_behavior"])
    assert paired["n_pairs"] > 0
    m = paired["mcnemar_task_success"]
    assert m["a_success"] == 27 and m["b_success"] == 36


@pytest.mark.skipif(not (RESULTS / "heb_final_summary.json").exists(), reason="artifact missing")
def test_summary_json_matches_independent_recomputation():
    committed = json.loads((RESULTS / "heb_final_summary.json").read_text())["systems"]
    rows = _load("heb_final_rows.jsonl")
    for system, expected in committed.items():
        rows_sys = [r for r in rows if r["system"] == system]
        summary = arm_summary(rows_sys)
        assert summary["n"] == expected["trials"]
        assert summary["coverage"]["value"] == pytest.approx(expected["coverage"], abs=1e-9)
        assert summary["bounded_success_among_accepted"]["value"] == pytest.approx(
            expected["bounded_success_rate_among_accepted"], abs=1e-9
        )


@pytest.mark.skipif(not (RESULTS / "heb_dev_rows.jsonl").exists(), reason="artifact missing")
def test_failure_taxonomy_runs_on_dev_rows():
    rows = _load("heb_dev_rows.jsonl")
    from evaluation.taxonomy import summarize

    s = summarize(rows)
    assert s["n_trials"] == len(rows)
    assert s["n_failures"] > 0  # dev set has documented failures