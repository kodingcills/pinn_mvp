"""Boundary-calibration campaign driver: selection, statistics, and
deterministic artifact reproduction.

The heavy physics run is not repeated here. These tests pin the pure
functions and, when the committed raw ledger exists, verify that the
committed derived artifacts reproduce exactly (the `--check` invariant).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_hes_boundary_calibration import (
    BOUNDARY_HI,
    GUARANTEED_ABORT_FORCE_N,
    RAW_ROWS,
    RESULTS,
    _self_check,
    build_artifacts,
    eligible_scenarios,
    evaluator_repair,
    mcnemar_exact,
    wilson,
)

ROOT = Path(__file__).resolve().parents[1]


def test_eligible_selection_is_the_declared_set() -> None:
    scs = eligible_scenarios()
    assert len(scs) == 142
    assert len({s.id for s in scs}) == 142
    for s in scs:
        assert s.task_family == "peg_insertion"
        assert BOUNDARY_HI[0] < s.initial_offset_m <= BOUNDARY_HI[1]
        assert s.max_force_n != GUARANTEED_ABORT_FORCE_N


def test_wilson_interval_brackets_the_point_estimate() -> None:
    for k, n in ((0, 10), (5, 10), (10, 10), (24, 142)):
        ci = wilson(k, n)
        assert ci["ci_low"] <= ci["value"] <= ci["ci_high"]
        assert 0.0 <= ci["ci_low"] and ci["ci_high"] <= 1.0
    assert wilson(0, 0) is None


def test_mcnemar_exact_is_two_sided_and_symmetric() -> None:
    a = mcnemar_exact(6, 0)
    b = mcnemar_exact(0, 6)
    assert a["p_value"] == b["p_value"] == pytest.approx(0.03125)
    assert mcnemar_exact(0, 0)["p_value"] == 1.0


def test_self_check_rejects_a_short_ledger() -> None:
    with pytest.raises(AssertionError):
        _self_check([])


def test_evaluator_repair_supersedes_the_legacy_harmful_labels() -> None:
    repair = evaluator_repair()
    assert repair["n_intervention_forks"] == 23
    migrated = sum(repair["migration"].values())
    assert migrated == 23
    assert repair["legacy_HARMFUL"]["n"] == 12
    # The historical completion-only rule labelled a continuation HARMFUL
    # even when it breached a declared hard contract.
    assert repair["legacy_HARMFUL"]["bounded_continuations"] <= 1
    assert all(
        r["corrected"] != "HARMFUL"
        for r in repair["rows"]
        if r["legacy"] == "HARMFUL" and not r["continuation_bounded"]
    )


@pytest.mark.skipif(not RAW_ROWS.is_file(), reason="no committed boundary-calibration ledger")
def test_committed_artifacts_reproduce_from_the_ledger() -> None:
    rows = [
        json.loads(line)
        for line in RAW_ROWS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    art = build_artifacts(rows)
    for name, payload in art.items():
        canonical = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        assert (RESULTS / name).read_text(encoding="utf-8") == canonical, name