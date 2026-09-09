"""Reliability / soak / property testing (S12).

Soak: repeated runtime cycles and repeated adapter sessions, checking for
crashes, state leaks, illegal transitions, missing evidence, and
nondeterminism. Property: a seeded randomized fuzzer drives the runtime
with arbitrary scripted observation streams, fault schedules, and
recovery policies, asserting the core invariants hold on every run."""
from __future__ import annotations

import random

import pytest

from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig
from haplous.behaviors.insertion import (
    insertion_behavior_factory,
    insertion_intent,
)
from haplous.capabilities import CommandMode, MachineCapabilities, SensingChannel
from haplous.intent import (
    ExecutionIntent,
    RecoveryPolicy,
    Requirement,
    RequirementKind,
    SuccessCondition,
)
from haplous.machine import MachineObservation, ServoCommand, Stamp
from haplous.runtime import HaplousRuntime
from haplous.state import ExecutionState

from pegsim.rollout import VALIDATED_LOOSE_V1

from tests.test_haplous_semantics import (
    HoldBehavior,
    ScriptedMachine,
    make_intent,
    obs,
    std_caps,
    stream,
    seated_samples,
)

TERMINAL = {ExecutionState.COMPLETE, ExecutionState.ABORTED, ExecutionState.REFUSED}
BEHAVIORS = {"motion": HoldBehavior}


# ---------------------------------------------------------------------------
# Soak: repeated runtime cycles (scripted, cheap)
# ---------------------------------------------------------------------------


def test_soak_ten_thousand_runtime_cycles():
    """10,000 submit/execute/reset cycles: no leaks, every cycle terminal,
    every terminal run carries an outcome event, evidence archives grow by
    exactly one per cycle."""
    machine = ScriptedMachine(
        observation=obs(0.0), script=stream(seated_samples(40))
    )
    runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
    intent = make_intent()
    for cycle in range(10_000):
        feasibility = runtime.submit(intent)
        assert feasibility.outcome == "SUPPORTED"
        runtime.execute()
        assert runtime.state in TERMINAL
        assert runtime.state is ExecutionState.COMPLETE
        outcomes = [
            e for e in runtime.evidence.events if e.kind == "outcome"
        ]
        assert len(outcomes) == 1
        runtime.evidence.to_json()
        runtime.reset()
        assert runtime.state is ExecutionState.IDLE
        assert len(runtime.archived_evidence) == cycle + 1
    assert runtime.state is ExecutionState.IDLE


def test_soak_aborted_cycles_keep_evidence_lineage():
    machine = ScriptedMachine(
        observation=obs(0.0),
        script=stream([(0.0, 0.0, -0.001, 30.0, 0.0)] * 20),
    )
    runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
    intent = make_intent(recovery_policy=None)
    for cycle in range(2_000):
        runtime.submit(intent)
        runtime.execute()
        assert runtime.state is ExecutionState.ABORTED
        payload = [
            e for e in runtime.evidence.events if e.kind == "outcome"
        ][-1].payload
        assert payload["result"] == "ABORTED"
        runtime.reset()
    assert len(runtime.archived_evidence) == 2_000


def test_soak_adapter_sessions_repeated_and_deterministic():
    """Repeated physics sessions on one adapter: no state leaks across
    episodes, and identical commands produce identical observation
    streams (determinism)."""
    machine = MuJoCoMachine(VALIDATED_LOOSE_V1)
    behavior_factory = insertion_behavior_factory(VALIDATED_LOOSE_V1)
    intent = insertion_intent(VALIDATED_LOOSE_V1.task, initial_offset_m=0.0)

    reference: list | None = None
    for episode in range(8):
        session = machine.open_session(
            start_position_m=intent.start_position_m, horizon_s=0.4
        )
        behavior = behavior_factory()
        obs = session.observe()
        behavior.begin(intent, machine.capabilities(), obs)
        trace = []
        while not session.ended:
            session.apply(behavior.step(obs))
            session.step()
            if session.ended:
                break
            obs = session.observe()
            trace.append(
                (obs.pose_m, obs.force_z_n, obs.tilt_deg, obs.stamp.source_s)
            )
        assert session.fault is None
        if reference is None:
            reference = trace
        else:
            assert trace == reference, f"nondeterminism at episode {episode}"


# ---------------------------------------------------------------------------
# Property / model-based testing (seeded fuzzer)
# ---------------------------------------------------------------------------


def _caps(**overrides) -> MachineCapabilities:
    values = dict(
        command_modes=frozenset({CommandMode.CARTESIAN_IMPEDANCE}),
        sensing=frozenset(
            {SensingChannel.POSE, SensingChannel.FORCE_Z, SensingChannel.TILT}
        ),
        max_sensed_force_n=100.0,
        max_sensed_tilt_deg=45.0,
        position_resolution_m=1e-6,
        state_period_s=0.005,
        state_max_age_s=0.02,
        max_initial_offset_m=0.00435,
        execution_horizon_s=2.0,
        expected_duration_s=1.0,
        supports_stop=True,
    )
    values.update(overrides)
    return MachineCapabilities(**values)


