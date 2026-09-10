"""Statistical primitives for the Haplous evaluation campaign.

Shared by every system arm: one formula per metric, everywhere.
No scipy dependency; exact binomial logic is implemented directly.

Sources:
- Wilson score interval: Wilson 1927, JASA 22(158).
- McNemar exact: binomial(n_discordant, p=0.5).
- CVaR: average of the worst (1-alpha) tail mass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

__all__ = [
    "wilson_interval",
    "exact_binomial_p",
    "mcnemar_exact",
    "paired_bootstrap_mean",
    "tail_percentiles",
    "cvar",
    "proportion_ci",
    "StatResult",
]


@dataclass(frozen=True)
class StatResult:
    value: float
    n: int
    ci_low: float
    ci_high: float
    method: str

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "n": self.n,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "method": self.method,
        }


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95% by default)."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError("successes must be within [0, n]")
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return center - half, center + half


def proportion_ci(successes: int, n: int) -> StatResult:
    """A proportion with its Wilson interval, as a StatResult."""
    lo, hi = wilson_interval(successes, n)
    return StatResult(value=successes / n, n=n, ci_low=lo, ci_high=hi, method="wilson95")


def _log_factorial(k: int) -> float:
    return math.lgamma(k + 1.0)


def exact_binomial_p(k: int, n: int, p: float = 0.5) -> float:
    """Two-sided exact binomial test p-value for k successes in n trials."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= k <= n:
        raise ValueError("k must be within [0, n]")

    def pmf(i: int) -> float:
        logp = _log_factorial(n) - _log_factorial(i) - _log_factorial(n - i)
        logp += i * math.log(p) + (n - i) * math.log1p(-p)
        return math.exp(logp)

    observed = pmf(k)
    return min(1.0, sum(pmf(i) for i in range(n + 1) if pmf(i) <= observed + 1e-15))


@dataclass(frozen=True)
class McNemarResult:
    n_pairs: int
    n01: int  # arm A fails, arm B succeeds
    n10: int  # arm A succeeds, arm B fails
    delta: float  # B success rate - A success rate
    p_value: float
    a_success: int
    b_success: int

    def as_dict(self) -> dict:
        return {
            "n_pairs": self.n_pairs,
            "n01": self.n01,
            "n10": self.n10,
            "delta": self.delta,
            "p_value": self.p_value,
            "a_success": self.a_success,
            "b_success": self.b_success,
            "n_concordant_success": self.n_pairs - self.n01 - self.n10,
        }


def mcnemar_exact(a: Sequence[bool], b: Sequence[bool]) -> McNemarResult:
    """Exact McNemar test on paired binary outcomes.

    a[i] and b[i] are the paired outcomes of the same scenario under
    arm A and arm B. discordant pairs (a != b) drive the test.
    """
    if len(a) != len(b):
        raise ValueError("paired sequences must have equal length")
    if not a:
        raise ValueError("empty pairing")
    n01 = sum(1 for x, y in zip(a, b) if not x and y)
    n10 = sum(1 for x, y in zip(a, b) if x and not y)
    discordant = n01 + n10
    p = exact_binomial_p(min(n01, n10), discordant) if discordant else 1.0
    return McNemarResult(
        n_pairs=len(a),
        n01=n01,
        n10=n10,
        delta=(sum(b) - sum(a)) / len(a),
        p_value=p,
        a_success=sum(a),
        b_success=sum(b),
    )


def paired_bootstrap_mean(
    a: Sequence[float],
    b: Sequence[float],
    n_resamples: int = 10_000,
    seed: int = 0,
    statistic=np.mean,
) -> dict:
    """Bootstrap CI for statistic(b) - statistic(a) over paired samples."""
    if len(a) != len(b) or not a:
        raise ValueError("paired sequences must be non-empty and equal length")
    rng = np.random.default_rng(seed)
    av = np.asarray(a, dtype=float)
    bv = np.asarray(b, dtype=float)
    n = len(av)
    idx = rng.integers(0, n, size=(n_resamples, n))
    deltas = statistic(bv[idx], axis=1) - statistic(av[idx], axis=1)
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    return {
        "delta": float(statistic(bv) - statistic(av)),
        "ci_low": float(lo),
        "ci_high": float(hi),
        "n": n,
        "n_resamples": n_resamples,
        "method": f"paired_bootstrap_{statistic.__name__}",
    }


def tail_percentiles(values: Sequence[float]) -> dict:
    """P50/P90/P95/P99/P99.9/max with the sample count."""
    if not values:
        raise ValueError("no values")
    v = np.asarray(values, dtype=float)
    return {
        "n": int(v.size),
        "p50": float(np.percentile(v, 50)),
        "p90": float(np.percentile(v, 90)),
        "p95": float(np.percentile(v, 95)),
        "p99": float(np.percentile(v, 99)),
        "p999": float(np.percentile(v, 99.9)),
        "max": float(v.max()),
        "mean": float(v.mean()),
    }


def cvar(values: Sequence[float], alpha: float = 0.95) -> float:
    """CVaR_alpha: the mean of the worst (1 - alpha) fraction of losses."""
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if not values:
        raise ValueError("no values")
    v = np.sort(np.asarray(values, dtype=float))[::-1]
    # -1e-9 guards the float representation of e.g. (1 - 0.95) * 100
    tail = max(1, int(math.ceil((1.0 - alpha) * v.size - 1e-9)))
    return float(v[:tail].mean())
