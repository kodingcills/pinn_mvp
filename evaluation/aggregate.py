"""Aggregate trial rows into the campaign's primary outcome metrics.

Input is the HEB row schema (committed artifacts:
results/heb_*_rows.jsonl). Every system arm is aggregated with the SAME
formulas. All headline proportions carry Wilson intervals.

Row contract (per trial):
  system, scenario_id, task_success (bool), bounded_success (bool),
  graded_disposition, hard_violations (list), refusal_codes (list),
  task_metrics{peak_abs_force_n, max_tilt_deg, ...}, tags, wall_s
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from evaluation import stats as st
from evaluation.taxonomy import classify, summarize

__all__ = [
    "load_rows",
    "arm_summary",
    "paired_comparison",
    "campaign_summary",
]


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _accepted(row: dict[str, Any]) -> bool:
    disp = str(row.get("reported_disposition") or row.get("graded_disposition") or "")
    return not disp.startswith("REFUSED")


def arm_summary(rows: list[dict[str, Any]], force_limit_n: float = 25.0) -> dict:
    """Primary outcome metrics for one system arm, one formula each."""
    if not rows:
        raise ValueError("no rows")
    n = len(rows)
    succ = sum(1 for r in rows if r.get("task_success"))
    bnd = sum(1 for r in rows if r.get("bounded_success"))
    viol = sum(1 for r in rows if r.get("hard_violations"))
    acc = [r for r in rows if _accepted(r)]
    viol_acc = sum(1 for r in acc if r.get("hard_violations"))

    forces = [r["task_metrics"]["peak_abs_force_n"] for r in rows if r.get("task_metrics", {}).get("peak_abs_force_n") is not None]
    exceed = [max(0.0, f - force_limit_n) for f in forces]

    summary: dict[str, Any] = {
        "n": n,
        "task_success": st.proportion_ci(succ, n).as_dict(),
        "bounded_success": st.proportion_ci(bnd, n).as_dict(),
        "any_violation": st.proportion_ci(viol, n).as_dict(),
        "coverage": st.proportion_ci(len(acc), n).as_dict(),
        "violation_conditional_acceptance": (
            st.proportion_ci(viol_acc, len(acc)).as_dict() if acc else {"value": None, "n": 0, "note": "no accepted trials"}
        ),
    }
    # HEB headline semantics: BSR among accepted trials (genuine
    # infeasibility refusals are coverage, not bounded-success failures).
    bnd_acc = sum(1 for r in acc if r.get("bounded_success"))
    summary["bounded_success_among_accepted"] = (
        st.proportion_ci(bnd_acc, len(acc)).as_dict() if acc else {"value": None, "n": 0, "note": "no accepted trials"}
    )
    if forces:
        summary["peak_force_tail"] = st.tail_percentiles(forces)
        summary["force_exceedance_tail"] = st.tail_percentiles(exceedance := [max(0.0, f - force_limit_n) for f in forces])
        summary["force_cvar95"] = st.cvar(exceed, 0.95)
    summary["failure_mass"] = summarize(rows)
    return summary


def paired_comparison(
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
    continuous_key: str = "task_metrics.peak_abs_force_n",
) -> dict:
    """Pair rows by scenario_id: McNemar on success/bounded-success,
    paired bootstrap on a continuous metric."""
    a = {r["scenario_id"]: r for r in rows_a}
    b = {r["scenario_id"]: r for r in rows_b}
    shared = sorted(set(a) & set(b))
    if not shared:
        raise ValueError("no shared scenario ids to pair on")

    def _get(r: dict, dotted: str) -> float | None:
        cur: Any = r
        for part in dotted.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return float(cur) if isinstance(cur, (int, float)) else None

    out: dict[str, Any] = {
        "n_pairs": len(shared),
        "unpaired": sorted((set(a) | set(b)) - set(shared)),
        "mcnemar_task_success": st.mcnemar_exact(
            [a[s]["task_success"] for s in shared], [b[s]["task_success"] for s in shared]
        ).as_dict(),
        "mcnemar_bounded_success": st.mcnemar_exact(
            [a[s]["bounded_success"] for s in shared], [b[s]["bounded_success"] for s in shared]
        ).as_dict(),
    }

    pairs = [(_get(a[s], continuous_key), _get(b[s], continuous_key)) for s in shared]
    finite = [(x, y) for x, y in pairs if x is not None and y is not None]
    if finite:
        xs, ys = zip(*finite)
        out["paired_delta_" + continuous_key.replace(".", "_")] = st.paired_bootstrap_mean(xs, ys)
    out["identical_value_pairs_" + continuous_key.replace(".", "_")] = (
        sum(1 for x, y in finite if x == y) if finite else 0
    )
    return out


def campaign_summary(rows_by_system: dict[str, list[dict[str, Any]]], force_limit_n: float = 25.0) -> dict:
    return {
        name: arm_summary(rows, force_limit_n=force_limit_n) for name, rows in rows_by_system.items()
    }