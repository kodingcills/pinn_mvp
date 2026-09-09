"""X30 paired G0/G1 governor microbenchmark on HEB contact tasks."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import random
import subprocess
import time
from pathlib import Path
from typing import TypedDict

import numpy as np

from benchmarks.governor.heb import GovernorBenchmarkSystem, GovernorParameters
from benchmarks.heb import HEB_VERSION
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.protocol import Scenario, SystemUnderTest, TrialRecord
from benchmarks.heb.runner import evaluate
from benchmarks.heb.sliding import sliding_scenarios
from haplous import __version__ as HAPLOUS_VERSION

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
ROWS = RESULTS / "x30_governor_rows.jsonl"
SUMMARY = RESULTS / "x30_governor_summary.json"
SEED = 30031
G1_PARAMETERS = GovernorParameters(
    activation_fraction=0.8,
    minimum_stiffness_scale=0.25,
    window_samples=20,
)
FORCE_LIMITS_N = {"peg_insertion": 25.0, "constrained_slide": 2.0}


class CellMetrics(TypedDict):
    trials: int
    bounded_successes: int
    bounded_success_rate: float
    task_successes: int
    task_success_rate: float
    peak_abs_force_n: float
    mean_force_integral_n_s: float
    mean_contact_force_std_n: float
    completion_p50_s: float | None
    completion_p95_s: float | None


def build_scenarios() -> list[Scenario]:
    rng = random.Random(SEED)
    insertion = next(
        item for item in insertion_scenarios() if item.id == "insert_offset_1mm"
    )
    slide = next(item for item in sliding_scenarios() if item.id == "slide_nominal")
    scenarios: list[Scenario] = []
    for index, offset_m in enumerate((0.0, 0.001, 0.002)):
        scenarios.append(
            dataclasses.replace(
                insertion,
                id=f"x30_baseline_insert_{index}",
                initial_offset_m=offset_m,
                initial_bearing_rad=rng.uniform(0.0, 2.0 * np.pi),
                tags=("x30_baseline", "task_a"),
            )
        )
    scenarios.append(
        dataclasses.replace(
            slide,
            id="x30_baseline_slide_0",
            tags=("x30_baseline", "task_b"),
        )
    )
    for index, kick_mps in enumerate((0.30, 0.32, 0.34, 0.36, 0.38, 0.40)):
        scenarios.append(
            dataclasses.replace(
                slide,
                id=f"x30_kick_{index}",
                disturbances=((1.1, (kick_mps, 0.0, 0.0, 0.0, 0.0, 0.0)),),
                tags=("x30_slide_kick", f"kick_{kick_mps:.2f}_mps"),
            )
        )
    for index, width_ms in enumerate((10, 15, 20, 25, 30, 40)):
        scenarios.append(
            dataclasses.replace(
                insertion,
                id=f"x30_dropout_{index}",
                dropout_windows_s=((1.0, 1.0 + width_ms * 1e-3),),
                tags=("x30_dropout", f"width_{width_ms}_ms"),
            )
        )
    for index, delay_ms in enumerate((10, 15, 20, 25, 30, 40)):
        scenarios.append(
            dataclasses.replace(
                insertion,
                id=f"x30_stale_{index}",
                stale_windows_s=((1.0, 1.1, delay_ms * 1e-3),),
                tags=("x30_stale", f"delay_{delay_ms}_ms"),
            )
        )
    return scenarios


def percentile(values: list[float], quantile: float) -> float | None:
    return float(np.quantile(values, quantile)) if values else None


def aggregate(
    records: list[TrialRecord], family: str | None, system: str
) -> CellMetrics:
    selected = [
        item
        for item in records
        if item.system == system and (family is None or family in item.tags)
    ]
    completions = [
        float(item.task_metrics["seating_time_s"])
        for item in selected
        if item.task_success and item.task_metrics.get("seating_time_s") is not None
    ]
    return CellMetrics(
        trials=len(selected),
        bounded_successes=sum(item.bounded_success for item in selected),
        bounded_success_rate=sum(item.bounded_success for item in selected)
        / len(selected),
        task_successes=sum(item.task_success for item in selected),
        task_success_rate=sum(item.task_success for item in selected) / len(selected),
        peak_abs_force_n=max(
            float(item.task_metrics.get("peak_abs_force_n") or 0.0)
            for item in selected
        ),
        mean_force_integral_n_s=float(
            np.mean(
                [item.task_metrics["force_integral_n_s"] for item in selected]
            )
        ),
        mean_contact_force_std_n=float(
            np.mean(
                [item.task_metrics["contact_force_std_n"] for item in selected]
            )
        ),
        completion_p50_s=percentile(completions, 0.5),
        completion_p95_s=percentile(completions, 0.95),
    )


def main() -> None:
    scenarios = build_scenarios()
    systems: list[SystemUnderTest] = [
        GovernorBenchmarkSystem("g0_fixed"),
        GovernorBenchmarkSystem(
            "g1_force_governor",
            governor_parameters=G1_PARAMETERS,
            force_limits_n=FORCE_LIMITS_N,
        ),
    ]
    started = time.perf_counter()
    records = evaluate(scenarios, systems)
    elapsed = time.perf_counter() - started
    rows = [
        {
            **dataclasses.asdict(record),
            "cell_family": record.tags[0],
            "seed": SEED,
        }
        for record in records
    ]
    config = {
        "seed": SEED,
        "scenarios": [dataclasses.asdict(item) for item in scenarios],
        "g1_parameters": dataclasses.asdict(G1_PARAMETERS),
        "force_limits_n": FORCE_LIMITS_N,
    }
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    families = ("x30_baseline", "x30_slide_kick", "x30_dropout", "x30_stale")
    cells = {
        family: {
            "g0_fixed": aggregate(records, family, "g0_fixed"),
            "g1_force_governor": aggregate(
                records, family, "g1_force_governor"
            ),
            "g1_configuration": {
                "parameters": dataclasses.asdict(G1_PARAMETERS),
                "force_limits_n": FORCE_LIMITS_N,
            },
        }
        for family in families
    }
    totals = {
        system.name: aggregate(records, None, system.name) for system in systems
    }
    g0 = totals["g0_fixed"]
    g1 = totals["g1_force_governor"]
    row_text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    summary = {
        "campaign": "X30",
        "hypothesis": "G01: G1 enlarges bounded success without unacceptable task-success cost",
        "promotion_rule": "more bounded successes and no loss of task successes versus G0",
        "promotion_candidate": (
            g1["bounded_successes"] > g0["bounded_successes"]
            and g1["task_successes"] >= g0["task_successes"]
        ),
        "pairing": "same HEB scenarios, seeds, behavior factories, and machine realization",
        "provenance": {
            "haplous_version": HAPLOUS_VERSION,
            "heb_version": HEB_VERSION,
            "system_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "seed": SEED,
            "config_sha256": config_hash,
            "rows_sha256": hashlib.sha256(row_text.encode()).hexdigest(),
        },
        "g1_parameters": dataclasses.asdict(G1_PARAMETERS),
        "force_limits_n": FORCE_LIMITS_N,
        "n_scenarios": len(scenarios),
        "n_trials": len(records),
        "wall_s": elapsed,
        "cells": cells,
        "totals": totals,
    }
    _ = ROWS.write_text(row_text, encoding="utf-8")
    _ = SUMMARY.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
