"""HEB 0.2.0 grading and metrics (revised per the post-freeze review). The benchmark evaluates every trial from its
own view: it re-evaluates the scenario's hard requirements over the full
observation stream and grades task completion from machine-observation
geometry — never from the system's own success claim."""
from __future__ import annotations

import math
from dataclasses import dataclass

from haplous.machine import MachineObservation

from benchmarks.heb.protocol import Scenario, SystemRun

# benchmark-owned grading constants (m), derived from the measured
# substrate residuals: 8 um seated lateral penetration (x1.5), 70 um
# z-stop residual (x2), and a settled-tip span of 10 um (well under 1%
# of the 10 mm/s descent rate over the 0.1 s persistence window).
PEN_ALLOW_M = 12e-6
SEAT_DEPTH_MARGIN_M = 140e-6
SEATED_Z_SPAN_M = 10e-6
# A declared interaction floor is graded as a sustained condition: below
# floor for longer than this grace (counted from the first establishment
# of contact) is a hard violation; transients during contact re-acquisition
# after a disturbance are not.
FLOOR_GRACE_S = 0.05


def grade_requirements(
    scenario: Scenario, observations: tuple[MachineObservation, ...]
) -> tuple[dict, ...]:
    """Benchmark-owned hard-requirement check over the observed stream.
    The max-force ceiling is per-observation; the declared force floor is
    graded as a sustained condition (see FLOOR_GRACE_S)."""
    if not observations:
        return ()
    violations: list[dict] = []
    deadline = scenario.deadline_s
    floor_streak_start: float | None = None
    contact_established = scenario.min_force_n is None
    # Source-time gaps are graded too: a blackout longer than the declared
    # state-age bound is a freshness violation even though no delivered
    # observation carries a stale age (the grader only sees delivered
    # samples; without this, a system that caches its last observation
    # through a blackout grades clean — the loophole the 0.1.0 review
    # found). The bound matches the runtime's own hard-breach rule.
    for prev, obs in zip(observations, observations[1:]):
        gap = obs.stamp.source_s - prev.stamp.source_s
        if gap > scenario.state_max_age_s:
            violations.append(
                {
                    "code": "state_freshness",
                    "t_s": obs.stamp.source_s,
                    "value": gap,
                    "limit": scenario.state_max_age_s,
                    "kind": "source_gap",
                }
            )
    for obs in observations:
        t = obs.stamp.source_s
        fz = abs(obs.force_z_n)
        if (
            scenario.max_force_n is not None
            and fz > scenario.max_force_n
        ):
            violations.append(
                {
                    "code": "process_force",
                    "t_s": t,
                    "value": fz,
                    "limit": scenario.max_force_n,
                }
            )
        if scenario.max_tilt_deg is not None and obs.tilt_deg > scenario.max_tilt_deg:
            violations.append(
                {
                    "code": "tilt",
                    "t_s": t,
                    "value": obs.tilt_deg,
                    "limit": scenario.max_tilt_deg,
                }
            )
        if obs.stamp.age_s > scenario.state_max_age_s:
            violations.append(
                {
                    "code": "state_freshness",
                    "t_s": t,
                    "value": obs.stamp.age_s,
                    "limit": scenario.state_max_age_s,
                }
            )
        if scenario.min_force_n is not None:
            if not contact_established:
                if fz >= scenario.min_force_n:
                    contact_established = True
            elif fz < scenario.min_force_n:
                if floor_streak_start is None:
                    floor_streak_start = t
                elif t - floor_streak_start > FLOOR_GRACE_S:
                    violations.append(
                        {
                            "code": "process_force_floor",
                            "t_s": t,
                            "value": fz,
                            "limit": scenario.min_force_n,
                        }
                    )
            else:
                floor_streak_start = None
    if deadline is not None and observations[-1].stamp.source_s > deadline:
        violations.append(
            {
                "code": "deadline",
                "t_s": observations[-1].stamp.source_s,
                "value": observations[-1].stamp.source_s,
                "limit": deadline,
            }
        )
    return tuple(violations)


