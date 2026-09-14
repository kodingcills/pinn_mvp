"""Runtime-semantic conformance tests. No physics: a scripted session
machine exercises the live spine (refusal, freshness, constraints,
recovery, degradation, evidence) tick by tick."""
from __future__ import annotations

from dataclasses import replace

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
from haplous.machine import CommandOutcome, MachineObservation, ServoCommand, Stamp
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


def stream(samples, period=PERIOD):
    """Script entries from (x, y, z, fz, tilt) tuples, stamped at i*period."""
    return [
        obs(i * period, x=x, y=y, z=z, fz=fz, tilt=tilt)
        for i, (x, y, z, fz, tilt) in enumerate(samples)
    ]


def seated_samples(n=60, z=-0.010, fz=15.0):
    return [(0.0, 0.0, z, fz, 0.0)] * n


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


class HoldBehavior:
    """A scripted behavior: holds the observed pose with a fixed stiffness."""

    def __init__(self) -> None:
        self.begin_calls = 0
        self.k_z = 1000.0

    def begin(self, intent, caps, observation) -> None:
        self.begin_calls += 1

    def step(self, observation) -> ServoCommand:
        return ServoCommand(
            target_pose_m=observation.pose_m, k_z_n_per_m=self.k_z
        )


class ScriptedMachine:
    """Machine whose sessions play a per-tick script. Script items:
    MachineObservation (content; the session owns the clock and restamps it
    advancing by one period per tick), None (dropout tick: no observation),
    Exception (machine fault), ("reject", n) style not needed -- use
    reject_windows_s instead."""

    def __init__(
        self,
        caps=None,
        observation=None,
        script=(),
        hold_last=True,
        reject_windows_s=(),
        health_claims=None,
    ) -> None:
        self._caps = caps or std_caps()
        self._observation = observation
        self._script = list(script)
        self._hold_last = hold_last
        self._reject_windows_s = tuple(reject_windows_s)
        self.open_session_calls = []
        self.stop_calls = 0
        self.health_claims = health_claims or {"status": "ok"}
        self.applied_commands: list[tuple[float, ServoCommand]] = []

    def capabilities(self):
        return self._caps

    def observe(self):
        return self._observation

    def open_session(self, *, start_position_m, horizon_s):
        self.open_session_calls.append((start_position_m, horizon_s))
        return ScriptedSession(
            self,
            horizon_s=horizon_s,
            reject_windows_s=self._reject_windows_s,
        )

    def stop(self):
        self.stop_calls += 1

    def health(self):
        return self.health_claims


class ScriptedSession:
    def __init__(self, machine: ScriptedMachine, *, horizon_s, reject_windows_s):
        self._machine = machine
        self._caps = machine._caps
        self._script = list(machine._script)
        self._hold_last = machine._hold_last
        self._reject_windows_s = reject_windows_s
        self._period = self._caps.state_period_s
        self._horizon = horizon_s
        self._idx = 0
        self._t = 0.0
        self._last: MachineObservation | None = None
        self._ended = False
        self._fault: str | None = None
        self.stop_t_s: float | None = None

    def _next(self):
        if self._idx < len(self._script):
            item = self._script[self._idx]
            self._idx += 1
            return item
        if self._hold_last and self._last is not None:
            # Repeat the last content, re-stamped at the advancing session
            # clock, so "hold forever" scripts still produce a live clock.
            return replace(
                self._last, stamp=Stamp(source_s=self._t, received_s=self._t)
            )
        return None

    def observe(self) -> MachineObservation | None:
        item = self._next()
        if isinstance(item, BaseException):
            self._fault = type(item).__name__
            self._ended = True
            return self._last
        if item is None:
            return None
        self._last = item
        return item

    def apply(self, command: ServoCommand) -> CommandOutcome:
        if self._ended or self._fault is not None:
            return CommandOutcome(accepted=False, reason="session_inactive")
        if any(lo <= self._t <= hi for lo, hi in self._reject_windows_s):
            return CommandOutcome(accepted=False, reason="command_rejected")
        self._machine.applied_commands.append((self._t, command))
        return CommandOutcome(accepted=True)

    def step(self) -> None:
        if self._ended:
            return
        self._t += self._period
        if self._t >= self._horizon:
            self._ended = True

    def stop(self) -> None:
        self._ended = True
        self.stop_t_s = self._t

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def fault(self) -> str | None:
        return self._fault


