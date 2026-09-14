"""Ablation study (campaign sec. 35-36): which mechanism drives the v3
result? Three ablated arms on the same 27 dev scenarios:

- haplous_mediated: full v3 (measured separately)
- hold_disabled: max_hold_unobserved_s = 0 -> temporal faults abort (v2
  semantics) with negotiation calibration still active
- recovery_disabled: recovery policy stripped -> hold semantics active,
  recovery never available (isolates recovery's contribution)

Writes results/ablation_summary.json.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import replace

from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig
from haplous.intent import ExecutionIntent

from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.metrics import grade_run
from benchmarks.heb.mujoco_backend import build_realization
from benchmarks.heb.protocol import TrialRecord
from benchmarks.heb.sliding import sliding_scenarios
from benchmarks.heb.systems import HaplousSystem, build_intent


class HoldDisabledSystem(HaplousSystem):
    """v3 with the hold budget zeroed: temporal faults abort as in v2."""

    def run(self, realization, scenario):
        machine = realization.machine
        caps = machine.capabilities()
        machine_caps = replace(caps, max_hold_unobserved_s=0.0)
        machine.capabilities = lambda: machine_caps  # ablation override
        try:
            return super().run(realization, scenario)
        finally:
            del machine.capabilities


class NoRecoveryIntentMixin:
    @staticmethod
    def _strip_recovery(intent: ExecutionIntent) -> ExecutionIntent:
        return replace(intent, recovery_policy=None)


class RecoveryDisabledSystem(HaplousSystem):
    """v3 with every recovery policy stripped: hold semantics active,
    recovery never available."""

    def run(self, realization, scenario):
        condition = realization.condition_factory(scenario, realization.bundle)
        intent = build_intent(scenario, condition)
        intent = replace(intent, recovery_policy=None)
        from haplous.runtime import HaplousRuntime

        runtime = HaplousRuntime(
            realization.machine,
            behaviors={scenario.task_family: realization.behavior_factory},
        )
        t0 = time.perf_counter()
        feasibility = runtime.submit(intent)
        if feasibility.outcome == "REFUSED":
            from benchmarks.heb.protocol import SystemRun

            return SystemRun(
                system=self.name,
                disposition="REFUSED",
                reason="negotiation_refusal",
                refusal_codes=tuple(r.code for r in feasibility.reasons),
                wall_s=time.perf_counter() - t0,
            )
        evidence = runtime.execute()
        outcome = [e for e in evidence.events if e.kind == "outcome"][-1]
        applied = getattr(realization.machine, "last_session_applied_command_times", ())
        from benchmarks.heb.protocol import SystemRun

        return SystemRun(
            system=self.name,
            disposition=outcome.payload["result"],
            reason=outcome.payload.get("reason"),
            observations=realization.machine.last_session_observations,
            wall_s=time.perf_counter() - t0,
            detail={
                "evidence_events": len(evidence.events),
                "applied_command_times": tuple(applied),
            },
        )


def main() -> int:
    scenarios = list(insertion_scenarios()) + list(sliding_scenarios())
    systems = {
        "hold_disabled": HoldDisabledSystem(),
        "recovery_disabled": RecoveryDisabledSystem(),
    }

    rows: list[dict] = []
    t0 = time.perf_counter()
    for scenario in scenarios:
        for system_name, system in systems.items():
            realization = build_realization(scenario)
            bundle = realization.bundle
            run = system.run(realization, scenario)
            graded, task_success, bounded, violations, task_metrics = grade_run(
                scenario, bundle, run
            )
            rows.append(
                {
                    "scenario_id": scenario.id,
                    "system": system_name,
                    "graded_disposition": graded,
                    "reason": run.reason,
                    "refusal_codes": list(run.refusal_codes),
                    "task_success": task_success,
                    "bounded_success": bounded,
                    "hard_violations": list(violations),
                    "task_metrics": task_metrics,
                    "tags": list(scenario.tags),
                    "wall_s": run.wall_s,
                }
            )
    elapsed = time.perf_counter() - t0

    by_system: dict[str, list[dict]] = {}
    for row in rows:
        by_system.setdefault(row["system"], []).append(row)

    # Full v3 reference from the three-arm run.
    v3 = {}
    for line in open("results/competent_supervisor_dev_rows.jsonl"):
        r = json.loads(line)
        if r["system"] == "haplous_mediated":
            v3[r["scenario_id"]] = r

    summary = {"elapsed_s": elapsed, "platform": platform.platform(), "arms": {}}
    for name, rs in by_system.items():
        sr = sum(1 for r in rs if r["task_success"])
        bsr = sum(1 for r in rs if r["bounded_success"])
        viol = sum(1 for r in rs if r["hard_violations"])
        summary["arms"][name] = {
            "task_success": sr,
            "bounded_success": bsr,
            "violation_trials": viol,
        }
    summary["arms"]["haplous_v3_full"] = {
        "task_success": sum(1 for r in v3.values() if r["task_success"]),
        "bounded_success": sum(1 for r in v3.values() if r["bounded_success"]),
        "violation_trials": sum(1 for r in v3.values() if r["hard_violations"]),
    }

    Path("results/ablation_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary["arms"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())