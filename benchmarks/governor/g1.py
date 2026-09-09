"""Task-agnostic G1 force governor and benchmark timing stressors.

G1 is deliberately one mechanism: a recent absolute-force peak scales only
the requested axial stiffness. It preserves the behavior's target pose, so it
cannot create motion or redirect a task. Timing stress is separate and merely
delays or suppresses traffic at the public machine-session boundary.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from haplous.machine import CommandOutcome, MachineObservation, ServoCommand

if TYPE_CHECKING:
    from haplous.capabilities import MachineCapabilities
    from haplous.machine import Machine, MachineSession


@dataclass(frozen=True, slots=True)
class GovernorConfig:
    """Four parameters fully defining the G1 control law."""

    force_limit_n: float
    activation_fraction: float = 0.8
    minimum_stiffness_scale: float = 0.25
    window_samples: int = 20


class GovernorSession:
    def __init__(self, inner: MachineSession, config: GovernorConfig) -> None:
        self._inner = inner
        self._config = config
        self._forces: deque[float] = deque(maxlen=config.window_samples)
        self._observations: list[MachineObservation] = []
        self._attenuation_count = 0
        self._minimum_applied_scale = 1.0

    def observe(self) -> MachineObservation | None:
        observation = self._inner.observe()
        if observation is not None:
            self._forces.append(abs(observation.force_z_n))
            self._observations.append(observation)
        return observation

    def apply(self, command: ServoCommand) -> CommandOutcome:
        scale = self._stiffness_scale()
        if scale < 1.0:
            self._attenuation_count += 1
            self._minimum_applied_scale = min(self._minimum_applied_scale, scale)
        governed = ServoCommand(
            target_pose_m=command.target_pose_m,
            k_z_n_per_m=command.k_z_n_per_m * scale,
        )
        return self._inner.apply(governed)

    def _stiffness_scale(self) -> float:
        peak = max(self._forces, default=0.0)
        activation = self._config.force_limit_n * self._config.activation_fraction
        if peak <= activation:
            return 1.0
        span = self._config.force_limit_n - activation
        progress = min((peak - activation) / span, 1.0)
        reduction = progress * (1.0 - self._config.minimum_stiffness_scale)
        return 1.0 - reduction

    def step(self) -> None:
        self._inner.step()

    def stop(self) -> None:
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

    @property
    def attenuation_count(self) -> int:
        return self._attenuation_count

    @property
    def minimum_applied_scale(self) -> float:
        return self._minimum_applied_scale


class GovernorMachine:
    def __init__(self, inner: Machine, config: GovernorConfig) -> None:
        self._inner = inner
        self.config = config
        self._session: GovernorSession | None = None

    def capabilities(self) -> MachineCapabilities:
        return self._inner.capabilities()

    def observe(self) -> MachineObservation | None:
        return self._inner.observe()

    def open_session(
        self,
        *,
        start_position_m: tuple[float, float, float],
        horizon_s: float,
    ) -> GovernorSession:
        self._session = GovernorSession(
            self._inner.open_session(
                start_position_m=start_position_m,
                horizon_s=horizon_s,
            ),
            self.config,
        )
        return self._session

    def stop(self) -> None:
        self._inner.stop()

    def health(self) -> Mapping[str, float | str | bool]:
        return self._inner.health()

    @property
    def last_session_observations(self) -> tuple[MachineObservation, ...]:
        return self._session.observations if self._session is not None else ()

    @property
    def last_session_stats(self) -> Mapping[str, float | int]:
        if self._session is None:
            return {"attenuation_count": 0, "minimum_applied_scale": 1.0}
        return {
            "attenuation_count": self._session.attenuation_count,
            "minimum_applied_scale": self._session.minimum_applied_scale,
        }
