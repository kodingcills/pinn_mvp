from __future__ import annotations

from itertools import pairwise

import pytest

from benchmarks.governor import (
    GovernorConfig,
    GovernorMachine,
    TimingMachine,
    TimingStressConfig,
)
from benchmarks.governor.heb import execution_metrics
from haplous.capabilities import MachineCapabilities
from haplous.machine import CommandOutcome, MachineObservation, ServoCommand, Stamp


def observation(t_s: float, force_n: float = 0.0) -> MachineObservation:
    return MachineObservation(
        stamp=Stamp(source_s=t_s, received_s=t_s),
        pose_m=(0.1, -0.2, 0.3),
        force_z_n=force_n,
        tilt_deg=0.0,
    )


class RecordingSession:
    def __init__(self, observations: list[MachineObservation]) -> None:
        self.observations = observations
        self.commands: list[ServoCommand] = []
        self.index = 0
        self.steps = 0
        self.stopped = False

    def observe(self) -> MachineObservation | None:
        if self.index >= len(self.observations):
            return None
        result = self.observations[self.index]
        self.index += 1
        return result

    def apply(self, command: ServoCommand) -> CommandOutcome:
        self.commands.append(command)
        return CommandOutcome(accepted=True)

    def step(self) -> None:
        self.steps += 1

    def stop(self) -> None:
        self.stopped = True

    @property
    def ended(self) -> bool:
        return self.stopped

    @property
    def fault(self) -> str | None:
        return None


class RecordingMachine:
    def __init__(self, session: RecordingSession) -> None:
        self.session = session
        self.stop_calls = 0

    def capabilities(self) -> MachineCapabilities:
        return MachineCapabilities(
            command_modes=frozenset(),
            sensing=frozenset(),
            max_sensed_force_n=100.0,
            max_sensed_tilt_deg=45.0,
            position_resolution_m=1e-6,
            state_period_s=0.001,
            state_max_age_s=0.02,
            max_initial_offset_m=0.01,
            execution_horizon_s=5.0,
            expected_duration_s=1.0,
            supports_stop=True,
        )

    def observe(self) -> MachineObservation | None:
        return observation(0.0)

    def open_session(
        self,
        *,
        start_position_m: tuple[float, float, float],
        horizon_s: float,
    ) -> RecordingSession:
        return self.session

    def stop(self) -> None:
        self.stop_calls += 1

    def health(self) -> dict[str, float | str | bool]:
        return {"status": "ok"}


def command(k_z: float = 1000.0) -> ServoCommand:
    return ServoCommand(target_pose_m=(1.0, 2.0, -3.0), k_z_n_per_m=k_z)


def test_governor_preserves_command_below_activation_force() -> None:
    inner = RecordingSession([observation(0.0, 4.0)])
    session = GovernorMachine(
        RecordingMachine(inner),
        GovernorConfig(force_limit_n=10.0, activation_fraction=0.8),
    ).open_session(start_position_m=(0.0, 0.0, 0.0), horizon_s=1.0)

    session.observe()
    session.apply(command())

    assert inner.commands == [command()]


def test_governor_only_reduces_stiffness_at_force_limit() -> None:
    inner = RecordingSession([observation(0.0, 10.0)])
    session = GovernorMachine(
        RecordingMachine(inner),
        GovernorConfig(
            force_limit_n=10.0,
            activation_fraction=0.8,
            minimum_stiffness_scale=0.25,
        ),
    ).open_session(start_position_m=(0.0, 0.0, 0.0), horizon_s=1.0)

    session.observe()
    session.apply(command())

    assert inner.commands[0].target_pose_m == command().target_pose_m
    assert inner.commands[0].k_z_n_per_m == pytest.approx(250.0)


def test_governor_force_window_holds_then_releases_attenuation() -> None:
    inner = RecordingSession(
        [observation(0.0, 10.0), observation(0.001), observation(0.002)]
    )
    session = GovernorMachine(
        RecordingMachine(inner),
        GovernorConfig(force_limit_n=10.0, window_samples=2),
    ).open_session(start_position_m=(0.0, 0.0, 0.0), horizon_s=1.0)

    for _ in range(3):
        session.observe()
        session.apply(command())

    assert [item.k_z_n_per_m for item in inner.commands] == [250.0, 250.0, 1000.0]


def test_timing_jitter_is_seeded_and_bounded() -> None:
    observations = [observation(index * 0.001) for index in range(20)]
    config = TimingStressConfig(seed=17, period_jitter_fraction=1.0)

    def delivered_sources() -> list[float]:
        wrapped = TimingMachine(
            RecordingMachine(RecordingSession(observations.copy())), config
        )
        session = wrapped.open_session(
            start_position_m=(0.0, 0.0, 0.0), horizon_s=1.0
        )
        delivered = []
        for _ in observations:
            item = session.observe()
            if item is not None:
                delivered.append(item.stamp.source_s)
        return delivered

    first = delivered_sources()
    second = delivered_sources()

    assert first == second
    assert max(b - a for a, b in pairwise(first)) <= 0.002 + 1e-12
    assert len(first) < len(observations)


def test_timing_burst_can_drop_and_stale_observations() -> None:
    inner = RecordingSession(
        [observation(0.0), observation(0.001), observation(0.002)]
    )
    session = TimingMachine(
        RecordingMachine(inner),
        TimingStressConfig(
            seed=1,
            dropout_windows_s=((0.0005, 0.0015),),
            stale_windows_s=((0.0015, 0.0025, 0.03),),
        ),
    ).open_session(start_position_m=(0.0, 0.0, 0.0), horizon_s=1.0)

    first = session.observe()
    dropped = session.observe()
    stale = session.observe()

    assert first is not None
    assert dropped is None
    assert stale is not None
    assert stale.stamp.age_s == pytest.approx(0.03)


def test_timing_command_delay_forwards_without_changing_command() -> None:
    inner = RecordingSession([observation(0.0)])
    session = TimingMachine(
        RecordingMachine(inner), TimingStressConfig(seed=1, command_delay_steps=2)
    ).open_session(start_position_m=(0.0, 0.0, 0.0), horizon_s=1.0)

    submitted = command()
    assert session.apply(submitted).accepted
    session.step()
    session.step()
    assert inner.commands == []
    session.step()

    assert inner.commands == [submitted]


def test_execution_metrics_integrate_force_and_contact_oscillation() -> None:
    samples = (
        observation(0.0, 0.0),
        MachineObservation(
            stamp=Stamp(source_s=1.0, received_s=1.0),
            pose_m=(0.0, 0.0, 0.0),
            force_z_n=2.0,
            tilt_deg=0.0,
            extra=(("in_contact", 1.0),),
        ),
        MachineObservation(
            stamp=Stamp(source_s=2.0, received_s=2.0),
            pose_m=(0.0, 0.0, 0.0),
            force_z_n=4.0,
            tilt_deg=0.0,
            extra=(("in_contact", 1.0),),
        ),
    )

    metrics = execution_metrics(samples)

    assert metrics["force_integral_n_s"] == pytest.approx(4.0)
    assert metrics["contact_force_std_n"] == pytest.approx(1.0)