def grade_insertion_task(
    scenario: Scenario, bundle, observations: tuple[MachineObservation, ...]
) -> tuple[bool, dict]:
    """Insertion task truth from observation geometry: the peg must be
    stationary at seating depth with tip and mouth laterally inside the
    true bore (world origin) plus the measured allowances. This never
    reads Haplous state or its success decision."""
    empty = {
        "peak_abs_force_n": None,
        "max_tilt_deg": None,
        "seating_time_s": None,
    }
    if not observations:
        return False, empty
    task = bundle.task
    tolerance = task.radial_clearance + PEN_ALLOW_M
    target_depth = task.plate_thickness - SEAT_DEPTH_MARGIN_M
    window = [
        o
        for o in observations
        if o.stamp.source_s > observations[-1].stamp.source_s - scenario.persistence_s
    ]
    zs = [o.pose_m[2] for o in window]
    ok = (
        max(zs) - min(zs) <= SEATED_Z_SPAN_M
        and all(z <= -target_depth for z in zs)
    )
    if scenario.max_force_n is not None:
        ok = ok and all(
            abs(o.force_z_n) <= scenario.max_force_n for o in window
        )
    last = observations[-1]
    mouth_x = last.extra_value("mouth_x")
    mouth_y = last.extra_value("mouth_y")
    tip_lateral = math.hypot(last.pose_m[0], last.pose_m[1])
    if mouth_x is None or mouth_y is None:
        return False, {
            "reason": "missing_mouth_channel",
            "tip_lateral_m": tip_lateral,
        }
    mouth_lateral = math.hypot(mouth_x, mouth_y)
    ok = ok and tip_lateral <= tolerance and mouth_lateral <= tolerance
    metrics = {
        "tip_lateral_m": tip_lateral,
        "mouth_lateral_m": mouth_lateral,
        "final_depth_m": last.pose_m[2],
        "seating_time_s": last.stamp.source_s,
        "peak_abs_force_n": max(abs(o.force_z_n) for o in observations),
        "max_tilt_deg": max(o.tilt_deg for o in observations),
    }
    return ok, metrics


def grade_slide_task(
    scenario: Scenario, bundle, observations: tuple[MachineObservation, ...]
) -> tuple[bool, dict]:
    """Constrained-contact task truth from observation geometry: the part
    is at the path end (within tolerance), stayed there through the
    persistence window, and the declared force band held over that window.
    This never reads Haplous state or its success decision."""
    empty = {
        "peak_abs_force_n": None,
        "max_tilt_deg": None,
        "seating_time_s": None,
        "endpoint_lateral_m": None,
        "min_post_contact_force_n": None,
    }
    if not observations or scenario.path_end_m is None:
        return False, empty
    end = scenario.path_end_m[:2]
    tolerance = scenario.position_tolerance_m or 6e-4
    window = [
        o
        for o in observations
        if o.stamp.source_s > observations[-1].stamp.source_s - scenario.persistence_s
    ]
    last = observations[-1]
    lateral = math.hypot(last.pose_m[0] - end[0], last.pose_m[1] - end[1])
    zs = [o.pose_m[2] for o in window]
    fzs = [abs(o.force_z_n) for o in window]
    ok = (
        lateral <= tolerance
        and max(zs) - min(zs) <= 30e-6
    )
    if scenario.min_force_n is not None:
        ok = ok and min(fzs) >= 0.5 * scenario.min_force_n
    if scenario.max_force_n is not None:
        ok = ok and max(fzs) <= scenario.max_force_n
    min_floor = scenario.min_force_n or 0.0
    post_contact = [
        f
        for f in (abs(o.force_z_n) for o in observations)
        if f >= min_floor
    ]
    metrics = {
        "endpoint_lateral_m": lateral,
        "seating_time_s": last.stamp.source_s,
        "peak_abs_force_n": max(abs(o.force_z_n) for o in observations),
        "max_tilt_deg": max(o.tilt_deg for o in observations),
        "min_post_contact_force_n": min(post_contact) if post_contact else 0.0,
    }
    return ok, metrics


