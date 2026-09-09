"""Runtime-semantic conformance tests. No physics: a scripted fake machine
exercises the spine (refusal, freshness, constraints, recovery, evidence)."""
from __future__ import annotations

import pytest

from haplous.capabilities import (
    CommandMode,
    MachineCapabilities,
    SensingChannel,
)
from haplous.intent import (
    ExecutionIntent,
    RecoveryPolicy,
    Requirement,
    RequirementKind,
    SuccessCondition,
)
from haplous.machine import MachineObservation, MotionRecord, Stamp
from haplous.runtime import HaplousRuntime
from haplous.state import (
    ExecutionState,
    ExecutionStateTracker,
    IllegalTransitionError,
)

PERIOD = 0.005
MAX_AGE = 0.02


def std_caps(**overrides) -> MachineCapabilities:
    values = dict(
        command_modes=frozenset({CommandMode.CARTESIAN_IMPEDANCE}),
        sensing=frozenset(
            {SensingChannel.POSE, SensingChannel.FORCE_Z, SensingChannel.TILT}
        ),
        max_sensed_force_n=100.0,
        max_sensed_tilt_deg=45.0,
        position_resolution_m=1e-6,
        state_period_s=PERIOD,
        state_max_age_s=MAX_AGE,
        max_initial_offset_m=0.00435,
        execution_horizon_s=16.3,
        expected_duration_s=4.0,
        supports_stop=True,
    )
    values.update(overrides)
    return MachineCapabilities(**values)


def obs(t, x=0.0, y=0.0, z=-0.010, fz=15.0, tilt=0.0, received=None):
    return MachineObservation(
        stamp=Stamp(source_s=t, received_s=t if received is None else received),
        pose_m=(x, y, z),
        force_z_n=fz,
        tilt_deg=tilt,
    )


def motion_of(samples, period=PERIOD):
    observations = tuple(
        obs(i * period, x=x, y=y, z=z, fz=fz, tilt=tilt)
        for i, (x, y, z, fz, tilt) in enumerate(samples)
    )
    return MotionRecord(observations=observations, ended_early=False)


def seated_samples(n=60, z=-0.010, fz=15.0):
    return [(0.0, 0.0, z, fz, 0.0)] * n


def lossy_motion(gap_after: float, gap_s: float, seated_n: int = 10):
    times = [0.0, 0.005, 0.010, 0.015]
    assert gap_after == 0.015
    t = gap_after + gap_s
    times.append(t)
    for _ in range(seated_n):
        t += PERIOD
        times.append(t)
    observations = tuple(
        MachineObservation(
            stamp=Stamp(source_s=t, received_s=t),
            pose_m=(0.0, 0.0, -0.010),
            force_z_n=15.0,
            tilt_deg=0.0,
        )
        for t in times
    )
    return MotionRecord(observations=observations, ended_early=False)


def success_condition(depth=-0.009, force=25.0):
    return SuccessCondition(
        "seated",
        lambda observations: bool(observations)
        and observations[-1].pose_m[2] <= depth
        and abs(observations[-1].force_z_n) <= force,
    )


def make_intent(**overrides):
    values = dict(
        behavior="motion",
        goal_description="reach the commanded region",
        goal_position_m=(0.0, 0.0, 0.0),
        start_position_m=(0.0, 0.0, 0.0),
        requirements=(
            Requirement(
                RequirementKind.MAX_PROCESS_FORCE_N, hard=True, limit=25.0
            ),
            Requirement(RequirementKind.MAX_TILT_DEG, hard=True, limit=10.0),
            Requirement(
                RequirementKind.STATE_MAX_AGE_S, hard=True, limit=MAX_AGE
            ),
            Requirement(RequirementKind.DEADLINE_S, hard=True, limit=16.3),
            Requirement(RequirementKind.INITIAL_OFFSET_M, hard=True, limit=0.0),
        ),
        success_condition=success_condition(),
        recovery_policy=RecoveryPolicy(
            action="retry_motion",
            permitted_conditions=frozenset({"process_force"}),
            max_recovery_actions=1,
        ),
    )
    values.update(overrides)
    return ExecutionIntent(**values)


class FakeMachine:
    def __init__(self, caps=None, observation=None, motions=()):
        self._caps = caps or std_caps()
        self._observation = observation
        self._motions = list(motions)
        self.execute_calls = []
        self.stop_calls = 0
        self.health_claims = {"status": "ok"}

    def capabilities(self):
        return self._caps

    def observe(self):
        return self._observation

    def execute(self, *, start_position_m, horizon_s):
        self.execute_calls.append((start_position_m, horizon_s))
        item = self._motions.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def stop(self):
        self.stop_calls += 1

    def health(self):
        return self.health_claims


def fresh():
    return obs(0.0)


