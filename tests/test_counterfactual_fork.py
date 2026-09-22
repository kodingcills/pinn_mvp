"""Counterfactual fork correctness: restored-state continuation must
reproduce uninterrupted execution bit-identically."""

import copy

import mujoco
import pytest

from benchmarks.heb.counterfactual import (
    AMBIGUOUS,
    BENEFICIAL,
    BOUNDED_SUCCESS_PRESERVED,
    HARMFUL,
    INSUFFICIENT,
    PROTECTIVE_STOP,
    UNNECESSARY,
    ContinuationOutcome,
    RecordingBehavior,
    classify_intervention,
    classify_intervention_detailed,
    legacy_classification,
)
from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig
from haplous.behaviors.insertion import insertion_behavior_factory, insertion_intent
from pegsim.rollout import VALIDATED_LOOSE_V1


class _NoopBehavior:
    def __init__(self):
        self.begun = 0
        self.steps = 0

    def begin(self, intent, caps, observation):
        self.begun += 1

    def step(self, observation):
        self.steps += 1
        return None


def test_recording_behavior_carries_deep_copied_state():
    inner = _NoopBehavior()
    wrapper = RecordingBehavior(inner)
    wrapper.begin(None, None, None)
    assert wrapper.snapshot is not inner and wrapper.snapshot.begun == 1
    wrapper.step(None)
    assert wrapper.snapshot.steps == 1
    inner.steps = 99
    assert wrapper.snapshot.steps == 1


def test_classification_bounded_execution_rules():
    completed = lambda peak: type("C", (), {"completed": True, "peak_force_n": peak})()
    failed = lambda peak: type("C", (), {"completed": False, "peak_force_n": peak})()
    # Continuation breaches the contract (14.95 > 2.0) while the stop caps
    # exposure at the limit: protective stop.
    assert classify_intervention(2.0, failed(14.95), hard_force_limit_n=2.0) == BENEFICIAL
    # A continuation that completes while breaching the hard contract is
    # NOT bounded success, so the stop is not harmful.
    assert classify_intervention(2.0, completed(2.18), hard_force_limit_n=2.0) == UNNECESSARY
    assert classify_intervention(0.0, completed(2.18), hard_force_limit_n=None) == HARMFUL
    assert classify_intervention(0.0, failed(2.18), hard_force_limit_n=None) == UNNECESSARY
    # Boundary: the expose-to-continuation is only 5% worse -> no material
    # difference, same bounded failure.
    assert classify_intervention(2.0, completed(2.1), hard_force_limit_n=2.0) == UNNECESSARY


def test_historical_inversion_is_superseded_not_rewritten():
    """Regression: the completion-only rule called a non-bounded
    completion HARMFUL. The corrected rule must not, and the legacy
    function must still reproduce the old label."""
    cont = ContinuationOutcome(
        completed=True,
        peak_force_n=2.18,
        max_tilt_deg=1.0,
        wall_s=1.0,
        task_success=True,
        bounded_success=False,
        violations=({"code": "process_force", "value": 2.18, "limit": 2.0},),
    )
    assert legacy_classification(2.0, cont, hard_force_limit_n=2.0) == HARMFUL
    assert classify_intervention(2.0, cont, hard_force_limit_n=2.0) == UNNECESSARY


def test_bounded_execution_subkinds_and_insufficient():
    bounded = ContinuationOutcome(
        completed=True, peak_force_n=1.0, max_tilt_deg=1.0, wall_s=1.0,
        task_success=True, bounded_success=True, violations=(),
    )
    # A genuine stop that destroys a bounded continuation is HARMFUL.
    harmful = classify_intervention_detailed(0.0, bounded, hard_force_limit_n=2.0)
    assert harmful.classification == HARMFUL
    # An intervention that bounded-succeeds where the continuation cannot
    # is BENEFICIAL / BOUNDED_SUCCESS_PRESERVED.
    stopped = ContinuationOutcome(
        completed=False, peak_force_n=9.0, max_tilt_deg=1.0, wall_s=1.0,
        task_success=False, bounded_success=False,
        violations=({"code": "process_force", "value": 9.0, "limit": 2.0},),
    )
    preserved = classify_intervention_detailed(
        0.0, stopped, hard_force_limit_n=2.0, intervention_bounded=True
    )
    assert preserved.classification == BENEFICIAL
    assert preserved.beneficial_kind == BOUNDED_SUCCESS_PRESERVED
    protective = classify_intervention_detailed(
        2.0, stopped, hard_force_limit_n=2.0,
        intervention_violations=(),
    )
    assert protective.beneficial_kind == PROTECTIVE_STOP
    # Haplous's own exceedance is materially worse than the continuation's.
    mild = ContinuationOutcome(
        completed=False, peak_force_n=1.01, max_tilt_deg=1.0, wall_s=1.0,
        task_success=False, bounded_success=False,
        violations=({"code": "process_force", "value": 2.02, "limit": 2.0},),
    )
    insufficient = classify_intervention_detailed(
        4.0, mild, hard_force_limit_n=2.0,
        intervention_violations=(({"code": "process_force", "value": 4.0, "limit": 2.0},)),
    )
    assert insufficient.classification == INSUFFICIENT


