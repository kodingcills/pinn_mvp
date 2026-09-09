"""The Haplous runtime spine: submit intent -> negotiate feasibility ->
authorize execution on fresh state -> monitor constraints over the observed
stream -> recover / degrade / refuse / abort / complete -> evidence."""
from __future__ import annotations

from typing import Optional

from haplous.capabilities import FeasibilityResult, negotiate
from haplous.constraints import Constraint, build_constraints
from haplous.evidence import ExecutionEvidence
from haplous.intent import ExecutionIntent
from haplous.machine import Machine, MachineObservation, MotionRecord
from haplous.state import ExecutionState, ExecutionStateTracker

_GAP_TOLERANCE_FACTOR = 1.5


class HaplousRuntime:
    def __init__(self, machine: Machine) -> None:
        self._machine = machine
        self._tracker = ExecutionStateTracker()
        self._evidence = ExecutionEvidence()
        self._archived: list[ExecutionEvidence] = []
        self._intent: Optional[ExecutionIntent] = None
        self._feasibility: Optional[FeasibilityResult] = None
        self._recoveries_used = 0
        self._last_motion: tuple[MachineObservation, ...] = ()

    @property
    def state(self) -> ExecutionState:
        return self._tracker.state

    @property
    def evidence(self) -> ExecutionEvidence:
        return self._evidence

    @property
    def archived_evidence(self) -> tuple[ExecutionEvidence, ...]:
        return tuple(self._archived)

    def submit(self, intent: ExecutionIntent) -> FeasibilityResult:
        if self._tracker.state is not ExecutionState.IDLE:
            raise RuntimeError(
                f"runtime is {self._tracker.state.value}; reset() before "
                "submitting a new intent"
            )
        self._tracker.transition(ExecutionState.VALIDATING)
        self._intent = intent
        caps = self._machine.capabilities()
        self._evidence.intent_behavior = intent.behavior
        self._evidence.intent_summary = {
            "behavior": intent.behavior,
            "goal_description": intent.goal_description,
            "goal_position_m": list(intent.goal_position_m),
            "start_position_m": list(intent.start_position_m),
            "requirements": [
                {
                    "kind": r.kind.value,
                    "hard": r.hard,
                    "limit": r.limit,
                }
                for r in intent.requirements
            ],
            "has_recovery_policy": intent.recovery_policy is not None,
        }
        feasibility = negotiate(intent, caps)
        self._feasibility = feasibility
        self._evidence.append(
            "feasibility",
            t_s=0.0,
            outcome=feasibility.outcome,
            reasons=[
                {"code": r.code, "detail": r.detail} for r in feasibility.reasons
            ],
            capabilities={
                "command_modes": sorted(m.value for m in caps.command_modes),
                "sensing": sorted(s.value for s in caps.sensing),
                "max_sensed_force_n": caps.max_sensed_force_n,
                "position_resolution_m": caps.position_resolution_m,
                "state_period_s": caps.state_period_s,
                "state_max_age_s": caps.state_max_age_s,
                "max_initial_offset_m": caps.max_initial_offset_m,
                "execution_horizon_s": caps.execution_horizon_s,
                "supports_stop": caps.supports_stop,
            },
        )
        if feasibility.outcome == "REFUSED":
            self._tracker.transition(ExecutionState.REFUSED)
        else:
            if feasibility.outcome == "DEGRADED":
                self._evidence.append(
                    "degraded_requirements",
                    t_s=0.0,
                    reasons=[
                        {"code": r.code, "detail": r.detail}
                        for r in feasibility.reasons
                    ],
                )
            self._tracker.transition(ExecutionState.READY)
        return feasibility

    def execute(self) -> ExecutionEvidence:
        if self._tracker.state is not ExecutionState.READY:
            raise RuntimeError(
                f"execution requires READY, runtime is "
                f"{self._tracker.state.value}"
            )
        caps = self._machine.capabilities()
        assert self._intent is not None
        observation = self._machine.observe()
        if (
            observation is None
            or observation.stamp.age_s > caps.state_max_age_s
        ):
            self._evidence.append(
                "pre_execution_state_invalid",
                t_s=0.0,
                reason="state_too_stale",
                observed_age_s=(
                    observation.stamp.age_s if observation is not None else None
                ),
                state_max_age_s=caps.state_max_age_s,
            )
            self._tracker.transition(ExecutionState.REFUSED)
            self._evidence.append("outcome", t_s=0.0, result="REFUSED")
            return self._evidence
        self._evidence.append(
            "execution_authorized",
            t_s=observation.stamp.source_s,
            observation_age_s=observation.stamp.age_s,
        )
        self._tracker.transition(ExecutionState.EXECUTING)
        self._execute_monitored(caps.state_period_s, caps.state_max_age_s)
        return self._evidence

    def reset(self) -> None:
        self._archived.append(self._evidence)
        self._evidence = ExecutionEvidence()
        self._intent = None
        self._feasibility = None
        self._recoveries_used = 0
        self._tracker.reset()

    # -- monitored execution ------------------------------------------------

    def _execute_monitored(self, state_period_s: float, state_max_age_s: float) -> None:
        assert self._intent is not None
        policy = self._intent.recovery_policy
        max_attempts = 1 + (policy.max_recovery_actions if policy else 0)
        for attempt in range(max_attempts):
            if self._tracker.state is ExecutionState.RECOVERING:
                self._tracker.transition(ExecutionState.EXECUTING)
            breach = self._run_and_replay(state_period_s, state_max_age_s)
            if breach is None:
                self._finish_on_success_condition()
                return
            code, hard = breach
            if (
                hard
                and policy is not None
                and code in policy.permitted_conditions
                and self._recoveries_used < policy.max_recovery_actions
            ):
                self._recoveries_used += 1
                self._evidence.append(
                    "recovery_decision",
                    t_s=0.0,
                    condition=code,
                    action=policy.action,
                    attempt=self._recoveries_used,
                )
                self._tracker.transition(ExecutionState.RECOVERING)
                continue
            self._machine.stop()
            reason = (
                f"hard_constraint_{code}"
                if hard
                else f"soft_constraint_{code}"
            )
            if (
                policy is not None
                and code not in policy.permitted_conditions
            ):
                reason = f"condition_not_permitted_{code}"
            elif policy is not None:
                reason = "recovery_budget_exhausted"
            self._tracker.transition(ExecutionState.ABORTED)
            self._evidence.append(
                "outcome", t_s=0.0, result="ABORTED", reason=reason
            )
            return
        self._machine.stop()
        self._tracker.transition(ExecutionState.ABORTED)
        self._evidence.append(
            "outcome",
            t_s=0.0,
            result="ABORTED",
            reason="recovery_budget_exhausted",
        )

    def _run_and_replay(
        self, state_period_s: float, state_max_age_s: float
    ) -> Optional[tuple[str, bool]]:
        """Execute one motion and replay its observation stream through the
        constraint monitors. Returns (condition_code, hard) for the first
        hard breach (or an early motion termination), or None when the motion
        ran clean to its end."""
        assert self._intent is not None
        caps = self._machine.capabilities()
        constraints: tuple[Constraint, ...] = build_constraints(self._intent)
        motion: MotionRecord = self._machine.execute(
            start_position_m=self._intent.start_position_m,
            horizon_s=caps.execution_horizon_s,
        )
        self._last_motion = motion.observations
        self._evidence.append(
            "motion_started",
            t_s=(
                motion.observations[0].stamp.source_s
                if motion.observations
                else 0.0
            ),
            n_observations=len(motion.observations),
        )
        if motion.ended_early:
            self._evidence.append(
                "motion_ended_early", t_s=0.0, reason=motion.end_reason
            )
            return (motion.end_reason or "motion_ended_early", True)
        if not motion.observations:
            self._evidence.append("motion_empty", t_s=0.0)
            return ("motion_empty", True)
        previous_source: Optional[float] = None
        for observation in motion.observations:
            if previous_source is not None:
                gap_s = observation.stamp.source_s - previous_source
                if gap_s > state_period_s * _GAP_TOLERANCE_FACTOR:
                    self._evidence.append(
                        "measurement_loss",
                        t_s=observation.stamp.source_s,
                        gap_s=gap_s,
                        state_max_age_s=state_max_age_s,
                    )
                    if gap_s > state_max_age_s:
                        return ("state_freshness", True)
                    if self._tracker.state is ExecutionState.EXECUTING:
                        self._tracker.transition(ExecutionState.DEGRADED)
                        self._evidence.append(
                            "state_transition", t_s=observation.stamp.source_s,
                            to="degraded",
                        )
            previous_source = observation.stamp.source_s
            hard_breach: Optional[tuple[str, bool]] = None
            soft_breached = False
            for constraint in constraints:
                status = constraint.evaluate(observation)
                if not status.breached:
                    continue
                if status.hard:
                    hard_breach = (status.code, True)
                    self._evidence.append(
                        "constraint_violation",
                        t_s=observation.stamp.source_s,
                        code=status.code,
                        value=status.value,
                        limit=status.limit,
                        hard=True,
                    )
                    break
                soft_breached = True
                if self._tracker.state is not ExecutionState.CONSTRAINED:
                    self._evidence.append(
                        "constraint_violation",
                        t_s=observation.stamp.source_s,
                        code=status.code,
                        value=status.value,
                        limit=status.limit,
                        hard=False,
                    )
            if hard_breach is not None:
                self._evidence.append(
                    "state_transition",
                    t_s=observation.stamp.source_s, to="breach_detected",
                )
                return hard_breach
            if soft_breached:
                if self._tracker.state is ExecutionState.EXECUTING:
                    self._tracker.transition(ExecutionState.CONSTRAINED)
                    self._evidence.append(
                        "state_transition",
                        t_s=observation.stamp.source_s, to="constrained",
                    )
            elif self._tracker.state is ExecutionState.CONSTRAINED:
                self._tracker.transition(ExecutionState.EXECUTING)
                self._evidence.append(
                    "state_transition",
                    t_s=observation.stamp.source_s, to="executing",
                )
            elif self._tracker.state is ExecutionState.DEGRADED:
                self._tracker.transition(ExecutionState.EXECUTING)
                self._evidence.append(
                    "state_transition",
                    t_s=observation.stamp.source_s, to="executing",
                )
        self._evidence.append(
            "motion_completed",
            t_s=motion.observations[-1].stamp.source_s,
            n_observations=len(motion.observations),
        )
        return None

    def _finish_on_success_condition(self) -> None:
        assert self._intent is not None and self._intent.success_condition is not None
        condition = self._intent.success_condition
        observations = self._last_motion
        success = condition.evaluate(observations)
        if success:
            self._tracker.transition(ExecutionState.COMPLETE)
            self._evidence.append(
                "outcome",
                t_s=(
                    observations[-1].stamp.source_s if observations else 0.0
                ),
                result="COMPLETE",
                success_condition=condition.name,
            )
        else:
            self._tracker.transition(ExecutionState.ABORTED)
            self._evidence.append(
                "outcome",
                t_s=0.0,
                result="ABORTED",
                reason="success_condition_unmet",
                success_condition=condition.name,
            )