def outcome_payload(evidence):
    outcomes = [e for e in evidence.events if e.kind == "outcome"]
    assert outcomes, "no outcome event recorded"
    return outcomes[-1].payload


# ---------------------------------------------------------------------------
# Feasibility / negotiation
# ---------------------------------------------------------------------------


def test_refuses_unsupported_requirement():
    caps = std_caps(
        sensing=frozenset({SensingChannel.POSE, SensingChannel.TILT})
    )
    machine = FakeMachine(caps=caps, observation=fresh())
    runtime = HaplousRuntime(machine)
    result = runtime.submit(make_intent())
    assert result.outcome == "REFUSED"
    assert result.reasons[0].code == "missing_force_measurement"
    assert result.reasons[0].detail
    assert runtime.state is ExecutionState.REFUSED


def test_hard_force_over_sensed_range_refuses():
    machine = FakeMachine(observation=fresh())
    runtime = HaplousRuntime(machine)
    result = runtime.submit(
        make_intent(
            requirements=(
                Requirement(
                    RequirementKind.MAX_PROCESS_FORCE_N, hard=True, limit=150.0
                ),
            ),
        )
    )
    assert result.outcome == "REFUSED"
    assert result.reasons[0].code == "force_requirement_unsensed"


def test_hard_tolerance_below_resolution_refuses():
    machine = FakeMachine(observation=fresh())
    runtime = HaplousRuntime(machine)
    result = runtime.submit(
        make_intent(
            requirements=(
                Requirement(
                    RequirementKind.POSITION_TOLERANCE_M,
                    hard=True,
                    limit=1e-7,
                ),
            ),
        )
    )
    assert result.outcome == "REFUSED"
    assert result.reasons[0].code == "requested_tolerance_unachievable"


def test_soft_requirement_degrades_never_refuses():
    machine = FakeMachine(observation=fresh(), motions=[motion_of(seated_samples())])
    runtime = HaplousRuntime(machine)
    result = runtime.submit(
        make_intent(
            requirements=(
                Requirement(
                    RequirementKind.POSITION_TOLERANCE_M,
                    hard=False,
                    limit=1e-7,
                ),
            ),
        )
    )
    assert result.outcome == "DEGRADED"
    assert result.reasons[0].code == "tolerance_at_resolution_limit"
    assert runtime.state is ExecutionState.READY


def test_unreachable_goal_refuses():
    machine = FakeMachine(observation=fresh())
    runtime = HaplousRuntime(machine)
    result = runtime.submit(
        make_intent(
            requirements=(
                Requirement(
                    RequirementKind.INITIAL_OFFSET_M, hard=True, limit=0.005
                ),
            ),
        )
    )
    assert result.outcome == "REFUSED"
    assert result.reasons[0].code == "unreachable_goal"


# ---------------------------------------------------------------------------
# Temporal semantics
# ---------------------------------------------------------------------------


def test_expired_state_cannot_authorize_execution():
    stale = obs(0.0, received=0.1)
    machine = FakeMachine(observation=stale, motions=[motion_of(seated_samples())])
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.REFUSED
    assert machine.execute_calls == []
    kinds = [e.kind for e in evidence.events]
    assert "pre_execution_state_invalid" in kinds
    assert evidence.feasibility_decision()["outcome"] == "SUPPORTED"


def test_missing_observation_cannot_authorize_execution():
    machine = FakeMachine(observation=None, motions=[motion_of(seated_samples())])
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.REFUSED
    assert machine.execute_calls == []
    assert evidence.feasibility_decision()["outcome"] == "SUPPORTED"


def test_small_measurement_loss_degrades_then_completes():
    machine = FakeMachine(
        observation=fresh(), motions=[lossy_motion(0.015, gap_s=0.01)]
    )
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    kinds = [e.kind for e in evidence.events]
    assert "measurement_loss" in kinds
    transitions = [
        e.payload["to"] for e in evidence.events if e.kind == "state_transition"
    ]
    assert "degraded" in transitions
    assert outcome_payload(evidence)["result"] == "COMPLETE"


def test_large_measurement_loss_aborts():
    machine = FakeMachine(
        observation=fresh(), motions=[lossy_motion(0.015, gap_s=0.05)]
    )
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    assert len(machine.execute_calls) == 1
    assert outcome_payload(evidence)["result"] == "ABORTED"


# ---------------------------------------------------------------------------
# Constraints and recovery
# ---------------------------------------------------------------------------


def test_hard_breach_without_policy_aborts_immediately():
    machine = FakeMachine(
        observation=fresh(),
        motions=[motion_of([(0.0, 0.0, -0.001, 30.0, 0.0)] * 20)],
    )
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent(recovery_policy=None))
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    assert len(machine.execute_calls) == 1
    assert machine.stop_calls == 1
    violations = evidence.constraint_violations()
    assert violations and violations[0]["code"] == "process_force"
    assert violations[0]["hard"] is True