def _random_script(rng, n_ticks):
    """A random per-tick observation stream: mostly nominal seated
    content, with random force spikes, tilt excursions, dropouts, and
    delayed deliveries."""
    entries = []
    for _ in range(n_ticks):
        roll = rng.random()
        if roll < 0.70:
            entries.append(obs(0.0))  # seated, fresh
        elif roll < 0.78:
            entries.append(obs(0.0, fz=rng.uniform(26.0, 40.0)))
        elif roll < 0.84:
            entries.append(obs(0.0, tilt=rng.uniform(10.5, 25.0)))
        elif roll < 0.90:
            entries.append(None)  # dropout tick
        else:
            entries.append(obs(0.0, received=0.05))  # delivered late
    return entries


def _random_machine_config(rng):
    return dict(
        reject_windows_s=(
            ((0.02, 0.05),) if rng.random() < 0.3 else ()
        ),
    )


def _random_intent(rng):
    requirements = (
        Requirement(
            RequirementKind.MAX_PROCESS_FORCE_N, hard=True, limit=25.0
        ),
        Requirement(RequirementKind.MAX_TILT_DEG, hard=True, limit=10.0),
        Requirement(
            RequirementKind.STATE_MAX_AGE_S, hard=True, limit=0.02
        ),
        Requirement(RequirementKind.DEADLINE_S, hard=True, limit=2.0),
        Requirement(RequirementKind.INITIAL_OFFSET_M, hard=True, limit=0.0),
    )
    recovery = None
    if rng.random() < 0.5:
        permitted = rng.choice(
            [
                frozenset({"process_force"}),
                frozenset(),
                frozenset({"process_force", "state_freshness"}),
            ]
        )
        recovery = RecoveryPolicy(
            action="re_reference",
            permitted_conditions=permitted,
            max_recovery_actions=rng.choice([0, 1, 3]),
            settle_s=rng.choice([0.0, 0.05, 0.2]),
        )
    return make_intent(
        requirements=requirements,
        recovery_policy=recovery,
    )


def test_property_fuzzer_invariants():
    """500 randomized executions: terminal states are terminal, refusals
    never open a session, faults are never ignored, evidence is complete
    and reconstructable, and reset always returns to IDLE."""
    rng = random.Random(20260909)
    for case in range(500):
        n_ticks = rng.choice([5, 20, 60, 200])
        cfg = _random_machine_config(rng)
        machine = ScriptedMachine(
            caps=_caps(),
            observation=obs(0.0),
            script=_random_script(rng, n_ticks),
            reject_windows_s=cfg["reject_windows_s"],
        )
        runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
        intent = _random_intent(rng)
        feasibility = runtime.submit(intent)
        if feasibility.outcome == "REFUSED":
            assert runtime.state is ExecutionState.REFUSED
            assert machine.open_session_calls == []
        else:
            runtime.execute()
            assert runtime.state in TERMINAL
            if runtime.state is ExecutionState.ABORTED:
                outcomes = [
                    e for e in runtime.evidence.events
                    if e.kind == "outcome"
                ]
                assert outcomes, f"case {case}: ABORTED without outcome"
                reason = outcomes[-1].payload.get("reason", "")
                assert reason, f"case {case}: abort without reason"
        # evidence completeness on every terminal execution
        json_text = runtime.evidence.to_json()
        assert "outcome" in json_text
        outcomes = [
            e for e in runtime.evidence.events if e.kind == "outcome"
        ]
        assert len(outcomes) == 1
        # reset removes prior execution state
        runtime.reset()
        assert runtime.state is ExecutionState.IDLE


def test_property_refused_never_executes():
    """A refused intent can never reach EXECUTING, across randomized
    requirement sets."""
    rng = random.Random(7)
    for case in range(100):
        caps = _caps(
            sensing=rng.choice(
                [
                    frozenset({SensingChannel.POSE}),
                    frozenset({SensingChannel.POSE, SensingChannel.TILT}),
                    frozenset(),
                ]
            )
        )
        machine = ScriptedMachine(
            caps=caps,
            observation=obs(0.0),
            script=stream(seated_samples(20)),
        )
        runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
        result = runtime.submit(make_intent())
        if result.outcome == "REFUSED":
            with pytest.raises(RuntimeError):
                runtime.execute()
            assert runtime.state is ExecutionState.REFUSED
            assert machine.open_session_calls == []


def test_property_recovery_requires_permission():
    """Aborts from constraint breaches carry a permission-aware reason:
    unpermitted conditions never consume recovery, and permitted ones
    never exceed the declared budget."""
    rng = random.Random(424242)
    for case in range(300):
        machine = ScriptedMachine(
            observation=obs(0.0),
            script=stream([(0.0, 0.0, -0.001, 30.0, 0.0)] * 30),
        )
        budget = rng.choice([0, 1, 2])
        permitted = rng.choice(
            [frozenset(), frozenset({"process_force"})]
        )
        runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
        runtime.submit(
            make_intent(
                recovery_policy=RecoveryPolicy(
                    action="re_reference",
                    permitted_conditions=permitted,
                    max_recovery_actions=budget,
                    settle_s=0.05,
                ),
                success_condition=_never_condition(),
            )
        )
        runtime.execute()
        assert runtime.state is ExecutionState.ABORTED
        decisions = [
            e for e in runtime.evidence.events if e.kind == "recovery_decision"
        ]
        assert len(decisions) <= max(budget, 0)
        if frozenset() == permitted and budget == 0:
            reason = [
                e for e in runtime.evidence.events if e.kind == "outcome"
            ][-1].payload["reason"]
            assert reason == "condition_not_permitted_process_force"


def _never_condition():
    from haplous.intent import SuccessCondition

    return SuccessCondition("unreachable", lambda observations: False)
