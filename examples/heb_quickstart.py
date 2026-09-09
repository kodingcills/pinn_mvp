"""Quickstart: submit an insertion intent to the Haplous runtime, watch it
negotiate feasibility, execute live, and report its evidence. Requires the
repository checkout (the MuJoCo adapter wraps the pegsim research
substrate)."""
from pegsim.rollout import VALIDATED_LOOSE_V1

from haplous import ExecutionState, HaplousRuntime, RecoveryPolicy
from haplous.behaviors.insertion import (
    insertion_behavior_factory,
    insertion_intent,
    insertion_machine,
)


def main() -> None:
    bundle = VALIDATED_LOOSE_V1
    machine = insertion_machine(bundle)
    intent = insertion_intent(
        bundle.task,
        initial_offset_m=0.0015,
        initial_bearing_rad=1.0,
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
    print(f"feasibility: {feasibility.outcome}")
    for reason in feasibility.reasons:
        print(f"  {reason.code}: {reason.detail}")
    if feasibility.outcome == "REFUSED":
        return
    evidence = runtime.execute()
    print(f"final state: {runtime.state.value}")
    for event in evidence.events:
        if event.kind in ("outcome", "recovery_decision"):
            print(f"  {event.kind}: {event.payload}")


if __name__ == "__main__":
    main()
