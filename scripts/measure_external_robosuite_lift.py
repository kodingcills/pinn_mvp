"""R00/R01 robosuite Lift successful-execution baseline: paired direct vs Haplous.

Usage: RSUITE_PY scripts/measure_external_robosuite_lift.py --seeds 5
Requires the isolated robosuite venv (.venv-robosuite, robosuite 1.5.2 +
mujoco 3.9.0). The X20 campaign's direct/strong variants are intentionally
not repeated here: they were bit-identical and the nut policy was 0/15.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.external.robosuite_lift_policy import LiftPolicy
from benchmarks.external.robosuite_lift_task import RobosuiteLiftTask
from haplous import (
    ExecutionIntent,
    HaplousRuntime,
    Requirement,
    RequirementKind,
    SuccessCondition,
    __version__ as haplous_version,
)
from haplous.adapters.external import ExternalMachineAdapter


RESULTS_ROOT = ROOT / "results" / "external_eval" / "robosuite-1.5.2"
FORCE_LIMIT_N = 25.0
TILT_LIMIT_DEG = 30.0


@dataclass(frozen=True, slots=True)
class Trial:
    seed: int
    system: str
    success: bool
    bounded_success: bool
    peak_force_n: float
    peak_tilt_deg: float
    completion_time_s: float | None
    refusals: int
    n_steps: int
    n_contact_steps: int
    wall_s: float
    force_limit_breached: bool
    staleness_breached: bool
    disposition: str
    outcome_reason: str | None


def _success(observations) -> bool:
    return bool(observations and observations[-1].extra_value("task_success") == 1.0)


def _direct_trial(seed: int) -> Trial:
    policy = LiftPolicy()
    task = RobosuiteLiftTask(lambda: policy.gripper_action)
    started = time.perf_counter()
    task.reset(seed=seed)
    succeeded = False
    while not task.session_ended() and not succeeded:
        observation = task.observe()
        assert observation is not None
        # same stopping semantics as the mediated arm: end on the first
        # success observation (reviewer-gate harmonization)
        succeeded = observation.extra_value("task_success") == 1.0
        if succeeded:
            break
        task.apply_command(policy.step(observation))
        task.step()
    wall_s = time.perf_counter() - started
    result = task.task_result()
    peak = float(result["peak_force_n"])
    peak_tilt = float(result["peak_tilt_deg"])
    success = bool(result["success"])
    task.close()
    return Trial(
        seed=seed,
        system="direct_minimal",
        success=success,
        bounded_success=success and peak <= FORCE_LIMIT_N and peak_tilt <= TILT_LIMIT_DEG,
        peak_force_n=peak,
        peak_tilt_deg=peak_tilt,
        completion_time_s=result["completion_time_s"],
        refusals=0,
        n_steps=int(result["n_steps"]),
        n_contact_steps=int(result["n_contact_steps"]),
        wall_s=wall_s,
        force_limit_breached=peak > FORCE_LIMIT_N,
        staleness_breached=False,
        disposition="COMPLETED" if success else "FAILED",
        outcome_reason=None,
    )


def _haplous_trial(seed: int) -> Trial:
    policy = LiftPolicy()
    task = RobosuiteLiftTask(lambda: policy.gripper_action)
    started = time.perf_counter()
    task.reset(seed=seed)
    observation = task.observe()
    assert observation is not None
    runtime = HaplousRuntime(
        ExternalMachineAdapter(task),
        behaviors={"robosuite_lift": lambda: policy},
    )
    intent = ExecutionIntent(
        behavior="robosuite_lift",
        goal_description="grasp the cube and lift it",
        goal_position_m=observation.pose_m,
        start_position_m=observation.pose_m,
        requirements=(
            Requirement(RequirementKind.MAX_PROCESS_FORCE_N, True, FORCE_LIMIT_N),
            Requirement(RequirementKind.MAX_TILT_DEG, True, TILT_LIMIT_DEG),
            Requirement(RequirementKind.STATE_MAX_AGE_S, True, 0.1),
            Requirement(RequirementKind.DEADLINE_S, False, 12.0),
        ),
        success_condition=SuccessCondition("cube_lifted", _success, window_s=0.25),
    )
    feasibility = runtime.submit(intent)
    evidence = runtime.execute() if feasibility.outcome != "REFUSED" else runtime.evidence
    wall_s = time.perf_counter() - started
    result = task.task_result()
    violations = evidence.constraint_violations()
    stale_breach = any(item["code"] == "state_freshness" for item in violations)
    outcome_events = [event for event in evidence.events if event.kind == "outcome"]
    disposition = str(outcome_events[-1].payload.get("result", "UNKNOWN")) if outcome_events else "UNKNOWN"
    outcome_reason = str(outcome_events[-1].payload.get("reason", "")) or None if outcome_events else None
    success = bool(result["success"])
    peak = float(result["peak_force_n"])
    peak_tilt = float(result["peak_tilt_deg"])
    task.close()
    return Trial(
        seed=seed,
        system="haplous_mediated",
        success=success,
        # identical grading formula as the direct arm: recorded physical
        # peaks (3D force norm + tilt), not runtime violation events
        bounded_success=(
            success and peak <= FORCE_LIMIT_N and peak_tilt <= TILT_LIMIT_DEG
        ),
        peak_force_n=peak,
        peak_tilt_deg=peak_tilt,
        completion_time_s=result["completion_time_s"],
        refusals=int(feasibility.outcome == "REFUSED"),
        n_steps=int(result["n_steps"]),
        n_contact_steps=int(result["n_contact_steps"]),
        wall_s=wall_s,
        force_limit_breached=peak > FORCE_LIMIT_N,
        staleness_breached=stale_breach,
        disposition=disposition,
        outcome_reason=outcome_reason,
    )


def _summarize(trials: list[Trial]) -> dict[str, dict]:
    summary = {}
    for system in ("direct_minimal", "haplous_mediated"):
        rows = [t for t in trials if t.system == system]
        if not rows:
            continue
        summary[system] = {
            "trials": len(rows),
            "successes": sum(t.success for t in rows),
            "bounded_successes": sum(t.bounded_success for t in rows),
            "refusals": sum(t.refusals for t in rows),
            "force_breaches": sum(t.force_limit_breached for t in rows),
            "mean_peak_force_n": sum(t.peak_force_n for t in rows) / len(rows),
            "max_peak_force_n": max(t.peak_force_n for t in rows),
            "max_peak_tilt_deg": max(t.peak_tilt_deg for t in rows),
            "mean_completion_time_s": (
                sum(t.completion_time_s for t in rows if t.completion_time_s is not None)
                / max(1, sum(t.completion_time_s is not None for t in rows))
            ),
            "mean_wall_s": sum(t.wall_s for t in rows) / len(rows),
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--systems", default="direct,haplous")
    parser.add_argument("--evidence-stage", default="development numbers, not qualification evidence")
    args = parser.parse_args()
    seeds = range(args.seed_start, args.seed_start + args.seeds)
    selected = tuple(item.strip() for item in args.systems.split(","))
    system_names = {"direct": "direct_minimal", "haplous": "haplous_mediated"}
    unknown = set(selected) - system_names.keys()
    if unknown:
        parser.error(f"unknown systems: {sorted(unknown)}")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RESULTS_ROOT / f"lift-{run_id}"
    run_dir.mkdir(parents=True)
    trials: list[Trial] = []
    for seed in seeds:
        for system in selected:
            trial = _haplous_trial(seed) if system == "haplous" else _direct_trial(seed)
            trials.append(trial)
            print(json.dumps(asdict(trial), sort_keys=True), flush=True)

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    manifest = {
        "campaign": "R00/R01 robosuite Lift successful-execution baseline (paired)",
        "evidence_status": args.evidence_stage,
        "haplous_version": haplous_version,
        "system_commit": commit,
        "benchmark": "robosuite/1.5.2/Lift",
        "system_variants": [system_names[name] for name in selected],
        "force_limit_n": FORCE_LIMIT_N,
        "tilt_limit_deg": TILT_LIMIT_DEG,
        "seeds": list(seeds),
        "started_at": run_id,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    summary = {"manifest": manifest, "systems": _summarize(trials)}
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with (run_dir / "trials.jsonl").open("x", encoding="utf-8") as stream:
        for trial in trials:
            stream.write(json.dumps(asdict(trial), sort_keys=True) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(run_dir)
    print(json.dumps(summary["systems"], indent=2))


if __name__ == "__main__":
    main()
