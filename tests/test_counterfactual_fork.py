"""Counterfactual fork correctness: restored-state continuation must
reproduce uninterrupted execution bit-identically."""

import copy

import mujoco
import pytest

from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig
from haplous.behaviors.insertion import insertion_behavior_factory, insertion_intent
from pegsim.rollout import VALIDATED_LOOSE_V1


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