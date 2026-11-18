"""Machine-facing types and the Machine protocol. The core runtime depends
only on these abstractions; machines and simulators implement them.

Execution is live and interruptible: the runtime opens a session, and on
every supervision tick it observes fresh state, decides, and submits an
ActionEnvelope, then waits on the TickSource the integration supplied. The
runtime never advances the world: a simulator integration advances it from
its TickSource (haplous.adapters.legacy.SimTickSource); on a real machine
time simply passes (RealtimeTickSource). Constraint evaluation,
degradation, recovery, and abort can all occur mid-motion."""
from __future__ import annotations

import time
from dataclasses import dataclass
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from haplous.capabilities import MachineCapabilities


class ClockDomain(StrEnum):
    """Which clock a time value is read from (ARCH-001 decision 6). Values
    from different domains are never compared without a known mapping."""

    SIM = "sim"
    CONTROLLER = "controller"
    PRODUCER = "producer"
    MONOTONIC = "monotonic"
    WALL = "wall"


@dataclass(frozen=True)
class Stamp:
    """Temporal semantics of one observation: when the producer sampled it
    and when the consumer received it, both on `clock`. Age is
    consumer-view staleness."""

    source_s: float
    received_s: float
    clock: ClockDomain

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
class CartesianImpedanceTarget:
    """Cartesian impedance command surface: one supervision-period target.
    The machine owns every gain the task logic does not depend on; the task
    logic commands only what its own phase semantics choose: the target
    pose and the axial stiffness it wants regulated toward that pose."""

    target_pose_m: tuple[float, float, float]
    k_z_n_per_m: float


@dataclass(frozen=True)
class CommandOutcome:
    """Result of handing a target to a step-driven simulator session or
    ExternalTask (haplous.adapters.legacy). Not part of the product session
    protocol, which answers with Ack. A rejected target means the simulator
    keeps holding its previous one."""

    accepted: bool
    reason: str | None = None


@dataclass(frozen=True)
class ActionEnvelope:
    """The only form in which a command reaches a machine (ARCH-001
    decision 3). command_id is unique within one session; lease_epoch is
    the authority epoch the command was issued under; origin_source_s is
    the source time of the observation the decision was made on, and
    valid_until_s the last time the command may take effect, both on
    `clock`; correlation_id ties the command to its execution."""

    command_id: int
    lease_epoch: int
    origin_source_s: float
    clock: ClockDomain
    valid_until_s: float
    payload: CartesianImpedanceTarget
    correlation_id: str


class AckStatus(StrEnum):
    """Machine acknowledgement of one envelope. Synchronous sessions answer
    with CONTROLLER_ACCEPTED or REJECTED only."""

    CONTROLLER_ACCEPTED = "controller_accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Ack:
    """Acknowledgement of envelope `command_id`. Anything but
    CONTROLLER_ACCEPTED means the machine keeps holding its previous
    command."""

    command_id: int
    status: AckStatus
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status is AckStatus.CONTROLLER_ACCEPTED


@runtime_checkable
class MachineSession(Protocol):
    """One live execution session on the machine. The runtime observes,
    decides and submits; the machine moves only under envelopes it
    accepted. A session ends by exhausting its horizon (ended, fault None)
    or by a machine fault (reported through fault). There is no step():
    world advancement belongs to the integration's TickSource."""

    def observe(self) -> MachineObservation | None: ...

    def submit(self, envelope: ActionEnvelope) -> Ack: ...

    def stop(self) -> None: ...

    @property
    def ended(self) -> bool: ...

    @property
    def fault(self) -> str | None: ...


@runtime_checkable
class TickSource(Protocol):
    """Paces supervision. wait() returns when the next supervision period
    has begun; the runtime decides once per tick on fresh observations."""

    def wait(self) -> None: ...


class RealtimeTickSource:
    """Ticks on a fixed grid of `period_s` on `clock` (monotonic seconds).
    A tick that is late by more than the remaining period does not burst to
    catch up: missed periods are dropped and the grid re-anchors at now."""

    def __init__(
        self,
        period_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if period_s <= 0:
            raise ValueError("period_s must be positive")
        self.period_s = period_s
        self._clock = clock
        self._sleep = sleep
        self._deadline: float | None = None

    def wait(self) -> None:
        now = self._clock()
        deadline = (now if self._deadline is None else self._deadline) + self.period_s
        if deadline < now:
            deadline = now
        self._sleep(deadline - now)
        self._deadline = deadline


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
