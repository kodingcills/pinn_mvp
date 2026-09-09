"""Focused adapter-session tests for the Task B substrate and the
benchmark-owned disturbance schedule: disturbances fire exactly once at
the intended step, in deterministic order, and re-fire in a fresh
episode; the non-spiral (slide) bundle constructs and steps without
touching spiral machinery."""
import math

import numpy as np
import pytest

from pegsim.rollout import VALIDATED_LOOSE_V1, VALIDATED_SLIDE_V1

from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig
from haplous.machine import ServoCommand


def _free_fall_trace(machine, n_steps):
    """Step a session without any accepted command: pure gravity dynamics.
    Returns the peg z-velocity after each step."""
    session = machine.open_session(
        start_position_m=(0.0, 0.0, 0.0), horizon_s=16.3
    )
    session.apply(
        ServoCommand(
            target_pose_m=tuple(float(v) for v in session.observe().pose_m),
            k_z_n_per_m=2000.0,
        )
    )
    velocities = []
    for _ in range(n_steps):
        session.step()
        velocities.append(float(machine._data.qvel[2]))
    return velocities


def test_disturbance_fires_exactly_once_at_scheduled_step():
    plain = MuJoCoMachine(VALIDATED_LOOSE_V1, config=MuJoCoMachineConfig())
    kicked = MuJoCoMachine(
        VALIDATED_LOOSE_V1,
        config=MuJoCoMachineConfig(
            disturbances=((0.0015, (0.0, 0.0, 0.05, 0.0, 0.0, 0.0)),)
        ),
    )
    a = _free_fall_trace(plain, 10)
    b = _free_fall_trace(kicked, 10)
    diffs = [bz - az for az, bz in zip(a, b)]
    # steps start at t=0; the 4th step begins at t=0.0015 >= t_s
    for k in range(3):
        assert diffs[k] == pytest.approx(0.0, abs=1e-12)
    for k in range(3, 10):
        assert diffs[k] == pytest.approx(0.05, abs=1e-12)


def test_multiple_disturbances_fire_in_order():
    machine = MuJoCoMachine(
        VALIDATED_LOOSE_V1,
        config=MuJoCoMachineConfig(
            disturbances=(
                (0.0005, (0.01, 0.0, 0.0, 0.0, 0.0, 0.0)),
                (0.0015, (0.0, 0.02, 0.0, 0.0, 0.0, 0.0)),
            )
        ),
    )
    session = machine.open_session(
        start_position_m=(0.0, 0.0, 0.0), horizon_s=16.3
    )
    session.apply(
        ServoCommand(
            target_pose_m=tuple(float(v) for v in session.observe().pose_m),
            k_z_n_per_m=2000.0,
        )
    )
    qvx = []
    qvy = []
    for _ in range(6):
        session.step()
        qvx.append(float(machine._data.qvel[0]))
        qvy.append(float(machine._data.qvel[1]))
    # each kick applied once, at its own step
    for k in range(6):
        expected_x = 0.01 if k >= 1 else 0.0
        expected_y = 0.02 if k >= 3 else 0.0
        assert qvx[k] == pytest.approx(expected_x, abs=1e-12)
        assert qvy[k] == pytest.approx(expected_y, abs=1e-12)


def test_disturbance_schedule_refires_in_new_episode():
    machine = MuJoCoMachine(
        VALIDATED_SLIDE_V1,
        config=MuJoCoMachineConfig(
            disturbances=((0.0005, (0.0, 0.0, 0.03, 0.0, 0.0, 0.0)),)
        ),
    )
    fired_per_episode = []
    for _episode in range(2):
        session = machine.open_session(
            start_position_m=(0.0, 0.0, 0.0), horizon_s=0.0009
        )
        session.apply(
            ServoCommand(
                target_pose_m=(
                    float(session.observe().pose_m[0]),
                    float(session.observe().pose_m[1]),
                    0.0,
                ),
                k_z_n_per_m=2000.0,
            )
        )
        fired = False
        while not session.ended:
            z_before = float(machine._data.qvel[2])
            session.step()
            if abs(float(machine._data.qvel[2]) - z_before) > 1e-9:
                fired = True
        fired_per_episode.append(fired)
    assert fired_per_episode == [True, True]


def test_slide_bundle_session_steps_without_spiral():
    machine = MuJoCoMachine(VALIDATED_SLIDE_V1, config=MuJoCoMachineConfig())
    capabilities = machine.capabilities()
    assert capabilities.max_initial_offset_m == pytest.approx(
        VALIDATED_SLIDE_V1.task.plate_outer_radius
        - VALIDATED_SLIDE_V1.task.peg_radius
    )
    session = machine.open_session(
        start_position_m=(0.0039, 0.0, 0.0), horizon_s=16.3
    )
    first = session.observe()
    for _ in range(20):
        outcome = session.apply(
            ServoCommand(
                target_pose_m=tuple(float(v) for v in first.pose_m),
                k_z_n_per_m=2000.0,
            )
        )
        assert outcome.accepted
        session.step()
    last = session.observe()
    assert last is not None
    assert session.fault is None
    # hovering with bias compensation: the tip stays near its start height
    assert abs(last.pose_m[2] - first.pose_m[2]) < 1e-3
