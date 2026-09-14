"""Constraint model: each relevant constraint exposes current value, limit,
margin, and status. Constraints are built from intent requirements so their
hardness is the requirement's hardness."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from haplous.capabilities import MachineCapabilities, SensingChannel
from haplous.intent import ExecutionIntent, RequirementKind
from haplous.machine import MachineObservation


@dataclass(frozen=True)
class ConstraintStatus:
    code: str
    value: float
    limit: float
    margin: float
    breached: bool
    hard: bool


@dataclass(frozen=True)
class Constraint:
    """measure returns the monitored magnitude; the constraint is breached
    when it exceeds limit."""

    code: str
    limit: float
    hard: bool
    measure: Callable[[MachineObservation], float]

    def evaluate(self, obs: MachineObservation) -> ConstraintStatus:
        value = self.measure(obs)
        margin = self.limit - value
        return ConstraintStatus(
            code=self.code,
            value=value,
            limit=self.limit,
            margin=margin,
            breached=value > self.limit,
            hard=self.hard,
        )


def build_constraints(
    intent: ExecutionIntent, caps: MachineCapabilities | None = None
) -> tuple[Constraint, ...]:
    constraints: list[Constraint] = []
    for req in intent.requirements:
        if req.kind is RequirementKind.MAX_PROCESS_FORCE_N:
            limit = req.limit
            if (
                caps is not None
                and SensingChannel.FORCE_Z in caps.sensing
                and limit > caps.max_sensed_force_n
            ):
                # Above the sensor ceiling a breach is undetectable
                # (saturated readings cannot distinguish 120 N from
                # 200 N), so enforcement clamps to the sensed ceiling:
                # stricter than requested, and actually verifiable.
                limit = caps.max_sensed_force_n
            constraints.append(
                Constraint(
                    code="process_force",
                    limit=limit,
                    hard=req.hard,
                    measure=lambda obs: abs(obs.force_z_n),
                )
            )
        elif req.kind is RequirementKind.MIN_PROCESS_FORCE_N:
            # Breached when the measured magnitude falls BELOW the floor:
            # the constraint model is one-sided (breach when measure >
            # limit), so the contact-force floor is monitored as the
            # negated magnitude against the negated limit.
            constraints.append(
                Constraint(
                    code="process_force_floor",
                    limit=-req.limit,
                    hard=req.hard,
                    measure=lambda obs: -abs(obs.force_z_n),
                )
            )
        elif req.kind is RequirementKind.MAX_TILT_DEG:
            constraints.append(
                Constraint(
                    code="tilt",
                    limit=req.limit,
                    hard=req.hard,
                    measure=lambda obs: obs.tilt_deg,
                )
            )
        elif req.kind is RequirementKind.STATE_MAX_AGE_S:
            constraints.append(
                Constraint(
                    code="state_freshness",
                    limit=req.limit,
                    hard=req.hard,
                    measure=lambda obs: obs.stamp.age_s,
                )
            )
    # POSITION_TOLERANCE_M is deliberately NOT a live path constraint: a
    # tolerance on the reached state is enforced by the behavior's success
    # condition at completion, not against every instant of an approach
    # motion that legitimately travels far from the goal.
    return tuple(constraints)
