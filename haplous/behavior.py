"""Execution behavior contract. The behavior layer owns task execution
logic: from each fresh observation it decides the next servo command.
The runtime owns admissibility and lifecycle semantics and never computes
task control; a behavior never talks to the machine directly — it only
returns commands the runtime chooses to authorize and apply."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from haplous.capabilities import MachineCapabilities
    from haplous.intent import ExecutionIntent
    from haplous.machine import MachineObservation, ServoCommand


@runtime_checkable
class ExecutionBehavior(Protocol):
    """A task strategy behind one intent behavior name.

    begin() (re)seeds the strategy from the intent and the CURRENT
    observation; it is called at session start and again as the
    implementation of an authorized recovery action, so recovery is
    re-referencing from the live state, never a blind replay. step() is
    called once per supervision tick with the freshest observation and
    returns the next servo command."""

    def begin(
        self,
        intent: "ExecutionIntent",
        caps: "MachineCapabilities",
        observation: "MachineObservation",
    ) -> None: ...

    def step(self, observation: "MachineObservation") -> "ServoCommand": ...
