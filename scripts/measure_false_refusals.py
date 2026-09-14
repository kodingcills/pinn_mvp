"""False-refusal counterfactual campaign: run the Haplous arm over the
HEB dev scenarios, fork every refusal, execute the exact refused request
without negotiation, and classify TRUE_REFUSAL / FALSE_REFUSAL.

Writes results/false_refusal_summary.json.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.heb.counterfactual import InterventionRecordingMachine
from benchmarks.heb.false_refusal import (
    AMBIGUOUS,
    FALSE_REFUSAL,
    TRUE_REFUSAL,
    classify_refusal,
    run_refusal_counterfactual,
)
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.mujoco_backend import build_realization
from benchmarks.heb.sliding import sliding_scenarios
from benchmarks.heb.systems import HaplousSystem


def main() -> int:
    scenarios = list(insertion_scenarios()) + list(sliding_scenarios())

    refusals = []
    t0 = time.perf_counter()
    for scenario in scenarios:
        realization = build_realization(scenario, machine_factory=InterventionRecordingMachine)
        run = HaplousSystem().run(realization, scenario)
        if run.disposition != "REFUSED":
            continue
        fork_realization = build_realization(scenario)
        counterfactual = run_refusal_counterfactual(fork_realization.machine, fork_realization, scenario)
        refusals.append(
            {
                "scenario_id": scenario.id,
                "task_family": scenario.task_family,
                "refusal_codes": list(run.refusal_codes),
                "reason": run.reason,
                "classification": classify_refusal(counterfactual),
                "counterfactual": {
                    "completed": counterfactual.completed,
                    "deadline_met": counterfactual.deadline_met,
                    "peak_force_n": counterfactual.peak_force_n,
                    "max_tilt_deg": counterfactual.max_tilt_deg,
                    "force_limit_breached": counterfactual.force_limit_breached,
                    "tilt_limit_breached": counterfactual.tilt_limit_breached,
                },
            }
        )
    elapsed = time.perf_counter() - t0

    by_class = {c: 0 for c in (TRUE_REFUSAL, FALSE_REFUSAL, AMBIGUOUS)}
    by_code: dict[str, dict[str, int]] = {}
    for r in refusals:
        by_class[r["classification"]] += 1
        for code in r["refusal_codes"]:
            slot = by_code.setdefault(code, {TRUE_REFUSAL: 0, FALSE_REFUSAL: 0, AMBIGUOUS: 0})
            slot[r["classification"]] += 1

    n = len(refusals)
    summary = {
        "n_scenarios": len(scenarios),
        "n_refusals": n,
        "classifications": by_class,
        "false_refusal_rate": by_class[FALSE_REFUSAL] / n if n else None,
        "true_refusal_rate": by_class[TRUE_REFUSAL] / n if n else None,
        "ambiguous_rate": by_class[AMBIGUOUS] / n if n else None,
        "by_refusal_code": by_code,
        "refusals": refusals,
        "elapsed_s": elapsed,
        "platform": f"{platform.python_version()} {platform.platform()}",
    }
    Path("results/false_refusal_summary.json").write_text(json.dumps(summary, indent=1))
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in ("refusals",)}, indent=1
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())