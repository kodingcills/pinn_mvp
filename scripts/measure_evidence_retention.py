"""Evidence-retention benchmark (campaign spec sec. 6): soak execution/
reset cycles with a durable evidence sink, measuring steady-state RAM,
per-cycle cost vs lifetime, evidence accounting, and replay integrity.

Writes results/evidence_retention_benchmark.json.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import resource
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haplous.evidence_store import JsonlEvidenceStore
from haplous.runtime import HaplousRuntime
from haplous.state import ExecutionState

from tests.test_haplous_reliability import BEHAVIORS
from tests.test_haplous_semantics import (
    ScriptedMachine,
    make_intent,
    obs,
    seated_samples,
    stream,
)

TERMINAL = {ExecutionState.COMPLETE, ExecutionState.ABORTED}


def rss_peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def live_objects() -> int:
    return len(gc.get_objects())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=1_000_000)
    parser.add_argument("--abort-cycles", type=int, default=0)
    parser.add_argument("--ticks-per-cycle", type=int, default=10)
    parser.add_argument("--max-archived", type=int, default=64)
    parser.add_argument("--store-dir", default="/var/folders/fm/2lpfzkpx72568chshr2qznw00000gn/T/opencode/evidence_soak")
    parser.add_argument("--checkpoint-every", type=int, default=100_000)
    args = parser.parse_args()

    store_dir = Path(args.store_dir)
    if store_dir.exists():
        shutil.rmtree(store_dir)
    store = JsonlEvidenceStore(store_dir)

    machine = ScriptedMachine(observation=obs(0.0), script=stream(seated_samples(args.ticks_per_cycle)))
    runtime = HaplousRuntime(
        machine, behaviors=BEHAVIORS, evidence_sink=store, max_archived_evidence=args.max_archived
    )
    intent = make_intent()

    failures: list[str] = []
    checkpoints: list[dict] = []
    # Fixed-size timing window: a growing per-cycle list would make the
    # HARNESS the memory-growth source and corrupt the boundedness
    # measurement.
    from collections import deque

    per_cycle_us: deque = deque(maxlen=args.checkpoint_every)
    t0 = time.perf_counter()
    gc_before = gc.get_stats()[0]["collections"]

    for cycle in range(args.cycles):
        tc = time.perf_counter()
        runtime.submit(intent)
        runtime.execute()
        if runtime.state is not ExecutionState.COMPLETE:
            failures.append(f"cycle {cycle}: state {runtime.state}")
        runtime.reset()
        per_cycle_us.append((time.perf_counter() - tc) * 1e6)
        if (cycle + 1) % args.checkpoint_every == 0:
            elapsed = time.perf_counter() - t0
            window = list(per_cycle_us)
            checkpoints.append(
                {
                    "cycles": cycle + 1,
                    "elapsed_s": elapsed,
                    "rss_peak_mb": rss_peak_mb(),
                    "live_objects": live_objects(),
                    "retained": runtime.evidence_retained,
                    "flushed": runtime.flushed_evidence_count,
                    "store_records": store.entries_written,
                    "cycle_us_p50_us": sorted(window)[len(window) // 2],
                    "cycle_us_p99_us": sorted(window)[int(len(window) * 0.99)],
                }
            )
            print(json.dumps(checkpoints[-1]), flush=True)

    for cycle in range(args.abort_cycles):
        abort_machine = ScriptedMachine(
            observation=obs(0.0), script=stream([(0.0, 0.0, -0.001, 30.0, 0.0)] * 30)
        )
        abort_runtime = HaplousRuntime(
            abort_machine, behaviors=BEHAVIORS, evidence_sink=store, max_archived_evidence=args.max_archived
        )
        abort_runtime.submit(make_intent(recovery_policy=None))
        abort_runtime.execute()
        if abort_runtime.state is not ExecutionState.ABORTED:
            failures.append(f"abort cycle {cycle}: state {abort_runtime.state}")
        abort_runtime.reset()

    total = args.cycles + args.abort_cycles
    elapsed = time.perf_counter() - t0
    gc_after = gc.get_stats()[0]["collections"]

    # Replay integrity: every record parses, archive_seq strictly
    # increasing, count matches flush accounting.
    replay_failures = 0
    last_seq = 0
    n_replayed = 0
    for record in store.replay():
        seq = record["archive_seq"]
        if seq <= last_seq:
            replay_failures += 1
        last_seq = seq
        n_replayed += 1
    store.close()

    retained = runtime.evidence_retained
    flushed = runtime.flushed_evidence_count
    accounting_ok = (retained + flushed == total) and (n_replayed == flushed)

    # Lifetime cost trend comes from checkpoint windows (first vs last),
    # not list halves: the deque is intentionally bounded.
    first_p50 = checkpoints[0]["cycle_us_p50_us"]
    last_p50 = checkpoints[-1]["cycle_us_p50_us"]
    first_p99 = checkpoints[0]["cycle_us_p99_us"]
    last_p99 = checkpoints[-1]["cycle_us_p99_us"]
    summary = {
        "cycles": args.cycles,
        "abort_cycles": args.abort_cycles,
        "max_archived": args.max_archived,
        "n_failures": len(failures),
        "failures_head": failures[:10],
        "replay_failures": replay_failures,
        "replayed_records": n_replayed,
        "accounting_ok": accounting_ok,
        "retained_at_end": retained,
        "flushed_at_end": flushed,
        "rss_peak_mb": rss_peak_mb(),
        "gc_collections": gc_after - gc_before,
        "cycle_us_first_checkpoint_p50": first_p50,
        "cycle_us_last_checkpoint_p50": last_p50,
        "cycle_us_first_checkpoint_p99": first_p99,
        "cycle_us_last_checkpoint_p99": last_p99,
        "cost_growth_ratio_p50": last_p50 / max(first_p50, 1e-9),
        "rss_growth_mb_over_run": checkpoints[-1]["rss_peak_mb"] - checkpoints[0]["rss_peak_mb"],
        "live_objects_first_checkpoint": checkpoints[0]["live_objects"],
        "live_objects_last_checkpoint": checkpoints[-1]["live_objects"],
        "elapsed_s": elapsed,
        "checkpoints": checkpoints,
        "platform": f"{platform.python_version()} {platform.platform()}",
    }
    Path("results/evidence_retention_benchmark.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "checkpoints"}, indent=1))
    ok = (
        not failures
        and replay_failures == 0
        and accounting_ok
        and summary["cost_growth_ratio_p50"] < 1.5
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())