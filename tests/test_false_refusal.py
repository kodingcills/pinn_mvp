"""False-refusal classifier tests."""

import pytest

from benchmarks.heb.false_refusal import (
    FALSE_REFUSAL,
    TRUE_REFUSAL,
    RefusalCounterfactual,
    classify_refusal,
)


def _cf(**kw):
    base = dict(
        completed=True,
        deadline_met=True,
        freshness_satisfiable=True,
        peak_force_n=2.18,
        max_tilt_deg=0.1,
        force_limit_breached=False,
        tilt_limit_breached=False,
        wall_s=1.0,
    )
    base.update(kw)
    return RefusalCounterfactual(**base)


def test_false_refusal_requires_all_contracts():
    assert classify_refusal(_cf()) == FALSE_REFUSAL


def test_incomplete_is_true_refusal():
    assert classify_refusal(_cf(completed=False)) == TRUE_REFUSAL


def test_late_completion_is_true_refusal():
    assert classify_refusal(_cf(deadline_met=False)) == TRUE_REFUSAL


def test_force_breach_is_true_refusal_even_if_completed():
    assert classify_refusal(_cf(force_limit_breached=True)) == TRUE_REFUSAL


def test_tilt_breach_is_true_refusal():
    assert classify_refusal(_cf(tilt_limit_breached=True)) == TRUE_REFUSAL


def test_unsatisfiable_freshness_is_true_refusal_despite_completion():
    assert classify_refusal(_cf(freshness_satisfiable=False)) == TRUE_REFUSAL