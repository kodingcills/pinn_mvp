"""The insertion behavior: the first validated execution intent and the
MuJoCo machine that can execute it. All task-specific knowledge lives in
this behavior layer, never in haplous core.

InsertionExecutionBehavior is the live-execution port of the research
substrate's per-control-tick task logic (pegsim.rollout.run_episode):
capture snap, flat-table baseline tracker, the single commit gate with
hysteresis, force-continuous stiffness handoff, rate-limited force/depth
ramps, and the setpoint leash. The machine boundary only ever sees the
resulting servo command (target pose + phase axial stiffness); every task
decision is made here from observations, never from simulator ground
truth."""
from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np

from pegsim.rollout import (
    COMMIT_RELEASE_FRACTION,
    TARGET_DEPTH_MARGIN_M,
    VALIDATED_LOOSE_V1,
    ValidatedConfig,
    Z_FLAT_TAU_S,
)
from pegsim.search import ArchimedeanSpiral

from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig
from haplous.intent import (
    ExecutionIntent,
    RecoveryPolicy,
    Requirement,
    RequirementKind,
    SuccessCondition,
)
from haplous.machine import MachineObservation, ServoCommand

_SEATED_Z_SPAN_M = 10e-6


def seated_condition(
    goal: tuple[float, float, float],
    tolerance_m: float,
    target_depth_m: float,
    max_force_n: float,
    persistence_s: float,
) -> SuccessCondition:
    """The insertion task's completion truth: the tip must be stationary at
    seating depth with contact force inside the stated limit, laterally
    within tolerance of the commanded datum, over a persistence window.
    Shared by the intent builder and the Haplous Execution Benchmark, so
    every system is graded against the identical task truth."""
    return SuccessCondition(
        "seated",
        _make_seated(
            goal, tolerance_m, target_depth_m, max_force_n, persistence_s
        ),
        window_s=2.0 * persistence_s,
    )


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
    require_success_condition: bool = True,
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
    condition = (
        seated_condition(
            goal,
            position_tolerance_m,
            target_depth_m,
            max_force_n,
            persistence_s,
        )
        if require_success_condition
        else None
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
    def evaluate(observations) -> bool:
        if not observations:
            return False
        end_t = observations[-1].stamp.source_s
        window = [o for o in observations if o.stamp.source_s > end_t - persistence_s]
        if not window:
            return False
        if end_t - window[0].stamp.source_s < 0.9 * persistence_s:
            return False
        zs = [o.pose_m[2] for o in window]
        seated = all(
            z <= -target_depth_m
            and abs(o.force_z_n) <= max_force_n
            for z, o in zip(zs, window)
        )
        if not seated:
            return False
        # A peg still coasting down at the descent rate passes the depth
        # band with near-zero contact force; "seated" requires it to have
        # stopped (span well under 1% of the 10 mm/s descent rate over the
        # window).
        if max(zs) - min(zs) > _SEATED_Z_SPAN_M:
            return False
        last = observations[-1]
        lateral = math.hypot(last.pose_m[0] - goal[0], last.pose_m[1] - goal[1])
        return lateral <= tolerance_m

    return evaluate


class InsertionExecutionBehavior:
    """Live task logic for peg insertion: the run_episode control-tick
    block, driven per supervision tick from machine observations."""

    def __init__(self, bundle: ValidatedConfig) -> None:
        self._task = bundle.task
        self._insertion = bundle.insertion
        self._z_start = bundle.z_start
        self._depth_commit = bundle.depth_commit
        self._spiral_spec = bundle.spiral
        self._k_z_seating = bundle.gains.k_z
        self._k_z_search = bundle.insertion.k_z_search
        self._z_seating_target = (
            -bundle.task.plate_thickness
            - bundle.insertion.f_target / self._k_z_seating
        )
        self._z_search_target = -bundle.insertion.descent_allowance
        self._f_ramp_rate = bundle.insertion.v_descent * self._k_z_seating
        self._control_period_s: float | None = None

    def begin(self, intent, caps, observation: MachineObservation) -> None:
        self._control_period_s = caps.state_period_s
        self._z_flat_alpha = min(1.0, self._control_period_s / Z_FLAT_TAU_S)
        self._z_flat_band = 0.5 * self._depth_commit
        self._nominal_xy = np.array(
            [intent.start_position_m[0], intent.start_position_m[1]]
        )
        self._spiral = ArchimedeanSpiral(self._spiral_spec)
        tip_z = observation.pose_m[2]
        self._xy_des = self._nominal_xy.copy()
        self._search_done = False
        self._z_des_raw = tip_z
        self._f_cmd = self._insertion.f_search
        self._committed = False
        self._z_flat = 0.0
        self._k_z_prev_active = self._k_z_search
        self._z_des_prev = tip_z
        self._z_des_leashed = tip_z

    def step(self, observation: MachineObservation) -> ServoCommand:
        assert self._control_period_s is not None, "begin() before step()"
        tip = np.array(observation.pose_m)
        mouth_x = observation.extra_value("mouth_x")
        mouth_y = observation.extra_value("mouth_y")
        assert mouth_x is not None and mouth_y is not None, (
            "insertion behavior requires the machine's mouth observation "
            "channels"
        )
        contact = observation.extra_value("in_contact") or 0.0

        # 1. Capture: once the peg's own axis crosses the mouth plane
        #    within the clearance, snap the setpoint to the measured tip
        #    (never to the lagging commanded value) and stop searching.
        if not self._search_done and math.hypot(mouth_x, mouth_y) <= self._task.radial_clearance:
            self._search_done = True
            self._xy_des = tip[:2].copy()

        # 2. Flat-table baseline: EMA of measured rest depth, refined only
        #    while in resting contact within the band of the baseline.
        depth_below_flat = self._z_flat - tip[2]
        if contact > 0.0 and abs(depth_below_flat) < self._z_flat_band:
            self._z_flat = (
                (1.0 - self._z_flat_alpha) * self._z_flat
                + self._z_flat_alpha * tip[2]
            )

        # 3. ONE commit gate with hysteresis: both spiral pause and seating
        #    ramp key off the same physical event.
        was_committed = self._committed
        release_depth = COMMIT_RELEASE_FRACTION * self._depth_commit
        self._committed = depth_below_flat > (
            release_depth if self._committed else self._depth_commit
        )
        k_z_active = self._k_z_seating if self._committed else self._k_z_search

        # 4. Force-continuous stiffness handoff: re-reference so changing
        #    k_z preserves commanded force.
        if self._committed != was_committed:
            f_now = self._k_z_prev_active * (tip[2] - self._z_des_prev)
            self._f_cmd = f_now
            self._z_des_raw = tip[2] - f_now / k_z_active

        # 5. Rate-limited axial force reference.
        f_goal = (
            self._insertion.f_target if self._committed else self._insertion.f_search
        )
        self._f_cmd = _rate_limited_advance(
            self._f_cmd, f_goal, self._f_ramp_rate * self._control_period_s
        )

        # 6. Rate-limited depth setpoint toward the phase target.
        phase_target = self._z_seating_target if self._committed else self._z_search_target
        self._z_des_raw = _rate_limited_advance(
            self._z_des_raw,
            phase_target,
            self._insertion.v_descent * self._control_period_s,
        )

        # 7. The leash bounds steady-state contact force regardless of ramp.
        self._z_des_leashed = max(
            self._z_des_raw, tip[2] - self._f_cmd / k_z_active
        )

        # 8. Lateral search while at the plate and not yet captured/committed.
        at_plate = tip[2] <= self._z_flat + self._z_flat_band
        searching = (not self._search_done) and at_plate
        if searching and not self._committed:
            self._xy_des = self._nominal_xy + self._spiral.step(
                self._control_period_s
            )

        self._k_z_prev_active = k_z_active
        self._z_des_prev = self._z_des_leashed
        return ServoCommand(
            target_pose_m=(
                float(self._xy_des[0]),
                float(self._xy_des[1]),
                float(self._z_des_leashed),
            ),
            k_z_n_per_m=k_z_active,
        )


def _rate_limited_advance(current: float, target: float, max_step: float) -> float:
    delta = target - current
    if abs(delta) <= max_step:
        return target
    return current + math.copysign(max_step, delta)


def insertion_behavior_factory(
    bundle: ValidatedConfig,
) -> Callable[[], InsertionExecutionBehavior]:
    return lambda: InsertionExecutionBehavior(bundle)


def insertion_machine(
    bundle: ValidatedConfig = VALIDATED_LOOSE_V1,
    **config_kwargs,
) -> MuJoCoMachine:
    return MuJoCoMachine(
        bundle, config=MuJoCoMachineConfig(**config_kwargs)
    )
