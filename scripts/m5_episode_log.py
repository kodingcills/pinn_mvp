"""M5.2 evidence: one episode per backend logged at the machine-session boundary,
plus an A/A run of the same episode without the log.

    MUJOCO_GL=osmesa PYTHONPATH=. .venv/bin/python scripts/m5_episode_log.py --backend mujoco
    PYTHONPATH=. <robosuite venv>/bin/python scripts/m5_episode_log.py --backend robosuite

The robosuite venv is separate because robosuite 1.5.2 needs mujoco 3.9 and
numpy < 2 (see docs/eval_program/REPRODUCE.md). Each run writes
results/m5/episode_log_<backend>.json (A/A verdict, log schema, outcome) and
the log itself under --log-dir. tests/test_episode_log.py compares the two
committed schemas.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from benchmarks.episode_log import log_schema  # noqa: E402

MUJOCO_SCENARIO = "insert_dropout_large"
ROBOSUITE_SEED = 0


def _mujoco(log_dir: Path) -> dict:
    from benchmarks.recording import run_episode, scenario_by_id

    scenario = scenario_by_id(MUJOCO_SCENARIO)
    plain, plain_real, _ = run_episode(scenario, None)
    logged, logged_real, metadata = run_episode(scenario, log_dir)
    checks = {
        "observations": logged.observations == plain.observations,
        "applied_command_times": logged.detail["applied_command_times"] == plain.detail["applied_command_times"],
        "outcome": (logged.disposition, logged.reason, logged.refusal_codes)
        == (plain.disposition, plain.reason, plain.refusal_codes),
        "final_sim_time": float(logged_real.machine._data.time) == float(plain_real.machine._data.time),
    }
    return {"episode": scenario.id, "aa": checks, "metadata": metadata}


def _robosuite_run(log_dir: Path | None) -> tuple[dict, dict | None]:
    from measure_external_robosuite_lift import lift_intent

    from benchmarks.episode_log import LoggingMachine
    from benchmarks.external.robosuite_lift_policy import LiftPolicy
    from benchmarks.external.robosuite_lift_task import RobosuiteLiftTask
    from haplous import HaplousRuntime
    from haplous.adapters.external import ExternalMachineAdapter

    policy = LiftPolicy()
    task = RobosuiteLiftTask(lambda: policy.gripper_action)
    task.reset(seed=ROBOSUITE_SEED)
    machine = ExternalMachineAdapter(task)
    if log_dir is not None:
        machine = LoggingMachine(machine, log_dir)
    runtime = HaplousRuntime(machine, behaviors={"robosuite_lift": lambda: policy})
    feasibility = runtime.submit(lift_intent(task.observe()))
    evidence = runtime.execute() if feasibility.outcome != "REFUSED" else runtime.evidence
    seen = {
        "evidence": [[e.kind, e.t_s, e.payload] for e in evidence.events],
        "task_result": dict(task.task_result()),
        "ground_truth": dict(task.ground_truth()),
    }
    metadata = None
    if log_dir is not None:
        final = [e for e in evidence.events if e.kind == "outcome"]
        outcome = {
            "disposition": final[-1].payload.get("result") if final else None,
            "reason": final[-1].payload.get("reason") if final else None,
            "refusal_codes": [r.code for r in feasibility.reasons] if feasibility.outcome == "REFUSED" else [],
            "task_success": bool(seen["task_result"]["success"]),
        }
        metadata = machine.finish(evidence, system="haplous_mediated", outcome=outcome,
                                  backend="robosuite-1.5.2", task_id=task.task_id, seed=ROBOSUITE_SEED)
    task.close()
    return seen, metadata


def _robosuite(log_dir: Path) -> dict:
    plain, _ = _robosuite_run(None)
    logged, metadata = _robosuite_run(log_dir)
    checks = {key: json.dumps(logged[key], sort_keys=True, default=str) == json.dumps(plain[key], sort_keys=True, default=str)
              for key in plain}
    return {"episode": f"Lift seed {ROBOSUITE_SEED}", "aa": checks, "metadata": metadata}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("mujoco", "robosuite"), required=True)
    parser.add_argument("--log-dir", type=Path, help="where the episode log goes (default results/m5/robosuite-log, "
                        "or a temp dir for mujoco: its committed counterpart is the M2 recording, 2.6 MB)")
    parser.add_argument("--out", type=Path, help="summary JSON (default results/m5/episode_log_<backend>.json)")
    args = parser.parse_args(argv)
    log_dir = args.log_dir or (ROOT / "results" / "m5" / "robosuite-log" if args.backend == "robosuite"
                               else Path(tempfile.mkdtemp(prefix="m5-mujoco-log-")))
    out = args.out or ROOT / "results" / "m5" / f"episode_log_{args.backend}.json"
    result = (_mujoco if args.backend == "mujoco" else _robosuite)(log_dir)
    meta = result.pop("metadata")
    summary = {
        "backend": args.backend,
        **result,
        "aa_identical": all(result["aa"].values()),
        "schema": log_schema(log_dir),
        "n_ticks": meta["n_ticks"],
        "n_observations": meta["n_observations"],
        "outcome": meta["outcome"],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: summary[k] for k in ("backend", "episode", "aa_identical", "n_ticks", "outcome")}))
    return 0 if summary["aa_identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
