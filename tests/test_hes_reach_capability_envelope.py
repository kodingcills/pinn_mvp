"""Reach-capability envelope campaign driver: selection, statistics, and
deterministic artifact reproduction.

The heavy physics sweep is not repeated here. These tests pin the pure
functions and, when the committed raw ledger exists, verify that the
committed derived artifacts reproduce exactly (the ``--check`` invariant).
"""
from __future__ import annotations

import pytest

from scripts.run_hes_reach_capability_envelope import (
    BANDS,
    GUARANTEED_ABORT_FORCE_N,
    RAW_ROWS,
    OFFSET_HI_M,
    OFFSET_LO_M,
    _band_of,
    _isotonic_decreasing,
    _self_check,
    build_artifacts,
    capture_levels,
    eligible_scenarios,
    mcnemar_exact,
    population_audit,
    wilson,
)


def test_eligible_selection_is_the_envelope_band() -> None:
    scs = eligible_scenarios()
    assert len(scs) == len({s.id for s in scs})
    assert len(scs) == 664
    for s in scs:
        assert s.task_family == "peg_insertion"
        assert OFFSET_LO_M <= s.initial_offset_m <= OFFSET_HI_M


def test_population_audit_counts_are_internally_consistent() -> None:
    audit = population_audit()
    band = audit["band"]
    assert band["n"] == band["eligible_n"] + band["excluded_guaranteed_abort_n"]
    assert sum(band["eligible_counts_per_band"].values()) == band["eligible_n"]
    assert set(band["eligible_counts_per_band"]) == {
        f"{lo:.2f}-{hi:.2f}" for lo, hi in BANDS
    }


def test_band_lookup_covers_the_closed_band() -> None:
    assert _band_of(3.00) == (3.00, 3.25)
    assert _band_of(4.99) == (4.75, 5.00)
    assert _band_of(5.00) == (4.75, 5.00)
    assert _band_of(2.99) is None
    assert _band_of(5.01) is None


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


def test_isotonic_fit_is_non_increasing_and_weighted() -> None:
    pts = [(3.1, 0.9, 10), (3.4, 0.95, 10), (3.6, 0.5, 10)]
    fit = _isotonic_decreasing(pts)
    assert all(b <= a + 1e-12 for a, b in zip(fit, fit[1:]))
    assert fit[0] == pytest.approx((0.9 * 10 + 0.95 * 10) / 20)
    assert fit[-1] == pytest.approx(0.5)


def _row(sid: str, offset_mm: float, bounded: bool) -> dict:
    branch = {
        "disposition": "COMPLETE",
        "reason": "seated",
        "refusal_codes": [],
        "refusal_stage": "none",
        "graded": "COMPLETE",
        "task_success": bounded,
        "bounded_success": bounded,
        "n_hard_violations": 0,
        "hard_violations": [],
        "peak_abs_force_n": 5.0,
        "max_tilt_deg": 1.0,
        "seating_time_s": 1.0,
        "wall_s": 1.0,
    }
    return {
        "scenario_id": sid,
        "task_family": "peg_insertion",
        "guaranteed_abort_class": False,
        "in_frozen_population": False,
        "axes": {"initial_offset_m": offset_mm / 1e3},
        "branches": {b: dict(branch) for b in "ABCD"},
    }


def test_capture_levels_finds_the_declared_drop() -> None:
    rows = []
    for i in range(40):
        offset = 3.05 + 0.05 * i  # 3.05 .. 5.00
        rows.append(_row(f"s{i}", offset, bounded=offset <= 3.75))
    levels = capture_levels(rows, "B")
    # capture is 1.0 then 0.0; x50 must land in the band where it drops.
    assert levels["levels"]["x50"]["raw_crossing_mm"] is not None
    assert 3.75 <= levels["levels"]["x50"]["raw_crossing_mm"] <= 4.00


def test_self_check_rejects_duplicate_scenarios() -> None:
    row = _row("dup", 3.1, True)
    with pytest.raises(AssertionError):
        _self_check([row, row], complete=False)


def test_self_check_rejects_reach_authorised_branch_a() -> None:
    row = _row("r", 4.0, True)
    row["branches"]["A"]["refusal_codes"] = []
    with pytest.raises(AssertionError):
        _self_check([row], complete=False)


@pytest.mark.skipif(
    not (RAW_ROWS.is_file() and (RAW_ROWS.parent / "decision.json").is_file()),
    reason="committed raw ledger and derived artifacts not both present",
)
def test_committed_artifacts_reproduce() -> None:
    import json

    from scripts.run_hes_reach_capability_envelope import RESULTS, load_rows

    rows = load_rows()
    art = build_artifacts(rows, complete=len(rows) == 664)
    for name, payload in art.items():
        existing = (RESULTS / name).read_text(encoding="utf-8")
        assert existing == json.dumps(payload, indent=2, sort_keys=True) + "\n", name
