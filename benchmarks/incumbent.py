"""G1: the competent configured incumbent (docs/eval_program/designs/G1_incumbent.md).

The task behaviour behind the guards a competent integrator configures:
pre-motion admission (controller interfaces, validated start envelope, cycle
time, sensor readiness) and live monitors (force ceiling with authorized
retry, tilt, deadline, freshness watchdog with compliant hold, command
acceptance, machine fault, interface loss). It reads exactly what Haplous
reads: the ExecutionIntent built from the scenario's declared requirements,
machine.capabilities(), machine/session observations, health and command
outcomes, and the behaviour's declared command contract. It never imports
haplous runtime logic. The supervision itself lives in ``benchmarks.g1lib``;
this module is its HEB machine adapter and task configuration."""
from __future__ import annotations

import time

from benchmarks.g1lib import GUARDS as LIB_GUARDS
from benchmarks.g1lib import Limit, Recovery, Sample, Sent, Supervisor, Task
from benchmarks.heb import systems
from benchmarks.heb.divergence import divergence_detail
from benchmarks.heb.protocol import Realization, Scenario, SystemRun
from haplous.intent import RequirementKind

GUARDS = frozenset({
    "command_mode",       # pre-motion: required controller interfaces present
    "capability_loss",    # live: a required interface disappears -> stop
    "reach",              # pre-motion: start inside the validated envelope
    "cycle_time",         # pre-motion: deadline >= validated cycle time
    "sensor_ready",       # pre-motion: fresh state before motion
    "force_ceiling",      # live: |Fz| above the declared ceiling -> stop
    "recovery",           # live: authorized, budgeted retry on a ceiling trip
    "tilt",               # live: tilt above the declared limit -> stop
    "deadline",           # live: deadline passed -> stop
    "stale",              # live: stale sample -> hold, watchdog timeout
    "dropout",            # live: missing sample -> hold, watchdog timeout
    "command_rejection",  # live: rejected commands -> hold, timeout
    "machine_fault",      # live: fault or unhealthy machine -> diagnosed stop
})


def unmet_command_modes(behavior: object, caps) -> list[str] | None:
    """Required modes the machine does not offer by exact name, or None when
    the behaviour declares no contract. No substitution: an incumbent
    activates a controller only when its interfaces exist."""
    required = getattr(behavior, "required_command_modes", None)
    if not required:
        return None
    return sorted(m.value for m in required if m not in caps.command_modes)


class _HebAdapter:
    """g1lib adapter over a HEB machine: synchronous command outcomes, no exclusive control."""

    def __init__(self, machine, intent, caps) -> None:
        self.machine, self.session = machine, None
        self._intent, self._caps, self._t = intent, caps, 0.0

    def _sample(self, obs) -> Sample | None:
        if obs is None:
            return None
        self._t = max(self._t, obs.stamp.source_s)
        return Sample(obs.stamp.source_s, obs.stamp.age_s, obs)

    def now(self) -> float:
        return self._t

    def peek(self) -> Sample | None:
        return self._sample(self.machine.observe())

    def open(self) -> None:
        self.session = self.machine.open_session(
            start_position_m=self._intent.start_position_m,
            horizon_s=self._caps.execution_horizon_s,
        )

    def sample(self) -> Sample | None:
        return self._sample(self.session.observe())

    def health(self) -> str | None:
        status = str(self.machine.health().get("status", "ok"))
        return None if status == "ok" else status

    def has_authority(self) -> bool:
        return True

    def command_modes(self):
        return self.machine.capabilities().command_modes

    def send(self, command, valid_until_s: float) -> Sent:
        return Sent("accepted" if self.session.apply(command).accepted else "rejected")

    def poll_acks(self) -> tuple:
        return ()

    def hold_command(self) -> None:
        return None  # the machine holds its last setpoint

    def stop(self) -> None:
        self.session.stop()

    def step(self) -> None:
        self.session.step()

    def ended(self) -> bool:
        return self.session.ended

    def fault(self) -> str | None:
        return self.session.fault


