"""X31 paired G0/G1 observation-jitter and command-delay stress campaign."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import TypedDict

from benchmarks.governor.heb import GovernorBenchmarkSystem, GovernorParameters
from benchmarks.governor.timing import TimingStressConfig
from benchmarks.heb import HEB_VERSION
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.protocol import Scenario, SystemUnderTest, TrialRecord
from benchmarks.heb.runner import evaluate
from benchmarks.heb.sliding import sliding_scenarios
from haplous import __version__ as HAPLOUS_VERSION
from scripts.measure_x30_governor import aggregate

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
ROWS = RESULTS / "x31_jitter_rows.jsonl"
SUMMARY = RESULTS / "x31_jitter_summary.json"
SEED = 31031
G1_PARAMETERS = GovernorParameters(
    activation_fraction=0.8,
    minimum_stiffness_scale=0.25,
    window_samples=20,
)
FORCE_LIMITS_N = {"peg_insertion": 25.0, "constrained_slide": 2.0}


class TimingOutcomes(TypedDict):
    reported_completions: int
    safe_stops_or_refusals: int
    refusals: int
    hard_violation_trials: int
    hard_violation_count: int
    freshness_invalidations: int
    deadline_invalidations: int
    controller_faults: int
    task_failures_without_temporal_stop: int


def build_campaign() -> tuple[list[Scenario], dict[str, TimingStressConfig]]:
    insertion = dataclasses.replace(
        next(item for item in insertion_scenarios() if item.id == "insert_nominal"),
        deadline_s=4.0,
    )
    slide = dataclasses.replace(
        next(item for item in sliding_scenarios() if item.id == "slide_nominal"),
        deadline_s=3.0,
    )
    scenarios: list[Scenario] = []
    configs: dict[str, TimingStressConfig] = {}
    index = 0
    for fraction in (0.1, 0.25, 0.5, 1.0):
        for task in (insertion, slide):
            scenario_id = f"x31_random_{index}"
            scenarios.append(
                dataclasses.replace(
                    task,
                    id=scenario_id,
                    tags=("x31_random_period", f"jitter_fraction_{fraction}"),
                )
            )
            configs[scenario_id] = TimingStressConfig(
                seed=SEED + index,
                period_jitter_fraction=fraction,
            )
            index += 1
    for width_ms in (10, 15, 20, 40):
        width_s = width_ms * 1e-3
        for task in (insertion, slide):
            scenario_id = f"x31_burst_{index}"
            scenarios.append(
                dataclasses.replace(
                    task,
                    id=scenario_id,
                    tags=("x31_bursty", f"burst_width_{width_ms}_ms"),
                )
            )
            configs[scenario_id] = TimingStressConfig(
                seed=SEED + index,
                dropout_windows_s=((0.60, 0.60 + width_s),),
                stale_windows_s=((1.10, 1.10 + width_s, width_s),),
            )
            index += 1
    for delay_steps in (1, 4, 16, 40):
        for task in (insertion, slide):
            scenario_id = f"x31_delay_{index}"
            scenarios.append(
                dataclasses.replace(
                    task,
                    id=scenario_id,
                    tags=("x31_command_delay", f"delay_steps_{delay_steps}"),
                )
            )
            configs[scenario_id] = TimingStressConfig(
                seed=SEED + index,
                command_delay_steps=delay_steps,
            )
            index += 1
    return scenarios, configs


def timing_outcomes(
    records: list[TrialRecord], family: str | None, system: str
) -> TimingOutcomes:
    selected = [
        item
        for item in records
        if item.system == system and (family is None or family in item.tags)
    ]
    violations = [entry for item in selected for entry in item.hard_violations]
    return TimingOutcomes(
        reported_completions=sum(
            item.reported_disposition == "COMPLETE" for item in selected
        ),
        safe_stops_or_refusals=sum(
            item.reported_disposition in {"ABORTED", "REFUSED"} for item in selected
        ),
        refusals=sum(item.reported_disposition == "REFUSED" for item in selected),
        hard_violation_trials=sum(bool(item.hard_violations) for item in selected),
        hard_violation_count=len(violations),
        freshness_invalidations=sum(
            entry.get("code") == "state_freshness" for entry in violations
        ),
        deadline_invalidations=sum(
            entry.get("code") == "deadline" for entry in violations
        ),
        controller_faults=sum(
            "tilt_guard" in (item.reason or "") for item in selected
        ),
        task_failures_without_temporal_stop=sum(
            not item.task_success
            and item.reported_disposition == "COMPLETE"
            and not item.hard_violations
            for item in selected
        ),
    )


def main() -> None:
    scenarios, timing_configs = build_campaign()
    systems: list[SystemUnderTest] = [
        GovernorBenchmarkSystem(
            "g0_fixed",
            timing_configs=timing_configs,
        ),
        GovernorBenchmarkSystem(
            "g1_force_governor",
            governor_parameters=G1_PARAMETERS,
            force_limits_n=FORCE_LIMITS_N,
            timing_configs=timing_configs,
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
        "timing_configs": {
            key: dataclasses.asdict(value) for key, value in timing_configs.items()
        },
        "g1_parameters": dataclasses.asdict(G1_PARAMETERS),
        "force_limits_n": FORCE_LIMITS_N,
    }
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    families = ("x31_random_period", "x31_bursty", "x31_command_delay")
    cells = {}
    for family in families:
        cells[family] = {}
        for system in systems:
            cells[family][system.name] = {
                **aggregate(records, family, system.name),
                **timing_outcomes(records, family, system.name),
            }
        cells[family]["g1_configuration"] = {
            "parameters": dataclasses.asdict(G1_PARAMETERS),
            "force_limits_n": FORCE_LIMITS_N,
        }
    totals = {}
    for system in systems:
        totals[system.name] = {
            **aggregate(records, None, system.name),
            **timing_outcomes(records, None, system.name),
        }
    row_text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    summary = {
        "campaign": "X31",
        "pairing": "same HEB scenarios, seeds, behavior factories, machine realization, and timing schedule",
        "timing_stress": {
            "random_period_jitter_fractions": [0.1, 0.25, 0.5, 1.0],
            "bursty_dropout_and_stale_widths_ms": [10, 15, 20, 40],
            "quiet_gap_s": 0.5,
            "command_delay_steps": [1, 4, 16, 40],
            "nominal_step_s": 0.0005,
        },
        "interpretation": {
            "temporal_semantics": "freshness invalidations and safe stops/refusals",
            "controller_stability": "tilt-guard faults, oscillation, and task failures without temporal stop",
        },
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