def test_ambiguous_when_evidence_absent():
    stopped = ContinuationOutcome(
        completed=False, peak_force_n=1.0, max_tilt_deg=1.0, wall_s=1.0
    )
    result = classify_intervention_detailed(
        1.0, stopped, hard_force_limit_n=2.0, evidence_sufficient=False
    )
    assert result.classification == AMBIGUOUS


def _drive(machine: MuJoCoMachine, intent, n_ticks: int) -> list[tuple]:
    caps = machine.capabilities()
    session = machine.open_session(
        start_position_m=intent.start_position_m, horizon_s=caps.execution_horizon_s
    )
    behavior = insertion_behavior_factory(VALIDATED_LOOSE_V1)()
    observation = session.observe()
    behavior.begin(intent, caps, observation)
    trace = []
    for _ in range(n_ticks):
        command = behavior.step(observation)
        session.apply(command)
        session.step()
        observation = session.observe()
        if observation is None or session.ended:
            break
        trace.append(
            (
                tuple(observation.pose_m),
                observation.force_z_n,
                observation.tilt_deg,
                tuple(command.target_pose_m),
                command.k_z_n_per_m,
            )
        )
    return trace


def test_snapshot_restore_bit_identical_continuation():
    bundle = VALIDATED_LOOSE_V1
    config = MuJoCoMachineConfig()
    machine = MuJoCoMachine(bundle, config=config)
    intent = insertion_intent(bundle.task, initial_offset_m=0.001)

    full_trace = _drive(machine, intent, 300)

    fork = MuJoCoMachine(bundle, config=config)
    caps = fork.capabilities()
    session = fork.open_session(
        start_position_m=intent.start_position_m, horizon_s=caps.execution_horizon_s
    )
    behavior = insertion_behavior_factory(VALIDATED_LOOSE_V1)()
    observation = session.observe()
    behavior.begin(intent, caps, observation)
    for _ in range(120):
        session.apply(behavior.step(observation))
        session.step()
        observation = session.observe()
        assert observation is not None

    snap = session.snapshot_state()
    assert snap["time_s"] > 0.0
    carried = copy.deepcopy(behavior)

    resumed = MuJoCoMachine(bundle, config=config)
    resumed_session = resumed.open_session(
        start_position_m=intent.start_position_m, horizon_s=caps.execution_horizon_s
    )
    resumed_session.restore_state(snap)
    assert resumed_session._pending == snap["pending"]
    assert (resumed_session._held == snap["held"]).all()
    assert len(resumed_session._observations) == 0

    resumed_obs = resumed_session.observe()
    continuation = []
    for _ in range(180):
        command = carried.step(resumed_obs)
        resumed_session.apply(command)
        resumed_session.step()
        resumed_obs = resumed_session.observe()
        if resumed_obs is None or resumed_session.ended:
            break
        continuation.append(
            (
                tuple(resumed_obs.pose_m),
                resumed_obs.force_z_n,
                resumed_obs.tilt_deg,
                tuple(command.target_pose_m),
                command.k_z_n_per_m,
            )
        )

    assert len(continuation) == 180
    assert continuation == full_trace[120:300]


def test_snapshot_restore_time_and_disturbance_bookkeeping():
    bundle = VALIDATED_LOOSE_V1
    dv = (0.0, 0.0, 0.0, 0.1, 0.0, 0.0)
    config = MuJoCoMachineConfig(disturbances=((0.1, dv),))
    machine = MuJoCoMachine(bundle, config=config)
    intent = insertion_intent(bundle.task, initial_offset_m=0.0)
    caps = machine.capabilities()
    session = machine.open_session(
        start_position_m=intent.start_position_m, horizon_s=caps.execution_horizon_s
    )
    behavior = insertion_behavior_factory(VALIDATED_LOOSE_V1)()
    observation = session.observe()
    behavior.begin(intent, caps, observation)
    for _ in range(300):
        session.apply(behavior.step(observation))
        session.step()
        observation = session.observe()

    snap = session.snapshot_state()
    assert 0.1 in snap["applied_disturbances"]

    twin = MuJoCoMachine(bundle, config=config)
    twin_session = twin.open_session(
        start_position_m=intent.start_position_m, horizon_s=caps.execution_horizon_s
    )
    twin_session.restore_state(snap)
    assert twin_session._applied_disturbances == {0.1}
    assert abs(float(twin_session._data.time) - snap["time_s"]) < 1e-12
    assert not twin_session._stopped and not twin_session._ended