class _Restartable:
    """HEB behaviour as a g1lib Behavior: restarts re-run ``begin`` from the live pose."""

    def __init__(self, behavior, intent, caps) -> None:
        self._behavior, self._intent, self._caps = behavior, intent, caps

    def begin(self, obs) -> None:
        self._behavior.begin(self._intent, self._caps, obs)

    def step(self, obs):
        return self._behavior.step(obs)


class G1IncumbentSystem:
    """``disabled`` removes named guards; it exists for the planted
    guard-removal tests and is never used by an arm name."""

    def __init__(self, disabled: frozenset[str] = frozenset()) -> None:
        unknown = set(disabled) - GUARDS
        if unknown:
            raise ValueError(f"unknown G1 guards {sorted(unknown)}")
        self.name = "g1_incumbent"
        self._on = GUARDS - frozenset(disabled)
        self._supervisor = Supervisor(disabled=frozenset(disabled) & LIB_GUARDS)

    def _task(self, behavior, caps, condition, limits, policy) -> Task:
        on = self._on
        offset = limits.get(RequirementKind.INITIAL_OFFSET_M)
        deadline = limits.get(RequirementKind.DEADLINE_S)
        force_limit = limits.get(RequirementKind.MAX_PROCESS_FORCE_N)
        tilt_limit = limits.get(RequirementKind.MAX_TILT_DEG)
        checks = []
        if "reach" in on and offset is not None:
            checks.append(("start_outside_validated_envelope", offset > caps.max_initial_offset_m))
        if "cycle_time" in on and deadline is not None:
            checks.append(("cycle_time_infeasible", deadline < caps.expected_duration_s))
        live = []
        if "force_ceiling" in on and force_limit is not None:
            permitted = "recovery" in on and policy is not None and "process_force" in policy.permitted_conditions
            recovery = Recovery(policy.max_recovery_actions, policy.settle_s) if permitted else None
            live.append(Limit("force_ceiling", lambda o: abs(o.force_z_n) > force_limit, "force_limit", recovery))
        if "tilt" in on and tilt_limit is not None:
            live.append(Limit("tilt", lambda o: o.tilt_deg > tilt_limit, "tilt_limit"))
        if "deadline" in on and deadline is not None:
            live.append(Limit("deadline", lambda o: o.stamp.source_s > deadline, "deadline_exceeded"))
        return Task(
            required_modes=getattr(behavior, "required_command_modes", None),
            offered_modes=caps.command_modes,
            max_state_age_s=limits[RequirementKind.STATE_MAX_AGE_S],
            # Watchdog and rejection timeouts: the machine's declared compliant-hold time.
            max_untrusted_ticks=int(caps.max_hold_unobserved_s / caps.state_period_s),
            complete=condition.evaluate,
            admission_checks=tuple(checks),
            limits=tuple(live),
        )

    def run(self, realization: Realization, scenario: Scenario) -> SystemRun:
        machine = realization.machine
        condition = realization.condition_factory(scenario, realization.bundle)
        intent = systems.build_intent(scenario, condition)  # module lookup: the leak detector patches it
        limits = {r.kind: r.limit for r in intent.requirements}
        t0 = time.perf_counter()
        caps = machine.capabilities()
        behavior = realization.behavior_factory()
        task = self._task(behavior, caps, condition, limits, intent.recovery_policy)
        adapter = _HebAdapter(machine, intent, caps)
        out = self._supervisor.run(adapter, _Restartable(behavior, intent, caps), task)
        detail: dict = {}
        if out.stage == "admission":
            detail = {"refusal_stage": "pre_motion"}
        elif out.stage == "run":
            detail = {
                "applied_command_times": tuple(getattr(adapter.session, "applied_command_times", ())),
                **divergence_detail(machine),
            }
        return SystemRun(
            system=self.name,
            disposition=out.disposition,
            reason="g1_pre_motion_refusal" if out.stage == "admission" else out.reason,
            refusal_codes=out.codes,
            observations=tuple(out.history),
            wall_s=time.perf_counter() - t0,
            detail=detail,
        )
