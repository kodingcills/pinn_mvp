"""Benchmark-owned MuJoCo divergence guard (HEB 0.3.0, issue #21).

On NaN/Inf/huge qpos, qvel or qacc, MuJoCo warns and silently resets MjData
inside `mj_step`; the episode would then run on from the model default pose
and could be graded as a success. `DivergenceGuardedMachine` is the standard
HEB machine (`mujoco_backend.build_realization`'s default factory). It reads
the MuJoCo warning counters and the sim clock after every session step and,
on the first diverging step, stops the session and reports fault
`sim_diverged`. It never writes physics state before that step, so episodes
that do not diverge are unchanged. Systems carry the machine's record into
`SystemRun.detail["sim_diverged"]` and `grade_run` grades it `SIM_DIVERGED`.
"""
from __future__ import annotations

import mujoco

from haplous.adapters.mujoco_adapter import MuJoCoMachine

FAULT = "sim_diverged"
_RESET_WARNINGS = (
    mujoco.mjtWarning.mjWARN_BADQPOS,
    mujoco.mjtWarning.mjWARN_BADQVEL,
    mujoco.mjtWarning.mjWARN_BADQACC,
)


def _warning_counts(data) -> tuple[int, ...]:
    return tuple(int(data.warning[w].number) for w in _RESET_WARNINGS)


class DivergenceGuardedSession:
    """A MuJoCo session that ends itself at the first diverging step."""

    def __init__(self, inner, data) -> None:
        self.inner = inner
        self._data = data
        self._warnings = _warning_counts(data)
        self.divergence: dict | None = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def step(self) -> None:
        t_before = float(self._data.time)
        self.inner.step()
        if self.divergence is not None:
            return
        warnings = _warning_counts(self._data)
        signals = [w.name for w, a, b in zip(_RESET_WARNINGS, self._warnings, warnings, strict=True) if b > a]
        if float(self._data.time) < t_before:
            signals.append("sim_time_decreased")
        self._warnings = warnings
        if signals:
            # t_s: last sim time before the step that diverged and auto-reset.
            self.divergence = {"t_s": t_before, "signals": signals}
            self.inner.stop()

    @property
    def ended(self) -> bool:
        return self.divergence is not None or self.inner.ended

    @property
    def fault(self) -> str | None:
        return FAULT if self.divergence is not None else self.inner.fault


class DivergenceGuardedMachine(MuJoCoMachine):
    def open_session(self, *, start_position_m, horizon_s) -> DivergenceGuardedSession:
        inner = super().open_session(start_position_m=start_position_m, horizon_s=horizon_s)
        self._guarded = DivergenceGuardedSession(inner, self._data)
        return self._guarded

    @property
    def last_session_divergence(self) -> dict | None:
        guarded = getattr(self, "_guarded", None)
        return guarded.divergence if guarded is not None else None


def divergence_detail(machine) -> dict:
    """The machine's divergence record for SystemRun.detail; empty when none,
    so non-diverging rows keep their exact pre-0.3.0 shape."""
    divergence = getattr(machine, "last_session_divergence", None)
    return {FAULT: divergence} if divergence is not None else {}
