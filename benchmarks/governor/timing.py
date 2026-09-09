"""Deterministic timing impairments at the public machine-session boundary."""
from __future__ import annotations

import random
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from haplous.machine import CommandOutcome, MachineObservation, ServoCommand, Stamp

if TYPE_CHECKING:
    from haplous.capabilities import MachineCapabilities
    from haplous.machine import Machine, MachineSession


@dataclass(frozen=True, slots=True)
class TimingStressConfig:
    """Seeded observation jitter, bursts, and command transport delay."""

    seed: int
    period_jitter_fraction: float = 0.0
    dropout_windows_s: tuple[tuple[float, float], ...] = ()
    stale_windows_s: tuple[tuple[float, float, float], ...] = ()
    command_delay_steps: int = 0


class TimingSession:
    def __init__(
        self,
        inner: MachineSession,
        config: TimingStressConfig,
        nominal_period_s: float,
    ) -> None:
        self._inner = inner
        self._config = config
        self._period_s = nominal_period_s
        self._rng = random.Random(config.seed)
        self._next_delivery_s: float | None = None
        self._tick = 0
        self._commands: deque[tuple[int, ServoCommand]] = deque()
        self._observations: list[MachineObservation] = []

    def observe(self) -> MachineObservation | None:
        observation = self._inner.observe()
        if observation is None:
            return None
        now = observation.stamp.source_s
        if any(start <= now <= end for start, end in self._config.dropout_windows_s):
            return None
        if self._next_delivery_s is not None and now < self._next_delivery_s:
            return None
        jitter = self._rng.uniform(
            -self._config.period_jitter_fraction,
            self._config.period_jitter_fraction,
        )
        self._next_delivery_s = now + self._period_s * (1.0 + jitter)
        extra_delay = max(
            (
                delay
                for start, end, delay in self._config.stale_windows_s
                if start <= now <= end
            ),
            default=0.0,
        )
        delivered = replace(
            observation,
            stamp=Stamp(
                source_s=observation.stamp.source_s,
                received_s=observation.stamp.received_s + extra_delay,
            ),
        )
        self._observations.append(delivered)
        return delivered

    def apply(self, command: ServoCommand) -> CommandOutcome:
        due_tick = self._tick + self._config.command_delay_steps
        self._commands.append((due_tick, command))
        return CommandOutcome(accepted=True)

    def step(self) -> None:
        newest_due: ServoCommand | None = None
        while self._commands and self._commands[0][0] <= self._tick:
            _, newest_due = self._commands.popleft()
        if newest_due is not None:
            _ = self._inner.apply(newest_due)
        self._inner.step()
        self._tick += 1

    def stop(self) -> None:
        self._commands.clear()
        self._inner.stop()

    @property
    def ended(self) -> bool:
        return self._inner.ended

    @property
    def fault(self) -> str | None:
        return self._inner.fault

    @property
    def observations(self) -> tuple[MachineObservation, ...]:
        return tuple(self._observations)


class TimingMachine:
    def __init__(self, inner: Machine, config: TimingStressConfig) -> None:
        self._inner = inner
        self.config = config
        self._session: TimingSession | None = None

    def capabilities(self) -> MachineCapabilities:
        return self._inner.capabilities()

    def observe(self) -> MachineObservation | None:
        return self._inner.observe()

    def open_session(
        self,
        *,
        start_position_m: tuple[float, float, float],
        horizon_s: float,
    ) -> TimingSession:
        self._session = TimingSession(
            self._inner.open_session(
                start_position_m=start_position_m,
                horizon_s=horizon_s,
            ),
            self.config,
            self.capabilities().state_period_s,
        )
        return self._session

    def stop(self) -> None:
        self._inner.stop()

    def health(self) -> Mapping[str, float | str | bool]:
        return self._inner.health()

    @property
    def last_session_observations(self) -> tuple[MachineObservation, ...]:
        return self._session.observations if self._session is not None else ()