def grade_run(scenario: Scenario, bundle, run: SystemRun, task_grader=None):
    """Returns (graded_disposition, task_success, bounded_success,
    hard_violations, task_metrics). REFUSED trials are graded as refusals;
    everything else is graded from the observation stream. The task
    grader dispatches on the scenario's declared shape (a path defines the
    constrained-contact task), not on task-family names."""
    if task_grader is None:
        task_grader = (
            grade_slide_task
            if scenario.path_end_m is not None
            else grade_insertion_task
        )
    if run.disposition == "REFUSED":
        return "REFUSED", False, False, (), {"system": run.system}
    violations = grade_requirements(scenario, run.observations)
    task_success, task_metrics = task_grader(scenario, bundle, run.observations)
    graded = "COMPLETE" if task_success else "ABORTED"
    bounded = task_success and not violations
    task_metrics["system"] = run.system
    task_metrics["reported_disposition"] = run.disposition
    task_metrics["wall_s"] = run.wall_s
    if run.detail:
        task_metrics.update(run.detail)
    return graded, task_success, bounded, violations, task_metrics


@dataclass
class SystemSummary:
    system: str
    trials: int
    accepted: int
    bounded_successes: int
    task_successes: int
    refusals: int
    correct_refusals: int
    infeasible_presented: int
    completed_with_hard_violation_trials: int
    breach_trials: int
    recoverable_designated: int
    recoveries_successful: int
    completion_times_s: tuple[float, ...]

    @property
    def coverage(self) -> float:
        """Accepted requests / presented requests."""
        return self.accepted / self.trials if self.trials else 0.0

    @property
    def bounded_success_rate(self) -> float:
        """Bounded success among accepted (executed) requests."""
        return self.bounded_successes / self.accepted if self.accepted else 0.0

    @property
    def refusal_precision(self) -> float | None:
        return self.correct_refusals / self.refusals if self.refusals else None

    @property
    def refusal_recall(self) -> float | None:
        return (
            self.correct_refusals / self.infeasible_presented
            if self.infeasible_presented
            else None
        )

    @property
    def recovery_success_rate(self) -> float | None:
        """Successful recoveries / benchmark-designated recoverable
        disturbances. A designated disturbance counts as recovered for a
        system iff the task completed with bounded success on it."""
        return (
            self.recoveries_successful / self.recoverable_designated
            if self.recoverable_designated
            else None
        )

    def completion_percentiles(self) -> dict:
        s = sorted(self.completion_times_s)
        if not s:
            return {}

        def pct(p: float) -> float:
            i = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
            return s[i]

        return {"p50": pct(0.5), "p90": pct(0.9), "p95": pct(0.95), "max": s[-1]}


def summarize(scenarios, records) -> dict:
    """Aggregate graded trial records per system. Coverage separates
    refusals from execution: bounded success is scored only over accepted
    (non-refused) requests, so refusing everything cannot score well."""
    infeasible_ids = {s.id for s in scenarios if s.infeasible_request}
    recoverable_ids = {s.id for s in scenarios if s.recoverable}
    by_system: dict[str, list] = {}
    for record in records:
        by_system.setdefault(record.system, []).append(record)
    summaries: dict[str, SystemSummary] = {}
    for system, recs in by_system.items():
        accepted = [r for r in recs if r.reported_disposition != "REFUSED"]
        completion_times = tuple(
            r.task_metrics["seating_time_s"]
            for r in accepted
            if r.bounded_success and r.task_metrics.get("seating_time_s") is not None
        )
        summaries[system] = SystemSummary(
            system=system,
            trials=len(recs),
            accepted=len(accepted),
            bounded_successes=sum(1 for r in accepted if r.bounded_success),
            task_successes=sum(1 for r in accepted if r.task_success),
            refusals=len(recs) - len(accepted),
            correct_refusals=sum(
                1
                for r in recs
                if r.reported_disposition == "REFUSED"
                and r.scenario_id in infeasible_ids
            ),
            infeasible_presented=sum(
                1 for s in scenarios if s.infeasible_request
            ),
            completed_with_hard_violation_trials=sum(
                1 for r in accepted if r.hard_violations and r.task_success
            ),
            breach_trials=sum(
                1 for r in recs if r.hard_violations
            ),
            recoverable_designated=len(recoverable_ids),
            recoveries_successful=sum(
                1
                for r in accepted
                if r.scenario_id in recoverable_ids
                and r.bounded_success
                and r.task_success
            ),
            completion_times_s=completion_times,
        )
    return summaries


def rule_of_three_upper_bound(failures: int, trials: int) -> float | None:
    """Rule-of-three 95% upper bound on the failure probability when zero
    failures are observed in N deterministic trials; None otherwise."""
    if failures == 0 and trials > 0:
        return 3.0 / trials
    return None
