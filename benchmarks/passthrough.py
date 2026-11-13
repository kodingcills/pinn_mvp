"""Pass-through execution path for the A/A check (program section 10).

The pass-through arm is BareSystem's behaviour run through a Machine and
session wrapper that forwards observe/apply/step/stop unchanged and only
counts calls. If it differs from the direct arm under controlled
determinism, instrumentation perturbs the plant and no later A/B holds.
"""
from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass

from benchmarks.heb.metrics import grade_run
from benchmarks.heb.protocol import Realization, Scenario, SystemRun
from benchmarks.heb.systems import BareSystem

# Observable-trajectory fields compared. wall_s is excluded: it is host time, not plant behaviour.
COMPARED_FIELDS = (
    "observations",
    "commands",
    "applied_command_times",
    "disposition",
    "reason",
    "final_sim_time_s",
    "graded_disposition",
    "task_success",
    "bounded_success",
    "hard_violations",
)


class PassThroughSession:
    """Forwards every MachineSession call unchanged. Counts and logs only."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = {"observe": 0, "apply": 0, "step": 0, "stop": 0}
        self.forwarded: list[tuple] = []  # (command, outcome) as seen at the boundary

    def observe(self):
        self.calls["observe"] += 1
        return self._inner.observe()

    def apply(self, command):
        self.calls["apply"] += 1
        outcome = self._inner.apply(command)
        self.forwarded.append((command, outcome))
        return outcome

    def step(self) -> None:
        self.calls["step"] += 1
        self._inner.step()

    def stop(self) -> None:
        self.calls["stop"] += 1
        self._inner.stop()

    @property
    def ended(self) -> bool:
        return self._inner.ended

    @property
    def fault(self):
        return self._inner.fault

    def __getattr__(self, name):  # applied_command_times, snapshot_state, ...
        return getattr(self._inner, name)


class PassThroughMachine:
    """Machine wrapper that hands out PassThroughSessions."""

    def __init__(self, inner, session_cls=PassThroughSession) -> None:
        self._inner = inner
        self._session_cls = session_cls
        self.sessions: list = []

    def open_session(self, *, start_position_m, horizon_s):
        session = self._session_cls(
            self._inner.open_session(
                start_position_m=start_position_m, horizon_s=horizon_s
            )
        )
        self.sessions.append(session)
        return session

    def __getattr__(self, name):  # capabilities, observe, stop, health, last_session_*
        return getattr(self._inner, name)


class PassThroughSystem(BareSystem):
    """BareSystem behaviour behind the pass-through boundary."""

    def __init__(self, machine_cls=PassThroughMachine, **kw) -> None:
        super().__init__(**kw)
        self.name = "passthrough_bare"
        self._machine_cls = machine_cls
        self.machine = None  # last wrapped machine, for call counts

    def run(self, realization: Realization, scenario: Scenario) -> SystemRun:
        self.machine = self._machine_cls(realization.machine)
        return super().run(
            dataclasses.replace(realization, machine=self.machine), scenario
        )


@dataclass(frozen=True)
class Trajectory:
    """The observable record of one trial. Every field in COMPARED_FIELDS."""

    observations: tuple
    commands: tuple  # ServoCommand emitted by the behaviour, in order
    applied_command_times: tuple
    disposition: str
    reason: str | None
    final_sim_time_s: float
    graded_disposition: str
    task_success: bool
    bounded_success: bool
    hard_violations: tuple

    def digest(self) -> str:
        return hashlib.sha256(repr(self).encode()).hexdigest()


class _Tap:
    """Logs what the behaviour emits; values are returned untouched."""

    def __init__(self, inner, log: list) -> None:
        self._inner, self._log = inner, log

    def begin(self, *a, **k):
        return self._inner.begin(*a, **k)

    def step(self, observation):
        command = self._inner.step(observation)
        self._log.append(command)
        return command


class _HorizonCap:
    """Shortens the advertised horizon. Applied identically to both A/A arms
    so the long non-completing HEB runs stay affordable in tests."""

    def __init__(self, inner, horizon_s: float) -> None:
        self._inner, self._h = inner, horizon_s

    def capabilities(self):
        c = self._inner.capabilities()
        return dataclasses.replace(c, execution_horizon_s=min(c.execution_horizon_s, self._h))

    def __getattr__(self, name):
        return getattr(self._inner, name)


def run_trajectory(scenario: Scenario, system, horizon_s: float | None = None) -> Trajectory:
    """Build a fresh realization, run `system` once, grade it independently."""
    from benchmarks.heb import mujoco_backend

    r = mujoco_backend.build_realization(scenario)
    if horizon_s is not None:
        r = dataclasses.replace(r, machine=_HorizonCap(r.machine, horizon_s))
    commands: list = []
    inner_factory = r.behavior_factory
    tapped = dataclasses.replace(
        r, behavior_factory=lambda: _Tap(inner_factory(), commands)
    )
    run = system.run(tapped, scenario)
    graded, success, bounded, violations, _ = grade_run(scenario, r.bundle, run)
    return Trajectory(
        observations=tuple(run.observations),
        commands=tuple(commands),
        applied_command_times=tuple(run.detail.get("applied_command_times", ())),
        disposition=run.disposition,
        reason=run.reason,
        # shortcut: reads the adapter's private MuJoCo clock, no public accessor exists
        final_sim_time_s=float(r.machine._data.time),
        graded_disposition=graded,
        task_success=success,
        bounded_success=bounded,
        hard_violations=tuple(violations),
    )


def diff_fields(a: Trajectory, b: Trajectory) -> list[str]:
    return [f for f in COMPARED_FIELDS if getattr(a, f) != getattr(b, f)]


def aa_compare(scenario: Scenario, passthrough_cls=PassThroughSystem, horizon_s: float | None = None) -> dict:
    direct = run_trajectory(scenario, BareSystem(), horizon_s)
    through = run_trajectory(scenario, passthrough_cls(), horizon_s)
    differing = diff_fields(direct, through)
    return {
        "scenario": scenario.id,
        "task_family": scenario.task_family,
        "horizon_cap_s": horizon_s,
        "identical": not differing,
        "differing_fields": differing,
        "n_observations": len(direct.observations),
        "n_commands": len(direct.commands),
        "disposition": direct.disposition,
        "task_success": direct.task_success,
        "final_sim_time_s": direct.final_sim_time_s,
        "digest_direct": direct.digest(),
        "digest_passthrough": through.digest(),
    }
