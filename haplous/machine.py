"""Machine-facing types and the Machine protocol. The core runtime depends
only on these abstractions; simulators implement the protocol behind them.

Execution is live and interruptible: the runtime opens a session, and on
every supervision tick it observes fresh state, decides, and applies a
command BEFORE stepping the machine one period. The machine advances only
when told to, so constraint evaluation, degradation, recovery, and abort
can all occur mid-motion, before task completion."""
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
class ServoCommand:
    """One supervision-period servo command issued by the behavior layer.
    The machine owns every gain the task logic does not depend on; the task
    logic commands only what its own phase semantics choose: the target
    pose and the axial stiffness it wants regulated toward that pose."""

    target_pose_m: tuple[float, float, float]
    k_z_n_per_m: float


@dataclass(frozen=True)
class CommandOutcome:
    """Result of handing a command to the machine. A rejected command means
    the machine keeps holding its previous command."""

    accepted: bool
    reason: str | None = None


@runtime_checkable
class MachineSession(Protocol):
    """One live execution session on the machine. The runtime observes,
    decides, applies a command, and then steps the machine one supervision
    period; the machine moves only under commands it accepted. A session
    ends by exhausting its horizon (ended, fault None) or by a machine
    fault (ended or faulted state reported through fault)."""

    def observe(self) -> MachineObservation | None: ...

    def apply(self, command: ServoCommand) -> CommandOutcome: ...

    def step(self) -> None: ...

    def stop(self) -> None: ...

    @property
    def ended(self) -> bool: ...

    @property
    def fault(self) -> str | None: ...


@runtime_checkable
class Machine(Protocol):
    """The machine boundary. A runtime may only learn what the machine can
    do through capabilities(), may only act through open_session()/stop(),
    and may only know state through observe()/health()."""

    def capabilities(self) -> "MachineCapabilities": ...

    def observe(self) -> MachineObservation | None: ...

    def open_session(
        self,
        *,
        start_position_m: tuple[float, float, float],
        horizon_s: float,
    ) -> MachineSession: ...

    def stop(self) -> None: ...

    def health(self) -> Mapping[str, float | str | bool]: ...
