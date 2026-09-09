"""HEB runner: one shared machine realization per scenario, every system
run through the same contract, every trial graded by the benchmark."""
from __future__ import annotations

from benchmarks.heb.protocol import Scenario, SystemUnderTest, TrialRecord
from benchmarks.heb import mujoco_backend
from benchmarks.heb.metrics import grade_run


def evaluate(
    scenarios,
    systems: list[SystemUnderTest],
    task_grader=None,
):
    """Run every system against every scenario. The machine realization
    (physics + measurement corruption + fault schedule) is built once per
    scenario and shared by all systems, so paired comparisons share task,
    initial state, physics, corruption, faults, and backend."""
    records: list[TrialRecord] = []
    for scenario in scenarios:
        realization = mujoco_backend.build_realization(scenario)
        bundle = realization.bundle
        for system in systems:
            run = system.run(realization, scenario)
            graded, task_success, bounded, violations, task_metrics = grade_run(
                scenario, bundle, run, task_grader=task_grader
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
    return records
