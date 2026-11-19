"""Benchmark-side hold realizations of the HEB MuJoCo machine (issue #22).

`ClosedLoopHoldMachine` is the E19 `closed_loop_hold` realization: a simulator
fidelity option, not a Haplous feature. On a step with no newly accepted
command it re-applies the last accepted one, so the impedance servo runs on
live plant state against the held target (the E19 step-1 probe,
`scripts/e19_diagnose.py`). It declares `CLOSED_LOOP_HOLD`. Re-armed steps
are not logged as applied commands: the system issued nothing on them.
"""
from __future__ import annotations

from dataclasses import replace

from benchmarks.heb.divergence import DivergenceGuardedMachine
from haplous.capabilities import HoldSemantics


class _ReArmingSession:
    def __init__(self, inner) -> None:
        self.inner = inner
        self._last = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def apply(self, command):
        outcome = self.inner.apply(command)
        if outcome.accepted:
            self._last = command
        return outcome

    def step(self) -> None:
        raw = self.inner
        rearmed = raw._pending is None and self._last is not None
        if rearmed:
            raw._pending = self._last
        n = len(raw.applied_command_times)
        raw.step()
        if rearmed and len(raw.applied_command_times) > n:
            raw.applied_command_times.pop()


class ClosedLoopHoldMachine(DivergenceGuardedMachine):
    def capabilities(self):
        return replace(super().capabilities(), hold_semantics=HoldSemantics.CLOSED_LOOP_HOLD)

    def open_session(self, *, start_position_m, horizon_s):
        guarded = super().open_session(start_position_m=start_position_m, horizon_s=horizon_s)
        guarded.inner = _ReArmingSession(guarded.inner)
        return guarded


class PreI22AssumptionMachine(DivergenceGuardedMachine):
    """The standard zero-order machine, mis-declared `CLOSED_LOOP_HOLD`: the
    runtime then plain-holds on every cause, exactly the pre-#22 assumption.
    Used for parity with pre-#22 trials and as the E19 `haplous_mediated` arm.
    Physics is unchanged."""

    def capabilities(self):
        return replace(super().capabilities(), hold_semantics=HoldSemantics.CLOSED_LOOP_HOLD)
