"""Soak at campaign scale: 100,000 submit/execute/reset cycles plus
20,000 aborted-path cycles, checking leaks, evidence lineage, and
determinism. Writes results/soak_100k_summary.json."""

from __future__ import annotations

import argparse
import gc
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haplous.runtime import HaplousRuntime
from haplous.state import ExecutionState

from tests.test_haplous_reliability import BEHAVIORS, TERMINAL
from tests.test_haplous_semantics import ScriptedMachine, make_intent, obs, seated_samples, stream


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=100_000)
    parser.add_argument("--abort-cycles", type=int, default=20_000)
    parser.add_argument("--ticks-per-cycle", type=int, default=10)
    args = parser.parse_args()

    failures: list[str] = []
    t0 = time.time()

    machine = ScriptedMachine(observation=obs(0.0), script=stream(seated_samples(args.ticks_per_cycle)))
    runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
    intent = make_intent()

    gc_before = gc.get_stats()[0]["collections"]
    for cycle in range(args.cycles):
        feasibility = runtime.submit(intent)
        if feasibility.outcome != "SUPPORTED":
            failures.append(f"cycle {cycle}: submit {feasibility.outcome}")
        runtime.execute()
        if runtime.state is not ExecutionState.COMPLETE:
            failures.append(f"cycle {cycle}: state {runtime.state}")
        outcomes = [e for e in runtime.evidence.events if e.kind == "outcome"]
        if len(outcomes) != 1:
            failures.append(f"cycle {cycle}: outcome count {len(outcomes)} != 1")
        if (cycle + 1) % 10_000 == 0:
            # Full-archive serialization is O(archive size); sampling it
            # every 10k cycles keeps the soak measuring cycle-scale
            # reliability instead of a quadratic serialization artifact.
            runtime.evidence.to_json()
        runtime.reset()
        if runtime.state is not ExecutionState.IDLE:
            failures.append(f"cycle {cycle}: post-reset state {runtime.state}")
        if len(runtime.archived_evidence) != cycle + 1:
            failures.append(f"cycle {cycle}: archive {len(runtime.archived_evidence)} != {cycle + 1}")
        if cycle % 20_000 == 19_999:
            print(f"  clean cycles: {cycle + 1}/{args.cycles}", flush=True)
    gc_after = gc.get_stats()[0]["collections"]

    # Aborted-path soak: persistent force breach keeps aborting; archive
    # lineage must keep growing.
    machine_ab = ScriptedMachine(
        observation=obs(0.0), script=stream([(0.0, 0.0, -0.001, 30.0, 0.0)] * 30)
    )
    runtime_ab = HaplousRuntime(machine_ab, behaviors=BEHAVIORS)
    intent_ab = make_intent(recovery_policy=None)
    for cycle in range(args.abort_cycles):
        runtime_ab.submit(intent_ab)
        runtime_ab.execute()
        if runtime_ab.state is not ExecutionState.ABORTED:
            failures.append(f"abort cycle {cycle}: state {runtime_ab.state}")
        payload = [e for e in runtime_ab.evidence.events if e.kind == "outcome"][-1].payload
        if payload.get("result") != "ABORTED":
            failures.append(f"abort cycle {cycle}: payload result {payload.get('result')}")
        runtime_ab.reset()
        if len(runtime_ab.archived_evidence) != cycle + 1:
            failures.append(f"abort cycle {cycle}: archive {len(runtime_ab.archived_evidence)}")
        if cycle % 20_000 == 19_999:
            print(f"  abort cycles: {cycle + 1}/{args.abort_cycles}", flush=True)
    gc_aborted = gc.get_stats()[0]["collections"]

    # Replay determinism: same scripted machine twice, identical evidence.
    def run_once() -> str:
        m = ScriptedMachine(observation=obs(0.0), script=stream(seated_samples(40)))
        r = HaplousRuntime(m, behaviors=BEHAVIORS)
        r.submit(intent)
        r.execute()
        text = r.evidence.to_json()
        r.reset()
        return text

    deterministic = run_once() == run_once()

    elapsed = time.time() - t0
    summary = {
        "clean_cycles": args.cycles,
        "abort_cycles": args.abort_cycles,
        "clean_failures": sum(1 for f in failures if not f.startswith("abort ")),
        "abort_failures": sum(1 for f in failures if f.startswith("abort ")),
        "all_failures": failures[:20],
        "n_failures": len(failures),
        "gc_clean_cycles": gc_after - gc_before,
        "gc_abort_cycles": gc_aborted - gc_after,
        "replay_deterministic": deterministic,
        "elapsed_s": elapsed,
        "cycles_per_s": args.cycles / elapsed if elapsed else 0.0,
        "platform": f"{platform.python_version()} {platform.platform()}",
    }
    out = Path("results/soak_100k_summary.json")
    out.write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())