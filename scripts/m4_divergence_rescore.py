"""Re-score the sealed M4 campaign rows under HEB 0.3.0 (issue #21: sim divergence).

M4 (`results/m4/campaign/raw_rows.jsonl`, epoch hes-m4-e2) graded episodes that ran
on after a MuJoCo divergence and auto-reset. The sealed rows carry no per-episode
divergence evidence, so this driver re-executes, with the HEB 0.3.0 divergence guard
on (the default `build_realization` machine), through the M4 executor
(`m4_campaign.evaluate_with_digest`):

- every scenario a proxy flags in either population, all four arms: some row has
  `max_tilt_deg > 90` or non-monotone hard-violation times;
- an audit of AUDIT_N proxy-clean scenarios per population (fixed seed), the two
  confirmatory arms only, to bound how many divergences the proxy misses.

Every other row keeps its sealed score. A re-executed row that did not diverge must
reproduce its sealed outcome and applied-command digest; any mismatch is reported.
The sealed M4 files are never written.

    MUJOCO_GL=osmesa PYTHONPATH=. .venv/bin/python scripts/m4_divergence_rescore.py --estimate
    MUJOCO_GL=osmesa PYTHONPATH=. .venv/bin/python scripts/m4_divergence_rescore.py --workers 4

Writes results/m4_divergence_rescore/{rescored_rows.jsonl,summary.json}.
"""
from __future__ import annotations

import argparse
import json
import random
import resource
import sys
import time
from itertools import pairwise
from multiprocessing import Pool
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import m4_campaign as m4  # noqa: E402

ROWS = REPO / "results" / "m4" / "campaign" / "raw_rows.jsonl"
OUT = REPO / "results" / "m4_divergence_rescore"
SCORING = {"heb_version": "0.3.0", "scoring_epoch": "hes-m4-e3-simdiv", "replaces_for_these_rows": "hes-m4-e2"}
CONFIRMATORY_ARMS = ("haplous_mediated", "g1_incumbent")
AUDIT_N, AUDIT_SEED = 30, 21
CPU_BUDGET_S = 25 * 60
OUTCOME = ("reported_disposition", "reason", "graded_disposition", "task_success", "bounded_success",
           "n_hard_violations")


def suspect(row: dict) -> bool:
    tilt = row["task_metrics"].get("max_tilt_deg")
    ts = [v["t_s"] for v in row["hard_violations"]]
    return (tilt is not None and tilt > 90) or any(b < a for a, b in pairwise(ts))


def pop_key(row: dict, cfg: dict) -> str:
    return next(k for k, spec in cfg["populations"].items() if spec["id"] == row["metadata"]["population_id"])


def plan(rows: list[dict], cfg: dict) -> list[dict]:
    items: dict[tuple[str, str], dict] = {}
    for r in rows:
        if suspect(r):
            items[(pop_key(r, cfg), r["scenario_id"])] = {"arms": list(cfg["arms"]["all"]), "why": "proxy"}
    rng = random.Random(AUDIT_SEED)
    for pop in cfg["populations"]:
        clean = sorted({r["scenario_id"] for r in rows if pop_key(r, cfg) == pop} - {s for p, s in items if p == pop})
        for sid in rng.sample(clean, AUDIT_N):
            items[(pop, sid)] = {"arms": list(CONFIRMATORY_ARMS), "why": "audit"}
    return [{"population": p, "scenario_id": s, **v} for (p, s), v in sorted(items.items())]


def estimate_cpu_s(items: list[dict], rows: list[dict], cfg: dict) -> float:
    cpu = {(pop_key(r, cfg), r["scenario_id"], r["arm"]): r["task_metrics"]["cpu_s"] for r in rows}
    return sum(cpu[(i["population"], i["scenario_id"], a)] for i in items for a in i["arms"])


def run_item(item: dict) -> list[dict]:
    from benchmarks.heb.systems import system_by_name

    cfg = m4.load_config()
    scenario = next(s for s in m4.generate(cfg["populations"][item["population"]]) if s.id == item["scenario_id"])
    records = m4.evaluate_with_digest([scenario], [system_by_name(a) for a in item["arms"]])
    out = []
    for rec in records:
        metrics = {k: v for k, v in rec.task_metrics.items() if k not in ("applied_command_times", "wall_s")}
        out.append({
            "population": item["population"], "scenario_id": rec.scenario_id, "arm": rec.system, "why": item["why"],
            "reported_disposition": rec.reported_disposition, "reason": rec.reason,
            "graded_disposition": rec.graded_disposition, "task_success": rec.task_success,
            "bounded_success": rec.bounded_success, "n_hard_violations": len(rec.hard_violations),
            "hard_violations": list(rec.hard_violations), "task_metrics": metrics,
        })
    return out


def confirmatory(rows: list[dict], cfg: dict) -> dict:
    from hes.regression import paired_rate_delta

    stats, out = cfg["statistics"], {}
    for item in cfg["confirmatory"]:
        pop_rows = [r for r in rows if r["population"] == item["population"]]
        by_arm = {a: [r for r in pop_rows if r["arm"] == a] for a in (item["candidate"], item["reference"])}
        d = paired_rate_delta(by_arm[item["candidate"]], by_arm[item["reference"]], field=item["field"],
                              resamples=stats["bootstrap_resamples"], seed=stats["bootstrap_seed"])
        out[item["id"]] = {"population": item["population"], "n_pairs": d["n_pairs"], "delta": d["delta"],
                           "ci": [d["ci_low"], d["ci_high"]],
                           "decision": m4.decide(d["ci_low"], d["ci_high"], stats["equivalence_margin"])}
    return out


