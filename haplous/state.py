"""Execution states with explicit, exhaustive legal transitions."""
from __future__ import annotations

from enum import Enum


class ExecutionState(Enum):
    IDLE = "idle"
    VALIDATING = "validating"
    READY = "ready"
    EXECUTING = "executing"
    CONSTRAINED = "constrained"
    RECOVERING = "recovering"
    DEGRADED = "degraded"
    REFUSED = "refused"
    ABORTED = "aborted"
    COMPLETE = "complete"


TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.IDLE: frozenset({ExecutionState.VALIDATING}),
    ExecutionState.VALIDATING: frozenset(
        {ExecutionState.READY, ExecutionState.REFUSED}
    ),
    ExecutionState.READY: frozenset(
        {ExecutionState.EXECUTING, ExecutionState.REFUSED}
    ),
    ExecutionState.EXECUTING: frozenset(
        {
            ExecutionState.COMPLETE,
            ExecutionState.ABORTED,
            ExecutionState.CONSTRAINED,
            ExecutionState.RECOVERING,
            ExecutionState.DEGRADED,
        }
    ),
    ExecutionState.CONSTRAINED: frozenset(
        {
            ExecutionState.EXECUTING,
            ExecutionState.RECOVERING,
            ExecutionState.ABORTED,
            ExecutionState.COMPLETE,
        }
    ),
    ExecutionState.RECOVERING: frozenset(
        {ExecutionState.EXECUTING, ExecutionState.ABORTED}
    ),
    ExecutionState.DEGRADED: frozenset(
        {ExecutionState.EXECUTING, ExecutionState.ABORTED, ExecutionState.COMPLETE}
    ),
    ExecutionState.REFUSED: frozenset(),
    ExecutionState.ABORTED: frozenset(),
    ExecutionState.COMPLETE: frozenset(),
}


class IllegalTransitionError(RuntimeError):
    pass


class ExecutionStateTracker:
    """Owns the current state and enforces the transition table."""

    def __init__(self) -> None:
        self._state = ExecutionState.IDLE
        self._history: list[ExecutionState] = [ExecutionState.IDLE]

    @property
    def state(self) -> ExecutionState:
        return self._state

    @property
    def history(self) -> tuple[ExecutionState, ...]:
        return tuple(self._history)

    def transition(self, new: ExecutionState) -> ExecutionState:
        if new not in TRANSITIONS[self._state]:
            raise IllegalTransitionError(
                f"illegal transition {_state_name(self._state)} -> "
                f"{_state_name(new)}"
            )
        self._state = new
        self._history.append(new)
        return new

    def reset(self) -> None:
        self._state = ExecutionState.IDLE
        self._history = [ExecutionState.IDLE]


def _state_name(state: ExecutionState) -> str:
    return state.value.upper()