def test_permitted_recovery_runs_second_motion():
    clean = motion_of(seated_samples())
    spiky = motion_of([(0.0, 0.0, -0.001, 30.0, 0.0)] * 20)
    machine = FakeMachine(observation=fresh(), motions=[spiky, clean])
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    assert len(machine.execute_calls) == 2
    kinds = [e.kind for e in evidence.events]
    assert "recovery_decision" in kinds
    assert "constraint_violation" in kinds


def test_recovery_budget_exhaustion_aborts():
    spiky = motion_of([(0.0, 0.0, -0.001, 30.0, 0.0)] * 20)
    machine = FakeMachine(observation=fresh(), motions=[spiky, spiky])
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    assert len(machine.execute_calls) == 2
    assert outcome_payload(evidence)["reason"] == "recovery_budget_exhausted"


def test_unpermitted_condition_does_not_recover():
    machine = FakeMachine(
        observation=fresh(),
        motions=[motion_of([(0.0, 0.0, -0.001, 5.0, 20.0)] * 20)],
    )
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    assert len(machine.execute_calls) == 1
    assert outcome_payload(evidence)["reason"] == "condition_not_permitted_tilt"


def test_soft_force_breach_records_violation_but_completes():
    machine = FakeMachine(observation=fresh(), motions=[motion_of(seated_samples())])
    runtime = HaplousRuntime(machine)
    runtime.submit(
        make_intent(
            requirements=(
                Requirement(
                    RequirementKind.MAX_PROCESS_FORCE_N,
                    hard=False,
                    limit=12.0,
                ),
            ),
        )
    )
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    violations = evidence.constraint_violations()
    assert violations and violations[0]["hard"] is False


# ---------------------------------------------------------------------------
# Completion semantics
# ---------------------------------------------------------------------------


def test_missing_success_condition_refuses():
    machine = FakeMachine(observation=fresh())
    runtime = HaplousRuntime(machine)
    result = runtime.submit(make_intent(success_condition=None))
    assert result.outcome == "REFUSED"
    assert result.reasons[0].code == "missing_success_condition"


def test_machine_health_claim_cannot_complete():
    machine = FakeMachine(
        observation=fresh(),
        motions=[motion_of([(0.0, 0.0, -0.002, 1.0, 0.0)] * 60)],
    )
    machine.health_claims = {
        "status": "ok",
        "done": True,
        "pose_estimate": "aligned",
    }
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["result"] == "ABORTED"
    assert payload["reason"] == "success_condition_unmet"


def test_nominal_flow_completes_and_records_evidence():
    machine = FakeMachine(observation=fresh(), motions=[motion_of(seated_samples())])
    runtime = HaplousRuntime(machine)
    feasibility = runtime.submit(make_intent())
    assert feasibility.outcome == "SUPPORTED"
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    decision = evidence.feasibility_decision()
    assert decision["outcome"] == "SUPPORTED"
    assert decision["reasons"] == []
    kinds = [e.kind for e in evidence.events]
    assert "execution_authorized" in kinds
    assert "motion_started" in kinds
    assert "motion_completed" in kinds
    assert evidence.to_json()


# ---------------------------------------------------------------------------
# State machine and lifecycle
# ---------------------------------------------------------------------------


def test_abort_is_terminal_in_transition_table():
    tracker = ExecutionStateTracker()
    tracker.transition(ExecutionState.VALIDATING)
    tracker.transition(ExecutionState.READY)
    tracker.transition(ExecutionState.EXECUTING)
    tracker.transition(ExecutionState.ABORTED)
    with pytest.raises(IllegalTransitionError):
        tracker.transition(ExecutionState.EXECUTING)
    with pytest.raises(IllegalTransitionError):
        tracker.transition(ExecutionState.REFUSED)


def test_aborted_runtime_requires_reset():
    machine = FakeMachine(
        observation=fresh(),
        motions=[motion_of([(0.0, 0.0, -0.001, 30.0, 0.0)] * 20)],
    )
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent(recovery_policy=None))
    runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    with pytest.raises(RuntimeError):
        runtime.execute()
    with pytest.raises(RuntimeError):
        runtime.submit(make_intent(recovery_policy=None))


def test_reset_clears_execution_state():
    machine = FakeMachine(observation=fresh(), motions=[motion_of(seated_samples())])
    runtime = HaplousRuntime(machine)
    runtime.submit(make_intent())
    runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    runtime.reset()
    assert runtime.state is ExecutionState.IDLE
    assert runtime.submit(make_intent()).outcome == "SUPPORTED"
    assert len(runtime.archived_evidence) == 1


def test_execute_requires_ready():
    machine = FakeMachine(observation=fresh(), motions=[motion_of(seated_samples())])
    runtime = HaplousRuntime(machine)
    with pytest.raises(RuntimeError):
        runtime.execute()
