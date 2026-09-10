"""Statistical primitive tests: closed-form and known-answer checks."""

import math

import pytest

from evaluation.stats import (
    cvar,
    exact_binomial_p,
    mcnemar_exact,
    paired_bootstrap_mean,
    proportion_ci,
    tail_percentiles,
    wilson_interval,
)


def test_wilson_known_values():
    # Wilson 1927 sanity: 0/10 gives interval strictly inside [0, 0.31]
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0
    assert 0.20 < hi < 0.32
    # 5/10 symmetric about 0.5
    lo, hi = wilson_interval(5, 10)
    assert math.isclose(lo + hi, 1.0, abs_tol=1e-9)
    # 1/3: textbook upper bound ~0.79
    lo, hi = wilson_interval(1, 3)
    assert 0.75 < hi < 0.81
    with pytest.raises(ValueError):
        wilson_interval(3, 2)
    with pytest.raises(ValueError):
        wilson_interval(1, 0)


def test_rare_failure_rule_of_three():
    # N=300 zero failures: campaign-quoted rule of three is 3/N ~ 1%;
    # the Wilson 95% upper bound sits slightly above it.
    lo, hi = wilson_interval(0, 300)
    assert 0.012 < hi < 0.014
    assert hi > 3 / 300
    # and the bound shrinks with N as documented
    _, hi3k = wilson_interval(0, 3000)
    assert hi3k < 0.0015


def test_binomial_exact_two_tailed():
    # k=0, n=10, p=0.5 -> p = 2 * (1/2)^10 = 1/512
    assert math.isclose(exact_binomial_p(0, 10), 1 / 512, rel_tol=1e-12)
    # k=5, n=10 -> p=1.0 (most likely outcome)
    assert math.isclose(exact_binomial_p(5, 10), 1.0)
    # k=1, n=20 -> two-sided
    p = exact_binomial_p(1, 20)
    assert 0.0 < p < 0.001  # 2 * C(20,1)/2^20 = 40/1048576 ~ 3.8e-5


def test_mcnemar_exact_basic():
    # 10 pairs, all discordant favoring B: p = 2*(1/2)^10
    res = mcnemar_exact([False] * 10, [True] * 10)
    assert res.n01 == 10 and res.n10 == 0
    assert math.isclose(res.p_value, 1 / 512, rel_tol=1e-12)
    assert res.delta == 1.0
    # Perfectly matched: p=1
    res = mcnemar_exact([True] * 4 + [False] * 2, [True] * 4 + [False] * 2)
    assert res.p_value == 1.0
    assert res.n01 == 0 and res.n10 == 0
    # Symmetric discordance: 3 vs 3 -> p=1
    res = mcnemar_exact([True, False, True, False, True, False], [False, True, False, True, False, True])
    assert math.isclose(res.p_value, 1.0)


def test_mcnemar_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        mcnemar_exact([True], [True, False])


def test_paired_bootstrap_centered_on_truth():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [x + 1.5 for x in xs]
    out = paired_bootstrap_mean(xs, ys, n_resamples=2000, seed=1)
    assert out["delta"] == pytest.approx(1.5)
    assert out["ci_low"] > 0.5 and out["ci_high"] < 2.5


def test_tail_percentiles_and_cvar():
    vals = list(range(1, 101))  # 1..100
    t = tail_percentiles(vals)
    assert t["n"] == 100
    assert t["p50"] == pytest.approx(50.5)
    assert t["max"] == 100.0
    # CVaR95 over worst 5%: worst 5 values 96..100 -> mean 98
    assert cvar(vals, 0.95) == pytest.approx(98.0)
    assert cvar([5.0], 0.95) == 5.0
    with pytest.raises(ValueError):
        cvar([], 0.95)
    with pytest.raises(ValueError):
        cvar([1.0], 1.0)