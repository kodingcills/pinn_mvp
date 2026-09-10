"""L00/L01 per-tick Haplous mediation latency instrumentation (ManiSkill/PhysX).

Benchmark-owned timing wrappers only - no haplous core changes. Each mediated
tick is decomposed as:

    tick gap = observe + behavior.step + apply + machine step + runtime residual

where the runtime residual (constraint evaluation, state machine, evidence,
loop overhead) is what mediation adds over the direct loop. The direct arm is
timed with identical wrappers on the same episodes as the baseline residual.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.external import DemoStreamPolicy, ManiSkillPegInsertionTask
from haplous import (
    CommandMode,
    CommandOutcome,
    ExecutionIntent,
    HaplousRuntime,
    MachineCapabilities,
    MachineObservation,
    Requirement,
    RequirementKind,
    ServoCommand,
    SuccessCondition,
)
from haplous.adapters.external import ExternalMachineAdapter

from measure_external_maniskill import (
    FORCE_LIMIT_N,
    TILT_LIMIT_DEG,
    TRAJECTORY_PATH,
    _episode_seed,
    _success,
)


RESULTS_ROOT = ROOT / "results" / "external_eval" / "maniskill-3.0.1"


class TimingSession:
    def __init__(self, inner, machine: "TimingMachine") -> None:
        self._inner = inner
        self._machine = machine

    def observe(self):
        t0 = time.perf_counter_ns()
        value = self._inner.observe()
        self._machine.observe_us.append((time.perf_counter_ns() - t0) / 1000.0)
        return value

    def apply(self, command):
        t0 = time.perf_counter_ns()
        value = self._inner.apply(command)
        self._machine.apply_us.append((time.perf_counter_ns() - t0) / 1000.0)
        return value

    def step(self):
        t0 = time.perf_counter_ns()
        self._inner.step()
        duration = (time.perf_counter_ns() - t0) / 1000.0
        now = time.perf_counter_ns()
        if self._machine._last_step_end is not None:
            self._machine.tick_gaps_us.append((now - self._machine._last_step_end) / 1000.0)
        self._machine._last_step_end = now
        self._machine.step_us.append(duration)

    def stop(self):
        self._inner.stop()

    @property
    def ended(self) -> bool:
        return self._inner.ended

    @property
    def fault(self):
        return self._inner.fault


class TimingMachine:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.observe_us: list[float] = []
        self.apply_us: list[float] = []
        self.step_us: list[float] = []
        self.tick_gaps_us: list[float] = []
        self._last_step_end: int | None = None

    def capabilities(self):
        return self._inner.capabilities()

    def observe(self):
        t0 = time.perf_counter_ns()
        value = self._inner.observe()
        self.observe_us.append((time.perf_counter_ns() - t0) / 1000.0)
        return value

    def open_session(self, **kwargs):
        self._last_step_end = None
        return TimingSession(self._inner.open_session(**kwargs), self)

    def stop(self):
        self._inner.stop()

    def health(self):
        return self._inner.health()


class TimingBehavior:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.step_us: list[float] = []

    def begin(self, intent, caps, observation):
        self._inner.begin(intent, caps, observation)

    def step(self, observation):
        t0 = time.perf_counter_ns()
        value = self._inner.step(observation)
        self.step_us.append((time.perf_counter_ns() - t0) / 1000.0)
        return value


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)

    def pct(fraction: float) -> float:
        index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
        return ordered[index]

    return {
        "n": len(ordered),
        "p50": pct(0.50),
        "p95": pct(0.95),
        "p99": pct(0.99),
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }


def _run_episode(episode_id: int, system: str) -> dict:
    policy = DemoStreamPolicy(TRAJECTORY_PATH, episode_id)
    task = ManiSkillPegInsertionTask(
        lambda: policy.gripper_action,
        lambda: policy.rotation_action,
        stream_length=len(policy._actions),
    )
    task.reset(seed=_episode_seed(episode_id))
    if system == "direct_minimal":
        behavior = TimingBehavior(policy)
        machine = TimingMachine(ExternalMachineAdapter(task))
        session = machine.open_session(
            start_position_m=(0.0, 0.0, 0.0),
            horizon_s=task.capabilities().execution_horizon_s,
        )
        started = time.perf_counter()
        observation = session.observe()
        assert observation is not None
        behavior.begin(None, machine.capabilities(), observation)
        while not session.ended:
            obs = session.observe()
            assert obs is not None
            command = behavior.step(obs)
            session.apply(command)
            session.step()
        wall_s = time.perf_counter() - started
    else:
        inner_machine = ExternalMachineAdapter(task)
        machine = TimingMachine(inner_machine)
        behavior = TimingBehavior(policy)
        runtime = HaplousRuntime(machine, behaviors={"peg_insertion_side": lambda: behavior})
        observation = task.observe()
        assert observation is not None
        intent = ExecutionIntent(
            behavior="peg_insertion_side",
            goal_description="insert peg into the box-side hole",
            goal_position_m=observation.pose_m,
            start_position_m=observation.pose_m,
            requirements=(
                Requirement(RequirementKind.MAX_PROCESS_FORCE_N, True, FORCE_LIMIT_N),
                Requirement(RequirementKind.MAX_TILT_DEG, True, TILT_LIMIT_DEG),
                Requirement(RequirementKind.STATE_MAX_AGE_S, True, 0.1),
            ),
            success_condition=SuccessCondition("peg_inserted", _success, window_s=0.1),
        )
        started = time.perf_counter()
        feasibility = runtime.submit(intent)
        assert feasibility.outcome != "REFUSED"
        runtime.execute()
        wall_s = time.perf_counter() - started
    result = task.task_result()
    task.close()

    gaps = machine.tick_gaps_us
    observe = machine.observe_us
    behavior_us = behavior.step_us
    apply_us = machine.apply_us
    steps = machine.step_us
    n = min(len(gaps), len(steps))
    residuals: list[float] = []
    for i in range(n):
        observation_i = observe[i] if i < len(observe) else 0.0
        behavior_i = behavior_us[i] if i < len(behavior_us) else 0.0
        apply_i = apply_us[i] if i < len(apply_us) else 0.0
        residuals.append(gaps[i] - (observation_i + behavior_i + apply_i + steps[i]))
    return {
        "episode_id": episode_id,
        "system": system,
        "n_ticks": n,
        "n_steps": int(result["n_steps"]),
        "success": bool(result["success"]),
        "wall_s": wall_s,
        "tick_gap_us": _percentiles(gaps),
        "observe_us": _percentiles(observe),
        "behavior_step_us": _percentiles(behavior_us),
        "apply_us": _percentiles(apply_us),
        "machine_step_us": _percentiles(steps),
        "runtime_residual_us": _percentiles(residuals),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", required=True)
    parser.add_argument("--evidence-stage", default="development")
    args = parser.parse_args()
    episode_ids = [int(e) for e in args.episodes.split(",")]

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RESULTS_ROOT / run_id
    run_dir.mkdir(parents=True)

    rows = []
    for episode_id in episode_ids:
        for system in ("direct_minimal", "haplous_mediated"):
            rows.append(_run_episode(episode_id, system))
            print(json.dumps(rows[-1], sort_keys=True), flush=True)

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    summary = {
        "manifest": {
            "campaign": "Haplous final simulation evidence - L00/L01 per-tick mediation latency (ManiSkill/PhysX)",
            "evidence_status": args.evidence_stage,
            "system_commit": commit,
            "method": "benchmark-owned perf_counter_ns wrappers around machine + behavior; runtime residual = tick gap - timed components; direct arm timed with identical wrappers",
            "episodes": episode_ids,
            "clock": "time.perf_counter_ns",
            "completed_at": datetime.now(UTC).isoformat(),
        },
        "episodes": rows,
        "aggregate_stage_percentiles": {
            system: {
                stage: _percentiles(
                    [
                        value
                        for row in rows
                        if row["system"] == system
                        for value in (
                            [row[stage][k] for k in ("p50", "p95", "p99", "max")]
                            if row[stage]
                            else []
                        )
                    ]
                )
                for stage in ("tick_gap_us", "observe_us", "behavior_step_us", "apply_us", "machine_step_us", "runtime_residual_us")
            }
            for system in ("direct_minimal", "haplous_mediated")
        },
    }
    (run_dir / "latency_rows.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(run_dir)


if __name__ == "__main__":
    main()
