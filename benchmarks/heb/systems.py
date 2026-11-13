"""HEB systems under test: comparable execution systems over one shared
machine realization. The mediated system is Haplous through its public
runtime; the bare system drives the task behavior directly over the live
session — the same task primitive without runtime semantics. Neither may
read benchmark ground truth; grading happens in metrics.py."""
from __future__ import annotations

import math
import time

from haplous.intent import (
    ExecutionIntent,
    RecoveryPolicy,
    Requirement,
    RequirementKind,
    SuccessCondition,
)
from haplous.machine import Machine, MachineObservation
from haplous.runtime import HaplousRuntime

from benchmarks.heb.protocol import Realization, Scenario, SystemRun


def build_intent(
    scenario: Scenario,
    success_condition: SuccessCondition | None,
) -> ExecutionIntent:
    """Scenario requirements -> ExecutionIntent. Requirements stated as
    None are omitted. The success condition is task-family truth supplied
    by the task registry, identical for every system. The initial-offset
    requirement states the true commanded start distance so the runtime's
    reach check stays meaningful for both task families."""
    requirements: list[Requirement] = []
    if scenario.max_force_n is not None:
        requirements.append(
            Requirement(
                RequirementKind.MAX_PROCESS_FORCE_N,
                hard=True,
                limit=scenario.max_force_n,
            )
        )
    if scenario.min_force_n is not None:
        # Soft: the interaction floor cannot hold before contact is
        # established; the runtime records the degradation, the task
        # condition and the benchmark grading enforce the band.
        requirements.append(
            Requirement(
                RequirementKind.MIN_PROCESS_FORCE_N,
                hard=False,
                limit=scenario.min_force_n,
            )
        )
    if scenario.max_tilt_deg is not None:
        requirements.append(
            Requirement(
                RequirementKind.MAX_TILT_DEG, hard=True, limit=scenario.max_tilt_deg
            )
        )
    requirements.append(
        Requirement(
            RequirementKind.POSITION_TOLERANCE_M,
            hard=True,
            limit=scenario.position_tolerance_m or 1e-3,
        )
    )
    requirements.append(
        Requirement(
            RequirementKind.STATE_MAX_AGE_S,
            hard=True,
            limit=scenario.state_max_age_s,
        )
    )
    if scenario.deadline_s is not None:
        requirements.append(
            Requirement(
                RequirementKind.DEADLINE_S, hard=True, limit=scenario.deadline_s
            )
        )
    recovery = None
    if scenario.recovery_permitted_conditions:
        recovery = RecoveryPolicy(
            action="re_reference",
            permitted_conditions=frozenset(scenario.recovery_permitted_conditions),
            max_recovery_actions=scenario.max_recovery_actions,
            settle_s=scenario.recovery_settle_s,
        )
    if scenario.path_start_m is not None:
        start = scenario.path_start_m
        goal = scenario.path_end_m or (0.0, 0.0, 0.0)
    else:
        start = (
            scenario.initial_offset_m * math.cos(scenario.initial_bearing_rad),
            scenario.initial_offset_m * math.sin(scenario.initial_bearing_rad),
            0.0,
        )
        goal = (0.0, 0.0, 0.0)
    requirements.append(
        Requirement(
            RequirementKind.INITIAL_OFFSET_M,
            hard=True,
            limit=math.hypot(start[0], start[1]),
        )
    )
    return ExecutionIntent(
        behavior=scenario.task_family,
        # Task-level only: the scenario id names its fault and stays evaluator-side
        # (TrialRecord.scenario_id).
        goal_description=(
            f"{scenario.task_family} task under declared requirements: "
            + ", ".join(sorted(r.kind.value for r in requirements))
        ),
        goal_position_m=goal,
        start_position_m=start,
        requirements=tuple(requirements),
        success_condition=success_condition,
        recovery_policy=recovery,
    )


