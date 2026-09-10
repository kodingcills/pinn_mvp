"""Counterfactual fork campaign over the internal HEB scenarios: for every
scenario whose mediated arm performs a runtime-initiated stop, fork the
physics at the stop tick and continue the unmediated arm from the
identical state; classify BENEFICIAL / HARMFUL / UNNECESSARY per the
preregistered rules in evaluation/counterfactual.py.

Writes results/counterfactual_internal_summary.json.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.counterfactual import (
    BENEFICIAL,
    HARMFUL,
    NO_INTERVENTION,
    UNNECESSARY,
    InterventionRecordingMachine,
    RecordingBehavior,
    classify_intervention,
    direct_continuation,
)
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.mujoco_backend import build_realization
from benchmarks.heb.systems import HaplousSystem


def counterfactual_for_scenario(scenario) -> dict:
    created_behaviors: list[RecordingBehavior] = []

    fork_realization = build_realization(scenario)
    inner_factory = fork_realization.behavior_factory

    def recording_factory():
        wrapper = RecordingBehavior(inner_factory())
        created_behaviors.append(wrapper)
        return wrapper

    realization = build_realization(
        scenario,
        machine_factory=InterventionRecordingMachine,
        behavior_factory_override=recording_factory,
    )
    t0 = time.perf_counter()
    med_run = HaplousSystem().run(realization, scenario)
    med_wall = time.perf_counter() - t0

    machine = realization.machine
    reason = med_run.reason or ""
    runtime_abort = med_run.disposition == "ABORTED" and not reason.startswith("machine_error")
    if not machine.intervention_snapshots or not runtime_abort:
        return {
            "scenario_id": scenario.id,
            "mediated_disposition": med_run.disposition,
            "mediated_reason": med_run.reason,
            "classification": NO_INTERVENTION,
        }

    snapshot = machine.intervention_snapshots[-1]
    # Worsening baseline: the execution's own force peak at the stop, so
    # staleness/rejection stops (no force breach) are not spuriously
    # "worsened" against a zero baseline.
    intervention_force = max((abs(o.force_z_n) for o in med_run.observations), default=0.0)

    fork_machine = fork_realization.machine
    carried = created_behaviors[-1].snapshot if created_behaviors else None
    continuation = direct_continuation(
        fork_machine, snapshot, realization, scenario, behavior=carried
    )
    classification = classify_intervention(
        intervention_force, continuation, hard_force_limit_n=scenario.max_force_n
    )

    return {
        "scenario_id": scenario.id,
        "mediated_disposition": med_run.disposition,
        "mediated_reason": med_run.reason,
        "intervention_time_s": snapshot["time_s"],
        "intervention_force_n": intervention_force,
        "direct_continuation": {
            "completed": continuation.completed,
            "peak_force_n": continuation.peak_force_n,
            "max_tilt_deg": continuation.max_tilt_deg,
            "wall_s": continuation.wall_s,
        },
        "mediated_wall_s": med_wall,
        "classification": classification,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", choices=("insertion", "all"), default="insertion")
    args = parser.parse_args()

    scenarios = list(insertion_scenarios())
    t0 = time.perf_counter()
    rows = [counterfactual_for_scenario(s) for s in scenarios]
    elapsed = time.perf_counter() - t0

    by_class = {c: 0 for c in (BENEFICIAL, HARMFUL, UNNECESSARY, NO_INTERVENTION)}
    for row in rows:
        by_class[row["classification"]] += 1
    intervened = sum(v for k, v in by_class.items() if k != NO_INTERVENTION)

    summary = {
        "scenarios": len(rows),
        "interventions_forked": intervened,
        "classifications": by_class,
        "beneficial_rate": by_class[BENEFICIAL] / intervened if intervened else None,
        "harmful_rate": by_class[HARMFUL] / intervened if intervened else None,
        "unnecessary_rate": by_class[UNNECESSARY] / intervened if intervened else None,
        "elapsed_s": elapsed,
        "rows": rows,
        "platform": f"{platform.python_version()} {platform.platform()}",
    }
    out = Path("results/counterfactual_internal_summary.json")
    out.write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())