"""The Haplous runtime spine: submit intent -> negotiate feasibility ->
authorize execution on fresh state -> supervise a live session (observe ->
evaluate constraints -> decide -> command -> step) -> recover / degrade /
refuse / abort / complete -> evidence.

Supervision is live: on every tick the runtime observes fresh machine
state, validates it, evaluates constraints, and only then applies the
behavior's command and advances the machine one period. Stop, abort,
degrade, recovery, and refusal can all occur mid-motion, before task
completion."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Mapping, Optional

from haplous.behavior import ExecutionBehavior
from haplous.capabilities import FeasibilityResult, MachineCapabilities, RefusalReason, negotiate
from haplous.constraints import Constraint, build_constraints
from haplous.evidence import ExecutionEvidence
from haplous.intent import ExecutionIntent, RequirementKind
from haplous.machine import Machine, MachineObservation, MachineSession
from haplous.state import ExecutionState, ExecutionStateTracker

if TYPE_CHECKING:
    from haplous.evidence_store import EvidenceSink

_GAP_TOLERANCE_FACTOR = 1.5
_COMMAND_REJECTION_LIMIT = 3


class HaplousRuntime:
    def __init__(
        self,
        machine: Machine,
        behaviors: Optional[Mapping[str, Callable[[], ExecutionBehavior]]] = None,
        evidence_sink: Optional["EvidenceSink"] = None,
        max_archived_evidence: Optional[int] = None,
    ) -> None:
        if max_archived_evidence is not None and evidence_sink is None:
            raise ValueError(
                "max_archived_evidence requires an evidence_sink; "
                "eviction without a sink would silently lose evidence"
            )
        self._machine = machine
        self._behaviors = dict(behaviors or {})
        self._tracker = ExecutionStateTracker()
        self._evidence = ExecutionEvidence()
        self._archived: list[ExecutionEvidence] = []
        self._evidence_sink = evidence_sink
        self._max_archived = max_archived_evidence
        self._archive_seq = 0
        self._flushed = 0
        self._intent: Optional[ExecutionIntent] = None
        self._feasibility: Optional[FeasibilityResult] = None
        self._recoveries_used = 0
        self._recovery_episode_open_t: dict[str, float] = {}

    @property
    def state(self) -> ExecutionState:
        return self._tracker.state

    @property
    def evidence(self) -> ExecutionEvidence:
        return self._evidence

    @property
    def archived_evidence(self) -> tuple[ExecutionEvidence, ...]:
        return tuple(self._archived)

    @property
    def flushed_evidence_count(self) -> int:
        """Terminal evidences evicted from RAM and written to the sink."""
        return self._flushed

    @property
    def evidence_retained(self) -> int:
        """Terminal evidences currently held in RAM (hot archive)."""
        return len(self._archived)

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
        if intent.behavior not in self._behaviors:
            feasibility = FeasibilityResult(
                "REFUSED",
                feasibility.reasons
                + (
                    RefusalReason(
                        "behavior_unavailable",
                        f"no execution behavior registered for "
                        f"'{intent.behavior}'",
                    ),
                ),
            )
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
        assert self._intent is not None
        caps = self._machine.capabilities()
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
        try:
            session = self._machine.open_session(
                start_position_m=self._intent.start_position_m,
                horizon_s=caps.execution_horizon_s,
            )
        except Exception as exc:
            self._machine.stop()
            self._tracker.transition(ExecutionState.EXECUTING)
            self._tracker.transition(ExecutionState.ABORTED)
            self._evidence.append(
                "outcome",
                t_s=0.0,
                result="ABORTED",
                reason=f"machine_error_{type(exc).__name__}",
                detail=str(exc)[:200],
            )
            return self._evidence
        behavior = self._behaviors[self._intent.behavior]()
        self._tracker.transition(ExecutionState.EXECUTING)
        self._execute_live(session, behavior, caps)
        return self._evidence

    def reset(self) -> None:
        self._archive_seq += 1
        self._archived.append(self._evidence)
        if self._max_archived is not None:
            while len(self._archived) > self._max_archived:
                evicted = self._archived.pop(0)
                # Flush before drop: eviction never loses evidence.
                self._evidence_sink.write(evicted, self._archive_seq)
                self._flushed += 1
        self._evidence = ExecutionEvidence()
        self._intent = None
        self._feasibility = None
        self._recoveries_used = 0
        self._recovery_episode_open_t = {}
        self._tracker.reset()

    # -- live supervised execution -------------------------------------------

    def _execute_live(
        self,
        session: MachineSession,
        behavior: ExecutionBehavior,
        caps: MachineCapabilities,
    ) -> None:
        assert self._intent is not None
        intent = self._intent
        policy = intent.recovery_policy
        constraints: tuple[Constraint, ...] = build_constraints(intent, caps)
        deadline_req = next(
            (r for r in intent.requirements if r.kind is RequirementKind.DEADLINE_S),
            None,
        )
        deadline_s = deadline_req.limit if deadline_req is not None else None
        period_s = caps.state_period_s
        state_max_age_s = caps.state_max_age_s
        success = intent.success_condition
        assert success is not None
        window: list[MachineObservation] = []
        window_span_s = 1.5 * success.window_s if success.window_s else None
        last_source: Optional[float] = None
        rejected_streak = 0
        degraded_announced = False
        recovery_episode: set[str] = set()
        breached_codes: set[str] = set()
        # Temporal-integrity hold: authority suspended while state is
        # unobservable or stale, or while the machine rejects commands.
        # The machine compliantly holds its last accepted command; on
        # restoration the fresh observation is revalidated before any new
        # motion. Bounded by caps.max_hold_unobserved_s of accumulated
        # untrusted-state exposure.
        holding = False
        hold_cause: str | None = None
        untrusted_ticks = 0
        max_hold_ticks = (
            int(caps.max_hold_unobserved_s / period_s)
            if period_s > 0
            else 0
        )

        try:
            first = session.observe()
            if session.fault is not None:
                self._abort_machine_fault(session, 0.0)
                return
            if first is None:
                self._machine.stop()
                self._tracker.transition(ExecutionState.ABORTED)
                self._evidence.append(
                    "outcome",
                    t_s=0.0,
                    result="ABORTED",
                    reason="state_unavailable_at_start",
                )
                return
            behavior.begin(intent, caps, first)
            self._evidence.append(
                "motion_started",
                t_s=first.stamp.source_s,
                state_period_s=period_s,
            )
            window.append(first)
            last_source = first.stamp.source_s
            while True:
                obs = session.observe()
                if session.fault is not None:
                    self._abort_machine_fault(session, last_source)
                    return
                if obs is None:
                    # No observation this tick: no fresh state, so no new
                    # command may be authorized. The machine holds its last
                    # accepted command while the runtime waits. This is
                    # temporal uncertainty, not definitive invalidity: the
                    # hold budget bounds the exposure.
                    untrusted_ticks += 1
                    if untrusted_ticks > max_hold_ticks:
                        self._machine.stop()
                        self._tracker.transition(ExecutionState.ABORTED)
                        self._evidence.append(
                            "outcome",
                            t_s=last_source,
                            result="ABORTED",
                            reason="temporal_hold_budget_exhausted",
                            untrusted_ticks=untrusted_ticks,
                        )
                        return
                    if not holding:
                        holding = True
                        self._evidence.append(
                            "temporal_hold_entered",
                            t_s=last_source,
                            cause="observation_loss",
                        )
                    if self._tracker.state is ExecutionState.EXECUTING:
                        self._tracker.transition(ExecutionState.DEGRADED)
                        self._evidence.append(
                            "state_transition",
                            t_s=last_source,
                            to="degraded",
                        )
                        degraded_announced = True
                    session.step()
                    if session.ended:
                        self._finish_on_session_end(session, window)
                        return
                    continue
                gap_s = obs.stamp.source_s - last_source
                last_source = obs.stamp.source_s
                window.append(obs)
                if window_span_s is not None:
                    cutoff = last_source - window_span_s
                    while window and window[0].stamp.source_s < cutoff:
                        window.pop(0)
                if gap_s > period_s * _GAP_TOLERANCE_FACTOR:
                    self._evidence.append(
                        "measurement_loss",
                        t_s=obs.stamp.source_s,
                        gap_s=gap_s,
                        state_max_age_s=state_max_age_s,
                    )
                    if gap_s > state_max_age_s:
                        # Temporal-integrity fault: suspend authority and
                        # hold; revalidation happens on the first fresh
                        # observation. Never a terminal outcome by itself.
                        if not holding:
                            holding = True
                            hold_cause = "freshness_gap"
                            self._evidence.append(
                                "temporal_hold_entered",
                                t_s=obs.stamp.source_s,
                                cause="freshness_gap",
                                gap_s=gap_s,
                            )
                        untrusted_ticks += 1
                        if untrusted_ticks > max_hold_ticks:
                            self._machine.stop()
                            self._tracker.transition(ExecutionState.ABORTED)
                            self._evidence.append(
                                "outcome",
                                t_s=obs.stamp.source_s,
                                result="ABORTED",
                                reason="temporal_hold_budget_exhausted",
                                untrusted_ticks=untrusted_ticks,
                            )
                            return
                        skip_command = True
                    else:
                        skip_command = False
                else:
                    skip_command = False
                if (
                    holding
                    and hold_cause != "command_rejection"
                    and obs.stamp.age_s <= state_max_age_s
                ):
                    # Restoration: the fresh observation below reenters the
                    # normal loop - constraints are re-evaluated on it and
                    # a hard breach aborts exactly as before. A clean
                    # revalidation resumes authority.
                    holding = False
                    hold_cause = None
                    untrusted_ticks = 0
                    self._evidence.append(
                        "temporal_revalidation_resumed",
                        t_s=obs.stamp.source_s,
                        cause="fresh_state",
                    )
                    if self._tracker.state is ExecutionState.DEGRADED:
                        self._tracker.transition(ExecutionState.EXECUTING)
                        self._evidence.append(
                            "state_transition",
                            t_s=obs.stamp.source_s,
                            to="executing",
                        )
                if degraded_announced:
                    degraded_announced = False
                    if self._tracker.state is ExecutionState.DEGRADED:
                        self._tracker.transition(ExecutionState.EXECUTING)
                        self._evidence.append(
                            "state_transition",
                            t_s=obs.stamp.source_s,
                            to="executing",
                        )

                # Constraint evaluation on the freshest observation.
                hard_breach: Optional[tuple[str, bool]] = None
                soft_breached = False
                for constraint in constraints:
                    status = constraint.evaluate(obs)
                    if status.breached:
                        breached_codes.add(status.code)
                    elif (
                        constraint.code in recovery_episode
                        and self._tracker.state is ExecutionState.RECOVERING
                    ):
                        # The recovered condition holds again: the recovery
                        # episode succeeded, execution resumes.
                        recovery_episode.discard(constraint.code)
                        self._recovery_episode_open_t.pop(constraint.code, None)
                        self._tracker.transition(ExecutionState.EXECUTING)
                        self._evidence.append(
                            "state_transition",
                            t_s=obs.stamp.source_s,
                            to="executing",
                        )
                    if not status.breached:
                        continue
                    if status.hard:
                        if status.code == "state_freshness":
                            # Temporal-integrity fault (stale delivery):
                            # suspend authority and hold; the fresh
                            # revalidation below decides resumption.
                            if not holding:
                                holding = True
                                hold_cause = "stale_state"
                                self._evidence.append(
                                    "temporal_hold_entered",
                                    t_s=obs.stamp.source_s,
                                    cause="stale_state",
                                    age_s=obs.stamp.age_s,
                                )
                            untrusted_ticks += 1
                            if untrusted_ticks > max_hold_ticks:
                                self._machine.stop()
                                self._tracker.transition(ExecutionState.ABORTED)
                                self._evidence.append(
                                    "outcome",
                                    t_s=obs.stamp.source_s,
                                    result="ABORTED",
                                    reason="temporal_hold_budget_exhausted",
                                    untrusted_ticks=untrusted_ticks,
                                )
                                return
                            skip_command = True
                            continue
                        if hard_breach is None:
                            hard_breach = (status.code, True)
                            self._evidence.append(
                                "constraint_violation",
                                t_s=obs.stamp.source_s,
                                code=status.code,
                                value=status.value,
                                limit=status.limit,
                                hard=True,
                            )
                    else:
                        soft_breached = True
                        if self._tracker.state is not ExecutionState.CONSTRAINED:
                            self._evidence.append(
                                "constraint_violation",
                                t_s=obs.stamp.source_s,
                                code=status.code,
                                value=status.value,
                                limit=status.limit,
                                hard=False,
                            )
                # A failed recovery attempt is bounded: an episode that
                # outlives its declared settle window aborts instead of
                # continuing to violate.
                if (
                    policy is not None
                    and policy.settle_s > 0.0
                    and recovery_episode
                ):
                    for code in sorted(recovery_episode):
                        open_t = self._recovery_episode_open_t.get(code)
                        if open_t is None:
                            continue
                        if obs.stamp.source_s - open_t > policy.settle_s:
                            self._machine.stop()
                            self._tracker.transition(ExecutionState.ABORTED)
                            self._evidence.append(
                                "outcome",
                                t_s=obs.stamp.source_s,
                                result="ABORTED",
                                reason="recovery_timeout",
                                condition=code,
                            )
                            return
                if hard_breach is not None:
                    terminal, skip = self._handle_breach(
                        hard_breach,
                        policy,
                        behavior,
                        caps,
                        obs,
                        recovery_episode,
                    )
                    if terminal:
                        return
                    skip_command = skip
                if soft_breached:
                    if self._tracker.state is ExecutionState.EXECUTING:
                        self._tracker.transition(ExecutionState.CONSTRAINED)
                        self._evidence.append(
                            "state_transition",
                            t_s=obs.stamp.source_s,
                            to="constrained",
                        )
                elif self._tracker.state is ExecutionState.CONSTRAINED:
                    self._tracker.transition(ExecutionState.EXECUTING)
                    self._evidence.append(
                        "state_transition",
                        t_s=obs.stamp.source_s,
                        to="executing",
                    )

                # Deadline: an expired deadline stops the execution.
                if deadline_s is not None and obs.stamp.source_s > deadline_s:
                    self._machine.stop()
                    self._tracker.transition(ExecutionState.ABORTED)
                    self._evidence.append(
                        "outcome",
                        t_s=obs.stamp.source_s,
                        result="ABORTED",
                        reason="deadline_exceeded",
                    )
                    return

                # Declarative completion: observations only, never machine
                # claims. Evaluated on the trailing window the condition
                # declared. Completion is an action like any other: it can
                # only be authorized from fresh state, and never while a
                # recovery episode is still open (the execution resumes
                # first and completes on a clean tick).
                if (
                    self._tracker.state is not ExecutionState.RECOVERING
                    and obs.stamp.age_s <= state_max_age_s
                    and success.evaluate(
                        window if window_span_s is None else list(window)
                    )
                ):
                    self._machine.stop()
                    self._tracker.transition(ExecutionState.COMPLETE)
                    self._evidence.append(
                        "outcome",
                        t_s=obs.stamp.source_s,
                        result="COMPLETE",
                        success_condition=success.name,
                    )
                    return

                # Machine health is checked live, but a health claim can
                # never complete or succeed — only degrade or abort.
                health = self._machine.health()
                if str(health.get("status", "ok")) != "ok":
                    self._machine.stop()
                    self._tracker.transition(ExecutionState.ABORTED)
                    self._evidence.append(
                        "outcome",
                        t_s=obs.stamp.source_s,
                        result="ABORTED",
                        reason="machine_unhealthy",
                    )
                    return

                # Behavior decision on fresh state, then authorize.
                if not skip_command:
                    command = behavior.step(obs)
                    outcome = session.apply(command)
                    if outcome.accepted:
                        if holding and rejected_streak > 0:
                            # Restoration after command rejection: the
                            # machine accepts again; this tick's
                            # constraints were evaluated on fresh state
                            # above, so revalidation already passed.
                            holding = False
                            hold_cause = None
                            untrusted_ticks = 0
                            self._evidence.append(
                                "temporal_revalidation_resumed",
                                t_s=obs.stamp.source_s,
                                cause="command_accepted",
                            )
                            if self._tracker.state is ExecutionState.DEGRADED:
                                self._tracker.transition(ExecutionState.EXECUTING)
                                self._evidence.append(
                                    "state_transition",
                                    t_s=obs.stamp.source_s,
                                    to="executing",
                                )
                        rejected_streak = 0
                    else:
                        rejected_streak += 1
                        self._evidence.append(
                            "command_rejected",
                            t_s=obs.stamp.source_s,
                            reason=outcome.reason,
                            streak=rejected_streak,
                        )
                        if rejected_streak >= _COMMAND_REJECTION_LIMIT:
                            # Command rejection suspends NEW authority but
                            # the machine holds; attempts continue so
                            # restoration is discoverable. The hold budget
                            # bounds the exposure.
                            if not holding:
                                holding = True
                                hold_cause = "command_rejection"
                                self._evidence.append(
                                    "temporal_hold_entered",
                                    t_s=obs.stamp.source_s,
                                    cause="command_rejection",
                                )
                            untrusted_ticks += 1
                            if untrusted_ticks > max_hold_ticks:
                                self._machine.stop()
                                self._tracker.transition(ExecutionState.ABORTED)
                                self._evidence.append(
                                    "outcome",
                                    t_s=obs.stamp.source_s,
                                    result="ABORTED",
                                    reason="temporal_hold_budget_exhausted",
                                    untrusted_ticks=untrusted_ticks,
                                )
                                return
                session.step()
                if session.ended:
                    if session.fault is not None:
                        self._abort_machine_fault(session, last_source)
                    else:
                        self._finish_on_session_end(session, window)
                    return
        except Exception as exc:
            self._machine.stop()
            self._tracker.transition(ExecutionState.ABORTED)
            self._evidence.append(
                "outcome",
                t_s=0.0,
                result="ABORTED",
                reason=f"machine_error_{type(exc).__name__}",
                detail=str(exc)[:200],
            )

    def _abort_machine_fault(
        self, session: MachineSession, last_source: float
    ) -> None:
        self._machine.stop()
        self._tracker.transition(ExecutionState.ABORTED)
        self._evidence.append(
            "outcome",
            t_s=last_source,
            result="ABORTED",
            reason=f"machine_fault_{session.fault}",
        )

    def _handle_breach(
        self,
        breach: tuple[str, bool],
        policy,
        behavior: ExecutionBehavior,
        caps: MachineCapabilities,
        obs: MachineObservation,
        recovery_episode: set[str],
    ) -> tuple[bool, bool]:
        """Handle one hard breach. Returns (terminal, skip_command).

        Recovery is episode-based: the first breach of a condition opens a
        recovery episode (one budgeted re-reference of the behavior) and
        the runtime stays in RECOVERING while the condition persists,
        letting the re-referenced behavior work the problem without
        draining the action budget per tick. The episode closes when the
        condition's constraint evaluates clean; a later breach of the same
        condition opens a NEW episode and consumes budget again."""
        code, _hard = breach
        in_episode = code in recovery_episode
        permitted = (
            policy is not None and code in policy.permitted_conditions
        )
        if permitted and (
            in_episode or self._recoveries_used < policy.max_recovery_actions
        ):
            if not in_episode:
                recovery_episode.add(code)
                self._recoveries_used += 1
                self._recovery_episode_open_t[code] = obs.stamp.source_s
                self._evidence.append(
                    "recovery_decision",
                    t_s=obs.stamp.source_s,
                    condition=code,
                    action=policy.action,
                    attempt=self._recoveries_used,
                )
                if self._tracker.state is not ExecutionState.RECOVERING:
                    self._tracker.transition(ExecutionState.RECOVERING)
                    self._evidence.append(
                        "state_transition",
                        t_s=obs.stamp.source_s,
                        to="recovering",
                    )
                # The recovery action: re-reference the behavior from the
                # live observation (never a blind replay). Execution
                # resumes next tick with commands flowing from the
                # re-referenced behavior.
                behavior.begin(self._intent, caps, obs)
                return False, True
            return False, code == "state_freshness"
        self._machine.stop()
        reason = f"hard_constraint_{code}"
        if (
            policy is not None
            and code not in policy.permitted_conditions
        ):
            reason = f"condition_not_permitted_{code}"
        elif policy is not None:
            reason = "recovery_budget_exhausted"
        self._tracker.transition(ExecutionState.ABORTED)
        self._evidence.append(
            "outcome", t_s=obs.stamp.source_s, result="ABORTED", reason=reason
        )
        return True, True

    def _finish_on_session_end(
        self, session: MachineSession, window: list[MachineObservation]
    ) -> None:
        assert self._intent is not None and self._intent.success_condition
        success = self._intent.success_condition
        if window and success.evaluate(list(window)):
            self._machine.stop()
            self._tracker.transition(ExecutionState.COMPLETE)
            self._evidence.append(
                "outcome",
                t_s=window[-1].stamp.source_s,
                result="COMPLETE",
                success_condition=success.name,
            )
        else:
            self._machine.stop()
            self._tracker.transition(ExecutionState.ABORTED)
            self._evidence.append(
                "outcome",
                t_s=window[-1].stamp.source_s if window else 0.0,
                result="ABORTED",
                reason="success_condition_unmet",
                success_condition=success.name,
            )
