"""Compound perturbation study (campaign sec. 15): paired three-arm runs
over offset x stressor interactions on the internal insertion family.
Offsets sit near the known 3.0->3.5 mm capture boundary; stressors are
the single-axis fault patterns from the dev set (dropout small/large,
stale window, modelled mid-motion jolt).

Writes results/compound_perturbation_rows.jsonl and
results/compound_perturbation_summary.json.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.heb.metrics import grade_run
from benchmarks.heb.mujoco_backend import build_realization
from benchmarks.heb.protocol import Scenario, TrialRecord
from benchmarks.heb.systems import BareSystem, HaplousSystem, SupervisedSystem

PEN_ALLOW_M = 12e-6

OFFSETS_M = (0.0025, 0.0030, 0.0035)
BEARINGS = (0.9, 1.7, 2.6)

STRESSORS: dict[str, dict] = {
    "none": {},
    "dropout_small": {"dropout_windows_s": ((1.0, 1.005),)},
    "dropout_large": {"dropout_windows_s": ((1.0, 1.2),)},
    "stale_mid": {"stale_windows_s": ((0.3, 0.5, 0.05),)},
    "kick_y": {"disturbances": ((1.0, (0.0, 0.15, 0.0, 0.0, 0.0, 0.0)),)},
}


def compound_scenarios() -> list[Scenario]:
    scenarios = []
    for offset, bearing in zip(OFFSETS_M, BEARINGS):
        for stressor, fields in STRESSORS.items():
            scenarios.append(
                Scenario(
                    id=f"compound_{stressor}_off{int(offset * 1000)}um",
                    task_family="peg_insertion",
                    initial_offset_m=offset,
                    initial_bearing_rad=bearing,
                    position_tolerance_m=0.0005 + PEN_ALLOW_M,
                    tags=("compound", "task_a", stressor),
                    **fields,
                )
            )
    return scenarios


def main() -> int:
    systems = [BareSystem(), SupervisedSystem(), HaplousSystem()]
    scenarios = compound_scenarios()

    records: list[TrialRecord] = []
    t0 = time.perf_counter()
    for scenario in scenarios:
        realization = build_realization(scenario)
        bundle = realization.bundle
        for system in systems:
            run = system.run(realization, scenario)
            graded, task_success, bounded, violations, task_metrics = grade_run(
                scenario, bundle, run
            )
            records.append(
                TrialRecord(
                    scenario_id=scenario.id,
                    system=run.system,
                    reported_disposition=run.disposition,
                    reason=run.reason,
                    refusal_codes=run.refusal_codes,
                    wall_s=run.wall_s,
                    graded_disposition=graded,
                    task_success=task_success,
                    bounded_success=bounded,
                    hard_violations=violations,
                    task_metrics=task_metrics,
                    tags=scenario.tags,
                )
            )
    elapsed = time.perf_counter() - t0

    rows = [
        {
            "scenario_id": r.scenario_id,
            "system": r.system,
            "reported_disposition": r.reported_disposition,
            "graded_disposition": r.graded_disposition,
            "reason": r.reason,
            "refusal_codes": list(r.refusal_codes),
            "task_success": r.task_success,
            "bounded_success": r.bounded_success,
            "hard_violations": list(r.hard_violations),
            "task_metrics": r.task_metrics,
            "tags": list(r.tags),
            "wall_s": r.wall_s,
        }
        for r in records
    ]
    with open("results/compound_perturbation_rows.jsonl", "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    by = {}
    for row in rows:
        by.setdefault(row["system"], {})[row["scenario_id"]] = row

    # Interaction table: outcome shift vs the same-offset 'none' cell.
    interactions = {}
    for system, cells in by.items():
        shifts = []
        for offset in OFFSETS_M:
            base = cells[f"compound_none_off{int(offset * 1000)}um"]
            for stressor in STRESSORS:
                if stressor == "none":
                    continue
                cell = cells[f"compound_{stressor}_off{int(offset * 1000)}um"]
                shifts.append(
                    {
                        "offset_m": offset,
                        "stressor": stressor,
                        "base_bounded": base["bounded_success"],
                        "cell_bounded": cell["bounded_success"],
                        "base_reason": base["reason"],
                        "cell_reason": cell["reason"],
                        "flip": base["bounded_success"] != cell["bounded_success"],
                    }
                )
        interactions[system] = {
            "n_flips": sum(1 for s in shifts if s["flip"]),
            "shifts": shifts,
        }

    per_cell = {}
    for scenario in scenarios:
        per_cell[scenario.id] = {
            s: {
                "bounded_success": cells[scenario.id]["bounded_success"],
                "task_success": cells[scenario.id]["task_success"],
                "disposition": cells[scenario.id]["graded_disposition"],
                "reason": cells[scenario.id]["reason"],
            }
            for s, cells in by.items()
        }

    summary = {
        "n_scenarios": len(scenarios),
        "offsets_m": list(OFFSETS_M),
        "stressors": list(STRESSORS),
        "per_cell": per_cell,
        "interaction_shifts": {
            s: {"n_flips": v["n_flips"], "shifts": v["shifts"]} for s, v in interactions.items()
        },
        "elapsed_s": time.perf_counter() - t0,
        "platform": f"{platform.python_version()} {platform.platform()}",
    }
    Path("results/compound_perturbation_summary.json").write_text(json.dumps(summary, indent=1))

    for s, v in interactions.items():
        flips = [sh for sh in v["shifts"] if sh["flip"]]
        print(f"{system_name(s) if False else s}: {v['n_flips']} outcome flips out of 12 cells")
        for sh in flips:
            print(
                f"  {sh['stressor']} @ {sh['offset_m']:.4f} m: "
                f"{'bounded' if sh['base_bounded'] else 'unbounded'} -> "
                f"{'bounded' if sh['cell_bounded'] else 'unbounded'} ({sh['cell_reason']})"
            )
    return 0


def system_name(s: str) -> str:
    return s


if __name__ == "__main__":
    raise SystemExit(main())