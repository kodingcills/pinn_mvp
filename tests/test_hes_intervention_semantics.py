"""Regression guard against BENEFICIAL/HARMFUL inversion.

Section 18 of the historical qualification report described HARMFUL as the
case where continuation "would have breached a declared bound". That is the
definition of BENEFICIAL. This test pins the canonical semantics to the
classifier so report prose cannot silently invert it again.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from benchmarks.heb.counterfactual import (
    BENEFICIAL,
    HARMFUL,
    UNNECESSARY,
    ContinuationOutcome,
    classify_intervention,
    classify_intervention_detailed,
)
from hes.intervention import MEANING, is_intervention, is_refusal, meaning, semantic_ok


def _continuation(*, completed: bool, peak_force_n: float) -> ContinuationOutcome:
    return ContinuationOutcome(
        completed=completed, peak_force_n=peak_force_n, max_tilt_deg=0.0, wall_s=0.0
    )


def test_classifier_and_canonical_semantics_agree() -> None:
    # Continuation worsens force -> BENEFICIAL (mediation prevented excess).
    worse = classify_intervention(10.0, _continuation(completed=True, peak_force_n=200.0), 25.0)
    assert worse == BENEFICIAL
    assert semantic_ok(worse, continued_completed=True, worsened=True)
    assert "better bounded outcome" in MEANING[BENEFICIAL]

    # Continuation completes without worsening -> HARMFUL (stop destroyed work).
    harmless = classify_intervention(10.0, _continuation(completed=True, peak_force_n=10.0), 25.0)
    assert harmless == HARMFUL
    assert semantic_ok(harmless, continued_completed=True, worsened=False)
    assert "bounded success" in MEANING[HARMFUL]

    # Neither -> UNNECESSARY.
    neither = classify_intervention(10.0, _continuation(completed=False, peak_force_n=10.0), 25.0)
    assert neither == UNNECESSARY
    assert semantic_ok(neither, continued_completed=False, worsened=False)


def test_bounded_epoch_semantic_guard() -> None:
    # Completion without a hard-contract violation is bounded success, so
    # a stop that destroys it is genuinely HARMFUL.
    assert semantic_ok(
        HARMFUL, continued_completed=True, worsened=False,
        cont_bounded=True, intervention_bounded=False,
    )
    # Completion WITH a hard-contract violation is not bounded success, so
    # the same stop is UNNECESSARY and calling it HARMFUL is rejected.
    assert semantic_ok(
        UNNECESSARY, continued_completed=True, worsened=False,
        cont_bounded=False, intervention_bounded=False,
    )
    assert not semantic_ok(
        HARMFUL, continued_completed=True, worsened=False,
        cont_bounded=False, intervention_bounded=False,
    )


def test_semantic_ok_rejects_inverted_labels() -> None:
    # The historical inverted prose: calling the worsening case HARMFUL.
    assert semantic_ok(HARMFUL, continued_completed=True, worsened=True) is False
    # ...and calling the benign-completion case BENEFICIAL.
    assert semantic_ok(BENEFICIAL, continued_completed=True, worsened=False) is False


def test_no_frozen_report_prose_inverts_the_classifier() -> None:
    renderer = Path(__file__).resolve().parent.parent / "scripts" / "render_v3_hardening_report.py"
    source = renderer.read_text(encoding="utf-8")
    # HARMFUL must never be described as continuation-would-breach.
    assert "HARMFUL (the direct\ncontinuation from the runtime stop would have breached" not in source
    assert "BENEFICIAL (the direct" in source


def test_classification_helpers() -> None:
    assert is_intervention(BENEFICIAL) and is_intervention(HARMFUL) and is_intervention(UNNECESSARY)
    assert is_refusal("TRUE_REFUSAL") and is_refusal("FALSE_REFUSAL")
    assert meaning("NOPE") == "NOT_AVAILABLE"
    assert "BENEFICIAL" in inspect.getsource(classify_intervention_detailed)