"""Physical intent and requirements. Requirements are execution semantics
(force, tolerance, freshness, deadline); controller parameters are a
different type (haplous.machine.ControlParameters) and never appear here."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Sequence

from haplous.machine import MachineObservation


class RequirementKind(Enum):
    MAX_PROCESS_FORCE_N = "max_process_force_n"
    MAX_TILT_DEG = "max_tilt_deg"
    POSITION_TOLERANCE_M = "position_tolerance_m"
    STATE_MAX_AGE_S = "state_max_age_s"
    DEADLINE_S = "deadline_s"
    INITIAL_OFFSET_M = "initial_offset_m"


@dataclass(frozen=True)
class Requirement:
    """One stated execution requirement. Hard requirements must be fully
    satisfiable by the machine or the request is refused; soft requirements
    may degrade with a recorded reason."""

    kind: RequirementKind
    hard: bool
    limit: float
    detail: str = ""


@dataclass(frozen=True)
class SuccessCondition:
    """Declarative, observation-derived completion test supplied by the
    behavior layer. The runtime completes only when this evaluates true over
    the observed stream; machine health() or any external claim cannot
    complete an execution."""

    name: str
    evaluate: Callable[[Sequence[MachineObservation]], bool]


@dataclass(frozen=True)
class RecoveryPolicy:
    """Recovery is not retry-by-default: it requires a permitted condition,
    a bounded action budget, and a named action."""

    action: str
    permitted_conditions: frozenset[str]
    max_recovery_actions: int


@dataclass(frozen=True)
class ExecutionIntent:
    behavior: str
    goal_description: str
    goal_position_m: tuple[float, float, float]
    start_position_m: tuple[float, float, float]
    requirements: tuple[Requirement, ...]
    success_condition: SuccessCondition | None
    recovery_policy: RecoveryPolicy | None = None