class HaplousSystem:
    """B2: the task behavior executed behind the Haplous runtime. Task
    specifics (behavior factory, condition factory) come from the
    realization's task-registry entry."""

    def __init__(self) -> None:
        self.name = "haplous_mediated"

    def run(self, realization: Realization, scenario: Scenario) -> SystemRun:
        condition = realization.condition_factory(scenario, realization.bundle)
        intent = build_intent(scenario, condition)
        runtime = HaplousRuntime(
            realization.machine,
            behaviors={scenario.task_family: realization.behavior_factory},
        )
        t0 = time.perf_counter()
        feasibility = runtime.submit(intent)
        if feasibility.outcome == "REFUSED":
            return SystemRun(
                system=self.name,
                disposition="REFUSED",
                reason="negotiation_refusal",
                refusal_codes=tuple(r.code for r in feasibility.reasons),
                wall_s=time.perf_counter() - t0,
                detail={"refusal_stage": "submit"},
            )
        evidence = runtime.execute()
        wall = time.perf_counter() - t0
        outcome = [e for e in evidence.events if e.kind == "outcome"][-1]
        applied = getattr(
            realization.machine, "last_session_applied_command_times", ()
        )
        result = outcome.payload["result"]
        reason = outcome.payload.get("reason")
        refusal_codes: tuple[str, ...] = ()
        refusal_stage = "none"
        if result == "REFUSED":
            # Mid-execution refusal (pre-execution state invalid): the
            # submit-time feasibility was SUPPORTED, so capture the
            # refusal evidence so it is not silently code-less.
            refusal_stage = "execution"
            reason = "pre_execution_state_invalid"
            for event in evidence.events:
                if event.kind == "pre_execution_state_invalid":
                    code = event.payload.get("reason")
                    if code and code not in refusal_codes:
                        refusal_codes = refusal_codes + (code,)
        return SystemRun(
            system=self.name,
            disposition=result,
            reason=reason,
            refusal_codes=refusal_codes,
            observations=realization.machine.last_session_observations,
            wall_s=wall,
            detail={
                "evidence_events": len(evidence.events),
                "applied_command_times": tuple(applied),
                "refusal_stage": refusal_stage,
            },
        )


class SupervisedSystem:
    """COMPETENT SUPERVISOR: the task behavior plus independent watchdogs
    built only from the scenario's declared requirements — the realistic
    production supervision shape (force watchdog, tilt watchdog,
    freshness watchdog, deadline). No negotiation, no recovery
    authorization, no capability semantics, no evidence trail: it stops
    execution on a watched breach and otherwise lets the behavior run."""

    def __init__(self) -> None:
        self.name = "competent_supervisor"

    def run(self, realization: Realization, scenario: Scenario) -> SystemRun:
        machine = realization.machine
        condition = realization.condition_factory(scenario, realization.bundle)
        intent = build_intent(scenario, condition)
        t0 = time.perf_counter()
        caps = machine.capabilities()
        try:
            session = machine.open_session(
                start_position_m=intent.start_position_m,
                horizon_s=caps.execution_horizon_s,
            )
        except Exception as exc:
            return SystemRun(
                system=self.name,
                disposition="ABORTED",
                reason=f"machine_error_{type(exc).__name__}",
                wall_s=time.perf_counter() - t0,
            )
        behavior = realization.behavior_factory()
        observation = session.observe()
        if observation is None:
            return SystemRun(
                system=self.name,
                disposition="ABORTED",
                reason="state_unavailable_at_start",
                wall_s=time.perf_counter() - t0,
            )
        behavior.begin(intent, caps, observation)
        observations: list[MachineObservation] = [observation]
        disposition = "ABORTED"
        reason = "horizon_exhausted"
        while not session.ended:
            session.apply(behavior.step(observation))
            session.step()
            if session.ended:
                break
            observation = session.observe()
            if observation is None:
                # Watchdog view: no fresh sample at all is a freshness
                # violation, not a held-observation pass.
                session.stop()
                disposition = "ABORTED"
                reason = "supervisor_state_freshness"
                break
            observations.append(observation)
            if observation.stamp.age_s > scenario.state_max_age_s:
                session.stop()
                disposition = "ABORTED"
                reason = "supervisor_state_freshness"
                break
            if (
                scenario.max_force_n is not None
                and abs(observation.force_z_n) > scenario.max_force_n
            ):
                session.stop()
                disposition = "ABORTED"
                reason = "supervisor_force_limit"
                break
            if scenario.max_tilt_deg is not None and observation.tilt_deg > scenario.max_tilt_deg:
                session.stop()
                disposition = "ABORTED"
                reason = "supervisor_tilt_limit"
                break
            if scenario.deadline_s is not None and observation.stamp.source_s > scenario.deadline_s:
                session.stop()
                disposition = "ABORTED"
                reason = "supervisor_deadline"
                break
            if condition.evaluate(observations):
                session.stop()
                disposition = "COMPLETE"
                reason = None
                break
        wall = time.perf_counter() - t0
        if disposition == "ABORTED" and session.fault is not None:
            reason = f"machine_fault_{session.fault}"
        applied = getattr(session, "applied_command_times", ())
        return SystemRun(
            system=self.name,
            disposition=disposition,
            reason=reason,
            observations=tuple(observations),
            wall_s=wall,
            detail={"applied_command_times": tuple(applied)},
        )


