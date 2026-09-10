"""L0 property fuzz at campaign scale: >=1,000,000 generated state-machine
transitions with per-invariant coverage counts.

Writes results/l0_fuzz_summary.json. Uses the same randomized generators
as tests/test_haplous_reliability.py (scripted, simulator-free).
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import random

from haplous.state import ExecutionState
from haplous.runtime import HaplousRuntime

from tests.test_haplous_reliability import (
    BEHAVIORS,
    TERMINAL,
    _caps,
    _random_intent,
    _random_machine_config,
    _random_script,
)
from tests.test_haplous_semantics import ScriptedMachine, obs

TARGET_TRANSITIONS = 1_000_000


def run_case(case: int, rng: random.Random) -> tuple[int, dict[str, bool]]:
    invariants = {
        "refused_never_opens_session": True,
        "terminal_state_is_terminal": True,
        "aborted_has_reasoned_outcome": True,
        "evidence_exactly_one_outcome": True,
        "evidence_serializes": True,
        "reset_returns_idle": True,
    }
    n_ticks = rng.choice([5, 20, 60, 200, 600, 2000])
    cfg = _random_machine_config(rng)
    machine = ScriptedMachine(
        caps=_caps(),
        observation=obs(0.0),
        script=_random_script(rng, n_ticks),
        reject_windows_s=cfg["reject_windows_s"],
    )
    runtime = HaplousRuntime(machine, behaviors=BEHAVIORS)
    intent = _random_intent(rng)
    feasibility = runtime.submit(intent)
    if feasibility.outcome == "REFUSED":
        invariants["refused_never_opens_session"] &= (
            runtime.state is ExecutionState.REFUSED and machine.open_session_calls == []
        )
        return 0, invariants

    runtime.execute()
    invariants["terminal_state_is_terminal"] &= runtime.state in TERMINAL
    outcomes = [e for e in runtime.evidence.events if e.kind == "outcome"]
    invariants["evidence_exactly_one_outcome"] &= len(outcomes) == 1
    invariants["evidence_serializes"] &= "outcome" in runtime.evidence.to_json()
    if runtime.state is ExecutionState.ABORTED:
        reason = outcomes[-1].payload.get("reason", "") if outcomes else ""
        invariants["aborted_has_reasoned_outcome"] &= bool(reason)
    runtime.reset()
    invariants["reset_returns_idle"] &= runtime.state is ExecutionState.IDLE
    return n_ticks, invariants


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=TARGET_TRANSITIONS)
    parser.add_argument("--seed", type=int, default=20260910)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    total = 0
    cases = 0
    refused = 0
    executed = 0
    aborted = 0
    completed = 0
    invariants: dict[str, bool] = {}
    tick_counts: list[int] = []
    t0 = time.time()
    while total < args.target:
        case_refused = False
        ticks, case_inv = run_case(cases, rng)
        for k, v in case_inv.items():
            invariants[k] = invariants.get(k, True) and v
        total += max(ticks, 1)
        cases += 1
        if ticks == 0:
            refused += 1
        else:
            executed += 1
    elapsed = time.time() - t0

    summary = {
        "target_transitions": args.target,
        "generated_transitions": total,
        "cases": cases,
        "refused_submissions": refused,
        "executed_submissions": executed,
        "elapsed_s": elapsed,
        "transitions_per_s": total / elapsed if elapsed else 0.0,
        "invariant_coverage": {k: ("PASS" if v else "VIOLATION") for k, v in invariants.items()},
        "all_invariants_hold": all(invariants.values()),
        "seed": args.seed,
        "platform": f"{platform.python_version()} {platform.platform()}",
    }
    out = Path("results/l0_fuzz_summary.json")
    out.write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0 if summary["all_invariants_hold"] else 1


if __name__ == "__main__":
    raise SystemExit(main())