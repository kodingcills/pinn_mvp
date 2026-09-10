"""Three-arm dev comparison: bare behavior vs COMPETENT SUPERVISOR vs
Haplous-mediated, over the internal HEB development scenarios, graded by
the benchmark and aggregated through the evaluation-engine metric stack.

Writes results/competent_supervisor_dev_rows.jsonl and
results/competent_supervisor_dev_summary.json.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.aggregate import arm_summary, paired_comparison
from evaluation.taxonomy import summarize
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.metrics import grade_run
from benchmarks.heb.mujoco_backend import build_realization
from benchmarks.heb.protocol import TrialRecord
from benchmarks.heb.sliding import sliding_scenarios
from benchmarks.heb.systems import BareSystem, HaplousSystem, SupervisedSystem


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", choices=("insertion", "all"), default="all")
    args = parser.parse_args()

    systems = [BareSystem(), SupervisedSystem(), HaplousSystem()]
    scenarios = list(insertion_scenarios())
    if args.family == "all":
        scenarios += list(sliding_scenarios())

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
    rows_path = Path("results/competent_supervisor_dev_rows.jsonl")
    with open(rows_path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    by_system: dict[str, list[dict]] = {}
    for row in rows:
        by_system.setdefault(row["system"], []).append(row)

    summaries = {s: arm_summary(rs) for s, rs in by_system.items()}
    pairings = {}
    if "bare_behavior" in by_system and "competent_supervisor" in by_system:
        pairings["bare_vs_supervisor"] = paired_comparison(
            by_system["bare_behavior"], by_system["competent_supervisor"]
        )
    if "competent_supervisor" in by_system and "haplous_mediated" in by_system:
        pairings["supervisor_vs_haplous"] = paired_comparison(
            by_system["competent_supervisor"], by_system["haplous_mediated"]
        )

    summary = {
        "n_scenarios": len(scenarios),
        "systems": summaries,
        "failure_mass": {s: summarize(rs) for s, rs in by_system.items()},
        "paired": pairings,
        "elapsed_s": elapsed,
        "platform": f"{platform.python_version()} {platform.platform()}",
    }
    Path("results/competent_supervisor_dev_summary.json").write_text(
        json.dumps(summary, indent=1)
    )
    headline = {
        s: {
            "n": v["n"],
            "task_success": round(v["task_success"]["value"], 3),
            "BSR_among_accepted": (
                None
                if v["bounded_success_among_accepted"]["value"] is None
                else round(v["bounded_success_among_accepted"]["value"], 3)
            ),
            "coverage": round(v["coverage"]["value"], 3),
            "violations": round(v["any_violation"]["value"] * v["n"]),
            "reasons": sorted({row["reason"] for row in by_system[s] if row["reason"]}),
        }
        for s, v in summaries.items()
    }
    print(json.dumps(headline, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())