def rates(rows: list[dict], cfg: dict) -> dict:
    out = {}
    for pop in cfg["populations"]:
        out[pop] = {a: {f: sum(bool(r[f]) for r in rows if r["population"] == pop and r["arm"] == a)
                        for f in ("task_success", "bounded_success")}
                    for a in cfg["arms"]["all"]}
    return out


def variant(name: str, rows: list[dict], cfg: dict) -> dict:
    return {"variant": name, "n_rows": len(rows), "successes": rates(rows, cfg), "confirmatory": confirmatory(rows, cfg)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--estimate", action="store_true", help="print the plan's CPU estimate and stop")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cfg = m4.load_config()
    sealed = [json.loads(line) for line in ROWS.read_text().splitlines()]
    for r in sealed:
        r["population"] = pop_key(r, cfg)
    items = plan(sealed, cfg)
    est = estimate_cpu_s(items, sealed, cfg)
    print(f"{len(items)} scenarios, {sum(len(i['arms']) for i in items)} episodes, "
          f"estimated {est / 60:.1f} CPU-min (sealed per-episode cpu_s)")
    if args.estimate:
        return 0
    if est > CPU_BUDGET_S:
        print(f"estimate exceeds the {CPU_BUDGET_S / 60:.0f} CPU-min budget; not running")
        return 2

    t0 = time.time()
    with Pool(args.workers) as pool:
        reruns = [row for rows in pool.map(run_item, items, chunksize=1) for row in rows]
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_used = children.ru_utime + children.ru_stime

    by_key = {(r["population"], r["scenario_id"], r["arm"]): r for r in sealed}
    out_rows, mismatches, diverged = [], [], []
    for new in sorted(reruns, key=lambda r: (r["population"], r["scenario_id"], r["arm"])):
        old = by_key[(new["population"], new["scenario_id"], new["arm"])]
        div = new["task_metrics"].get("sim_diverged")
        if div:
            diverged.append({"population": new["population"], "scenario_id": new["scenario_id"], "arm": new["arm"],
                             "why": new["why"], "divergence": div,
                             "sealed": {k: old[k] for k in OUTCOME}, "rescored": {k: new[k] for k in OUTCOME}})
        else:
            diffs = [k for k in OUTCOME if old[k] != new[k]]
            if old["task_metrics"]["applied_commands_sha256"] != new["task_metrics"]["applied_commands_sha256"]:
                diffs.append("applied_commands_sha256")
            if diffs:
                mismatches.append({"scenario_id": new["scenario_id"], "arm": new["arm"], "fields": diffs})
        out_rows.append({**new, **SCORING, "sealed": {k: old[k] for k in OUTCOME}})

    rescored = [dict(r) for r in sealed]
    for r in rescored:
        new = next((n for n in out_rows if (n["population"], n["scenario_id"], n["arm"])
                    == (r["population"], r["scenario_id"], r["arm"])), None)
        if new is not None:
            r.update({k: new[k] for k in OUTCOME})
    worst = [{**r, "task_success": False, "bounded_success": False} if suspect(r) else r for r in sealed]
    div_ids = {(d["population"], d["scenario_id"]) for d in diverged}
    dropped = [r for r in rescored if (r["population"], r["scenario_id"]) not in div_ids]

    summary = {
        "issue": 21,
        "scoring": SCORING,
        "sealed_rows": str(ROWS.relative_to(REPO)),
        "sealed_rows_sha256": m4.sha256_bytes(ROWS.read_bytes()),
        "method": ("re-execute proxy-flagged scenarios (all arms) and a seeded audit of proxy-clean scenarios "
                   "(confirmatory arms) with the HEB 0.3.0 divergence guard; all other rows keep their sealed score"),
        "proxy": "some row of the scenario has max_tilt_deg > 90 or non-monotone hard-violation t_s",
        "audit": {"n_per_population": AUDIT_N, "seed": AUDIT_SEED, "arms": list(CONFIRMATORY_ARMS)},
        "n_scenarios_rerun": len(items),
        "n_episodes_rerun": len(out_rows),
        "cpu": {"estimate_s": round(est, 1), "used_s": round(cpu_used, 1), "wall_s": round(time.time() - t0, 1),
                "budget_s": CPU_BUDGET_S},
        "diverged": diverged,
        "diverged_by_why": {w: sum(d["why"] == w for d in diverged) for w in ("proxy", "audit")},
        "reproduction": {"n_non_diverged": len(out_rows) - len(diverged), "n_mismatch": len(mismatches),
                         "mismatches": mismatches},
        "sensitivity": [
            variant("sealed (hes-m4-e2)", sealed, cfg),
            variant("proxy worst case: every proxy-flagged row is a failure", worst, cfg),
            variant("re-scored (HEB 0.3.0 divergence guard)", rescored, cfg),
            variant("re-scored, scenarios with a detected divergence dropped", dropped, cfg),
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rescored_rows.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in out_rows))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    for v in summary["sensitivity"]:
        print(v["variant"], {k: (round(c["delta"], 4), [round(x, 4) for x in c["ci"]], c["decision"])
                             for k, c in v["confirmatory"].items()})
    print(f"diverged {len(diverged)} {summary['diverged_by_why']}; mismatches {len(mismatches)}; "
          f"cpu {cpu_used / 60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