def runtime_over(machine, **intent_kwargs):
    behaviors = {"motion": HoldBehavior}
    runtime = HaplousRuntime(machine, behaviors=behaviors)
    runtime.submit(make_intent(**intent_kwargs))
    return runtime


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
    machine = ScriptedMachine(caps=caps, observation=obs(0.0))
    runtime = HaplousRuntime(machine, behaviors={"motion": HoldBehavior})
    result = runtime.submit(make_intent())
    assert result.outcome == "REFUSED"
    assert result.reasons[0].code == "missing_force_measurement"
    assert result.reasons[0].detail
    assert runtime.state is ExecutionState.REFUSED


def test_hard_force_over_sensed_range_degrades_and_enforces_at_ceiling():
    """A hard force limit above the sensor ceiling is enforceable at the
    ceiling (stricter than requested): accept with disclosure, never
    refuse. Measured counterfactual evidence (results/
    false_refusal_summary.json): the refused execution completed at
    2.18 N against the 150 N contract."""
    machine = ScriptedMachine(observation=obs(0.0))
    runtime = HaplousRuntime(machine, behaviors={"motion": HoldBehavior})
    result = runtime.submit(
        make_intent(
            requirements=(
                Requirement(
                    RequirementKind.MAX_PROCESS_FORCE_N, hard=True, limit=150.0
                ),
            ),
        )
    )
    assert result.outcome == "DEGRADED"
    codes = [r.code for r in result.reasons]
    assert "force_requirement_enforced_at_sensor_ceiling" in codes


def test_hard_tolerance_below_resolution_refuses():
    machine = ScriptedMachine(observation=obs(0.0))
    runtime = HaplousRuntime(machine, behaviors={"motion": HoldBehavior})
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
    machine = ScriptedMachine(
        observation=obs(0.0), script=stream(seated_samples())
    )
    runtime = HaplousRuntime(machine, behaviors={"motion": HoldBehavior})
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
    machine = ScriptedMachine(observation=obs(0.0))
    runtime = HaplousRuntime(machine, behaviors={"motion": HoldBehavior})
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


def test_unknown_behavior_refuses():
    machine = ScriptedMachine(observation=obs(0.0), script=stream(seated_samples()))
    runtime = HaplousRuntime(machine)  # no behaviors registered
    result = runtime.submit(make_intent())
    assert result.outcome == "REFUSED"
    assert result.reasons[-1].code == "behavior_unavailable"
    assert machine.open_session_calls == []


# ---------------------------------------------------------------------------
# Temporal semantics
# ---------------------------------------------------------------------------


def test_expired_state_cannot_authorize_execution():
    stale = obs(0.0, received=0.1)
    machine = ScriptedMachine(observation=stale, script=stream(seated_samples()))
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.REFUSED
    assert machine.open_session_calls == []
    kinds = [e.kind for e in evidence.events]
    assert "pre_execution_state_invalid" in kinds
    assert evidence.feasibility_decision()["outcome"] == "SUPPORTED"


def test_missing_observation_cannot_authorize_execution():
    machine = ScriptedMachine(observation=None, script=stream(seated_samples()))
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.REFUSED
    assert machine.open_session_calls == []
    assert evidence.feasibility_decision()["outcome"] == "SUPPORTED"


def test_small_measurement_loss_degrades_then_completes():
    script = stream(seated_samples(4, z=-0.002, fz=1.0))
    script += [None, None]
    script += stream(seated_samples(30), period=PERIOD)[6:]
    machine = ScriptedMachine(observation=obs(0.0), script=script)
    runtime = runtime_over(machine)
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
    script = stream(seated_samples(4, z=-0.002, fz=1.0))
    script += [None] * 10
    script += stream(seated_samples(30), period=PERIOD)[14:]
    machine = ScriptedMachine(observation=obs(0.0), script=script)
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    assert len(machine.open_session_calls) == 1
    assert outcome_payload(evidence)["result"] == "ABORTED"


