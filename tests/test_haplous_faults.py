"""Fault simulation: runtime behavior under modeled machine-interface
failures, injected MID-EXECUTION while motion is occurring. Latency figures
are modeled logical timing (simulated supervision ticks), not hardware
timing claims."""
from __future__ import annotations

from dataclasses import replace

import pytest

from haplous.behaviors.insertion import (
    insertion_behavior_factory,
    insertion_intent,
    insertion_machine,
)
from haplous.intent import RecoveryPolicy
from haplous.machine import Stamp
from haplous.runtime import HaplousRuntime
from haplous.state import ExecutionState
from pegsim.rollout import VALIDATED_LOOSE_V1

from tests.test_haplous_semantics import (
    HoldBehavior,
    ScriptedMachine,
    make_intent,
    obs,
    outcome_payload,
    seated_samples,
    std_caps,
    stream,
)

BEHAVIORS = {"motion": HoldBehavior}
INSERTION_BEHAVIORS = {
    "peg_insertion": insertion_behavior_factory(VALIDATED_LOOSE_V1)
}


# ---------------------------------------------------------------------------
# Modeled faults on the Machine contract (fast, scripted machine)
# ---------------------------------------------------------------------------


def test_state_latency_fault_refuses_execution():
    """Every observation arrives 0.1 s after it was sampled."""
    machine = ScriptedMachine(observation=obs(0.0, received=0.1))
    runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.REFUSED
    payload = [
        e for e in evidence.events
        if e.kind == "pre_execution_state_invalid"
    ][-1].payload
    assert payload["reason"] == "state_too_stale"
    assert machine.open_session_calls == []


def test_command_rejection_fault_aborts_with_structured_reason():
    """The machine rejects the session request itself."""

    class RejectingMachine(ScriptedMachine):
        def open_session(self, *, start_position_m, horizon_s):
            self.open_session_calls.append((start_position_m, horizon_s))
            raise RuntimeError("controller rejected motion command")

    machine = RejectingMachine(observation=obs(0.0))
    runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["result"] == "ABORTED"
    assert payload["reason"] == "machine_error_RuntimeError"
    assert "rejected motion command" in payload["detail"]
    assert machine.stop_calls == 1


def test_machine_fault_at_session_start_aborts():
    machine = ScriptedMachine(
        observation=obs(0.0), script=[RuntimeError("actuator fault")]
    )
    runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["reason"] == "machine_fault_RuntimeError"


def test_limited_control_mode_refused():
    caps = std_caps(command_modes=frozenset())
    machine = ScriptedMachine(caps=caps, observation=obs(0.0))
    runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
    result = runtime.submit(make_intent())
    assert result.outcome == "REFUSED"
    assert result.reasons[0].code == "unsupported_control_mode"


# ---------------------------------------------------------------------------
# Mid-execution fault timing (modeled logical timing, scripted machine)
# ---------------------------------------------------------------------------


def stale_tail(script, onset_tick, end_tick=None, extra_delay_s=0.1):
    """Restamp script entries in [onset_tick, end_tick) as late-delivered."""
    out = []
    for i, item in enumerate(script):
        if onset_tick <= i and (end_tick is None or i < end_tick):
            item = replace(
                item,
                stamp=Stamp(
                    source_s=item.stamp.source_s,
                    received_s=item.stamp.source_s + extra_delay_s,
                ),
            )
        out.append(item)
    return out


def test_mid_motion_stale_delivery_detected_on_first_stale_tick():
    """A stale-delivery window opening at t=0.10 is detected on the first
    affected tick (detection latency 0 ticks); the stop is issued on the
    same tick (stop latency 0 ticks)."""
    onset = 0.10
    script = stream(seated_samples(30, z=-0.002, fz=1.0))
    script = stale_tail(script, onset_tick=20)
    machine = ScriptedMachine(observation=obs(0.0), script=script)
    runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
    runtime.submit(make_intent(recovery_policy=None))
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    violations = evidence.constraint_violations()
    assert violations and violations[0]["code"] == "state_freshness"
    assert violations[0]["t_s"] == pytest.approx(onset)
    assert outcome_payload(evidence)["reason"] == "hard_constraint_state_freshness"
    assert machine.stop_calls == 1


def test_mid_motion_machine_fault_zero_tick_stop_latency():
    """A machine fault surfacing at tick t=0.10 is detected and stopped on
    the same supervision tick."""
    script = stream(seated_samples(20, z=-0.002, fz=1.0))
    script.append(RuntimeError("actuator fault"))
    machine = ScriptedMachine(observation=obs(0.0), script=script)
    runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["reason"] == "machine_fault_RuntimeError"
    outcomes = [e for e in evidence.events if e.kind == "outcome"]
    assert outcomes[-1].t_s == pytest.approx(0.10, abs=0.006)
    assert machine.stop_calls == 1


def test_no_command_authorized_from_stale_state():
    """During a stale window with recovery permitted, recovery decisions
    re-reference the behavior but NO new command is applied from stale
    state; the machine holds its previous command."""
    script = stream(seated_samples(15, z=-0.002, fz=1.0))
    script += stream(seated_samples(40), period=0.005)[18:]
    script = stale_tail(script, onset_tick=15, end_tick=18, extra_delay_s=0.05)
    machine = ScriptedMachine(observation=obs(0.0), script=script)
    runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
    runtime.submit(
        make_intent(
            recovery_policy=RecoveryPolicy(
                action="re_reference",
                permitted_conditions=frozenset({"state_freshness"}),
                max_recovery_actions=10,
            )
        )
    )
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    decisions = [
        e for e in evidence.events if e.kind == "recovery_decision"
    ]
    assert decisions, "stale window did not trigger recovery"


