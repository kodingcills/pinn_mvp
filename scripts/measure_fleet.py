"""Simulation fleet runner: large randomized 3-arm paired evaluation
(Haplous / competent supervisor / bare) over benchmarks.heb.population
scenarios, with counterfactual intervention forks at every runtime stop
and refusal counterfactuals at every refusal.

Per scenario: one shared machine realization, three arms, benchmark-owned
grading. Forks run only when the mediated arm stops or refuses.

Resumable: append-only JSONL rows keyed by scenario id; already-present
ids are skipped on restart (failure-safe checkpointing).

Writes results/fleet_<phase>_rows.jsonl + results/fleet_<phase>_summary.json.
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.heb.counterfactual import (  # noqa: E402
    InterventionRecordingMachine,
    RecordingBehavior,
    classify_intervention_detailed,
    direct_continuation,
)
from benchmarks.heb.false_refusal import (  # noqa: E402
    classify_refusal,
    run_refusal_counterfactual,
)
from benchmarks.heb.metrics import grade_run, rule_of_three_upper_bound  # noqa: E402
from benchmarks.heb.mujoco_backend import build_realization  # noqa: E402
from benchmarks.heb.population import population  # noqa: E402
from benchmarks.heb.protocol import Scenario, SystemRun  # noqa: E402
from benchmarks.heb.systems import (  # noqa: E402
    BareSystem,
    HaplousSystem,
    SupervisedSystem,
)


def _arm_payload(run: SystemRun, scenario: Scenario, realization) -> dict:
    graded, task_success, bounded, violations, task_metrics = grade_run(
        scenario, realization.bundle, run
    )
    return {
        "disposition": run.disposition,
        "reason": run.reason,
        "refusal_codes": list(run.refusal_codes),
        "graded": graded,
        "task_success": task_success,
        "bounded_success": bounded,
        "hard_violations": violations,
        "task_metrics": task_metrics,
        "wall_s": run.wall_s,
    }


def evaluate_scenario(scenario: Scenario) -> dict:
    """One scenario: 3 arms on a shared realization + counterfactuals."""
    # Realization for the paired arms: a plain machine records nothing.
    realization = build_realization(scenario)

    # Paired arms run on a plain realization (no recording).
    realization = build_realization(scenario)

    # Mediated arm on an intervention-recording twin (same config; only
    # records physics snapshots at runtime-initiated stops).
    inner_factory = realization.behavior_factory
    created: list[RecordingBehavior] = []

    def recording_factory():
        wrapper = RecordingBehavior(inner_factory())
        created.append(wrapper)
        return wrapper

    fork_realization = build_realization(
        scenario,
        machine_factory=InterventionRecordingMachine,
        behavior_factory_override=recording_factory,
    )

    med = HaplousSystem().run(fork_realization, scenario)
    carried = created[-1].snapshot if created else None

    sup = SupervisedSystem().run(realization, scenario)
    bare = BareSystem().run(realization, scenario)

    row = {
        "scenario_id": scenario.id,
        "task_family": scenario.task_family,
        "tags": list(scenario.tags),
        "axes": {
            "initial_offset_m": scenario.initial_offset_m,
            "facet_count": scenario.facet_count,
            "clearance_delta_m": scenario.clearance_delta_m,
            "plate_thickness_m": scenario.plate_thickness_m,
            "max_force_n": scenario.max_force_n,
            "deadline_s": scenario.deadline_s,
            "state_max_age_s": scenario.state_max_age_s,
            "receive_delay_s": scenario.receive_delay_s,
            "n_dropout": len(scenario.dropout_windows_s),
            "n_stale": len(scenario.stale_windows_s),
            "n_rejection": len(scenario.command_rejection_windows_s),
            "fault_at_s": scenario.fault_at_s,
            "n_disturbances": len(scenario.disturbances),
            "recovery_permitted": bool(scenario.recovery_permitted_conditions),
        },
        "arms": {
            "haplous": _arm_payload(med, scenario, fork_realization),
            "supervisor": _arm_payload(sup, scenario, realization),
            "bare": _arm_payload(bare, scenario, realization),
        },
        "counterfactuals": {},
    }

    machine = fork_realization.machine
    reason = med.reason or ""
    runtime_abort = med.disposition == "ABORTED" and not reason.startswith("machine_error")
    forks: dict = {}
    if med.disposition == "REFUSED":
        try:
            cf = run_refusal_counterfactual(machine, realization, scenario)
        except AssertionError:
            # Fork tick inside a corruption window (dropout/stale/delay):
            # snapshot limitation prevents attribution (AMBIGUOUS class).
            forks["refusal"] = {"classification": "AMBIGUOUS", "reason": "fork_observation_unavailable"}
        else:
            forks["refusal"] = {
                "classification": classify_refusal(cf),
                "completed": cf.completed,
                "peak_force_n": cf.peak_force_n,
            }
    elif runtime_abort and machine.intervention_snapshots:
        snapshot = machine.intervention_snapshots[-1]
        intervention_force = max(
            (abs(o.force_z_n) for o in med.observations), default=0.0
        )
        try:
            continuation = direct_continuation(
                fork_realization.machine,
                snapshot,
                fork_realization,
                scenario,
                behavior=carried,
            )
        except AssertionError:
            forks["intervention"] = {
                "classification": "AMBIGUOUS",
                "reason": "fork_observation_unavailable",
            }
        else:
            result = classify_intervention_detailed(
                intervention_force, continuation, hard_force_limit_n=scenario.max_force_n
            )
            forks["intervention"] = {
                "classification": result.classification,
                "beneficial_kind": result.beneficial_kind,
                "bounded_epoch": result.epoch,
                "continuation_bounded_success": continuation.bounded_success,
                "intervention_bounded_success": result.intervention_bounded,
                "continuation_exceedance": result.continuation_exceedance,
                "intervention_exceedance": result.intervention_exceedance,
                "intervention_force_n": intervention_force,
                "continuation_completed": continuation.completed,
                "continuation_task_success": continuation.task_success,
                "continuation_peak_force_n": continuation.peak_force_n,
                "continuation_n_hard_violations": len(continuation.violations),
                "continuation_hard_violations": list(continuation.violations)[:5],
            }
    row["counterfactuals"] = forks
    return row


def _worker(scenario: Scenario) -> dict:
    return evaluate_scenario(scenario)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--n", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 2))
    args = parser.parse_args()

    scenarios = population(args.n, args.seed)
    rows_path = ROOT / "results" / f"fleet_{args.phase}_rows.jsonl"
    summary_path = ROOT / "results" / f"fleet_{args.phase}_summary.json"
    rows_path.parent.mkdir(parents=True, exist_ok=True)
    if summary_path.exists():
        prev = json.loads(summary_path.read_text(encoding="utf-8"))
        if (prev.get("seed"), prev.get("n_scenarios")) != (args.seed, args.n):
            raise SystemExit(
                f"phase '{args.phase}' was run with n/seed "
                f"{(prev.get('n_scenarios'), prev.get('seed'))}; "
                f"refusing to mix with requested {(args.n, args.seed)}"
            )

    done: set[str] = set()
    if rows_path.exists():
        with rows_path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["scenario_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
    pending = [s for s in scenarios if s.id not in done]
    print(f"phase={args.phase} total={len(scenarios)} done={len(done)} pending={len(pending)}")

    t0 = time.perf_counter()
    ctx = multiprocessing.get_context("spawn")
    with rows_path.open("a", encoding="utf-8") as out:
        with ctx.Pool(args.workers) as pool:
            for i, row in enumerate(pool.imap_unordered(_worker, pending, chunksize=4)):
                out.write(json.dumps(row, default=str) + "\n")
                if (i + 1) % 200 == 0:
                    out.flush()
                    el = time.perf_counter() - t0
                    print(
                        f"{i + 1}/{len(pending)} scenarios "
                        f"{el:.0f}s ({(i + 1) / el:.1f}/s)",
                        flush=True,
                    )
    wall = time.perf_counter() - t0

    # Summarize from the full (resumed) file.
    records = [json.loads(l) for l in rows_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    summary = summarize_fleet(records, phase=args.phase, seed=args.seed, wall_s=wall)
    summary_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary["headline"], indent=1))
    return 0


def summarize_fleet(records: list[dict], *, phase: str, seed: int, wall_s: float) -> dict:
    systems = ("haplous", "supervisor", "bare")
    n = len(records)

    def wilson(k: int, n_: int) -> tuple[float, float] | None:
        if n_ == 0:
            return None
        p = k / n_
        z = 1.959963984540054
        den = 1 + z * z / n_
        center = (p + z * z / (2 * n_)) / den
        half = z * math.sqrt(p * (1 - p) / n_ + z * z / (4 * n_ * n_)) / den
        return (max(0.0, center - half), min(1.0, center + half))

    per_system: dict = {}
    for s in systems:
        arms = [r["arms"][s] for r in records]
        k_succ = sum(1 for a in arms if a["task_success"])
        k_bounded = sum(1 for a in arms if a["bounded_success"])
        k_viol = sum(1 for a in arms if a["hard_violations"])
        accepted = sum(1 for a in arms if a["disposition"] != "REFUSED")
        k_b_acc = sum(
            1 for a in arms if a["bounded_success"] and a["disposition"] != "REFUSED"
        )
        refused = sum(1 for a in arms if a["disposition"] == "REFUSED")
        per_system[s] = {
            "n": n,
            "task_success": k_succ / n,
            "task_success_ci": wilson(k_succ, n),
            "bsr_system": k_bounded / n,
            "bsr_system_ci": wilson(k_bounded, n),
            "coverage": accepted / n,
            "bsr_conditional": k_b_acc / accepted if accepted else None,
            "violations": k_viol / n,
            "violations_ci": wilson(k_viol, n),
            "refusals": refused,
            "refusal_rate": refused / n,
            "failure_mass": {},
        }

    # Failure-mass map over failed scenarios (per arm): reason -> count.
    for s in systems:
        reasons: dict[str, int] = {}
        for r in records:
            a = r["arms"][s]
            if not a["task_success"] and a["disposition"] != "COMPLETE":
                key = f"{a['disposition']}:{a['reason']}"
                reasons[key] = reasons.get(key, 0) + 1
        per_system[s]["failure_mass"] = dict(
            sorted(reasons.items(), key=lambda kv: -kv[1])
        )

    # Paired McNemar: haplous vs supervisor, haplous vs bare.
    def mcnemar(a: str, b: str, field: str) -> dict:
        n01 = n10 = 0
        for r in records:
            x = bool(r["arms"][a][field])
            y = bool(r["arms"][b][field])
            if x and not y:
                n10 += 1
            elif y and not x:
                n01 += 1
        # exact binomial two-sided on n10 | discordant
        nd = n10 + n01
        if nd == 0:
            return {"n01": n01, "n10": n10, "p": 1.0}
        from math import comb

        k = min(n10, n01)
        p = sum(comb(nd, i) for i in range(0, k + 1)) * 2 / (2**nd)
        return {"n01": n01, "n10": n10, "p": min(1.0, p)}

    paired = {
        f"haplous_vs_{b}_{f}": mcnemar("haplous", b, f)
        for b in ("supervisor", "bare")
        for f in ("task_success", "bounded_success")
    }

    # Counterfactuals.
    iv = {"BENEFICIAL": 0, "HARMFUL": 0, "UNNECESSARY": 0, "INSUFFICIENT": 0, "AMBIGUOUS": 0}
    ref = {"TRUE": 0, "FALSE": 0, "AMBIGUOUS": 0}
    for r in records:
        cf = r["counterfactuals"]
        if "intervention" in cf:
            c = cf["intervention"]["classification"]
            iv[c] = iv.get(c, 0) + 1
        if "refusal" in cf:
            c = cf["refusal"]["classification"]
            ref[c] = ref.get(c, 0) + 1
    n_attributable = iv["BENEFICIAL"] + iv["HARMFUL"] + iv["UNNECESSARY"] + iv["INSUFFICIENT"]
    forked_ci = wilson(iv["BENEFICIAL"], n_attributable) if n_attributable else None

    # Per-stratum (offset) haplous vs bare task success.
    strata: dict[str, dict] = {}
    for r in records:
        st = next((t for t in r["tags"] if t.startswith("offset:")), "none")
        d = strata.setdefault(
            st, {"haplous_s": 0, "bare_s": 0, "haplous_b": 0, "bare_b": 0, "n": 0}
        )
        d["n"] += 1
        d["haplous_s"] += r["arms"]["haplous"]["task_success"]
        d["bare_s"] += r["arms"]["bare"]["task_success"]
        d["haplous_b"] += r["arms"]["haplous"]["bounded_success"]
        d["bare_b"] += r["arms"]["bare"]["bounded_success"]

    # Violation rare-failure bound for haplous.
    viol_h = sum(1 for r in records if r["arms"]["haplous"]["hard_violations"])

    headline = {
        "haplous": {
            "task_success": per_system["haplous"]["task_success"],
            "bsr_system": per_system["haplous"]["bsr_system"],
            "coverage": per_system["haplous"]["coverage"],
            "violations": per_system["haplous"]["violations"],
        },
        "supervisor": {
            "task_success": per_system["supervisor"]["task_success"],
            "bsr_system": per_system["supervisor"]["bsr_system"],
            "coverage": per_system["supervisor"]["coverage"],
            "violations": per_system["supervisor"]["violations"],
        },
        "bare": {
            "task_success": per_system["bare"]["task_success"],
            "bsr_system": per_system["bare"]["bsr_system"],
            "coverage": per_system["bare"]["coverage"],
            "violations": per_system["bare"]["violations"],
        },
    }

    return {
        "phase": phase,
        "seed": seed,
        "n_scenarios": n,
        "n_trials": 3 * n,
        "wall_s": wall_s,
        "systems": per_system,
        "paired": paired,
        "counterfactuals": {
            "interventions_forked": n_attributable,
            "classifications": iv,
            "beneficial_rate_ci": forked_ci,
            "refusals_forked": sum(ref.values()),
            "refusal_classifications": ref,
            "rule_of_three_violation_upper_bound": rule_of_three_upper_bound(viol_h, n),
        },
        "strata": strata,
        "headline": headline,
    }


if __name__ == "__main__":
    raise SystemExit(main())