class BareSystem:
    """B1: the task behavior driven directly over the machine session —
    the same task primitive and task success condition without runtime
    semantics (no negotiation, no constraint monitoring, no recovery
    authorization)."""

    def __init__(self, *, evaluate_success: bool = True) -> None:
        self.name = "bare_behavior" if evaluate_success else "direct_minimal"
        self._evaluate_success = evaluate_success

    def run(self, realization: Realization, scenario: Scenario) -> SystemRun:
        machine = realization.machine
        condition = (
            realization.condition_factory(scenario, realization.bundle)
            if self._evaluate_success
            else None
        )
        intent = build_intent(scenario, condition)
        t0 = time.perf_counter()
        caps = machine.capabilities()
        try:
            session = machine.open_session(
                start_position_m=intent.start_position_m,
                horizon_s=caps.execution_horizon_s,
            )
        except Exception as exc:
            return SystemRun(
                system=self.name,
                disposition="ABORTED",
                reason=f"machine_error_{type(exc).__name__}",
                wall_s=time.perf_counter() - t0,
            )
        behavior = realization.behavior_factory()
        observation = session.observe()
        if observation is None:
            return SystemRun(
                system=self.name,
                disposition="ABORTED",
                reason="state_unavailable_at_start",
                wall_s=time.perf_counter() - t0,
            )
        behavior.begin(intent, caps, observation)
        observations: list[MachineObservation] = [observation]
        disposition = "ABORTED"
        reason = "horizon_exhausted"
        while not session.ended:
            session.apply(behavior.step(observation))
            session.step()
            if session.ended:
                break
            observation = session.observe()
            if observation is None:
                # Dropout tick: a bare system holds its last observation.
                observation = observations[-1]
                continue
            observations.append(observation)
            if (
                condition is not None
                and condition.evaluate(observations)
            ):
                session.stop()
                disposition = "COMPLETE"
                reason = None
                break
        wall = time.perf_counter() - t0
        if disposition == "ABORTED" and session.fault is not None:
            reason = f"machine_fault_{session.fault}"
        applied = getattr(session, "applied_command_times", ())
        return SystemRun(
            system=self.name,
            disposition=disposition,
            reason=reason,
            observations=tuple(observations),
            wall_s=wall,
            detail={"applied_command_times": tuple(applied)},
        )


def _g1_incumbent() -> object:
    # Lazy: benchmarks.incumbent imports build_intent from this module.
    from benchmarks.incumbent import G1IncumbentSystem

    return G1IncumbentSystem()


def system_by_name(name: str) -> object:
    """Benchmark-owned system factory. The single mapping from an arm
    name to a system under test, shared by the CLI and the evaluation
    executor so an arm name can never mean two different systems."""
    factories = {
        "direct_minimal": lambda: BareSystem(evaluate_success=False),
        "bare_behavior": BareSystem,
        "competent_supervisor": SupervisedSystem,
        "haplous_mediated": HaplousSystem,
        "g1_incumbent": _g1_incumbent,
    }
    try:
        return factories[name]()
    except KeyError as exc:
        raise ValueError(
            f"unknown system {name!r}; expected one of {sorted(factories)}"
        ) from exc
