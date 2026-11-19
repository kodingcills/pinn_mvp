"""Issue #18: robosuite Lift on the joint-position surface through HaplousRuntime.

    # robosuite venv (robosuite 1.5.2 + mujoco 3.9.0, docs/eval_program/REPRODUCE.md M5.2)
    PYTHONPATH=. $RS scripts/i18_joint_surface.py episode --seed 0 --log-dir DIR
    PYTHONPATH=. $RS scripts/i18_joint_surface.py holdstop --out results/b4/joint_hold_stop.json
    # main venv: the same episode as a VERIFIED M0 run (the episode runs under $RS)
    PYTHONPATH=. .venv/bin/python scripts/i18_joint_surface.py record --python $RS \\
        --root results/recordings/registry --run-id b4-robosuite-lift-joint-seed0

`episode` exits 0 only when the runtime reports COMPLETE and robosuite's own
`_check_success` agrees. `holdstop` measures the physical response on the
sim clock after a runtime stop() (soft deadline expiry mid-approach) and
during a temporal hold (observation dropout mid-approach).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Speed below which the arm counts as stopped, and the measurement horizon.
STOPPED_RAD_S = 0.01
SETTLE_WINDOW_S = 2.0
STOP_AT_S = 1.0  # soft deadline: the arm is mid-approach to the cube
DROPOUT_S = (1.0, 1.4)


def _run(seed: int, *, log_dir: Path | None = None, dropout=(), deadline_s: float = 12.0):
    from benchmarks.episode_log import LoggingMachine
    from benchmarks.external.robosuite_lift_joint import (
        JointLiftPolicy,
        RobosuiteLiftJointTask,
        lift_joint_intent,
    )
    from haplous.adapters.external import ExternalMachineAdapter
    from haplous.adapters.legacy import sim_runtime

    policy = JointLiftPolicy()
    task = RobosuiteLiftJointTask(lambda: policy.gripper_action, dropout_windows_s=dropout)
    task.reset(seed=seed)
    machine = ExternalMachineAdapter(task)
    if log_dir is not None:
        machine = LoggingMachine(machine, log_dir)
    runtime = sim_runtime(machine, behaviors={"robosuite_lift_joint": lambda: policy})
    feasibility = runtime.submit(lift_joint_intent(task.observe(), deadline_s=deadline_s))
    evidence = runtime.execute() if feasibility.outcome != "REFUSED" else runtime.evidence
    final = [e for e in evidence.events if e.kind == "outcome"][-1].payload
    outcome = {
        "disposition": final.get("result"),
        "reason": final.get("reason"),
        "refusal_codes": [r.code for r in feasibility.reasons] if feasibility.outcome == "REFUSED" else [],
        "task_success": bool(task.task_result()["success"]),
    }
    if log_dir is not None:
        machine.finish(evidence, system="haplous_mediated", outcome=outcome, backend="robosuite-1.5.2",
                       task_id=task.task_id, seed=seed, command_surface="joint_position")
    return task, evidence, outcome


def episode(args) -> int:
    log_dir = args.log_dir or (Path(os.environ["M0_ARTIFACT_DIR"]) if "M0_ARTIFACT_DIR" in os.environ else None)
    task, evidence, outcome = _run(args.seed, log_dir=log_dir)
    started = [e.payload for e in evidence.events if e.kind == "motion_started"]
    print(json.dumps({"seed": args.seed, "outcome": outcome, "motion_started": started,
                      "task_result": dict(task.task_result())}, sort_keys=True))
    task.close()
    return 0 if outcome["disposition"] == "COMPLETE" and outcome["task_success"] else 1


def _response(trace: list[dict], t0: float, target_q: list[float], end_s: float) -> dict:
    """Physical response from the first control step at or after t0 until end_s."""
    window = [s for s in trace if t0 <= s["t_s"] <= end_s + 1e-9]
    at = window[0]
    speeds = [max(abs(v) for v in s["qdot"]) for s in window]
    # Overshoot: the farthest any joint travels past its hold target, in the
    # direction it was moving at t0 (0 when it never crosses the target).
    direction = [1.0 if v >= 0 else -1.0 for v in at["qdot"]]
    past = [max(0.0, max(d * (q - g) for d, q, g in zip(direction, s["q"], target_q, strict=True))) for s in window]
    stopped = next((s["t_s"] for s, v in zip(window, speeds, strict=True) if v < STOPPED_RAD_S), None)
    eef0 = at["eef_m"]
    travel = max(sum((a - b) ** 2 for a, b in zip(s["eef_m"], eef0, strict=True)) ** 0.5 for s in window)
    return {
        "event_t_s": round(at["t_s"], 6),
        "speed_at_event_rad_s": max(abs(v) for v in at["qdot"]),
        "time_to_stop_s": None if stopped is None else round(stopped - at["t_s"], 6),
        "stopped_threshold_rad_s": STOPPED_RAD_S,
        "overshoot_rad": max(past),
        "max_target_error_rad": max(max(abs(q - g) for q, g in zip(s["q"], target_q, strict=True)) for s in window),
        "eef_travel_after_event_m": travel,
        "final_speed_rad_s": speeds[-1],
        "sample_period_s": round(window[1]["t_s"] - window[0]["t_s"], 6),
        "window_end_s": round(window[-1]["t_s"], 6),
    }


def holdstop(args) -> int:
    # 1. stop(): the runtime's deadline expiry calls machine.stop() mid-motion.
    task, evidence, outcome = _run(args.seed, deadline_s=STOP_AT_S)
    assert outcome["reason"] == "deadline_exceeded", outcome
    stop_t = [e.t_s for e in evidence.events if e.kind == "outcome"][-1]
    for _ in range(round(SETTLE_WINDOW_S / task.capabilities().state_period_s)):
        task.step()  # the world keeps advancing; the controller holds the latched position
    t_stop_cmd = next(s["t_s"] for s in task.trace if s["t_s"] > stop_t)
    stop_target = task.trace[-1]["target_q"]
    stop = _response(task.trace, stop_t, stop_target, stop_t + SETTLE_WINDOW_S)
    stop["runtime_stop_t_s"] = stop_t
    stop["first_held_step_t_s"] = t_stop_cmd
    stop["stop_response_declared"] = task.capabilities().stop_response.value
    task.close()

    # 2. temporal hold: observations drop out, the runtime withholds commands,
    # the machine keeps servoing to its last accepted joint target.
    task, evidence, outcome = _run(args.seed, dropout=(DROPOUT_S,))
    kinds = [(e.kind, e.t_s, e.payload) for e in evidence.events]
    entered = next(t for k, t, p in kinds if k == "temporal_hold_entered")
    resumed = next(t for k, t, p in kinds if k == "temporal_revalidation_resumed")
    # The sample at `entered` predates that tick's command; from the next
    # step on, the target in force is the last accepted command.
    held = [s for s in task.trace if entered < s["t_s"] <= resumed + 1e-9]
    hold = _response(task.trace, entered, held[0]["target_q"], resumed)
    hold["hold_entered_t_s"] = entered
    hold["hold_resumed_t_s"] = resumed
    hold["target_constant_during_hold"] = all(s["target_q"] == held[0]["target_q"] for s in held)
    hold["episode_outcome"] = outcome
    task.close()

    result = {
        "issue": 18,
        "surface": "joint_position (robosuite 1.5.2 JointPositionController, absolute input, kp=50, damping_ratio=1)",
        "task": f"robosuite/1.5.2/Lift, seed {args.seed}",
        "clock": "sim (robosuite env.sim.data.time); samples are control steps, so times resolve to one period",
        "after_stop": stop,
        "during_temporal_hold": hold,
        "limits": "HARDWARE_NOT_VALIDATED: simulated controller and plant; no real Panda measured",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"after_stop": stop, "during_temporal_hold": hold}, indent=1, sort_keys=True))
    return 0


def record(args) -> int:
    from evaluation.run_registry import REPO_ROOT, Registry, record_run

    command = [args.python, str(Path(__file__).resolve()), "episode", "--seed", str(args.seed)]
    result = record_run(
        Registry(args.root),
        run_id=args.run_id,
        track="sim_policy_free",
        purpose=f"#18 robosuite Lift seed {args.seed} on the joint-position surface (haplous_mediated)",
        command=command,
        config={"task": "robosuite/1.5.2/Lift", "seed": args.seed, "command_surface": "joint_position",
                "controller": "robosuite JointPositionController, absolute input", "system": "haplous_mediated"},
        simulator={"name": "robosuite", "version": "1.5.2"},
        dependencies=("numpy", "jsonschema", "cryptography"),
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(REPO_ROOT), "MUJOCO_GL": os.environ.get("MUJOCO_GL", "osmesa")},
        subject_python=args.python,
        subject_dependencies=("robosuite", "mujoco", "numpy"),
    )
    print(json.dumps({k: result[k] for k in ("run_id", "state", "outcome", "manifest_sha256")}, indent=2))
    return 0 if result["state"] == "VERIFIED" and result["outcome"] == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("episode")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--log-dir", type=Path)
    p = sub.add_parser("holdstop")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", type=Path, default=ROOT / "results" / "b4" / "joint_hold_stop.json")
    p = sub.add_parser("record")
    p.add_argument("--python", required=True)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    return {"episode": episode, "holdstop": holdstop, "record": record}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
