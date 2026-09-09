"""External benchmark task boundary. External evaluation frameworks
(Isaac Lab, robosuite, ContactBench, recorded-data harnesses) implement
ExternalTask behind this protocol; ExternalMachineAdapter presents any
such task to the Haplous runtime as an ordinary Machine.

Design rules enforced here and by tests/test_haplous_structure.py:

- Haplous core imports no external simulator. This module imports only
  the machine-facing types it already depends on.
- Benchmark ground truth is benchmark-owned: ground_truth()/task_result()
  live on ExternalTask and are NOT part of the Machine protocol, so the
  runtime can never read grader-owned state.
- Framework-specific imports stay in the concrete task implementations
  (benchmarks/external/), never in this module."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Protocol, runtime_checkable

from haplous.machine import CommandOutcome, MachineObservation, ServoCommand

if TYPE_CHECKING:
    from haplous.capabilities import MachineCapabilities
    from haplous.machine import Machine, MachineSession


@runtime_checkable
class ExternalTask(Protocol):
    """One externally owned contact/assembly task. Lifecycle: reset(seed)
    establishes the start realization; the session protocol is identical
    to MachineSession; ground_truth() exposes framework-owned truth that
    the runtime never touches."""

    task_id: str

    def reset(self, *, seed: int) -> None: ...

    def capabilities(self) -> "MachineCapabilities": ...

    def observe(self) -> MachineObservation | None: ...

    def apply_command(self, command: ServoCommand) -> CommandOutcome: ...

    def step(self) -> None: ...

    def stop(self) -> None: ...

    def session_ended(self) -> bool: ...

    def session_fault(self) -> str | None: ...

    def ground_truth(self) -> Mapping[str, float | str | bool]: ...

    def task_result(self) -> Mapping[str, float | str | bool | None]: ...


class ExternalMachineSession:
    """MachineSession view over one started ExternalTask episode."""

    def __init__(self, task: "ExternalTask") -> None:
        self._task = task

    def observe(self) -> MachineObservation | None:
        return self._task.observe()

    def apply(self, command: ServoCommand) -> CommandOutcome:
        return self._task.apply_command(command)

    def step(self) -> None:
        self._task.step()

    def stop(self) -> None:
        self._task.stop()

    @property
    def ended(self) -> bool:
        return self._task.session_ended()

    @property
    def fault(self) -> str | None:
        return self._task.session_fault()


class ExternalMachineAdapter:
    """Machine view over an ExternalTask. The runtime consumes only the
    Machine protocol; task truth and benchmark metrics stay outside."""

    def __init__(self, task: "ExternalTask") -> None:
        self._task = task

    def capabilities(self) -> "MachineCapabilities":
        return self._task.capabilities()

    def observe(self) -> MachineObservation | None:
        return self._task.observe()

    def open_session(
        self,
        *,
        start_position_m: tuple[float, float, float],
        horizon_s: float,
    ) -> "MachineSession":
        return ExternalMachineSession(self._task)

    def stop(self) -> None:
        self._task.stop()

    def health(self) -> Mapping[str, float | str | bool]:
        return self._task.ground_truth()


def open_task_session(task: "ExternalTask", *, seed: int) -> ExternalMachineSession:
    """Reset the task with an explicit seed and return its session view."""
    task.reset(seed=seed)
    return ExternalMachineSession(task)
