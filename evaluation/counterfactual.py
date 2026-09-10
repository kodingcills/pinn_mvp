"""Counterfactual intervention value: fork execution at the intervention
tick and continue the unmediated arm from the identical physics state.

Preregistered classification (campaign spec sec. 6), operationalized for
stop-interventions (the mediated runtime's hard-constraint stop at the
first sampled breach observation):

- BENEFICIAL: the counterfactual direct continuation records a strictly
  worse breach than the intervention point: post-intervention peak force
  above max(fork-time peak, declared hard force limit) by more than
  WORSEN_MARGIN. Mediation prevented excess violation exposure.
- HARMFUL: the direct continuation completes the task AND does not
  worsen: the stop was a productivity loss, not safety.
- UNNECESSARY: the direct continuation neither completes nor worsens
  (both arms end in bounded failure of the same task).

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

from benchmarks.heb.protocol import Realization, Scenario
from benchmarks.heb.systems import build_intent

BENEFICIAL = "BENEFICIAL"
HARMFUL = "HARMFUL"
UNNECESSARY = "UNNECESSARY"
NO_INTERVENTION = "NO_INTERVENTION"

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
    return ContinuationOutcome(
        completed=completed,
        peak_force_n=peak_force,
        max_tilt_deg=max_tilt,
        wall_s=time.perf_counter() - t0,
    )


def classify_intervention(
    intervention_force_n: float,
    continuation: ContinuationOutcome,
    hard_force_limit_n: float | None = None,
) -> str:
    if hard_force_limit_n is not None:
        baseline = max(intervention_force_n, hard_force_limit_n)
        worsened = continuation.peak_force_n > max(baseline, 1e-9) * WORSEN_MARGIN
    else:
        # No hard force requirement declared: no force violation exists
        # to prevent, so worsening cannot be credited.
        worsened = False
    if worsened:
        return BENEFICIAL
    if continuation.completed:
        return HARMFUL
    return UNNECESSARY