# ---------------------------------------------------------------------------
# Modeled faults on the real adapter (integration; same validated substrate)
# ---------------------------------------------------------------------------


def test_adapter_latency_fault_refuses_before_motion():
    """A sensing channel with 50 ms latency against a 20 ms state-age bound."""
    machine = insertion_machine(
        VALIDATED_LOOSE_V1, state_max_age_s=0.02, receive_delay_s=0.05
    )
    intent = insertion_intent(VALIDATED_LOOSE_V1.task, initial_offset_m=0.0)
    runtime = HaplousRuntime(machine, behaviors=INSERTION_BEHAVIORS)
    runtime.submit(intent)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.REFUSED
    assert machine.open_session_calls == []
    assert evidence.feasibility_decision()["outcome"] == "SUPPORTED"


def test_adapter_packet_loss_small_window_degrades_and_completes():
    machine = insertion_machine(
        VALIDATED_LOOSE_V1,
        state_max_age_s=0.02,
        dropout_windows_s=((1.0, 1.005),),
    )
    intent = insertion_intent(
        VALIDATED_LOOSE_V1.task, initial_offset_m=0.001, initial_bearing_rad=1.3
    )
    runtime = HaplousRuntime(machine, behaviors=INSERTION_BEHAVIORS)
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
        dropout_windows_s=((1.0, 1.2),),
    )
    intent = insertion_intent(
        VALIDATED_LOOSE_V1.task, initial_offset_m=0.001, initial_bearing_rad=3.3
    )
    runtime = HaplousRuntime(machine, behaviors=INSERTION_BEHAVIORS)
    runtime.submit(intent)
    runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    kinds = [e.kind for e in runtime.evidence.events]
    assert "measurement_loss" in kinds
    payload = outcome_payload(runtime.evidence)
    assert payload["result"] == "ABORTED"


def test_adapter_stale_window_mid_motion_aborts_with_latency():
    """A 50 ms extra-delivery window opening at t=1.0 s: the first stale
    observation breaches the 20 ms freshness constraint on the tick it is
    received (detection latency < 1 tick); the stop is issued on that tick."""
    machine = insertion_machine(
        VALIDATED_LOOSE_V1,
        state_max_age_s=0.02,
        stale_windows_s=((1.0, 1.5, 0.05),),
    )
    intent = insertion_intent(VALIDATED_LOOSE_V1.task, initial_offset_m=0.0)
    runtime = HaplousRuntime(machine, behaviors=INSERTION_BEHAVIORS)
    runtime.submit(intent)
    runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    violations = runtime.evidence.constraint_violations()
    assert violations and violations[0]["code"] == "state_freshness"
    assert violations[0]["t_s"] == pytest.approx(1.0, abs=1e-9)
    assert machine.stop_calls == 1
    payload = outcome_payload(runtime.evidence)
    assert payload["reason"] == "hard_constraint_state_freshness"


def test_adapter_persistent_command_rejection_mid_motion_aborts():
    machine = insertion_machine(
        VALIDATED_LOOSE_V1,
        command_rejection_windows_s=((1.0, 1.01),),
    )
    intent = insertion_intent(VALIDATED_LOOSE_V1.task, initial_offset_m=0.0)
    runtime = HaplousRuntime(machine, behaviors=INSERTION_BEHAVIORS)
    runtime.submit(intent)
    runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(runtime.evidence)
    assert payload["reason"] == "command_rejection_persistent"
    rejections = [
        e for e in runtime.evidence.events if e.kind == "command_rejected"
    ]
    assert len(rejections) == 3
    assert rejections[0].t_s == pytest.approx(1.0, abs=1e-9)


def test_adapter_transient_command_rejection_completes():
    """Two rejected ticks are tolerated (the machine holds its last
    command); the insertion still completes."""
    machine = insertion_machine(
        VALIDATED_LOOSE_V1,
        command_rejection_windows_s=((1.0, 1.0005),),
    )
    intent = insertion_intent(VALIDATED_LOOSE_V1.task, initial_offset_m=0.0)
    runtime = HaplousRuntime(machine, behaviors=INSERTION_BEHAVIORS)
    runtime.submit(intent)
    runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    kinds = [e.kind for e in runtime.evidence.events]
    assert "command_rejected" in kinds


def test_adapter_machine_fault_mid_motion_aborts_at_fault_time():
    machine = insertion_machine(VALIDATED_LOOSE_V1, fault_at_s=1.5)
    intent = insertion_intent(VALIDATED_LOOSE_V1.task, initial_offset_m=0.0)
    runtime = HaplousRuntime(machine, behaviors=INSERTION_BEHAVIORS)
    runtime.submit(intent)
    runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(runtime.evidence)
    assert payload["reason"] == "machine_fault_machine_fault"
    outcomes = [e for e in runtime.evidence.events if e.kind == "outcome"]
    assert outcomes[-1].t_s == pytest.approx(1.5, abs=2e-3)
    assert machine.stop_calls == 1


def test_adapter_stale_window_with_recovery_policy_recovers():
    """With freshness explicitly permitted for recovery, a short stale
    window consumes recovery actions and the insertion still completes."""
    machine = insertion_machine(
        VALIDATED_LOOSE_V1,
        state_max_age_s=0.02,
        stale_windows_s=((1.0, 1.001, 0.05),),
    )
    intent = insertion_intent(
        VALIDATED_LOOSE_V1.task,
        initial_offset_m=0.0,
        recovery=RecoveryPolicy(
            action="re_reference",
            permitted_conditions=frozenset({"state_freshness"}),
            max_recovery_actions=5,
        ),
    )
    runtime = HaplousRuntime(machine, behaviors=INSERTION_BEHAVIORS)
    runtime.submit(intent)
    runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    kinds = [e.kind for e in runtime.evidence.events]
    assert "recovery_decision" in kinds
