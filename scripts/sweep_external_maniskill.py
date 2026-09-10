"""Single-factor robustness sweep: frozen official peg stream under perturbed
initial realizations (S00/S01) with per-stage latency instrumentation (L00).

The frozen stream for episode E is replayed while the environment is reset
with perturbation seeds S != E's recorded seed, producing different initial
peg-hole offsets. Both arms receive the identical stale stream; the axis is
the measured initial peg-hole offset at reset. Cross-physics alignment: the
internal MuJoCo HEB offset sweep (results/heb_sweeps_dev.json) is the other
engine's same-axis curve.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
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

sys.path.insert(0, str(ROOT / "scripts"))
from measure_external_maniskill import (
    FORCE_LIMIT_N,
    TILT_LIMIT_DEG,
    TRAJECTORY_JSON_PATH,
    TRAJECTORY_PATH,
    _episode_seed,
)


RESULTS_ROOT = ROOT / "results" / "external_eval" / "maniskill-3.0.1"


@dataclass(frozen=True, slots=True)
class SweepTrial:
    episode_id: int
    perturbation_seed: int
    initial_offset_m: float
    system: str
    success: bool
    bounded_success: bool
    peak_force_n: float
    max_tilt_deg: float
    completion_time_s: float | None
    n_steps: int
    disposition: str
    outcome_reason: str | None
    wall_s: float


def _run_direct(policy: DemoStreamPolicy, task: ManiSkillPegInsertionTask) -> tuple[SweepTrial, float]:
    started = time.perf_counter()
    max_tilt = 0.0
    while not task.session_ended():
        obs = task.observe()
        assert obs is not None
        max_tilt = max(max_tilt, obs.tilt_deg)
        task.apply_command(policy.step(obs))
        task.step()
    wall = time.perf_counter() - started
    result = task.task_result()
    peak = float(result["peak_force_n"])
    success = bool(result["success"])
    trial = SweepTrial(
        episode_id=-1,
        perturbation_seed=-1,
        initial_offset_m=0.0,
        system="direct_minimal",
        success=success,
        bounded_success=success and peak <= FORCE_LIMIT_N and max_tilt <= TILT_LIMIT_DEG,
        peak_force_n=peak,
        max_tilt_deg=max_tilt,
        completion_time_s=result["completion_time_s"],
        n_steps=int(result["n_steps"]),
        disposition="COMPLETED" if success else "FAILED_TIMEOUT",
        outcome_reason=None,
        wall_s=wall,
    )
    return trial, wall


def _run_haplous(policy: DemoStreamPolicy, task: ManiSkillPegInsertionTask) -> tuple[SweepTrial, float]:
    started = time.perf_counter()
    obs = task.observe()
    assert obs is not None
    runtime = HaplousRuntime(
        ExternalMachineAdapter(task),
        behaviors={"peg_insertion_side": lambda: policy},
    )
    intent = ExecutionIntent(
        behavior="peg_insertion_side",
        goal_description="insert peg into the box-side hole under perturbed initial realization",
        goal_position_m=obs.pose_m,
        start_position_m=obs.pose_m,
        requirements=(
            Requirement(RequirementKind.MAX_PROCESS_FORCE_N, True, FORCE_LIMIT_N),
            Requirement(RequirementKind.MAX_TILT_DEG, True, TILT_LIMIT_DEG),
            Requirement(RequirementKind.STATE_MAX_AGE_S, True, 0.1),
            Requirement(RequirementKind.DEADLINE_S, False, task.capabilities().expected_duration_s + 10.0),
        ),
        success_condition=SuccessCondition(
            "peg_inserted",
            lambda observations: bool(observations and observations[-1].extra_value("task_success") == 1.0),
            window_s=0.1,
        ),
    )
    feasibility = runtime.submit(intent)
    evidence = runtime.execute() if feasibility.outcome != "REFUSED" else runtime.evidence
    wall = time.perf_counter() - started
    result = task.task_result()
    violations = evidence.constraint_violations()
    force_breach = any(v["code"] == "process_force" for v in violations)
    stale_breach = any(v["code"] == "state_freshness" for v in violations)
    outcome_events = [e for e in evidence.events if e.kind == "outcome"]
    disposition = str(outcome_events[-1].payload.get("result", "UNKNOWN")) if outcome_events else "UNKNOWN"
    reason = str(outcome_events[-1].payload.get("reason", "")) or None if outcome_events else None
    peak = float(result["peak_force_n"])
    success = bool(result["success"])
    trial = SweepTrial(
        episode_id=-1,
        perturbation_seed=-1,
        initial_offset_m=0.0,
        system="haplous_mediated",
        success=success,
        bounded_success=success and not force_breach and not stale_breach,
        peak_force_n=peak,
        max_tilt_deg=task._peak_tilt_deg,
        completion_time_s=result["completion_time_s"],
        n_steps=int(result["n_steps"]),
        disposition=disposition,
        outcome_reason=reason,
        wall_s=wall,
    )
    return trial, wall


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", type=int, required=True, help="frozen stream episode id")
    parser.add_argument("--seeds", required=True, help="comma-separated perturbation seeds")
    parser.add_argument("--systems", default="direct,haplous")
    parser.add_argument("--evidence-stage", default="development")
    args = parser.parse_args()
    perturbation_seeds = [int(s) for s in args.seeds.split(",")]
    selected = [s.strip() for s in args.systems.split(",")]

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RESULTS_ROOT / run_id
    run_dir.mkdir(parents=True)

    policy_holder: list[DemoStreamPolicy] = []

    def make_policy() -> DemoStreamPolicy:
        policy = DemoStreamPolicy(TRAJECTORY_PATH, args.episode)
        policy_holder.append(policy)
        return policy

    trials: list[SweepTrial] = []
    for seed in perturbation_seeds:
        for system in selected:
            policy = make_policy()
            task = ManiSkillPegInsertionTask(
                lambda p=policy: p.gripper_action,
                lambda p=policy: p.rotation_action,
                stream_length=len(policy._actions),
            )
            task.reset(seed=seed)
            truth = task.ground_truth()
            offset = ((truth["peg_x_m"] - truth["hole_x_m"]) ** 2 + (truth["peg_y_m"] - truth["hole_y_m"]) ** 2) ** 0.5
            if system == "haplous":
                trial, _ = _run_haplous(policy, task)
            else:
                trial, _ = _run_direct(policy, task)
            trial = SweepTrial(
                episode_id=args.episode,
                perturbation_seed=seed,
                initial_offset_m=offset,
                **{k: getattr(trial, k) for k in (
                    "system", "success", "bounded_success", "peak_force_n",
                    "max_tilt_deg", "completion_time_s", "n_steps",
                    "disposition", "outcome_reason", "wall_s",
                )},
            )
            trials.append(trial)
            print(json.dumps(asdict(trial), sort_keys=True), flush=True)
            task.close()

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    manifest = {
        "campaign": "Haplous final simulation evidence - S00/S01 peg robustness sweep (Mode A frozen stream, perturbed initial realization)",
        "evidence_status": args.evidence_stage,
        "haplous_version": haplous_version,
        "system_commit": commit,
        "benchmark": "maniskill/3.0.1/PegInsertionSide",
        "physics_backend": "sapien 3.0.3 / PhysX CPU (macOS, MoltenVK)",
        "stream_sha256": hashlib.sha256(TRAJECTORY_PATH.read_bytes()).hexdigest(),
        "frozen_episode": args.episode,
        "recorded_seed": _episode_seed(args.episode),
        "perturbation_seeds": perturbation_seeds,
        "force_limit_n": FORCE_LIMIT_N,
        "tilt_limit_deg": TILT_LIMIT_DEG,
        "started_at": run_id,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    summary = {"manifest": manifest, "systems": _sweep_summary(trials)}
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with (run_dir / "trials.jsonl").open("x", encoding="utf-8") as stream:
        for trial in trials:
            stream.write(json.dumps(asdict(trial), sort_keys=True) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(run_dir)


def _sweep_summary(trials: list[SweepTrial]) -> dict:
    systems: dict[str, list[SweepTrial]] = {}
    for trial in trials:
        systems.setdefault(trial.system, []).append(trial)
    out = {}
    for system, rows in systems.items():
        out[system] = {
            "trials": len(rows),
            "successes": sum(t.success for t in rows),
            "bounded_successes": sum(t.bounded_success for t in rows),
            "max_peak_force_n": max(t.peak_force_n for t in rows),
            "mean_peak_force_n": sum(t.peak_force_n for t in rows) / len(rows),
            "dispositions": dict(
                sorted(
                    (d, sum(1 for t in rows if t.disposition == d))
                    for d in {t.disposition for t in rows}
                )
            ),
            "outcome_reasons": dict(
                sorted(
                    (r, sum(1 for t in rows if (t.outcome_reason or "none") == r))
                    for r in {t.outcome_reason or "none" for t in rows}
                )
            ),
        }
    return out


if __name__ == "__main__":
    main()
