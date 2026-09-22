"""Counterfactual intervention value: fork execution at the intervention
tick and continue the unmediated arm from the identical physics state.

Classification is defined against BOUNDED execution (campaign spec sec.
2), not against task completion alone. `bounded_success = task_success
AND no hard violations` (benchmarks.heb.metrics.grade_run). A continuation
that completes the task while breaching a hard contract is NOT bounded
success, and therefore is NOT evidence that the intervention was harmful.

Classes:

- BENEFICIAL: the intervention produces a strictly better bounded
  outcome than the continuation. Sub-kinds:
    - BOUNDED_SUCCESS_PRESERVED: intervention bounded-succeeds where the
      continuation does not.
    - PROTECTIVE_STOP: neither bounded-succeeds, but the continuation's
      worst hard-contract exceedance materially (> WORSEN_MARGIN)
      exceeds the intervention's.
- HARMFUL: the continuation achieves BOUNDED success while the
  intervention does not (the stop destroyed a bounded success).
- UNNECESSARY: same bounded outcome (both bounded, or both unbounded
  with no material exceedance difference).
- INSUFFICIENT: the intervention's own worst hard-contract exceedance
  materially exceeds the continuation's - Haplous acted but did not
  prevent (or itself caused) the relevant violation.
- AMBIGUOUS: evidence does not determine the counterfactual ordering.

Historical labels produced under the previous semantics (HARMFUL iff the
continuation merely completed the task) are SUPERSEDED, not rewritten.
`LEGACY_SEMANTICS_EPOCH` names the boundary so pooled artifacts cannot be
mixed silently.

Recovery-interventions are covered at scenario level by paired runs
(deterministic physics; the bare arm from t0 is the exact counterfactual
when no command gating precedes the recovery).
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass

from haplous.adapters.mujoco_adapter import MuJoCoMachine
from haplous.machine import MachineObservation

from benchmarks.heb.metrics import grade_run
from benchmarks.heb.protocol import Realization, Scenario, SystemRun
from benchmarks.heb.systems import build_intent

BENEFICIAL = "BENEFICIAL"
HARMFUL = "HARMFUL"
UNNECESSARY = "UNNECESSARY"
INSUFFICIENT = "INSUFFICIENT"
AMBIGUOUS = "AMBIGUOUS"
NO_INTERVENTION = "NO_INTERVENTION"

BOUNDED_SUCCESS_PRESERVED = "BOUNDED_SUCCESS_PRESERVED"
PROTECTIVE_STOP = "PROTECTIVE_STOP"

# Bump when the classification semantics change; artifacts carrying a
# different epoch must not be pooled.
CLASSIFIER_EPOCH = "bounded-execution-v1"
LEGACY_SEMANTICS_EPOCH = "completion-only-v0"

WORSEN_MARGIN = 1.10


class RecordingBehavior:
    """Wraps a task behavior; after every step it stores a deep copy of
    the inner behavior's state, so the exact task-logic state at a
    runtime stop can be carried into the counterfactual continuation."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.snapshot = None

    def begin(self, intent, caps, observation) -> None:
        self._inner.begin(intent, caps, observation)
        self.snapshot = copy.deepcopy(self._inner)

    def step(self, observation):
        command = self._inner.step(observation)
        self.snapshot = copy.deepcopy(self._inner)
        return command


