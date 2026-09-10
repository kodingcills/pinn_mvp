"""ManiSkill PegInsertionSide direct-vs-Haplous paired measurement.

Usage:
  .venv-maniskill/bin/python scripts/measure_external_maniskill.py \
      --episodes 0,1,2 --systems direct,haplous
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

from benchmarks.external import DemoStreamPolicy, ManiSkillPegInsertionTask
from haplous import (
    ExecutionIntent,
    HaplousRuntime,
    Requirement,
    RequirementKind,
    SuccessCondition,
    __version__ as haplous_version,
)
from haplous.adapters.external import ExternalMachineAdapter


TRAJECTORY_PATH = Path(
    "/Users/tofuinparis/.maniskill/demos/PegInsertionSide-v1/motionplanning/"
    "peg200.state.pd_ee_pose.physx_cpu.h5"
)
TRAJECTORY_JSON_PATH = TRAJECTORY_PATH.with_suffix(".json")


def _episode_seed(episode_id: int) -> int:
    episodes = json.loads(TRAJECTORY_JSON_PATH.read_text(encoding="utf-8"))["episodes"]
    return int(episodes[episode_id]["episode_seed"])
RESULTS_ROOT = ROOT / "results" / "external_eval" / "maniskill-3.0.1"
SYSTEM_NAMES = {"direct": "direct_minimal", "haplous": "haplous_mediated"}
FORCE_LIMIT_N = 25.0
TILT_LIMIT_DEG = 30.0


@dataclass(frozen=True, slots=True)
class Trial:
    episode_id: int
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
    tilt_limit_breached: bool
    staleness_breached: bool
    disposition: str
    outcome_reason: str | None
    max_tilt_deg: float


def _episode_action_count(episode_id: int) -> int:
    import h5py

    with h5py.File(TRAJECTORY_PATH, "r") as handle:
        return int(handle[f"traj_{episode_id}"]["actions"].shape[0])


def _success(observations) -> bool:
    return bool(observations and observations[-1].extra_value("task_success") == 1.0)


def _make_task(policy: DemoStreamPolicy) -> ManiSkillPegInsertionTask:
    return ManiSkillPegInsertionTask(
        lambda: policy.gripper_action,
        lambda: policy.rotation_action,
        stream_length=len(policy._actions),
    )


def _direct_trial(episode_id: int) -> Trial:
    policy = DemoStreamPolicy(TRAJECTORY_PATH, episode_id)
    task = _make_task(policy)
    started = time.perf_counter()
    task.reset(seed=_episode_seed(episode_id))
    max_tilt = 0.0
    while not task.session_ended():
        observation = task.observe()
        assert observation is not None
        max_tilt = max(max_tilt, observation.tilt_deg)
        task.apply_command(policy.step(observation))
        task.step()
    wall_s = time.perf_counter() - started
    result = task.task_result()
    peak = float(result["peak_force_n"])
    success = bool(result["success"])
    task.close()
    return Trial(
        episode_id=episode_id,
        system="direct_minimal",
        success=success,
        bounded_success=success and peak <= FORCE_LIMIT_N and max_tilt <= TILT_LIMIT_DEG,
        peak_force_n=peak,
        completion_time_s=result["completion_time_s"],
        refusals=0,
        n_steps=int(result["n_steps"]),
        n_contact_steps=int(result["n_contact_steps"]),
        wall_s=wall_s,
        force_limit_breached=peak > FORCE_LIMIT_N,
        tilt_limit_breached=max_tilt > TILT_LIMIT_DEG,
        staleness_breached=False,
        disposition="COMPLETED" if success else "FAILED",
        outcome_reason=None,
        max_tilt_deg=max_tilt,
    )


def _haplous_trial(episode_id: int) -> Trial:
    policy = DemoStreamPolicy(TRAJECTORY_PATH, episode_id)
    task = _make_task(policy)
    started = time.perf_counter()
    task.reset(seed=_episode_seed(episode_id))
    observation = task.observe()
    assert observation is not None
    runtime = HaplousRuntime(
        ExternalMachineAdapter(task),
        behaviors={"peg_insertion_side": lambda: policy},
    )
    intent = ExecutionIntent(
        behavior="peg_insertion_side",
        goal_description="insert peg into the box-side hole",
        goal_position_m=observation.pose_m,
        start_position_m=observation.pose_m,
        requirements=(
            Requirement(RequirementKind.MAX_PROCESS_FORCE_N, True, FORCE_LIMIT_N),
            Requirement(RequirementKind.MAX_TILT_DEG, True, TILT_LIMIT_DEG),
            Requirement(RequirementKind.STATE_MAX_AGE_S, True, 0.1),
            Requirement(RequirementKind.DEADLINE_S, False, task.capabilities().expected_duration_s + 10.0),
        ),
        success_condition=SuccessCondition("peg_inserted", _success, window_s=0.1),
    )
    feasibility = runtime.submit(intent)
    evidence = runtime.execute() if feasibility.outcome != "REFUSED" else runtime.evidence
    wall_s = time.perf_counter() - started
    result = task.task_result()
    violations = evidence.constraint_violations()
    force_breach = any(item["code"] == "process_force" for item in violations)
    stale_breach = any(item["code"] == "state_freshness" for item in violations)
    outcome_events = [e for e in evidence.events if e.kind == "outcome"]
    disposition = str(outcome_events[-1].payload.get("result", "UNKNOWN")) if outcome_events else "UNKNOWN"
    outcome_reason = str(outcome_events[-1].payload.get("reason", "")) or None if outcome_events else None
    success = bool(result["success"])
    task.close()
    return Trial(
        episode_id=episode_id,
        system="haplous_mediated",
        success=success,
        bounded_success=success and not force_breach and not stale_breach,
        peak_force_n=float(result["peak_force_n"]),
        completion_time_s=result["completion_time_s"],
        refusals=int(feasibility.outcome == "REFUSED"),
        n_steps=int(result["n_steps"]),
        n_contact_steps=int(result["n_contact_steps"]),
        wall_s=wall_s,
        force_limit_breached=force_breach,
        tilt_limit_breached=any(item["code"] == "tilt" for item in violations),
        staleness_breached=stale_breach,
        disposition=disposition,
        outcome_reason=outcome_reason,
        max_tilt_deg=float(getattr(task, "_peak_tilt_deg", 0.0)),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", required=True, help="comma-separated episode ids")
    parser.add_argument("--systems", default="direct,haplous")
    parser.add_argument("--evidence-stage", default="development")
    args = parser.parse_args()
    episode_ids = [int(item) for item in args.episodes.split(",")]
    selected = [item.strip() for item in args.systems.split(",")]

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RESULTS_ROOT / run_id
    run_dir.mkdir(parents=True)
    trials: list[Trial] = []
    for episode_id in episode_ids:
        for system in selected:
            if system == "haplous":
                trial = _haplous_trial(episode_id)
            else:
                trial = _direct_trial(episode_id)
            trials.append(trial)
            print(json.dumps(asdict(trial), sort_keys=True), flush=True)

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    stream_sha256 = hashlib.sha256(TRAJECTORY_PATH.read_bytes()).hexdigest()
    manifest = {
        "campaign": "Haplous final simulation evidence - ManiSkill peg (Mode A frozen stream)",
        "evidence_status": args.evidence_stage,
        "haplous_version": haplous_version,
        "system_commit": commit,
        "benchmark": "maniskill/3.0.1/PegInsertionSide",
        "physics_backend": "sapien 3.0.3 / PhysX CPU (macOS, MoltenVK)",
        "stream_source": "official motionplanning demos converted pd_joint_pos->pd_ee_pose via official replay_trajectory tool",
        "stream_sha256": stream_sha256,
        "system_variants": [SYSTEM_NAMES[name] for name in selected],
        "force_limit_n": FORCE_LIMIT_N,
        "tilt_limit_deg": TILT_LIMIT_DEG,
        "episodes": episode_ids,
        "episode_seeds": {str(e): _episode_seed(e) for e in episode_ids},
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


def _summarize(trials: list[Trial]) -> dict[str, dict[str, float | int]]:
    summary: dict[str, dict[str, float | int]] = {}
    for system in SYSTEM_NAMES.values():
        rows = [t for t in trials if t.system == system]
        if not rows:
            continue
        summary[system] = {
            "trials": len(rows),
            "successes": sum(t.success for t in rows),
            "bounded_successes": sum(t.bounded_success for t in rows),
            "refusals": sum(t.refusals for t in rows),
            "force_breaches": sum(t.force_limit_breached for t in rows),
            "tilt_breaches": sum(t.tilt_limit_breached for t in rows),
            "mean_peak_force_n": sum(t.peak_force_n for t in rows) / len(rows),
            "max_peak_force_n": max(t.peak_force_n for t in rows),
            "max_tilt_deg": max(t.max_tilt_deg for t in rows),
            "mean_wall_s": sum(t.wall_s for t in rows) / len(rows),
        }
    return summary


if __name__ == "__main__":
    main()
