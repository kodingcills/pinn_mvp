"""Machine-facing types and the Machine protocol. The core runtime depends
only on these abstractions; simulators implement the protocol behind them."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Protocol, runtime_checkable

if TYPE_CHECKING:
    from haplous.capabilities import MachineCapabilities


@dataclass(frozen=True)
class Stamp:
    """Temporal semantics of one observation: when the producer sampled it
    and when the consumer received it. Age is consumer-view staleness."""

    source_s: float
    received_s: float

    @property
    def age_s(self) -> float:
        return self.received_s - self.source_s


@dataclass(frozen=True)
class MachineObservation:
    """One stamped machine state sample. pose_m is the tracked frame origin
    in machine/world coordinates; extra carries behavior-specific channels
    (e.g. a secondary lateral measurement) without widening the core type."""

    stamp: Stamp
    pose_m: tuple[float, float, float]
    force_z_n: float
    tilt_deg: float
    extra: tuple[tuple[str, float], ...] = ()

    def extra_value(self, key: str) -> float | None:
        for k, v in self.extra:
            if k == key:
                return v
        return None


@dataclass(frozen=True)
class ControlParameters:
    """Mechanism parameters (gains, rates, radii). These are deliberately a
    different type from intent Requirements: execution semantics are never
    expressed as controller parameters and vice versa."""

    name: str


@dataclass(frozen=True)
class MotionRecord:
    """The observation stream of one executed motion, in time order."""

    observations: tuple[MachineObservation, ...]
    ended_early: bool
    end_reason: str | None = None


@runtime_checkable
class Machine(Protocol):
    """The machine boundary. A runtime may only learn what the machine can
    do through capabilities(), may only act through execute()/stop(), and
    may only know state through observe()/health()."""

    def capabilities(self) -> "MachineCapabilities": ...

    def observe(self) -> MachineObservation | None: ...

    def execute(
        self,
        *,
        start_position_m: tuple[float, float, float],
        horizon_s: float,
    ) -> MotionRecord: ...

    def stop(self) -> None: ...

    def health(self) -> Mapping[str, float | str | bool]: ...
