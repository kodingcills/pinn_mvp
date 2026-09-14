"""Machine capability model and capability negotiation. A hard requirement
that the machine cannot satisfy produces a structured REFUSED reason; it
never silently degrades."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from haplous.intent import ExecutionIntent, RequirementKind


class CommandMode(Enum):
    CARTESIAN_IMPEDANCE = "cartesian_impedance"
    FORCE_CONTROLLED_DESCENT = "force_controlled_descent"


class SensingChannel(Enum):
    POSE = "pose"
    FORCE_Z = "force_z"
    TILT = "tilt"


@dataclass(frozen=True)
class MachineCapabilities:
    """What the execution substrate can actually provide. The runtime may
    not assume anything that is not stated here."""

    command_modes: frozenset[CommandMode]
    sensing: frozenset[SensingChannel]
    max_sensed_force_n: float
    max_sensed_tilt_deg: float
    position_resolution_m: float
    state_period_s: float
    state_max_age_s: float
    max_initial_offset_m: float
    execution_horizon_s: float
    expected_duration_s: float
    supports_stop: bool


@dataclass(frozen=True)
class RefusalReason:
    code: str
    detail: str


@dataclass(frozen=True)
class FeasibilityResult:
    """SUPPORTED | DEGRADED | REFUSED with structured reasons. DEGRADED is
    reachable when a requirement is enforceable with disclosure: an upper
    bound above the sensor ceiling (enforced at the ceiling), a deadline
    below the nominal expected duration (enforced mid-execution), or a
    soft requirement at a capability margin. REFUSED is reserved for
    requirements that cannot be satisfied or enforced at all."""

    outcome: str
    reasons: tuple[RefusalReason, ...]


def negotiate(intent: ExecutionIntent, caps: MachineCapabilities) -> FeasibilityResult:
    refusals: list[RefusalReason] = []
    degradations: list[RefusalReason] = []

    if intent.success_condition is None:
        refusals.append(
            RefusalReason(
                "missing_success_condition",
                "no declared success condition; completion cannot be "
                "evaluated from observations",
            )
        )
    if not caps.command_modes:
        refusals.append(
            RefusalReason(
                "unsupported_control_mode",
                "the machine offers no supported command mode",
            )
        )

    for req in intent.requirements:
        if req.kind is RequirementKind.MAX_PROCESS_FORCE_N:
            if SensingChannel.FORCE_Z not in caps.sensing:
                refusals.append(
                    RefusalReason(
                        "missing_force_measurement",
                        "force limit required but no axial force sensing",
                    )
                )
            elif req.limit > caps.max_sensed_force_n:
                # An upper bound above the sensor ceiling is enforceable:
                # hold the sensed value at the ceiling (stricter than the
                # requested limit). Refusal would conflate "cannot verify
                # above ceiling" with "cannot honor".
                degradations.append(
                    RefusalReason(
                        "force_requirement_enforced_at_sensor_ceiling",
                        f"force limit {req.limit} N exceeds sensed range "
                        f"{caps.max_sensed_force_n} N; execution enforces "
                        f"at the sensed ceiling {caps.max_sensed_force_n} N "
                        "(stricter than requested)",
                    )
                )
        elif req.kind is RequirementKind.MIN_PROCESS_FORCE_N:
            if SensingChannel.FORCE_Z not in caps.sensing:
                refusals.append(
                    RefusalReason(
                        "missing_force_measurement",
                        "force floor required but no axial force sensing",
                    )
                )
            elif req.limit > caps.max_sensed_force_n:
                if req.hard:
                    refusals.append(
                        RefusalReason(
                            "force_floor_unsensed",
                            f"hard force floor {req.limit} N exceeds sensed "
                            f"range {caps.max_sensed_force_n} N",
                        )
                    )
                else:
                    degradations.append(
                        RefusalReason(
                            "force_floor_unverified",
                            f"soft force floor {req.limit} N exceeds sensed "
                            f"range {caps.max_sensed_force_n} N; it cannot "
                            "be verified during execution",
                        )
                    )
        elif req.kind is RequirementKind.MAX_TILT_DEG:
            if SensingChannel.TILT not in caps.sensing:
                refusals.append(
                    RefusalReason(
                        "missing_tilt_observation",
                        "tilt limit required but no tilt observation",
                    )
                )
            elif req.limit > caps.max_sensed_tilt_deg:
                if req.hard:
                    refusals.append(
                        RefusalReason(
                            "tilt_requirement_unsensed",
                            f"hard tilt limit {req.limit} deg exceeds sensed "
                            f"range {caps.max_sensed_tilt_deg} deg",
                        )
                    )
                else:
                    degradations.append(
                        RefusalReason(
                            "tilt_requirement_unverified",
                            f"soft tilt limit {req.limit} deg exceeds sensed "
                            f"range {caps.max_sensed_tilt_deg} deg; it cannot "
                            "be verified during execution",
                        )
                    )
        elif req.kind is RequirementKind.POSITION_TOLERANCE_M:
            if SensingChannel.POSE not in caps.sensing:
                refusals.append(
                    RefusalReason(
                        "missing_pose_observation",
                        "position tolerance required but no pose observation",
                    )
                )
            elif req.limit < caps.position_resolution_m:
                if req.hard:
                    refusals.append(
                        RefusalReason(
                            "requested_tolerance_unachievable",
                            f"tolerance {req.limit} m below position "
                            f"resolution {caps.position_resolution_m} m",
                        )
                    )
                else:
                    degradations.append(
                        RefusalReason(
                            "tolerance_at_resolution_limit",
                            f"soft tolerance {req.limit} m is at the "
                            f"position resolution {caps.position_resolution_m} m",
                        )
                    )
        elif req.kind is RequirementKind.INITIAL_OFFSET_M:
            if req.limit > caps.max_initial_offset_m:
                if req.hard:
                    refusals.append(
                        RefusalReason(
                            "unreachable_goal",
                            f"stated initial offset {req.limit} m exceeds "
                            f"machine reach {caps.max_initial_offset_m} m",
                        )
                    )
                else:
                    degradations.append(
                        RefusalReason(
                            "goal_near_reach_limit",
                            f"stated initial offset {req.limit} m exceeds "
                            f"machine reach {caps.max_initial_offset_m} m",
                        )
                    )
        elif req.kind is RequirementKind.DEADLINE_S:
            if req.limit < caps.expected_duration_s:
                if req.hard:
                    refusals.append(
                        RefusalReason(
                            "deadline_unachievable",
                            f"deadline {req.limit} s below expected motion "
                            f"duration {caps.expected_duration_s} s",
                        )
                    )
                else:
                    degradations.append(
                        RefusalReason(
                            "deadline_at_risk",
                            f"soft deadline {req.limit} s is below expected "
                            f"duration {caps.expected_duration_s} s",
                        )
                    )
        elif req.kind is RequirementKind.STATE_MAX_AGE_S:
            if req.limit < caps.state_period_s:
                if req.hard:
                    refusals.append(
                        RefusalReason(
                            "state_too_stale",
                            f"required state age {req.limit} s is below the "
                            f"state period {caps.state_period_s} s",
                        )
                    )
                else:
                    degradations.append(
                        RefusalReason(
                            "state_age_marginal",
                            f"soft state-age bound {req.limit} s is at the "
                            f"state period {caps.state_period_s} s",
                        )
                    )

    if refusals:
        return FeasibilityResult("REFUSED", tuple(refusals))
    if degradations:
        return FeasibilityResult("DEGRADED", tuple(degradations))
    return FeasibilityResult("SUPPORTED", ())
