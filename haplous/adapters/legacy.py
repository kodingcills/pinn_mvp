"""Step-driven simulator sessions behind the product protocol (ARCH-001,
migration and parity). Simulators (MuJoCo, external benchmark tasks and
the benchmark wrappers around them) keep their observe/apply/step session
shape; LegacyMachine presents such a machine to the runtime as a product
Machine whose sessions accept ActionEnvelopes, and SimTickSource advances
the simulator one supervision period per runtime tick."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from haplous.machine import (
    Ack,
    AckStatus,
    ActionEnvelope,
    CartesianImpedanceTarget,
    CommandOutcome,
    MachineObservation,
)
from haplous.runtime import HaplousRuntime

if TYPE_CHECKING:
    from haplous.capabilities import MachineCapabilities


@runtime_checkable
class SimSession(Protocol):
    """One step-driven simulator session: the world advances only on step()."""

    def observe(self) -> MachineObservation | None: ...

    def apply(self, command: CartesianImpedanceTarget) -> CommandOutcome: ...

    def step(self) -> None: ...

    def stop(self) -> None: ...

    @property
    def ended(self) -> bool: ...

    @property
    def fault(self) -> str | None: ...


@runtime_checkable
class SimMachine(Protocol):
    """A Machine whose open_session returns a step-driven SimSession."""

    def capabilities(self) -> MachineCapabilities: ...

    def observe(self) -> MachineObservation | None: ...

    def open_session(
        self, *, start_position_m: tuple[float, float, float], horizon_s: float
    ) -> SimSession: ...

    def stop(self) -> None: ...

    def health(self) -> Mapping[str, float | str | bool]: ...


class LegacySession:
    """Product MachineSession over a SimSession: submit unwraps the
    envelope and applies its payload, answering synchronously. Its
    machine does not declare leased_authority: the lease is the constant
    epoch 0, and an envelope under any other epoch is stale."""

    def __init__(self, inner: SimSession) -> None:
        self.inner = inner

    def observe(self) -> MachineObservation | None:
        return self.inner.observe()

    def submit(self, envelope: ActionEnvelope) -> Ack:
        if envelope.lease_epoch != 0:
            return Ack(envelope.command_id, AckStatus.REJECTED, "stale_lease_epoch")
        if not isinstance(envelope.payload, CartesianImpedanceTarget):
            return Ack(envelope.command_id, AckStatus.REJECTED, "unsupported_payload")
        outcome = self.inner.apply(envelope.payload)
        status = AckStatus.CONTROLLER_ACCEPTED if outcome.accepted else AckStatus.REJECTED
        return Ack(envelope.command_id, status, outcome.reason)

    def poll_acks(self) -> list[Ack]:
        return []

    def stop(self) -> None:
        self.inner.stop()

    @property
    def ended(self) -> bool:
        return self.inner.ended

    @property
    def fault(self) -> str | None:
        return self.inner.fault


class LegacyMachine:
    """Product Machine over a SimMachine; remembers the session it opened
    so SimTickSource can step it."""

    def __init__(self, inner: SimMachine) -> None:
        self.inner = inner
        self.session: LegacySession | None = None

    def capabilities(self) -> MachineCapabilities:
        return self.inner.capabilities()

    def observe(self) -> MachineObservation | None:
        return self.inner.observe()

    def open_session(
        self, *, start_position_m: tuple[float, float, float], horizon_s: float
    ) -> LegacySession:
        self.session = LegacySession(
            self.inner.open_session(start_position_m=start_position_m, horizon_s=horizon_s)
        )
        return self.session

    def stop(self) -> None:
        self.inner.stop()

    def health(self) -> Mapping[str, float | str | bool]:
        return self.inner.health()


class SimTickSource:
    """Adapter-owned world stepping: each tick advances the simulator
    session that `machine` currently holds by one supervision period."""

    def __init__(self, machine: LegacyMachine) -> None:
        self._machine = machine

    def wait(self) -> None:
        assert self._machine.session is not None, "tick before a session was opened"
        self._machine.session.inner.step()


def sim_runtime(machine: SimMachine, **kwargs: Any) -> HaplousRuntime:
    """A HaplousRuntime driving a step-driven simulator machine."""
    legacy = LegacyMachine(machine)
    return HaplousRuntime(legacy, tick_source=SimTickSource(legacy), **kwargs)
