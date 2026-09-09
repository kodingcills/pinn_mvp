"""The insertion behavior: the first validated execution intent and the
MuJoCo machine that can execute it. All task-specific knowledge lives in
this behavior layer, never in haplous core."""
from __future__ import annotations

import math
from typing import Optional, Sequence

from pegsim.rollout import (
    TARGET_DEPTH_MARGIN_M,
    VALIDATED_LOOSE_V1,
    ValidatedConfig,
)

from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig
from haplous.intent import (
    ExecutionIntent,
    RecoveryPolicy,
    Requirement,
    RequirementKind,
    SuccessCondition,
)
from haplous.machine import MachineObservation


def insertion_intent(
    task,
    *,
    initial_offset_m: float = 0.0,
    initial_bearing_rad: float = 0.0,
    position_tolerance_m: Optional[float] = None,
    target_depth_m: Optional[float] = None,
    max_force_n: float = 25.0,
    max_tilt_deg: float = 10.0,
    deadline_s: Optional[float] = None,
    state_max_age_s: float = 0.02,
    persistence_s: float = 0.1,
    recovery: Optional[RecoveryPolicy] = None,
) -> ExecutionIntent:
    """Build the insertion intent from semantic requirements. goal_position
    is the COMMANDED nominal bore location (the CAD datum the controller is
    given); it is task semantics, never a measured truth."""
    if position_tolerance_m is None:
        position_tolerance_m = task.radial_clearance + 12e-6
    if target_depth_m is None:
        target_depth_m = task.plate_thickness - TARGET_DEPTH_MARGIN_M
    if deadline_s is None:
        deadline_s = VALIDATED_LOOSE_V1.max_seconds
    goal = (0.0, 0.0, 0.0)
    start = (
        initial_offset_m * math.cos(initial_bearing_rad),
        initial_offset_m * math.sin(initial_bearing_rad),
        0.0,
    )
    requirements = (
        Requirement(
            RequirementKind.MAX_PROCESS_FORCE_N, hard=True, limit=max_force_n
        ),
        Requirement(RequirementKind.MAX_TILT_DEG, hard=True, limit=max_tilt_deg),
        Requirement(
            RequirementKind.POSITION_TOLERANCE_M,
            hard=True,
            limit=position_tolerance_m,
        ),
        Requirement(
            RequirementKind.STATE_MAX_AGE_S, hard=True, limit=state_max_age_s
        ),
        Requirement(RequirementKind.DEADLINE_S, hard=True, limit=deadline_s),
        Requirement(
            RequirementKind.INITIAL_OFFSET_M, hard=True, limit=initial_offset_m
        ),
    )
    condition = SuccessCondition(
        "seated",
        _make_seated(
            goal, position_tolerance_m, target_depth_m, max_force_n, persistence_s
        ),
    )
    return ExecutionIntent(
        behavior="peg_insertion",
        goal_description="seat the peg in the bore at the commanded datum",
        goal_position_m=goal,
        start_position_m=start,
        requirements=requirements,
        success_condition=condition,
        recovery_policy=recovery,
    )


def _make_seated(
    goal: tuple[float, float, float],
    tolerance_m: float,
    target_depth_m: float,
    max_force_n: float,
    persistence_s: float,
):
    def evaluate(observations: Sequence[MachineObservation]) -> bool:
        if not observations:
            return False
        end_t = observations[-1].stamp.source_s
        window = [o for o in observations if o.stamp.source_s > end_t - persistence_s]
        if not window:
            return False
        if end_t - window[0].stamp.source_s < 0.9 * persistence_s:
            return False
        seated = all(
            o.pose_m[2] <= -target_depth_m and abs(o.force_z_n) <= max_force_n
            for o in window
        )
        if not seated:
            return False
        last = observations[-1]
        lateral = math.hypot(last.pose_m[0] - goal[0], last.pose_m[1] - goal[1])
        return lateral <= tolerance_m

    return evaluate


def insertion_machine(
    bundle: ValidatedConfig = VALIDATED_LOOSE_V1,
    **config_kwargs,
) -> MuJoCoMachine:
    return MuJoCoMachine(
        bundle, config=MuJoCoMachineConfig(**config_kwargs)
    )
