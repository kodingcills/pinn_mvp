"""HEB task family B: constrained-contact sliding. A small part slides
along a tangential arc on the fixture's flat annulus while maintaining a
declared normal-force band. The condition factory below is benchmark-owned
task truth (shared by every system under test); the strict band grading
lives in metrics.grade_slide_task."""
from __future__ import annotations

import math

from pegsim.rollout import VALIDATED_SLIDE_V1, ValidatedConfig

from haplous.intent import SuccessCondition

from benchmarks.heb.protocol import Scenario

_R_PATH_M = 0.0039
_ARC_DEG = 60.0
_SLIDE_TOLERANCE_M = 6e-4
_SLIDE_MIN_FORCE_N = 0.06
_SLIDE_MAX_FORCE_N = 2.0
_SLIDE_PERSISTENCE_S = 0.2


def slide_condition_factory(scenario: Scenario, bundle: ValidatedConfig):
    """Task truth: the part is at the path end (within tolerance), has
    stayed there through the persistence window, and the declared force
    band held over that window."""
    path_end = scenario.path_end_m
    tolerance = scenario.position_tolerance_m or _SLIDE_TOLERANCE_M
    min_force = scenario.min_force_n
    max_force = scenario.max_force_n
    persistence = scenario.persistence_s

    def evaluate(observations) -> bool:
        if not observations or path_end is None:
            return False
        end_t = observations[-1].stamp.source_s
        window = [
            o for o in observations if o.stamp.source_s > end_t - persistence
        ]
        if not window or end_t - window[0].stamp.source_s < 0.9 * persistence:
            return False
        last = observations[-1]
        lateral = math.hypot(
            last.pose_m[0] - path_end[0], last.pose_m[1] - path_end[1]
        )
        if lateral > tolerance:
            return False
        zs = [o.pose_m[2] for o in window]
        fzs = [abs(o.force_z_n) for o in window]
        if max(zs) - min(zs) > 30e-6:
            return False
        if min_force is not None and min(fzs) < 0.5 * min_force:
            return False
        if max_force is not None and max(fzs) > max_force:
            return False
        return True

    return SuccessCondition("path_completed", evaluate, window_s=2.0 * persistence)


def slide_bundle(scenario: Scenario) -> ValidatedConfig:
    """The validated slide substrate. Geometry deltas do not apply to this
    bundle in v1: the slide corridor is derived from the bundle's own
    geometry, and the declared band was sized for it."""
    return VALIDATED_SLIDE_V1


def _path_points():
    half = math.radians(_ARC_DEG) / 2.0
    start = (
        _R_PATH_M * math.cos(-half),
        _R_PATH_M * math.sin(-half),
        0.0,
    )
    end = (
        _R_PATH_M * math.cos(half),
        _R_PATH_M * math.sin(half),
        0.0,
    )
    return start, end


