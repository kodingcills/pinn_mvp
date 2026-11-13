"""Execution behavior contract. The behavior layer owns task execution
logic: from each fresh observation it decides the next servo command.
The runtime owns admissibility and lifecycle semantics and never computes
task control; a behavior never talks to the machine directly — it only
returns commands the runtime chooses to authorize and apply."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from haplous.capabilities import CommandMode, MachineCapabilities
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
    returns the next servo command.

    Command contract (OD-1). ServoCommand carries no mode tag, so the modes
    a behavior's commands need are a declared fact. The runtime refuses a
    behavior that does not declare required_command_modes. A substitute
    (required, native) is admitted only if the behavior authorizes it here,
    the machine lists it in MachineCapabilities.verified_adapters, and the
    required mode is not in non_substitutable_modes."""

    required_command_modes: frozenset[CommandMode]
    authorized_substitutes: frozenset[tuple[CommandMode, CommandMode]]
    non_substitutable_modes: frozenset[CommandMode]

    def begin(
        self,
        intent: "ExecutionIntent",
        caps: "MachineCapabilities",
        observation: "MachineObservation",
    ) -> None: ...

    def step(self, observation: "MachineObservation") -> "ServoCommand": ...
