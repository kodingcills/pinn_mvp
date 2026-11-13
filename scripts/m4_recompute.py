"""Independent recomputation of every official M4 metric from the raw-row file.

    .venv/bin/python scripts/m4_recompute.py --rows results/m4/dryrun/raw_rows.jsonl \
        --config evaluation/m4_campaign.json

Written from the definitions in docs/eval_program/designs/M4_preregistration.md
section 4, not from HES code: it imports no `hes`, `evaluation` or `benchmarks`
module (checked at exit). Prints the metrics as JSON on stdout. HES hard gates
are HES verdicts and are not recomputed here; their inputs are.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import numpy as np

Z95 = 1.959963984540054
NA = {"value": None, "n": 0, "reason": "NOT_AVAILABLE"}


def wilson(k: int, n: int) -> dict:
    if n <= 0:
        return dict(NA)
    p = k / n
    denom = 1.0 + Z95 * Z95 / n
    centre = (p + Z95 * Z95 / (2.0 * n)) / denom
    half = Z95 * math.sqrt(p * (1.0 - p) / n + Z95 * Z95 / (4.0 * n * n)) / denom
    return {"value": p, "n": n, "ci_low": centre - half, "ci_high": centre + half, "method": "wilson95"}


def refused(r: dict) -> bool:
    return (r.get("reported_disposition") or r.get("graded_disposition")) == "REFUSED"


def bounded(r: dict) -> bool:
    return bool(r.get("bounded_success"))


def violated(r: dict) -> bool:
    return int(r.get("n_hard_violations") or 0) > 0 or bool(r.get("hard_violations"))


def sim_seconds(r: dict) -> float:
    return 0.0 if refused(r) else float(r["task_metrics"].get("seating_time_s") or 0.0)


def bootstrap_delta(cand: dict, ref: dict, resamples: int, seed: int) -> dict:
    ids = sorted(set(cand) & set(ref))
    d = np.array([float(bounded(cand[s])) - float(bounded(ref[s])) for s in ids])
    if d.size == 0:
        return {"n": 0, "reason": "no paired scenarios"}
    idx = np.random.default_rng(seed).integers(0, d.size, size=(resamples, d.size))
    zero = np.zeros(d.size)
    boots = d[idx].mean(axis=1) - zero[idx].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"field": "bounded_success", "n_pairs": int(d.size), "delta": float(d.mean() - zero.mean()),
            "ci_low": float(lo), "ci_high": float(hi), "n": int(d.size), "n_resamples": resamples,
            "method": "paired_bootstrap_mean"}


def classes(cand: dict, ref: dict) -> dict:
    out = {"rescued": [], "harmed": [], "both": 0, "neither": 0}
    for s in sorted(cand):
        c, r = bounded(cand[s]), bounded(ref[s])
        if c != r:
            out["rescued" if c else "harmed"].append(s)
        else:
            out["both" if c else "neither"] += 1
    return out


def stratum(r: dict) -> str:
    tags = [str(t) for t in r.get("tags") or ()]
    return next((t for t in tags if t.startswith("stratum:")), next((t for t in tags if t.startswith("task_")), "none"))


def decision(lo: float, hi: float, margin: float) -> str:
    """Section 6: ordered rules, the first match wins."""
    note = " (difference within the 0.05 equivalence margin: practically equivalent)"
    if lo > 0:
        return "Haplous better" + (note if hi <= margin else "")
    if hi < 0:
        return "G1 better" + (note if lo >= -margin else "")
    if -margin <= lo and hi <= margin:
        return "tie (equivalent within 0.05)"
    return "inconclusive at this sample size"


def population(rows: list[dict], cfg: dict, pop: str) -> dict:
    arms = cfg["arms"]["all"]
    direct_arm = cfg["arms"]["direct"]
    table: dict[str, dict[str, dict]] = {}
    for r in rows:
        if r["arm"] in table.setdefault(r["scenario_id"], {}):
            raise SystemExit(f"duplicate row {r['scenario_id']}/{r['arm']}")
        table[r["scenario_id"]][r["arm"]] = r
    if any(set(v) != set(arms) for v in table.values()):
        raise SystemExit(f"{pop}: unpaired scenarios")
    by_arm = {a: {s: table[s][a] for s in table} for a in arms}
    # Attainable when the direct arm, or else any arm, reached bounded success; otherwise UNKNOWN.
    attainable = {s for s, v in table.items() if any(bounded(x) for x in v.values())}
    direct = by_arm[direct_arm]
    out_arms = {}
    for arm in arms:
        a = by_arm[arm]
        n = len(a)
        acc = [r for r in a.values() if not refused(r)]
        n_att = sum(1 for s in a if s in attainable)
        ref_ok = [s for s in a if bounded(direct[s])]
        recovery = [r for r in a.values() if "recovery_authorized" in (r.get("tags") or ())]
        executed = len(acc)
        successes = sum(1 for r in a.values() if bounded(r))
        sim = sum(sim_seconds(r) for r in a.values())
        taxes = [sim_seconds(a[s]) - sim_seconds(direct[s]) for s in a if bounded(a[s]) and bounded(direct[s])]
        is_direct = arm == direct_arm
        out_arms[arm] = {
            "task_success": wilson(sum(1 for r in a.values() if r.get("task_success")), n),
            "bounded_success": wilson(successes, n),
            "coverage": wilson(executed, n),
            "violation_given_acceptance": wilson(sum(1 for r in acc if violated(r)), executed),
            "false_refusal_rate": (wilson(sum(1 for s, r in a.items() if refused(r) and s in attainable), n_att)
                                   if n_att else dict(NA)),
            "refusals_attainability_unknown": sum(1 for s, r in a.items() if refused(r) and s not in attainable),
            "preservation_vs_direct": None if is_direct else (
                wilson(sum(1 for s in ref_ok if bounded(a[s])), len(ref_ok)) if ref_ok else dict(NA)),
            "unnecessary_intervention_vs_direct": None if is_direct else (
                wilson(sum(1 for s in ref_ok if refused(a[s])), len(ref_ok)) if ref_ok else dict(NA)),
            "recovery_bounded_success": wilson(sum(1 for r in recovery if bounded(r)), len(recovery)),
            "time_per_bounded_success": {
                "sim_seconds_all_episodes": sim,
                "n_executed": executed,
                "n_bounded_success": successes,
                "n_executed_without_duration": sum(1 for r in acc if r["task_metrics"].get("seating_time_s") is None),
                "by_reset_s": {str(x): (sim + x * executed) / successes if successes else None
                               for x in cfg["time"]["reset_s"]},
            },
            "intervention_overhead_vs_direct": None if is_direct else {
                "median_sim_s": statistics.median(taxes) if taxes else None, "n": len(taxes)},
        }
    st = cfg["statistics"]
    paired = {f"{c}_vs_{r}": {**classes(by_arm[c], by_arm[r]),
                              "delta_bounded_success": bootstrap_delta(by_arm[c], by_arm[r],
                                                                       st["bootstrap_resamples"], st["bootstrap_seed"])}
              for c, r in cfg["comparisons"]}
    strata = {}
    for name in sorted({stratum(r) for r in rows}):
        group = {a: {s: r for s, r in by_arm[a].items() if stratum(r) == name} for a in arms}
        strata[name] = {
            "n_scenarios": len(group[direct_arm]),
            "bounded_success": {a: wilson(sum(1 for r in g.values() if bounded(r)), len(g)) for a, g in group.items()},
            "paired": {f"{c}_vs_{r}": {k: len(v) if isinstance(v, list) else v
                                       for k, v in classes(group[c], group[r]).items()}
                       for c, r in cfg["comparisons"]},
        }
    confirmatory = {}
    for item in cfg["confirmatory"]:
        if item["population"] == pop:
            pair = paired[f"{item['candidate']}_vs_{item['reference']}"]
            d = pair["delta_bounded_success"]
            confirmatory[item["id"]] = {**item, "delta": d,
                                        "decision": decision(d["ci_low"], d["ci_high"], st["equivalence_margin"]),
                                        "n_harmed": len(pair["harmed"]), "n_rescued": len(pair["rescued"])}
    n_unknown = len(table) - len(attainable)
    counts = {k: v for k, v in (("ATTAINABLE", len(attainable)), ("UNKNOWN", n_unknown)) if v}
    return {"n_scenarios": len(table), "attainability_counts": counts, "arms": out_arms, "paired": paired,
            "strata": strata, "confirmatory": confirmatory}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="m4_recompute")
    parser.add_argument("--rows", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    rows = [json.loads(x) for x in args.rows.read_text(encoding="utf-8").splitlines() if x.strip()]
    out = {"populations": {}}
    for pop, spec in cfg["populations"].items():
        pop_rows = [r for r in rows if r["metadata"]["population_id"] == spec["id"]]
        out["populations"][pop] = population(pop_rows, cfg, pop)
    leaked = sorted(m for m in sys.modules if m.split(".")[0] in ("hes", "evaluation", "benchmarks"))
    if leaked:
        raise SystemExit(f"recompute is not independent: imported {leaked}")
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
