"""Held-out scenario generation and evaluation for qualification and
final holdout (campaign sec. 47, 68-70).

Splits discipline:
- DEV: the committed 27-scenario family (results/competent_supervisor_*)
- QUALIFICATION: fresh generated sample, seed A (run before freeze)
- FINAL-HOLDOUT: fresh generated sample, seed B (generated only after
  the freeze tag; never inspected before the freeze)

Feasibility ground truth for false-refusal accounting is declared by
construction: offsets <= 3.0 mm are feasible (measured capture boundary
3.0-3.5 mm), offsets >= 4.0 mm are infeasible (search reach 4.35 mm);
the 3.0-4.0 mm transition band is EXCLUDED from refusal accounting.
Temporal faults are feasible within the 0.5 s hold budget.

Usage:
  python scripts/measure_holdout.py --seed A --label qualification
  python scripts/measure_holdout.py --seed B --label final
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.aggregate import arm_summary, paired_comparison
from evaluation.taxonomy import summarize
from benchmarks.heb.metrics import grade_run
from benchmarks.heb.mujoco_backend import build_realization
from benchmarks.heb.protocol import Scenario, TrialRecord
from benchmarks.heb.systems import BareSystem, HaplousSystem, SupervisedSystem

PEN_ALLOW_M = 12e-6


def generate_scenarios(seed: str, n_nominal: int = 12, n_offset: int = 12, n_fault: int = 12) -> list[Scenario]:
    rng = random.Random(f"haplous-holdout-{seed}")
    scenarios: list[Scenario] = []

    for i in range(n_nominal):
        scenarios.append(
            Scenario(
                id=f"{seed}_nominal_{i:02d}",
                task_family="peg_insertion",
                initial_offset_m=0.0,
                position_tolerance_m=0.0005 + PEN_ALLOW_M,
                tags=("holdout", "physical"),
            )
        )

    for i in range(n_offset):
        feasible = i % 2 == 0
        offset = rng.uniform(0.5, 3.0) * 1e-3 if feasible else rng.uniform(4.0, 4.3) * 1e-3
        scenarios.append(
            Scenario(
                id=f"{seed}_offset_{i:02d}",
                task_family="peg_insertion",
                initial_offset_m=offset,
                initial_bearing_rad=rng.uniform(0, 6.283),
                position_tolerance_m=0.0005 + PEN_ALLOW_M,
                infeasible_request=not feasible,
                expected_disposition="REFUSED" if not feasible else "COMPLETE",
                tags=("holdout", "physical"),
            )
        )

    for i in range(n_fault):
        kind = rng.choice(["dropout", "stale", "rejection"])
        onset = rng.uniform(0.3, 1.4)
        if kind == "dropout":
            width = rng.uniform(0.02, 0.25)
            fields = {"dropout_windows_s": ((onset, onset + width),)}
        elif kind == "stale":
            delay = rng.uniform(0.03, 0.15)
            fields = {"stale_windows_s": ((onset, onset + 0.2, delay),)}
        else:
            fields = {"command_rejection_windows_s": ((onset, onset + 0.02),)}
        scenarios.append(
            Scenario(
                id=f"{seed}_fault_{kind}_{i:02d}",
                task_family="peg_insertion",
                initial_offset_m=rng.choice([0.0, 0.001, 0.002]),
                initial_bearing_rad=rng.uniform(0, 6.283),
                position_tolerance_m=0.0005 + PEN_ALLOW_M,
                tags=("holdout", "measurement"),
                **fields,
            )
        )
    return scenarios


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", required=True)
    parser.add_argument("--label", required=True, choices=("qualification", "final"))
    args = parser.parse_args()

    scenarios = generate_scenarios(args.seed)
    systems = [BareSystem(), SupervisedSystem(), HaplousSystem()]

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
    rows_path = Path(f"results/holdout_{args.label}_rows.jsonl")
    with open(rows_path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    by_system: dict[str, list[dict]] = {}
    for row in rows:
        by_system.setdefault(row["system"], []).append(row)

    summaries = {s: arm_summary(rs) for s, rs in by_system.items()}
    pairings = {
        f"{a}_vs_{b}": paired_comparison(by_system[a], by_system[b])
        for a, b in (("bare_behavior", "competent_supervisor"), ("competent_supervisor", "haplous_mediated"), ("bare_behavior", "haplous_mediated"))
        if a in by_system and b in by_system
    }

    refusals = [
        row for row in rows
        if row["system"] == "haplous_mediated" and row["reported_disposition"] == "REFUSED"
    ]
    refusal_accounting = {
        "n_refusals": len(refusals),
        "on_infeasible_scenarios": sum(
            1 for r in refusals if "_offset_" in r["scenario_id"] and "_offset_" in r["scenario_id"] and int(r["scenario_id"].split("_")[-1]) % 2 == 1
        ),
        "refusal_scenarios": [r["scenario_id"] for r in refusals],
    }

    summary = {
        "label": args.label,
        "seed": args.seed,
        "n_scenarios": len(scenarios),
        "systems": summaries,
        "failure_mass": {s: summarize(rs) for s, rs in by_system.items()},
        "paired": pairings,
        "refusal_accounting": refusal_accounting,
        "scenario_ids": [s.id for s in scenarios],
        "elapsed_s": elapsed,
        "platform": f"{platform.python_version()} {platform.platform()}",
    }
    Path(f"results/holdout_{args.label}_summary.json").write_text(json.dumps(summary, indent=1))

    for s, v in summaries.items():
        print(
            f"{s}: SR {v['task_success']['value']:.3f} "
            f"BSR_sys {v['bsr_system']['value']:.3f} "
            f"BSR_acc {v['bsr_conditional']['value'] if v['bsr_conditional']['value'] is not None else float('nan'):.3f} "
            f"cov {v['coverage']['value']:.3f} viol {v['any_violation']['value'] * v['n']:.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())