def test_stale_observation_mid_motion_aborts_without_command():
    """A delivered-but-stale observation cannot authorize a command: the
    hard freshness constraint breaches and the execution stops."""
    script = stream(seated_samples(3, z=-0.002, fz=1.0))
    script.append(obs(0.0, received=0.1))  # arrives 0.1 s late
    script += stream(seated_samples(30), period=PERIOD)[6:]
    machine = ScriptedMachine(observation=obs(0.0), script=script)
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["reason"] == "condition_not_permitted_state_freshness"
    assert machine.stop_calls == 1
    violations = evidence.constraint_violations()
    assert violations and violations[0]["code"] == "state_freshness"
    # No command was authorized from the stale state.
    stale_t = 0.1
    assert all(t < stale_t - 1e-9 for t, _ in machine.applied_commands)


# ---------------------------------------------------------------------------
# Constraints and recovery
# ---------------------------------------------------------------------------


def test_hard_breach_without_policy_aborts_immediately():
    machine = ScriptedMachine(
        observation=obs(0.0),
        script=stream([(0.0, 0.0, -0.001, 30.0, 0.0)] * 20),
    )
    runtime = runtime_over(machine, recovery_policy=None)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    assert len(machine.open_session_calls) == 1
    assert machine.stop_calls == 1
    violations = evidence.constraint_violations()
    assert violations and violations[0]["code"] == "process_force"
    assert violations[0]["hard"] is True


def test_permitted_recovery_rereferences_and_completes():
    script = stream([(0.0, 0.0, -0.001, 30.0, 0.0)] * 2)
    script += stream(seated_samples(40), period=PERIOD)[2:]
    machine = ScriptedMachine(observation=obs(0.0), script=script)
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    kinds = [e.kind for e in evidence.events]
    assert "recovery_decision" in kinds
    assert "constraint_violation" in kinds


def test_recovery_timeout_aborts_failed_recovery():
    """A recovery episode that outlives its declared settle window aborts
    with recovery_timeout instead of continuing to violate."""
    from haplous.intent import SuccessCondition as SC

    machine = ScriptedMachine(
        observation=obs(0.0),
        script=stream([(0.0, 0.0, -0.001, 30.0, 0.0)] * 20),
    )
    runtime = runtime_over(
        machine,
        recovery_policy=RecoveryPolicy(
            action="retry_motion",
            permitted_conditions=frozenset({"process_force"}),
            max_recovery_actions=1,
            settle_s=0.05,
        ),
        success_condition=SC(
            "unreachable", lambda observations: False
        ),
    )
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["reason"] == "recovery_timeout"
    assert payload["condition"] == "process_force"
    # one budgeted action for the whole episode, not one per tick
    decisions = [
        e for e in evidence.events if e.kind == "recovery_decision"
    ]
    assert len(decisions) == 1


def test_recovery_second_episode_exhausts_budget():
    """A cleared-and-rebreached condition opens a NEW recovery episode;
    with the budget spent, that episode aborts with
    recovery_budget_exhausted."""
    from haplous.intent import SuccessCondition as SC

    script = stream([(0.0, 0.0, -0.001, 30.0, 0.0)] * 2)
    script += stream([(0.0, 0.0, -0.010, 15.0, 0.0)] * 3, period=PERIOD)[2:]
    script += [
        obs(0.025, fz=30.0),
        obs(0.030, fz=30.0),
    ]
    machine = ScriptedMachine(observation=obs(0.0), script=script)
    runtime = runtime_over(
        machine,
        recovery_policy=RecoveryPolicy(
            action="retry_motion",
            permitted_conditions=frozenset({"process_force"}),
            max_recovery_actions=1,
        ),
        success_condition=SC(
            "unreachable", lambda observations: False
        ),
    )
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["reason"] == "recovery_budget_exhausted"
    decisions = [
        e for e in evidence.events if e.kind == "recovery_decision"
    ]
    assert len(decisions) == 1


def test_unpermitted_condition_does_not_recover():
    machine = ScriptedMachine(
        observation=obs(0.0),
        script=stream([(0.0, 0.0, -0.001, 5.0, 20.0)] * 20),
    )
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    assert len(machine.open_session_calls) == 1
    assert outcome_payload(evidence)["reason"] == "condition_not_permitted_tilt"


