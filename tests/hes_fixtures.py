"""Shared fixtures for HES tests: synthetic canonical rows and runs.

These synthetic rows use the canonical episode schema
(``haplous-episode-v1``) so the same code paths as the real campaign are
exercised without a MuJoCo dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hes.run import Run

FIELDS = (
    "schema_version",
    "experiment_id",
    "shard_id",
    "shard_index",
    "arm",
    "episode_id",
    "scenario_id",
    "seed",
    "graded_disposition",
    "reported_disposition",
    "reason",
    "task_success",
    "bounded_success",
    "wall_s",
    "n_hard_violations",
    "refusal_codes",
    "hard_violations",
    "task_metrics",
    "tags",
)


def row(
    scenario_id: str,
    arm: str,
    disposition: str,
    *,
    task_success: bool = False,
    bounded_success: bool = False,
    n_hard_violations: int = 0,
    reason: str | None = None,
    refusal_codes: tuple[str, ...] = (),
    wall_s: float = 1.0,
    peak_force_n: float | None = None,
    tags: tuple[str, ...] = (),
    hard_violations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "haplous-episode-v1",
        "experiment_id": "synthetic",
        "shard_id": "shard-000000",
        "shard_index": 0,
        "arm": arm,
        "episode_id": f"{scenario_id}:{arm}",
        "scenario_id": scenario_id,
        "seed": 0,
        "graded_disposition": disposition,
        "reported_disposition": disposition,
        "reason": reason,
        "task_success": task_success,
        "bounded_success": bounded_success,
        "wall_s": wall_s,
        "n_hard_violations": n_hard_violations,
        "refusal_codes": list(refusal_codes),
        "hard_violations": hard_violations or [],
        "task_metrics": {"peak_abs_force_n": peak_force_n} if peak_force_n is not None else {},
        "tags": list(tags),
    }


def paired_rows(
    *,
    n: int = 20,
    baseline_bounded: int = 10,
    runtime_bounded: int = 8,
    runtime_refused: int = 5,
    runtime_violations: int | None = None,
) -> list[dict[str, Any]]:
    """Build a small three-arm population with controllable ground truth.

    Scenarios 0..baseline_bounded-1 are demonstrated attainable (the
    baseline reaches bounded success). ``runtime_violations`` counts how
    many of the remaining non-refused runtime attempts record a hard
    violation (``None`` = all of them).
    """
    violated_from = runtime_bounded + runtime_refused
    n_violating = (n - violated_from) if runtime_violations is None else runtime_violations
    rows: list[dict[str, Any]] = []
    for i in range(n):
        sid = f"s{i:03d}"
        attainable = i < baseline_bounded
        rows.append(
            row(
                sid,
                "direct_minimal",
                "COMPLETE" if attainable else "ABORTED",
                task_success=attainable,
                bounded_success=attainable,
                reason=None if attainable else "horizon_exhausted",
                tags=("task_a", "offset:interior"),
            )
        )
        runtime_ok = i < runtime_bounded
        refused = runtime_bounded <= i < runtime_bounded + runtime_refused
        violating = violated_from <= i < violated_from + max(0, n_violating)
        rows.append(
            row(
                sid,
                "haplous_mediated",
                "COMPLETE" if runtime_ok else ("REFUSED" if refused else "ABORTED"),
                task_success=runtime_ok,
                bounded_success=runtime_ok,
                n_hard_violations=1 if violating else 0,
                reason="negotiation_refusal" if refused else ("success_condition_unmet" if not runtime_ok else None),
                refusal_codes=("unreachable_goal",) if refused else (),
                wall_s=1.2 if runtime_ok else 0.8,
                tags=("task_a", "offset:interior"),
            )
        )
        rows.append(
            row(
                sid,
                "competent_supervisor",
                "COMPLETE" if attainable else "ABORTED",
                task_success=attainable,
                bounded_success=attainable,
                tags=("task_a", "offset:interior"),
            )
        )
    return rows


def make_run(
    rows: list[dict[str, Any]] | None = None,
    *,
    forks: list[dict[str, Any]] | None = None,
    baseline_arm: str = "direct_minimal",
) -> Run:
    return Run(
        root=Path("/tmp/synthetic-hes-run"),
        rows=rows if rows is not None else paired_rows(),
        manifest={
            "experiment_id": "synthetic",
            "reference_arm": "haplous_mediated",
        },
        provenance={"git_sha": "deadbeef", "command": "synthetic"},
        forks=forks or [],
        experiment_id="synthetic",
        arms=("direct_minimal", "haplous_mediated", "competent_supervisor"),
        baseline_arm=baseline_arm,
    )