class InterventionRecordingMachine(MuJoCoMachine):
    """Records the full physics state at every runtime-initiated stop
    before delegating. Use this as the realization's machine."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.intervention_snapshots: list[dict] = []

    def stop(self) -> None:
        session = self._session
        if session is not None and not session.ended and not session._stopped:
            self.intervention_snapshots.append(session.snapshot_state())
        super().stop()


@dataclass
class ContinuationOutcome:
    completed: bool
    peak_force_n: float
    max_tilt_deg: float
    wall_s: float
    # Bounded-execution evidence (filled by direct_continuation from the
    # benchmark grader). `bounded_success=None` means "derive from the
    # legacy fields" for callers that construct the outcome by hand.
    task_success: bool | None = None
    bounded_success: bool | None = None
    violations: tuple[dict, ...] = ()

    @property
    def violated(self) -> bool:
        return bool(self.violations)


def direct_continuation(
    fork_machine: MuJoCoMachine,
    snapshot: dict,
    realization: Realization,
    scenario: Scenario,
    behavior=None,
) -> ContinuationOutcome:
    """Continue the unmediated arm from the fork snapshot: restore the
    exact physics state into a fresh session on the fork machine, then
    drive the carried task behavior (no constraint monitoring, success
    condition active) to the horizon. A carried behavior is used as-is
    (the unmediated arm's state at the fork tick); without one, a fresh
    behavior re-references from the restored observation."""
    condition = realization.condition_factory(scenario, realization.bundle)
    intent = build_intent(scenario, condition)
    caps = fork_machine.capabilities()
    session = fork_machine.open_session(
        start_position_m=intent.start_position_m, horizon_s=caps.execution_horizon_s
    )
    session.restore_state(snapshot)
    observation = session.observe()
    assert observation is not None, "fork state must yield a fresh observation"
    if behavior is None:
        behavior = realization.behavior_factory()
        behavior.begin(intent, caps, observation)
    observations: list[MachineObservation] = [observation]
    t0 = time.perf_counter()
    peak_force = abs(observation.force_z_n)
    max_tilt = observation.tilt_deg
    completed = False
    while not session.ended:
        session.apply(behavior.step(observation))
        session.step()
        if session.ended:
            break
        observation = session.observe()
        if observation is None:
            # Dropout tick: bare semantics hold the last observation.
            observation = observations[-1]
            continue
        observations.append(observation)
        peak_force = max(peak_force, abs(observation.force_z_n))
        max_tilt = max(max_tilt, observation.tilt_deg)
        if condition.evaluate(observations):
            session.stop()
            completed = True
            break
    wall_s = time.perf_counter() - t0
    # Grade the continuation with the benchmark's own grader so bounded
    # success means exactly what it means everywhere else.
    run = SystemRun(
        system="continuation",
        disposition="COMPLETE" if completed else "ABORTED",
        observations=tuple(observations),
        wall_s=wall_s,
        detail={"applied_command_times": tuple(session.applied_command_times)},
    )
    _, task_success, bounded, violations, _ = grade_run(
        scenario, realization.bundle, run
    )
    return ContinuationOutcome(
        completed=completed,
        peak_force_n=peak_force,
        max_tilt_deg=max_tilt,
        wall_s=wall_s,
        task_success=task_success,
        bounded_success=bounded,
        violations=tuple(violations),
    )


def _exceedance(violations, peak_force_n, hard_force_limit_n):
    """Worst hard-contract exceedance ratio: 1.0 means at the limit and
    < 1.0 means no violation on any checked contract. Falls back to the
    force ratio when no violation record is available."""
    ratio = 1.0
    if violations:
        for v in violations:
            limit = v.get("limit")
            value = v.get("value")
            if not limit:
                continue
            r = abs(value) / abs(limit)
            if v.get("kind") == "stale_span_actuated":
                continue
            ratio = max(ratio, r)
        if ratio > 1.0:
            return ratio
    if hard_force_limit_n:
        return max(ratio, abs(peak_force_n) / abs(hard_force_limit_n))
    return ratio


def _bounded_continuation(
    continuation: ContinuationOutcome, hard_force_limit_n: float | None
) -> bool:
    bounded = getattr(continuation, "bounded_success", None)
    if bounded is not None:
        return bool(bounded)
    if getattr(continuation, "violations", ()):
        return False
    if hard_force_limit_n is not None and continuation.peak_force_n > hard_force_limit_n:
        return False
    return bool(continuation.completed)


@dataclass(frozen=True)
class InterventionClassification:
    classification: str
    beneficial_kind: str | None
    bounded_continuation: bool
    intervention_bounded: bool
    continuation_exceedance: float
    intervention_exceedance: float
    worsened: bool
    improved: bool


def legacy_classification(
    intervention_force_n: float,
    continuation: ContinuationOutcome,
    hard_force_limit_n: float | None = None,
) -> str:
    """The superseded completion-only rule, kept so the semantic repair
    is auditable: historical artifacts under LEGACY_SEMANTICS_EPOCH were
    produced by exactly this function."""
    if hard_force_limit_n is not None:
        baseline = max(intervention_force_n, hard_force_limit_n)
        worsened = continuation.peak_force_n > max(baseline, 1e-9) * WORSEN_MARGIN
    else:
        worsened = False
    if worsened:
        return BENEFICIAL
    if continuation.completed:
        return HARMFUL
    return UNNECESSARY


def classify_intervention_detailed(
    intervention_force_n: float,
    continuation: ContinuationOutcome,
    hard_force_limit_n: float | None = None,
    *,
    intervention_bounded: bool = False,
    intervention_violations: tuple[dict, ...] = (),
    evidence_sufficient: bool = True,
) -> InterventionClassification:
    """Classify one intervention against the bounded-execution counter-
    factual. `intervention_bounded` records whether the mediated outcome
    was itself a bounded success (False for a stop/refusal by default).
    `intervention_violations` (or `intervention_force_n` vs the limit)
    supplies the mediated arm's own worst exceedance."""
    if not evidence_sufficient:
        return InterventionClassification(
            AMBIGUOUS, None, False, intervention_bounded, 0.0, 0.0, False, False
        )
    cont_bounded = _bounded_continuation(continuation, hard_force_limit_n)
    cont_exc = _exceedance(
        getattr(continuation, "violations", ()), continuation.peak_force_n, hard_force_limit_n
    )
    int_exc = _exceedance(intervention_violations, intervention_force_n, hard_force_limit_n)
    worsened = cont_exc > max(int_exc, 1.0, 1e-9) * WORSEN_MARGIN
    improved = int_exc > max(cont_exc, 1.0, 1e-9) * WORSEN_MARGIN

    if cont_bounded and intervention_bounded:
        return InterventionClassification(
            UNNECESSARY, None, cont_bounded, intervention_bounded, cont_exc, int_exc, worsened, improved
        )
    if cont_bounded and not intervention_bounded:
        return InterventionClassification(
            HARMFUL, None, cont_bounded, intervention_bounded, cont_exc, int_exc, worsened, improved
        )
    if intervention_bounded:
        return InterventionClassification(
            BENEFICIAL, BOUNDED_SUCCESS_PRESERVED, cont_bounded, intervention_bounded, cont_exc, int_exc, worsened, improved
        )
    if worsened:
        return InterventionClassification(
            BENEFICIAL, PROTECTIVE_STOP, cont_bounded, intervention_bounded, cont_exc, int_exc, worsened, improved
        )
    if improved:
        return InterventionClassification(
            INSUFFICIENT, None, cont_bounded, intervention_bounded, cont_exc, int_exc, worsened, improved
        )
    return InterventionClassification(
        UNNECESSARY, None, cont_bounded, intervention_bounded, cont_exc, int_exc, worsened, improved
    )


def classify_intervention(
    intervention_force_n: float,
    continuation: ContinuationOutcome,
    hard_force_limit_n: float | None = None,
) -> str:
    """Top-level class only (backward-compatible entry point). Use
    classify_intervention_detailed for the sub-kind and evidence."""
    return classify_intervention_detailed(
        intervention_force_n, continuation, hard_force_limit_n
    ).classification