def test_soft_force_breach_records_violation_but_completes():
    machine = ScriptedMachine(
        observation=obs(0.0), script=stream(seated_samples())
    )
    runtime = runtime_over(
        machine,
        requirements=(
            Requirement(
                RequirementKind.MAX_PROCESS_FORCE_N,
                hard=False,
                limit=12.0,
            ),
        ),
    )
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    violations = evidence.constraint_violations()
    assert violations and violations[0]["hard"] is False


def test_command_rejection_persistent_aborts():
    script = stream([(0.0, 0.0, -0.002, 1.0, 0.0)] * 5)
    script += stream(seated_samples(60), period=PERIOD)[5:]
    machine = ScriptedMachine(
        observation=obs(0.0),
        script=script,
        reject_windows_s=((0.005, 0.02),),
    )
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["reason"] == "command_rejection_persistent"
    kinds = [e.kind for e in evidence.events]
    assert "command_rejected" in kinds


def test_transient_command_rejection_degrades_and_completes():
    script = stream([(0.0, 0.0, -0.002, 1.0, 0.0)] * 5)
    script += stream(seated_samples(60), period=PERIOD)[5:]
    machine = ScriptedMachine(
        observation=obs(0.0),
        script=script,
        reject_windows_s=((0.005, 0.01),),  # 2 ticks < rejection limit
    )
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    kinds = [e.kind for e in evidence.events]
    assert "command_rejected" in kinds


def test_machine_fault_mid_motion_aborts_and_stops():
    script = stream(seated_samples(2, z=-0.002, fz=1.0))
    script.append(RuntimeError("actuator fault"))
    machine = ScriptedMachine(observation=obs(0.0), script=script)
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["reason"] == "machine_fault_RuntimeError"
    assert machine.stop_calls == 1


def test_stop_request_issued_on_hard_fault():
    machine = ScriptedMachine(
        observation=obs(0.0),
        script=stream([(0.0, 0.0, -0.001, 30.0, 0.0)] * 20),
    )
    runtime = runtime_over(machine, recovery_policy=None)
    runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    assert machine.stop_calls == 1


# ---------------------------------------------------------------------------
# Completion semantics
# ---------------------------------------------------------------------------


def test_missing_success_condition_refuses():
    machine = ScriptedMachine(observation=obs(0.0))
    runtime = HaplousRuntime(machine, behaviors={"motion": HoldBehavior})
    result = runtime.submit(make_intent(success_condition=None))
    assert result.outcome == "REFUSED"
    assert result.reasons[0].code == "missing_success_condition"


def test_machine_health_claim_cannot_complete():
    machine = ScriptedMachine(
        observation=obs(0.0),
        script=stream([(0.0, 0.0, -0.002, 1.0, 0.0)]),
    )
    machine.health_claims = {
        "status": "ok",
        "done": True,
        "pose_estimate": "aligned",
    }
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["result"] == "ABORTED"
    assert payload["reason"] == "success_condition_unmet"


def test_nominal_flow_completes_and_records_evidence():
    machine = ScriptedMachine(
        observation=obs(0.0), script=stream(seated_samples())
    )
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    decision = evidence.feasibility_decision()
    assert decision["outcome"] == "SUPPORTED"
    assert decision["reasons"] == []
    kinds = [e.kind for e in evidence.events]
    assert "execution_authorized" in kinds
    assert "motion_started" in kinds
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
    machine = ScriptedMachine(
        observation=obs(0.0),
        script=stream([(0.0, 0.0, -0.001, 30.0, 0.0)] * 20),
    )
    runtime = runtime_over(machine, recovery_policy=None)
    runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    with pytest.raises(RuntimeError):
        runtime.execute()
    with pytest.raises(RuntimeError):
        runtime.submit(make_intent(recovery_policy=None))


def test_reset_clears_execution_state():
    machine = ScriptedMachine(
        observation=obs(0.0), script=stream(seated_samples())
    )
    runtime = runtime_over(machine)
    runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    runtime.reset()
    assert runtime.state is ExecutionState.IDLE
    assert runtime.submit(make_intent()).outcome == "SUPPORTED"
    assert len(runtime.archived_evidence) == 1


def test_execute_requires_ready():
    machine = ScriptedMachine(
        observation=obs(0.0), script=stream(seated_samples())
    )
    runtime = HaplousRuntime(machine, behaviors={"motion": HoldBehavior})
    with pytest.raises(RuntimeError):
        runtime.execute()
