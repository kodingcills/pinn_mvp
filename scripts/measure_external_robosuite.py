"""Run the X20/X21 robosuite development comparison.

Usage: RSUITE_PY scripts/measure_external_robosuite.py --seeds 5
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

from benchmarks.external import NutAssemblyPolicy, RobosuiteNutAssemblyTask
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
SYSTEM_NAMES = {"direct": "direct_minimal", "strong": "strong_osc", "haplous": "haplous_mediated"}


@dataclass(frozen=True, slots=True)
class Trial:
    seed: int
    system: str
    success: bool
    bounded_success: bool
    peak_force_n: float
    completion_time_s: float | None
    refusals: int
    n_steps: int
    n_contact_steps: int
    wall_s: float
    force_limit_breached: bool
    staleness_breached: bool
    disposition: str
    telemetry_level: str
    outcome_reason: str | None


def _success(observations) -> bool:
    return bool(observations and observations[-1].extra_value("task_success") == 1.0)


def _direct_trial(seed: int, system: str) -> tuple[Trial, str]:
    policy = NutAssemblyPolicy()
    task = RobosuiteNutAssemblyTask(lambda: policy.gripper_action)
    started = time.perf_counter()
    task.reset(seed=seed)
    observation = task.observe()
    assert observation is not None
    intent = ExecutionIntent(
        behavior="nut_assembly_square",
        goal_description="thread square nut on peg 1",
        goal_position_m=observation.pose_m,
        start_position_m=observation.pose_m,
        requirements=(),
        success_condition=None,
    )
    policy.begin(intent, task.capabilities(), observation)
    while not task.session_ended():
        current = task.observe()
        assert current is not None
        task.apply_command(policy.step(current))
        task.step()
    wall_s = time.perf_counter() - started
    result = task.task_result()
    peak = float(result["peak_force_n"])
    success = bool(result["success"])
    trial = Trial(
        seed=seed,
        system=system,
        success=success,
        bounded_success=success and peak <= 25.0,
        peak_force_n=peak,
        completion_time_s=result["completion_time_s"],
        refusals=0,
        n_steps=int(result["n_steps"]),
        n_contact_steps=int(result["n_contact_steps"]),
        wall_s=wall_s,
        force_limit_breached=peak > 25.0,
        staleness_breached=False,
        disposition="COMPLETED" if success else "FAILED",
        telemetry_level="minimal" if system == "direct_minimal" else "full",
        outcome_reason=None,
    )
    config_json = json.dumps(task.controller_config, sort_keys=True)
    task.close()
    return trial, config_json


def _haplous_trial(seed: int) -> tuple[Trial, str]:
    policy = NutAssemblyPolicy()
    task = RobosuiteNutAssemblyTask(lambda: policy.gripper_action)
    started = time.perf_counter()
    task.reset(seed=seed)
    observation = task.observe()
    assert observation is not None
    runtime = HaplousRuntime(
        ExternalMachineAdapter(task),
        behaviors={"nut_assembly_square": lambda: policy},
    )
    intent = ExecutionIntent(
        behavior="nut_assembly_square",
        goal_description="thread square nut on peg 1",
        goal_position_m=observation.pose_m,
        start_position_m=observation.pose_m,
        requirements=(
            Requirement(RequirementKind.MAX_PROCESS_FORCE_N, True, 25.0),
            Requirement(RequirementKind.MAX_TILT_DEG, True, 10.0),
            Requirement(RequirementKind.STATE_MAX_AGE_S, True, 0.1),
            Requirement(RequirementKind.DEADLINE_S, False, 20.0),
        ),
        success_condition=SuccessCondition("nut_threaded", _success, window_s=0.1),
    )
    feasibility = runtime.submit(intent)
    evidence = runtime.execute() if feasibility.outcome != "REFUSED" else runtime.evidence
    wall_s = time.perf_counter() - started
    result = task.task_result()
    violations = evidence.constraint_violations()
    force_breach = any(item["code"] == "process_force" for item in violations)
    stale_breach = any(item["code"] == "state_freshness" for item in violations)
    outcome_events = [event for event in evidence.events if event.kind == "outcome"]
    disposition = str(outcome_events[-1].payload.get("result", "UNKNOWN")) if outcome_events else "UNKNOWN"
    outcome_reason = str(outcome_events[-1].payload.get("reason", "")) or None if outcome_events else None
    success = bool(result["success"])
    trial = Trial(
        seed=seed,
        system=SYSTEM_NAMES["haplous"],
        success=success,
        bounded_success=success and not force_breach and not stale_breach,
        peak_force_n=float(result["peak_force_n"]),
        completion_time_s=result["completion_time_s"],
        refusals=int(feasibility.outcome == "REFUSED"),
        n_steps=int(result["n_steps"]),
        n_contact_steps=int(result["n_contact_steps"]),
        wall_s=wall_s,
        force_limit_breached=force_breach,
        staleness_breached=stale_breach,
        disposition=disposition,
        telemetry_level="full",
        outcome_reason=outcome_reason,
    )
    config_json = json.dumps(task.controller_config, sort_keys=True)
    task.close()
    return trial, config_json


def _summarize(trials: list[Trial]) -> dict[str, dict[str, float | int]]:
    summary: dict[str, dict[str, float | int]] = {}
    for system in SYSTEM_NAMES.values():
        rows = [trial for trial in trials if trial.system == system]
        if not rows:
            continue
        summary[system] = {
            "trials": len(rows),
            "successes": sum(trial.success for trial in rows),
            "bounded_successes": sum(trial.bounded_success for trial in rows),
            "refusals": sum(trial.refusals for trial in rows),
            "mean_peak_force_n": sum(trial.peak_force_n for trial in rows) / len(rows),
            "max_peak_force_n": max(trial.peak_force_n for trial in rows),
            "mean_wall_s": sum(trial.wall_s for trial in rows) / len(rows),
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--systems", default="direct,strong,haplous")
    parser.add_argument(
        "--evidence-stage",
        default="development numbers, not qualification evidence",
    )
    args = parser.parse_args()
    seeds = range(args.seed_start, args.seed_start + args.seeds)
    selected = tuple(item.strip() for item in args.systems.split(","))
    unknown = set(selected) - SYSTEM_NAMES.keys()
    if unknown:
        parser.error(f"unknown systems: {sorted(unknown)}")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RESULTS_ROOT / run_id
    run_dir.mkdir(parents=True)
    trials: list[Trial] = []
    config_json = ""
    started_at = datetime.now(UTC).isoformat()
    for seed in seeds:
        for system in selected:
            if system == "haplous":
                trial, config_json = _haplous_trial(seed)
            else:
                trial, config_json = _direct_trial(seed, SYSTEM_NAMES[system])
            trials.append(trial)
            print(json.dumps(asdict(trial), sort_keys=True))

    controller_hash = hashlib.sha256(config_json.encode()).hexdigest()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    manifest = {
        "campaign": "X20/X21 external robosuite campaign",
        "evidence_status": args.evidence_stage,
        "haplous_version": haplous_version,
        "system_commit": commit,
        "benchmark": "robosuite/1.5.2/NutAssemblySquare",
        "system_variants": [SYSTEM_NAMES[name] for name in selected],
        "controller_config_sha256": controller_hash,
        "seeds": list(seeds),
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "trials": [
            {"seed": seed, "system_variant": SYSTEM_NAMES[name]}
            for seed in seeds
            for name in selected
        ],
    }
    summary = {
        "manifest": manifest,
        "systems": _summarize(trials),
        "paired_r2_minus_r1_mean_wall_s": None,
    }
    strong = {trial.seed: trial for trial in trials if trial.system == "strong_osc"}
    mediated = {trial.seed: trial for trial in trials if trial.system == "haplous_mediated"}
    paired = [mediated[seed].wall_s - strong[seed].wall_s for seed in strong.keys() & mediated.keys()]
    if paired:
        summary["paired_r2_minus_r1_mean_wall_s"] = sum(paired) / len(paired)

    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with (run_dir / "trials.jsonl").open("x", encoding="utf-8") as stream:
        for trial in trials:
            stream.write(json.dumps(asdict(trial), sort_keys=True) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(run_dir)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
