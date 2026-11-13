"""G1: the competent configured incumbent (docs/eval_program/designs/G1_incumbent.md).

The task behaviour behind the guards a competent integrator configures:
pre-motion admission (controller interfaces, validated start envelope, cycle
time, sensor readiness) and live monitors (force ceiling with authorized
retry, tilt, deadline, freshness watchdog with compliant hold, command
acceptance, machine fault, interface loss). It reads exactly what Haplous
reads: the ExecutionIntent built from the scenario's declared requirements,
machine.capabilities(), machine/session observations, health and command
outcomes, and the behaviour's declared command contract. It never imports
haplous runtime logic."""
from __future__ import annotations

import time

from benchmarks.heb import systems
from benchmarks.heb.protocol import Realization, Scenario, SystemRun
from haplous.intent import RequirementKind
from haplous.machine import MachineObservation

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


class G1IncumbentSystem:
    """``disabled`` removes named guards; it exists for the planted
    guard-removal tests and is never used by an arm name."""

    def __init__(self, disabled: frozenset[str] = frozenset()) -> None:
        unknown = set(disabled) - GUARDS
        if unknown:
            raise ValueError(f"unknown G1 guards {sorted(unknown)}")
        self.name = "g1_incumbent"
        self._on = GUARDS - frozenset(disabled)

    def _pre_motion(self, machine, behavior, caps, limits, max_age) -> tuple[str, ...]:
        codes: list[str] = []
        if "command_mode" in self._on:
            unmet = unmet_command_modes(behavior, caps)
            if unmet is None:
                codes.append("command_contract_undeclared")
            elif unmet:
                codes.append("controller_interface_unavailable")
        offset = limits.get(RequirementKind.INITIAL_OFFSET_M)
        if "reach" in self._on and offset is not None and offset > caps.max_initial_offset_m:
            codes.append("start_outside_validated_envelope")
        deadline = limits.get(RequirementKind.DEADLINE_S)
        if "cycle_time" in self._on and deadline is not None and deadline < caps.expected_duration_s:
            codes.append("cycle_time_infeasible")
        if not codes and "sensor_ready" in self._on:
            obs = machine.observe()
            if obs is None or obs.stamp.age_s > max_age:
                codes.append("sensor_not_ready")
        return tuple(codes)

    def run(self, realization: Realization, scenario: Scenario) -> SystemRun:
        machine = realization.machine
        condition = realization.condition_factory(scenario, realization.bundle)
        intent = systems.build_intent(scenario, condition)  # module lookup: the leak detector patches it
        limits = {r.kind: r.limit for r in intent.requirements}
        max_age = limits[RequirementKind.STATE_MAX_AGE_S]
        force_limit = limits.get(RequirementKind.MAX_PROCESS_FORCE_N)
        tilt_limit = limits.get(RequirementKind.MAX_TILT_DEG)
        deadline = limits.get(RequirementKind.DEADLINE_S)
        policy = intent.recovery_policy
        t0 = time.perf_counter()
        caps = machine.capabilities()
        behavior = realization.behavior_factory()

        def result(disposition, reason, observations=(), codes=(), **detail):
            return SystemRun(
                system=self.name,
                disposition=disposition,
                reason=reason,
                refusal_codes=codes,
                observations=tuple(observations),
                wall_s=time.perf_counter() - t0,
                detail=detail,
            )

        codes = self._pre_motion(machine, behavior, caps, limits, max_age)
        if codes:
            return result("REFUSED", "g1_pre_motion_refusal", codes=codes, refusal_stage="pre_motion")
        try:
            session = machine.open_session(
                start_position_m=intent.start_position_m,
                horizon_s=caps.execution_horizon_s,
            )
        except Exception as exc:
            return result("ABORTED", f"machine_error_{type(exc).__name__}")
        obs = session.observe()
        if obs is None:
            session.stop()
            return result("ABORTED", "state_unavailable_at_start")
        behavior.begin(intent, caps, obs)
        observations: list[MachineObservation] = [obs]
        # Watchdog and rejection timeouts: the machine's declared compliant-hold time.
        max_untrusted = int(caps.max_hold_unobserved_s / caps.state_period_s)
        untrusted = rejected = retries = 0
        episode_t: float | None = None

        def stop(disposition, reason):
            session.stop()
            return result(
                disposition,
                reason,
                observations,
                applied_command_times=tuple(getattr(session, "applied_command_times", ())),
            )

        while True:
            if "machine_fault" in self._on and str(machine.health().get("status", "ok")) != "ok":
                return stop("ABORTED", "machine_unhealthy")
            if "capability_loss" in self._on and unmet_command_modes(behavior, machine.capabilities()):
                return stop("ABORTED", "controller_interface_lost")
            if obs is None and "dropout" not in self._on:
                obs = observations[-1]  # guard removed: act on the last sample
            trusted = obs is not None and (obs.stamp.age_s <= max_age or "stale" not in self._on)
            command = None
            if not trusted:
                untrusted += 1
                if untrusted > max_untrusted:
                    return stop("ABORTED", "watchdog_timeout")
            else:
                untrusted = 0
                t = obs.stamp.source_s
                skip = False
                breached = (
                    "force_ceiling" in self._on
                    and force_limit is not None
                    and abs(obs.force_z_n) > force_limit
                )
                if breached:
                    permitted = (
                        "recovery" in self._on
                        and policy is not None
                        and "process_force" in policy.permitted_conditions
                    )
                    if not permitted:
                        return stop("ABORTED", "force_limit")
                    if episode_t is None:
                        if retries >= policy.max_recovery_actions:
                            return stop("ABORTED", "recovery_budget_exhausted")
                        retries += 1
                        episode_t = t
                        behavior.begin(intent, caps, obs)  # back off: restart from the live pose
                        skip = True
                    elif policy.settle_s > 0.0 and t - episode_t > policy.settle_s:
                        return stop("ABORTED", "recovery_timeout")
                else:
                    episode_t = None
                if "tilt" in self._on and tilt_limit is not None and obs.tilt_deg > tilt_limit:
                    return stop("ABORTED", "tilt_limit")
                if "deadline" in self._on and deadline is not None and t > deadline:
                    return stop("ABORTED", "deadline_exceeded")
                if episode_t is None and condition.evaluate(observations):
                    return stop("COMPLETE", None)
                if not skip:
                    command = behavior.step(obs)
            if command is not None:
                accepted = session.apply(command).accepted
                if "command_rejection" in self._on:
                    rejected = 0 if accepted else rejected + 1
                    if rejected > max_untrusted:
                        return stop("ABORTED", "command_rejection_timeout")
            session.step()
            if session.ended:
                break
            obs = session.observe()
            if obs is not None:
                observations.append(obs)
        if session.fault is not None and "machine_fault" in self._on:
            return stop("ABORTED", f"machine_fault_{session.fault}")
        if condition.evaluate(observations):
            return stop("COMPLETE", None)
        return stop("ABORTED", "horizon_exhausted")
