"""Hardware-contract fault simulation: runtime behavior under modeled
machine-interface failures. These tests establish software behavior under
modeled faults; they do NOT establish hardware performance."""
from __future__ import annotations

import pytest

from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig
from haplous.behaviors.insertion import insertion_intent, insertion_machine
from haplous.capabilities import CommandMode, SensingChannel
from haplous.runtime import HaplousRuntime
from haplous.state import ExecutionState
from pegsim.rollout import VALIDATED_LOOSE_V1

from tests.test_haplous_semantics import (
    FakeMachine,
    make_intent,
    motion_of,
    obs,
    outcome_payload,
    seated_samples,
    std_caps,
)


# ---------------------------------------------------------------------------
# Modeled faults on the Machine contract (fast, scripted machine)
# ---------------------------------------------------------------------------


def test_state_latency_fault_refuses_execution():
    """Every observation arrives 0.1 s after it was sampled."""
    machine = FakeMachine(observation=obs(0.0, received=0.1), motions=[motion_of(seated_samples())])
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.REFUSED
    payload = [
        e for e in evidence.events
        if e.kind == "pre_execution_state_invalid"
    ][-1].payload
    assert payload["reason"] == "state_too_stale"
    assert machine.execute_calls == []


def test_command_rejection_fault_aborts_with_structured_reason():
    """The machine rejects the motion command mid-handshake."""

    class RejectingMachine(FakeMachine):
        def execute(self, *, start_position_m, horizon_s):
            self.execute_calls.append((start_position_m, horizon_s))
            raise RuntimeError("controller rejected motion command")

    machine = RejectingMachine(observation=obs(0.0))
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["result"] == "ABORTED"
    assert payload["reason"] == "machine_error_RuntimeError"
    assert "rejected motion command" in payload["detail"]
    assert machine.stop_calls == 1


def test_machine_fault_during_motion_aborts():
    """The machine faults after the motion starts (exception mid-trace)."""
    machine = FakeMachine(
        observation=obs(0.0),
        motions=[RuntimeError("actuator fault")],
    )
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["reason"] == "machine_error_RuntimeError"


def test_limited_control_mode_refused():
    caps = std_caps(command_modes=frozenset())
    machine = FakeMachine(caps=caps, observation=obs(0.0))
    runtime = HaplousRuntime(machine)
    result = runtime.submit(make_intent())
    assert result.outcome == "REFUSED"
    assert result.reasons[0].code == "unsupported_control_mode"


def test_stop_request_issued_on_hard_fault():
    machine = FakeMachine(
        observation=obs(0.0),
        motions=[motion_of([(0.0, 0.0, -0.001, 30.0, 0.0)] * 20)],
    )
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent(recovery_policy=None))
    runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    assert machine.stop_calls == 1


# ---------------------------------------------------------------------------
# Modeled faults on the real adapter (integration; same validated substrate)
# ---------------------------------------------------------------------------


def test_adapter_latency_fault_refuses_before_motion():
    """A sensing channel with 50 ms latency against a 20 ms state-age bound."""
    machine = insertion_machine(
        VALIDATED_LOOSE_V1, state_max_age_s=0.02, receive_delay_s=0.05
    )
    intent = insertion_intent(VALIDATED_LOOSE_V1.task, initial_offset_m=0.0)
    runtime = HaplousRuntime(machine)
    runtime.submit(intent)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.REFUSED
    assert machine.execute_calls == []
    assert evidence.feasibility_decision()["outcome"] == "SUPPORTED"


def test_adapter_packet_loss_small_window_degrades_and_completes():
    machine = insertion_machine(
        VALIDATED_LOOSE_V1,
        state_max_age_s=0.02,
        dropout_windows_s=((2.0, 2.01),),
    )
    intent = insertion_intent(
        VALIDATED_LOOSE_V1.task, initial_offset_m=0.001, initial_bearing_rad=1.3
    )
    runtime = HaplousRuntime(machine)
    feasibility = runtime.submit(intent)
    assert feasibility.outcome == "SUPPORTED"
    runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    kinds = [e.kind for e in runtime.evidence.events]
    assert "measurement_loss" in kinds
    transitions = [
        e.payload["to"]
        for e in runtime.evidence.events
        if e.kind == "state_transition"
    ]
    assert "degraded" in transitions


def test_adapter_packet_loss_large_window_aborts():
    machine = insertion_machine(
        VALIDATED_LOOSE_V1,
        state_max_age_s=0.02,
        dropout_windows_s=((2.0, 2.5),),
    )
    intent = insertion_intent(
        VALIDATED_LOOSE_V1.task, initial_offset_m=0.001, initial_bearing_rad=3.3
    )
    runtime = HaplousRuntime(machine)
    runtime.submit(intent)
    runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    kinds = [e.kind for e in runtime.evidence.events]
    assert "measurement_loss" in kinds
    payload = outcome_payload(runtime.evidence)
    assert payload["result"] == "ABORTED"
