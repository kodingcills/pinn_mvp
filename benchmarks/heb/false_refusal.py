"""False-refusal counterfactual analysis (campaign spec sec. 4).

For every Haplous refusal where simulation permits meaningful
continuation, execute the exact refused request without negotiation
(bare semantics, success condition and declared contracts active) and
classify:

- TRUE_REFUSAL: the counterfactual execution violates at least one
  declared hard contract (physical limits, deadline, or temporal
  freshness), or cannot complete within the declared contract
  (completion never fires, or fires after a declared deadline).
- FALSE_REFUSAL: the counterfactual execution completes AND satisfies
  all declared hard requirements.
- AMBIGUOUS: the counterfactual cannot establish contract validity
  reliably (reserved for stochastic/fork-invalid cases; deterministic
  single-run classification never forces it).

A lucky unsafe completion is NOT contract satisfaction: physical peaks
are measured against the declared limits, not ignored.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from benchmarks.heb.protocol import Realization, Scenario, SystemRun
from benchmarks.heb.systems import build_intent

TRUE_REFUSAL = "TRUE_REFUSAL"
FALSE_REFUSAL = "FALSE_REFUSAL"
AMBIGUOUS = "AMBIGUOUS"


@dataclass
class RefusalCounterfactual:
    completed: bool
    deadline_met: bool
    freshness_satisfiable: bool
    peak_force_n: float
    max_tilt_deg: float
    force_limit_breached: bool
    tilt_limit_breached: bool
    wall_s: float

    @property
    def satisfies_all_hard_contracts(self) -> bool:
        return (
            self.completed
            and self.deadline_met
            and self.freshness_satisfiable
            and not self.force_limit_breached
            and not self.tilt_limit_breached
        )


def run_refusal_counterfactual(
    fork_machine,
    realization: Realization,
    scenario: Scenario,
) -> RefusalCounterfactual:
    """Execute the exact refused request: bare behavior, no negotiation,
    no supervision, success condition and declared physical limits
    active for measurement."""
    condition = realization.condition_factory(scenario, realization.bundle)
    intent = build_intent(scenario, condition)
    caps = fork_machine.capabilities()
    session = fork_machine.open_session(
        start_position_m=intent.start_position_m, horizon_s=caps.execution_horizon_s
    )
    observation = session.observe()
    assert observation is not None, "counterfactual must start from a fresh observation"
    behavior = realization.behavior_factory()
    behavior.begin(intent, caps, observation)
    observations: list = [observation]
    t0 = time.perf_counter()
    peak_force = abs(observation.force_z_n)
    max_tilt = observation.tilt_deg
    completed = False
    completion_time = None
    while not session.ended:
        session.apply(behavior.step(observation))
        session.step()
        if session.ended:
            break
        observation = session.observe()
        if observation is None:
            observation = observations[-1]
            continue
        observations.append(observation)
        peak_force = max(peak_force, abs(observation.force_z_n))
        max_tilt = max(max_tilt, observation.tilt_deg)
        if condition.evaluate(observations):
            session.stop()
            completed = True
            completion_time = observation.stamp.source_s
            break
    deadline_met = (
        True
        if scenario.deadline_s is None
        else (completion_time is not None and completion_time <= scenario.deadline_s)
    )
    # Persistent temporal contracts: if every delivered observation is
    # staler than the declared hard freshness bound, no execution path
    # can satisfy the contract, whatever the task outcome.
    freshness_satisfiable = scenario.receive_delay_s <= scenario.state_max_age_s
    return RefusalCounterfactual(
        completed=completed,
        deadline_met=deadline_met,
        freshness_satisfiable=freshness_satisfiable,
        peak_force_n=peak_force,
        max_tilt_deg=max_tilt,
        force_limit_breached=(
            scenario.max_force_n is not None and peak_force > scenario.max_force_n
        ),
        tilt_limit_breached=(
            scenario.max_tilt_deg is not None and max_tilt > scenario.max_tilt_deg
        ),
        wall_s=time.perf_counter() - t0,
    )


def classify_refusal(counterfactual: RefusalCounterfactual) -> str:
    if counterfactual.satisfies_all_hard_contracts:
        return FALSE_REFUSAL
    return TRUE_REFUSAL
