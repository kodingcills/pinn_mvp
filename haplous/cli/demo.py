"""`haplous demo`: self-contained clean-room quickstart. Three reference
executions through the public runtime — one completed insertion, one
negotiation refusal, one bounded hard-constraint abort — each recorded as
a registry run so `haplous replay` / `haplous report` work immediately.
Wheel-internal only: pegsim substrate + haplous runtime/behaviors; no
repository or evaluation-harness imports."""
from __future__ import annotations

from argparse import Namespace

from haplous.cli.registry import RunMetadata, RunRegistry


def _demo_leg(offset_m: float, *, max_force_n: float | None = None) -> dict:
    from pegsim.rollout import VALIDATED_LOOSE_V1

    from haplous import ExecutionState, HaplousRuntime, RecoveryPolicy
    from haplous.behaviors.insertion import (
        insertion_behavior_factory,
        insertion_intent,
        insertion_machine,
    )

    bundle = VALIDATED_LOOSE_V1
    machine = insertion_machine(bundle)
    intent = insertion_intent(
        bundle.task,
        initial_offset_m=offset_m,
        initial_bearing_rad=1.0,
        max_force_n=max_force_n if max_force_n is not None else 25.0,
        recovery=RecoveryPolicy(
            action="re_reference",
            permitted_conditions=frozenset({"process_force"}),
            max_recovery_actions=1,
        ),
    )
    runtime = HaplousRuntime(
        machine,
        behaviors={"peg_insertion": insertion_behavior_factory(bundle)},
    )
    feasibility = runtime.submit(intent)
    if feasibility.outcome == "REFUSED":
        return {
            "disposition": "REFUSED",
            "reason": "negotiation_refusal",
            "codes": [r.code for r in feasibility.reasons],
            "evidence": runtime.evidence.to_json_dict(),
        }
    evidence = runtime.execute()
    outcome = [e for e in evidence.events if e.kind == "outcome"][-1]
    return {
        "disposition": outcome.payload["result"],
        "reason": outcome.payload.get("reason"),
        "codes": [],
        "final_state": runtime.state.value,
        "evidence": evidence.to_json_dict(),
    }


def demo_command(_args: Namespace) -> int:
    legs = (
        ("demo_success_offset1.5mm", 0.0015, None),
        ("demo_refusal_offset5mm", 0.005, None),
        ("demo_bounded_abort_force2n", 0.0015, 2.0),
    )
    trials: list[dict] = []
    for scenario_id, offset_m, max_force_n in legs:
        result = _demo_leg(offset_m, max_force_n=max_force_n)
        disposition = result.pop("disposition")
        bounded = disposition == "COMPLETE"
        trials.append(
            {
                "scenario_id": scenario_id,
                "system": "haplous_mediated",
                "disposition": disposition,
                "reason": result.get("reason"),
                "bounded_success": bounded,
                "task_success": bounded,
                "detail": result,
            }
        )
        print(f"{scenario_id}: {disposition} ({result.get('reason') or 'ok'})")

    completed = sum(1 for t in trials if t["task_success"])
    summary = {
        "benchmark_version": "demo-0.5.0",
        "n_scenarios": len(trials),
        "n_trials": len(trials),
        "systems": {
            "haplous_mediated": {
                "trials": len(trials),
                "coverage": sum(1 for t in trials if t["disposition"] != "REFUSED") / len(trials),
                "bounded_success_rate_among_accepted": completed,
                "task_successes": completed,
                "refusals": sum(1 for t in trials if t["disposition"] == "REFUSED"),
            }
        },
    }
    paths = RunRegistry().write_run(
        RunMetadata(
            benchmark_name="haplous-demo",
            benchmark_version="0.5.0",
            framework="internal",
            task_id="peg_insertion",
            system_variant="haplous_mediated",
            controller_config={"systems": ["haplous_mediated"]},
            runtime_config={"demo": True},
            seed=0,
            scenarios=[],
            metric_implementation_version="demo-0.5.0",
            started_at_utc=None,
        ),
        trials,
        summary,
    )
    print(f"\nrun id: {paths.run_id}")
    print(f"inspect: haplous inspect {paths.run_id}")
    print(f"replay:  haplous replay {paths.run_id}")
    print(f"report:  haplous report {paths.run_id}")
    return 0
