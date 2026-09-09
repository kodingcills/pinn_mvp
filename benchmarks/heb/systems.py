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
    behavior_name: str,
    success_condition: SuccessCondition,
) -> ExecutionIntent:
    """Scenario requirements -> ExecutionIntent. Requirements stated as
    None are omitted. The success condition is task-family truth supplied
    by the task layer, identical for every system."""
    requirements: list[Requirement] = []
    if scenario.max_force_n is not None:
        requirements.append(
            Requirement(
                RequirementKind.MAX_PROCESS_FORCE_N,
                hard=True,
                limit=scenario.max_force_n,
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
    requirements.append(
        Requirement(
            RequirementKind.INITIAL_OFFSET_M,
            hard=True,
            limit=scenario.initial_offset_m,
        )
    )
    recovery = None
    if scenario.recovery_permitted_conditions:
        recovery = RecoveryPolicy(
            action="re_reference",
            permitted_conditions=frozenset(scenario.recovery_permitted_conditions),
            max_recovery_actions=scenario.max_recovery_actions,
        )
    start = (
        scenario.initial_offset_m * math.cos(scenario.initial_bearing_rad),
        scenario.initial_offset_m * math.sin(scenario.initial_bearing_rad),
        0.0,
    )
    return ExecutionIntent(
        behavior=behavior_name,
        goal_description=f"HEB scenario {scenario.id}",
        goal_position_m=(0.0, 0.0, 0.0),
        start_position_m=start,
        requirements=tuple(requirements),
        success_condition=success_condition,
        recovery_policy=recovery,
    )


class HaplousSystem:
    """B2: the task behavior executed behind the Haplous runtime."""

    def __init__(
        self,
        behavior_name: str,
        condition_factory,
    ) -> None:
        self.name = "haplous_mediated"
        self._behavior_name = behavior_name
        self._condition_factory = condition_factory

    def run(self, realization: Realization, scenario: Scenario) -> SystemRun:
        condition = self._condition_factory(scenario, realization.bundle)
        intent = build_intent(scenario, self._behavior_name, condition)
        runtime = HaplousRuntime(
            realization.machine,
            behaviors={self._behavior_name: realization.behavior_factory},
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
            )
        evidence = runtime.execute()
        wall = time.perf_counter() - t0
        outcome = [e for e in evidence.events if e.kind == "outcome"][-1]
        return SystemRun(
            system=self.name,
            disposition=outcome.payload["result"],
            reason=outcome.payload.get("reason"),
            observations=realization.machine.last_session_observations,
            wall_s=wall,
            detail={"evidence_events": len(evidence.events)},
        )


class BareSystem:
    """B1: the task behavior driven directly over the machine session —
    the same task primitive and task success condition without runtime
    semantics (no negotiation, no constraint monitoring, no recovery
    authorization)."""

    def __init__(
        self,
        behavior_name: str,
        condition_factory,
        *,
        evaluate_success: bool = True,
    ) -> None:
        self.name = "bare_behavior" if evaluate_success else "direct_minimal"
        self._behavior_name = behavior_name
        self._condition_factory = condition_factory
        self._evaluate_success = evaluate_success

    def run(self, realization: Realization, scenario: Scenario) -> SystemRun:
        machine = realization.machine
        condition = (
            self._condition_factory(scenario, realization.bundle)
            if self._evaluate_success
            else None
        )
        intent = build_intent(scenario, self._behavior_name, condition)
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
        return SystemRun(
            system=self.name,
            disposition=disposition,
            reason=reason,
            observations=tuple(observations),
            wall_s=wall,
        )