def sliding_scenarios() -> list[Scenario]:
    """Task B development scenarios: the discriminating semantic set first
    (nominal, recoverable vs disallowed disturbance, severe disturbance,
    tight ceiling, stale observation, infeasible declarations), plus
    machine-fault reuse."""
    start, end = _path_points()
    mid_slide = 1.1  # approach ~0.6s, slide ~0.8s: t=1.1 is mid-arc
    scenarios: list[Scenario] = [
        Scenario(
            id="slide_nominal",
            task_family="constrained_slide",
            path_start_m=start,
            path_end_m=end,
            start_position_m=start,
            min_force_n=_SLIDE_MIN_FORCE_N,
            max_force_n=_SLIDE_MAX_FORCE_N,
            position_tolerance_m=_SLIDE_TOLERANCE_M,
            persistence_s=_SLIDE_PERSISTENCE_S,
            expected_disposition="COMPLETE",
            tags=("task_b", "physical"),
        ),
        Scenario(
            id="slide_disturbance_recoverable",
            task_family="constrained_slide",
            path_start_m=start,
            path_end_m=end,
            start_position_m=start,
            min_force_n=_SLIDE_MIN_FORCE_N,
            max_force_n=_SLIDE_MAX_FORCE_N,
            position_tolerance_m=_SLIDE_TOLERANCE_M,
            persistence_s=_SLIDE_PERSISTENCE_S,
            # Vertical pop mid-slide: contact is lost briefly (soft force
            # floor breach, CONSTRAINED) and the behavior re-acquires.
            # No declared bound is exceeded, so this stays bounded.
            disturbances=((mid_slide, (0.0, 0.0, 0.05, 0.0, 0.0, 0.0)),),
            recoverable=True,
            expected_disposition="COMPLETE",
            tags=("task_b", "disturbance"),
        ),
        Scenario(
            id="slide_disturbance_recoverable_ceiling",
            task_family="constrained_slide",
            path_start_m=start,
            path_end_m=end,
            start_position_m=start,
            min_force_n=_SLIDE_MIN_FORCE_N,
            max_force_n=1.5,
            position_tolerance_m=_SLIDE_TOLERANCE_M,
            persistence_s=_SLIDE_PERSISTENCE_S,
            # The 1.5N ceiling sits below the measured facet-tap peaks
            # (1.73-1.74N, three crossings per 60-degree arc): each tap is
            # a hard breach, and only authorized recovery keeps the
            # execution alive. Graded bounded=False (the taps do exceed
            # the declared ceiling); the disposition discrimination is
            # against slide_disturbance_no_recovery.
            disturbances=((mid_slide, (0.0, 0.0, -0.25, 0.0, 0.0, 0.0)),),
            recovery_permitted_conditions=("process_force",),
            max_recovery_actions=6,
            recovery_settle_s=0.3,
            expected_disposition="COMPLETE",
            tags=("task_b", "disturbance"),
        ),
        Scenario(
            id="slide_disturbance_no_recovery",
            task_family="constrained_slide",
            path_start_m=start,
            path_end_m=end,
            start_position_m=start,
            min_force_n=_SLIDE_MIN_FORCE_N,
            max_force_n=1.5,
            position_tolerance_m=_SLIDE_TOLERANCE_M,
            persistence_s=_SLIDE_PERSISTENCE_S,
            # Same facet-tap physics as the recoverable_ceiling case with
            # recovery permission removed: the first tap aborts.
            expected_disposition="ABORTED",
            tags=("task_b", "disturbance"),
        ),
        Scenario(
            id="slide_disturbance_severe",
            task_family="constrained_slide",
            path_start_m=start,
            path_end_m=end,
            start_position_m=start,
            min_force_n=_SLIDE_MIN_FORCE_N,
            max_force_n=_SLIDE_MAX_FORCE_N,
            position_tolerance_m=_SLIDE_TOLERANCE_M,
            persistence_s=_SLIDE_PERSISTENCE_S,
            # Rotational impulse large enough to tip the part past the
            # machine's tilt guard: measured unrecoverable (dev smoke,
            # fault at the disturbance step).
            disturbances=((mid_slide, (0.0, 0.0, 0.0, 100.0, 0.0, 0.0)),),
            expected_disposition="ABORTED",
            tags=("task_b", "disturbance"),
        ),
        Scenario(
            id="slide_stale_mid_slide",
            task_family="constrained_slide",
            path_start_m=start,
            path_end_m=end,
            start_position_m=start,
            min_force_n=_SLIDE_MIN_FORCE_N,
            max_force_n=_SLIDE_MAX_FORCE_N,
            position_tolerance_m=_SLIDE_TOLERANCE_M,
            persistence_s=_SLIDE_PERSISTENCE_S,
            stale_windows_s=((mid_slide, mid_slide + 0.2, 0.05),),
            expected_disposition="ABORTED",
            tags=("task_b", "measurement"),
        ),
        Scenario(
            id="slide_deadline_infeasible",
            task_family="constrained_slide",
            path_start_m=start,
            path_end_m=end,
            start_position_m=start,
            min_force_n=_SLIDE_MIN_FORCE_N,
            max_force_n=_SLIDE_MAX_FORCE_N,
            position_tolerance_m=_SLIDE_TOLERANCE_M,
            deadline_s=1.0,
            expected_disposition="REFUSED",
            infeasible_request=True,
            tags=("task_b", "semantic"),
        ),
        Scenario(
            id="slide_command_rejection_persistent",
            task_family="constrained_slide",
            path_start_m=start,
            path_end_m=end,
            start_position_m=start,
            min_force_n=_SLIDE_MIN_FORCE_N,
            max_force_n=_SLIDE_MAX_FORCE_N,
            position_tolerance_m=_SLIDE_TOLERANCE_M,
            persistence_s=_SLIDE_PERSISTENCE_S,
            command_rejection_windows_s=((mid_slide, mid_slide + 0.02),),
            expected_disposition="ABORTED",
            tags=("task_b", "capability"),
        ),
        Scenario(
            id="slide_machine_fault",
            task_family="constrained_slide",
            path_start_m=start,
            path_end_m=end,
            start_position_m=start,
            min_force_n=_SLIDE_MIN_FORCE_N,
            max_force_n=_SLIDE_MAX_FORCE_N,
            position_tolerance_m=_SLIDE_TOLERANCE_M,
            persistence_s=_SLIDE_PERSISTENCE_S,
            fault_at_s=mid_slide,
            expected_disposition="ABORTED",
            tags=("task_b", "fault"),
        ),
    ]
    return scenarios
