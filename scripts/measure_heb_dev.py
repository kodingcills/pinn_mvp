"""HEB development evaluation: the insertion task family through B0/B1/B2.

Development use only: results here are for debugging, tuning, range
finding, and benchmark validation. They are not qualification or final
evidence. Grading is benchmark-owned (metrics.py), never the systems'
own claims."""
from __future__ import annotations

import json
import time
from pathlib import Path

from benchmarks.heb import HEB_VERSION
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.sliding import sliding_scenarios
from benchmarks.heb.metrics import summarize
from benchmarks.heb.runner import evaluate
from benchmarks.heb.systems import BareSystem, HaplousSystem

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def main() -> None:
    scenarios = insertion_scenarios() + sliding_scenarios()
    systems = [
        HaplousSystem(),
        BareSystem(),
        BareSystem(evaluate_success=False),
    ]
    t0 = time.perf_counter()
    records = evaluate(scenarios, systems)
    wall = time.perf_counter() - t0

    rows = RESULTS / "heb_dev_rows.jsonl"
    with rows.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r.__dict__, sort_keys=True, default=str) + "\n")

    summaries = summarize(scenarios, records)
    expected = {s.id: s.expected_disposition for s in scenarios}
    conformance = {}
    for name in summaries:
        system_records = [r for r in records if r.system == name]
        scored = [r for r in system_records if expected.get(r.scenario_id)]
        conformance[name] = (
            f"{sum(1 for r in scored if r.reported_disposition == expected.get(r.scenario_id))}"
            f"/{len(scored)}"
        )
    summary = {
        "heb_version": HEB_VERSION,
        "task_families": ["peg_insertion", "constrained_slide"],
        "n_scenarios": len(scenarios),
        "systems": {
            name: {
                "trials": s.trials,
                "coverage": s.coverage,
                "bounded_success_rate_among_accepted": s.bounded_success_rate,
                "task_successes": s.task_successes,
                "refusals": s.refusals,
                "refusal_precision": s.refusal_precision,
                "refusal_recall": s.refusal_recall,
                "hard_violation_trials": s.hard_violation_trials,
                "recovery_success_rate": s.recovery_success_rate,
                "completion_percentiles_s": s.completion_percentiles(),
            }
            for name, s in summaries.items()
        },
        "semantic_conformance": conformance,
        "wall_s": wall,
    }
    out = RESULTS / "heb_dev_summary.json"
    out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
