"""Physics integration: the runtime mediates real insertions through the
MuJoCo machine adapter over the validated research substrate."""
from haplous.behaviors.insertion import insertion_intent, insertion_machine
from haplous.runtime import HaplousRuntime
from haplous.state import ExecutionState
from pegsim.rollout import VALIDATED_LOOSE_V1


def run_insertion(offset_m, bearing=0.0, **intent_kwargs):
    machine = insertion_machine()
    intent = insertion_intent(
        VALIDATED_LOOSE_V1.task,
        initial_offset_m=offset_m,
        initial_bearing_rad=bearing,
        **intent_kwargs,
    )
    runtime = HaplousRuntime(machine)
    feasibility = runtime.submit(intent)
    if feasibility.outcome == "REFUSED":
        return feasibility, runtime, machine
    runtime.execute()
    return feasibility, runtime, machine


def test_nominal_insertion_completes():
    feasibility, runtime, machine = run_insertion(0.0)
    assert feasibility.outcome == "SUPPORTED"
    assert runtime.state is ExecutionState.COMPLETE
    decision = runtime.evidence.feasibility_decision()
    assert decision["outcome"] == "SUPPORTED"
    payload = [
        e for e in runtime.evidence.events if e.kind == "outcome"
    ][-1].payload
    assert payload["result"] == "COMPLETE"
    assert payload["success_condition"] == "seated"
    assert machine.execute_calls[0][1] == 16.3


def test_recoverable_offset_recovers():
    feasibility, runtime, machine = run_insertion(0.002, bearing=1.0)
    assert feasibility.outcome == "SUPPORTED"
    assert runtime.state is ExecutionState.COMPLETE
    start_x, start_y = machine.execute_calls[0][0][:2]
    assert (start_x**2 + start_y**2) ** 0.5 > 0.001
    kinds = [e.kind for e in runtime.evidence.events]
    assert "motion_completed" in kinds
    assert "outcome" in kinds


def test_unrecoverable_request_refuses_before_motion():
    feasibility, runtime, machine = run_insertion(0.005)
    assert feasibility.outcome == "REFUSED"
    assert feasibility.reasons[0].code == "unreachable_goal"
    assert runtime.state is ExecutionState.REFUSED
    assert machine.execute_calls == []


def test_unsensed_force_requirement_refuses():
    feasibility, runtime, _ = run_insertion(0.0, max_force_n=150.0)
    assert feasibility.outcome == "REFUSED"
    assert feasibility.reasons[0].code == "force_requirement_unsensed"
    assert runtime.state is ExecutionState.REFUSED
