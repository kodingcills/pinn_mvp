"""E19 development comparison (docs/engineering/experiments/E19_stale_resume_dev.md, #19 step 3).

    MUJOCO_GL=osmesa PYTHONPATH=. .venv/bin/python scripts/e19_dev.py run
    .venv/bin/python scripts/e19_dev.py analyze            # summary.json from raw_rows.jsonl only
    .venv/bin/python scripts/e19_dev.py parity-mediated --before 73db19d

`run` records an M0 run (results/e19/dev/m0) whose child (`execute`) runs every
arm on every scheduled scenario of each realization and writes raw rows; it then
copies the rows to results/e19/dev/raw_rows.jsonl and runs `analyze`. `analyze`
is a pure function of the raw rows (the recompute). `parity-mediated` checks the
`haplous_mediated` arm trial by trial against the Haplous of an earlier commit.

Arms (protocol section 3 and the step-3 binding amendment):
  haplous_mediated   Haplous on PreI22AssumptionMachine: the pre-#22 runtime path
  haplous_candidate  Haplous at this commit on the machine's real declaration (#22)
  g1_incumbent       G1, unchanged
  g1_parity          G1IncumbentSystem(hold_semantics_aware=True)
  bare_behavior      direct execution
Realizations: zoh_wrench (the standard machine, all 5 arms, 64 scenarios) and
closed_loop_hold (ClosedLoopHoldMachine; mediated, incumbent, candidate on
replicate 1, 32 scenarios).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("MUJOCO_GL", "osmesa")

OUT = REPO / "results" / "e19" / "dev"
RUN_ID = "e19-dev-episodes"
ARMS = {
    "zoh_wrench": ("haplous_mediated", "g1_incumbent", "bare_behavior", "haplous_candidate", "g1_parity"),
    "closed_loop_hold": ("haplous_mediated", "g1_incumbent", "haplous_candidate"),
}
SECONDARY_N = 32  # replicate 1: indices 0..31 cover every cell once
FAULTS = ("stale", "rejection")
RESETS_S = (1.0, 0.0)
BOOT = {"resamples": 10_000, "seed": 20261119}
MARGIN = 0.05
COMPARISONS = {
    "zoh_wrench": [("haplous_candidate", "g1_incumbent"), ("haplous_candidate", "haplous_mediated"),
                   ("haplous_candidate", "g1_parity"), ("haplous_mediated", "g1_incumbent"),
                   ("g1_parity", "g1_incumbent")],
    "closed_loop_hold": [("haplous_candidate", "g1_incumbent"), ("haplous_mediated", "g1_incumbent")],
}


def sha(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def population():
    from benchmarks.stale_resume_population import e19_population

    return e19_population()


def window(scenario) -> tuple[str, float, float]:
    if scenario.stale_windows_s:
        (t0, t1, _), = scenario.stale_windows_s
        return "stale", t0, t1
    (t0, t1), = scenario.command_rejection_windows_s
    return "rejection", t0, t1


def observed_phase(observations, t0: float) -> str | None:
    """Protocol section 2: phase at the first tick with source_s >= t0."""
    obs = next((o for o in observations if o.stamp.source_s >= t0), None)
    if obs is None:
        return None
    z, contact = obs.pose_m[2], bool(obs.extra_value("in_contact"))
    if not contact and z > 0:
        return "free_approach"
    if -0.0005 < z <= 0 or (contact and z > -0.0005):
        return "contact_onset"
    if -0.009 < z <= -0.0005:
        return "in_hole"
    return "near_seat"


def arm_setup(arm: str, realization: str):
    from benchmarks.heb.divergence import DivergenceGuardedMachine
    from benchmarks.heb.hold_realizations import ClosedLoopHoldMachine, PreI22AssumptionMachine
    from benchmarks.heb.systems import system_by_name
    from benchmarks.incumbent import G1IncumbentSystem

    if realization == "closed_loop_hold":
        factory = ClosedLoopHoldMachine
    elif arm == "haplous_mediated":
        factory = PreI22AssumptionMachine
    else:
        factory = DivergenceGuardedMachine
    system = {
        "haplous_mediated": lambda: system_by_name("haplous_mediated"),
        "haplous_candidate": lambda: system_by_name("haplous_mediated"),
        "g1_incumbent": lambda: G1IncumbentSystem(),
        "g1_parity": lambda: G1IncumbentSystem(hold_semantics_aware=True),
        "bare_behavior": lambda: system_by_name("bare_behavior"),
    }[arm]()
    return system, factory


def trial(scenario, arm: str, realization: str) -> tuple[dict, tuple]:
    from benchmarks.heb import mujoco_backend
    from benchmarks.heb.metrics import grade_run

    system, factory = arm_setup(arm, realization)
    real = mujoco_backend.build_realization(scenario, machine_factory=factory)
    t_cpu = time.process_time()
    run = system.run(real, scenario)
    cpu = time.process_time() - t_cpu
    graded, task_success, bounded, violations, metrics = grade_run(scenario, real.bundle, run)
    fault, t0, t1 = window(scenario)
    times = [float(t) for t in metrics.get("applied_command_times", ())]
    codes = sorted({v["code"] for v in violations})
    row = {
        "scenario_id": scenario.id,
        "arm": arm,
        "realization": realization,
        "tags": list(scenario.tags),
        "fault": fault,
        "window_s": [t0, t1],
        "reported_disposition": run.disposition,
        "reason": run.reason,
        "refusal_codes": list(run.refusal_codes),
        "graded_disposition": graded,
        "task_success": task_success,
        "bounded_success": bounded,
        "hard_violations": list(violations),
        "hard_violation_codes": codes,
        "process_force_exceeded": "process_force" in codes,
        "sim_diverged": graded == "SIM_DIVERGED",
        "peak_abs_force_n": metrics.get("peak_abs_force_n"),
        "max_tilt_deg": metrics.get("max_tilt_deg"),
        "seating_time_s": metrics.get("seating_time_s"),
        "n_applied_commands": len(times),
        "applied_commands_sha256": sha(times),
        "prefix_commands_sha256": sha([t for t in times if t < t0]),
        "cpu_s": cpu,
    }
    return row, run.observations


def cmd_execute(args) -> int:
    out = Path(os.environ.get("M0_ARTIFACT_DIR", args.out))
    out.mkdir(parents=True, exist_ok=True)
    scenarios = population()
    rows = []
    for realization, arms in ARMS.items():
        chosen = scenarios if realization == "zoh_wrench" else scenarios[:SECONDARY_N]
        for scenario in chosen:
            fault, t0, _ = window(scenario)
            batch, phase = [], None
            for arm in arms:
                row, observations = trial(scenario, arm, realization)
                if arm == "haplous_mediated" and realization == "zoh_wrench":
                    phase = observed_phase(observations, t0)
                batch.append(row)
            rows += batch
            print(f"{realization} {scenario.id} " + " ".join(
                f"{r['arm']}={r['graded_disposition']}{'*' if r['process_force_exceeded'] else ''}" for r in batch),
                flush=True)
            if realization == "zoh_wrench":
                for r in batch:
                    r["observed_phase"] = phase
    phases = {r["scenario_id"]: r["observed_phase"] for r in rows if r["realization"] == "zoh_wrench"}
    for r in rows:
        r["observed_phase"] = phases[r["scenario_id"]]
    (out / "raw_rows.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return 0


# --------------------------------------------------------------------------
# Analysis: a pure function of the raw rows
# --------------------------------------------------------------------------


def check_aligned(rows, arms) -> None:
    seen: dict[str, list[str]] = {}
    for r in rows:
        seen.setdefault(r["scenario_id"], []).append(r["arm"])
    bad = {s: a for s, a in seen.items() if sorted(a) != sorted(arms)}
    if bad:
        raise ValueError(f"unpaired or duplicated scenarios: {dict(list(bad.items())[:5])}")


def quantile(values, q):
    values = sorted(values)
    if not values:
        return None
    return values[min(len(values) - 1, int(q * len(values)))]


def arm_estimands(rows, attainable: set[str], mediated: dict[str, dict]) -> dict:
    from hes.metrics import _prop, system_metrics
    from scripts.m4_campaign import time_per_bounded_success

    n = len(rows)
    att = [r for r in rows if r["scenario_id"] in attainable]
    unnecessary = [r for r in att if r["reported_disposition"] in ("ABORTED", "REFUSED")
                   and not r["hard_violations"] and not r["sim_diverged"]]
    paired_t = [r["seating_time_s"] - mediated[r["scenario_id"]]["seating_time_s"] for r in rows
                if r["bounded_success"] and mediated[r["scenario_id"]]["bounded_success"]]
    peaks = [r["peak_abs_force_n"] for r in rows if r["peak_abs_force_n"] is not None]
    no_freshness = sum(1 for r in rows if r["task_success"]
                       and set(r["hard_violation_codes"]) <= {"state_freshness"})
    return {
        "n": n,
        "E1_force_exceedance": _prop(sum(r["process_force_exceeded"] for r in rows), n),
        "E2_bounded_success": system_metrics(rows)["bounded_success"],
        "E3_accepted_work": {"n_bounded_success": sum(r["bounded_success"] for r in rows),
                             "time_per_bounded_success": time_per_bounded_success(rows, RESETS_S)},
        "E4_unnecessary_stops": {**_prop(len(unnecessary), len(att)),
                                 "scenario_ids": sorted(r["scenario_id"] for r in unnecessary)},
        "E5_added_sim_time_vs_mediated": {"median_s": statistics.median(paired_t) if paired_t else None,
                                          "n": len(paired_t)},
        "cpu_s_per_episode": statistics.fmean(r["cpu_s"] for r in rows) if rows else None,
        "descriptive": {
            "peak_abs_force_n": {"median": statistics.median(peaks) if peaks else None,
                                 "p90": quantile(peaks, 0.9), "max": max(peaks) if peaks else None},
            "tumbles_tilt_gt_10deg": sum(1 for r in rows if (r["max_tilt_deg"] or 0.0) > 10.0),
            "sim_diverged": sum(r["sim_diverged"] for r in rows),
            "hard_violation_code_counts": {c: sum(c in r["hard_violation_codes"] for r in rows)
                                           for c in sorted({c for r in rows for c in r["hard_violation_codes"]})},
            "reasons": {k: sum(1 for r in rows if str(r["reason"]) == k)
                        for k in sorted({str(r["reason"]) for r in rows})},
            "sensitivity_bounded_success_ignoring_state_freshness": _prop(no_freshness, n),
        },
    }


def realization_analysis(rows, realization: str) -> dict:
    from hes.regression import paired_rate_delta
    from hes.run import Run

    arms = ARMS[realization]
    check_aligned(rows, arms)
    run = Run(root=OUT, rows=rows, manifest={}, provenance={}, arms=arms,
              baseline_arm="bare_behavior" if "bare_behavior" in arms else None)
    attainability = run.attainability()
    attainable = {s for s, a in attainability.items() if a.axis.value == "ATTAINABLE"}
    by_arm = run.by_arm
    mediated = {r["scenario_id"]: r for r in by_arm["haplous_mediated"]}
    subsets = {"stale": lambda r: r["fault"] == "stale", "rejection": lambda r: r["fault"] == "rejection",
               "all": lambda r: True}
    out: dict = {"n_scenarios": len(run.by_scenario), "n_attainable": len(attainable), "arms": {}, "paired": {},
                 "strata": {}}
    for arm in arms:
        out["arms"][arm] = {name: arm_estimands([r for r in by_arm[arm] if keep(r)], attainable, mediated)
                            for name, keep in subsets.items()}
    for cand, ref in COMPARISONS[realization]:
        out["paired"][f"{cand}_vs_{ref}"] = {
            name: {f: paired_rate_delta([r for r in by_arm[cand] if keep(r)], [r for r in by_arm[ref] if keep(r)],
                                        field=f, **BOOT)
                   for f in ("process_force_exceeded", "bounded_success")}
            for name, keep in subsets.items()}
    for fault in FAULTS:
        for phase in ("free_approach", "contact_onset", "in_hole", "near_seat", None):
            group = {arm: [r for r in by_arm[arm] if r["fault"] == fault and r["observed_phase"] == phase]
                     for arm in arms}
            if group[arms[0]]:
                out["strata"][f"{fault}/{phase}"] = {
                    "n": len(group[arms[0]]),
                    "force_exceeded": {a: sum(r["process_force_exceeded"] for r in g) for a, g in group.items()},
                    "bounded_success": {a: sum(r["bounded_success"] for r in g) for a, g in group.items()}}
    prefixes = {}
    for r in rows:
        prefixes.setdefault(r["scenario_id"], set()).add(r["prefix_commands_sha256"])
    out["prefix_mismatch_scenarios"] = sorted(s for s, p in prefixes.items() if len(p) > 1)
    return out


def inside(d) -> bool:
    return d["ci_low"] >= -MARGIN and d["ci_high"] <= MARGIN


def decide(zoh: dict, closed: dict, rows) -> dict:
    """Protocol section 6, first match wins, as bound by the step-3 amendment."""
    p = zoh["paired"]
    trace = []
    unique_codes = {}
    for real in ("zoh_wrench", "closed_loop_hold"):
        rr = [r for r in rows if r["realization"] == real]
        cand = {c for r in rr if r["arm"] == "haplous_candidate" for c in r["hard_violation_codes"]}
        other = {c for r in rr if r["arm"] != "haplous_candidate" for c in r["hard_violation_codes"]}
        unique_codes[real] = sorted(cand - other)
    revert = []
    for fault in FAULTS:
        d1 = p["haplous_candidate_vs_g1_incumbent"][fault]["process_force_exceeded"]
        d2 = p["haplous_candidate_vs_g1_incumbent"][fault]["bounded_success"]
        if d1["ci_low"] > 0:
            revert.append(f"{fault}: dE1 CI low {d1['ci_low']:+.4f} > 0")
        if d2["ci_high"] < -MARGIN:
            revert.append(f"{fault}: dE2 CI high {d2['ci_high']:+.4f} < -0.05")
    if any(unique_codes.values()):
        revert.append(f"candidate-only hard-violation codes {unique_codes}")
    trace.append({"rule": "1 REVERT", "matched": bool(revert), "because": revert})
    d1 = p["haplous_candidate_vs_g1_incumbent"]["stale"]["process_force_exceeded"]
    d2 = p["haplous_candidate_vs_g1_incumbent"]["stale"]["bounded_success"]
    par = p["haplous_candidate_vs_g1_parity"]["stale"]
    niv_a = d1["ci_low"] <= 0 <= d1["ci_high"] and inside(d2)
    niv_b = inside(par["process_force_exceeded"]) and inside(par["bounded_success"])
    trace.append({"rule": "2 NO INCREMENTAL VALUE", "matched": niv_a or niv_b,
                  "because": {"dE1_ci_contains_0_and_dE2_ci_inside_margin": niv_a,
                              "candidate_minus_g1_parity_inside_margin_on_E1_and_E2": niv_b}})
    arms = zoh["arms"]
    e4_gap = (arms["haplous_candidate"]["stale"]["E4_unnecessary_stops"]["value"] or 0.0) - (
        arms["g1_incumbent"]["stale"]["E4_unnecessary_stops"]["value"] or 0.0)
    e5 = arms["haplous_candidate"]["stale"]["E5_added_sim_time_vs_mediated"]["median_s"]
    keep = {"g1_parity_instantiated_and_run": "g1_parity" in arms,
            "dE1_ci_high_lt_0": d1["ci_high"] < 0, "dE2_ci_low_ge_-0.05": d2["ci_low"] >= -MARGIN,
            "E4_gap_le_0.05": e4_gap <= MARGIN, "E5_median_le_0.10s": e5 is not None and e5 <= 0.10}
    trace.append({"rule": "3 KEEP", "matched": all(keep.values()), "because": {**keep, "E4_gap": e4_gap, "E5_median_s": e5}})
    trace.append({"rule": "4 REVISE", "matched": True, "because": "anything else"})
    decision = next(t["rule"].split(" ", 1)[1] for t in trace if t["matched"])
    cl = closed["arms"]
    scope = all(cl[a]["all"]["E1_force_exceedance"]["value"] * cl[a]["all"]["n"] <= 1
                for a in ("haplous_mediated", "g1_incumbent"))
    return {"decision": decision, "trace": trace, "candidate_only_codes": unique_codes,
            "scope_note_zoh_artifact": scope}


def analyze(rows) -> dict:
    by_real = {real: [r for r in rows if r["realization"] == real] for real in ARMS}
    zoh = realization_analysis(by_real["zoh_wrench"], "zoh_wrench")
    closed = realization_analysis(by_real["closed_loop_hold"], "closed_loop_hold")
    return {
        "protocol": "docs/engineering/experiments/E19_stale_resume_dev.md",
        "population": "e19_stale_resume_dev@v1",
        "grading": "HEB 0.3.0 / hes-m4-e3-simdiv (system under test at scoring epoch hes-i22-hold)",
        "raw_rows_sha256": sha(rows),
        "n_rows": len(rows),
        "cpu_s_total_system_runs": sum(r["cpu_s"] for r in rows),
        "statistics": {"proportions": "Wilson 95% (hes.metrics._prop)",
                       "paired": f"hes.regression.paired_rate_delta, {BOOT}", "margin": MARGIN},
        "zoh_wrench": zoh,
        "closed_loop_hold": closed,
        "decision": decide(zoh, closed, rows),
    }


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def cmd_analyze(args) -> int:
    summary = analyze(load_rows(args.rows))
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps(summary["decision"], indent=1))
    return 0


def cmd_run(args) -> int:
    import mujoco

    from evaluation.run_registry import Registry, record_run

    registry = Registry(OUT / "m0")
    command = [sys.executable, str(Path(__file__).resolve()), "execute"]
    result = record_run(
        registry, run_id=RUN_ID, track="sim_policy_free", purpose="E19 development comparison (#19 step 3)",
        command=command, config={"population": "e19_stale_resume_dev@v1", "arms": ARMS,
                                 "secondary_n": SECONDARY_N},
        simulator={"name": "mujoco", "version": mujoco.__version__},
        dependencies=("numpy", "mujoco", "jsonschema", "cryptography"),
        cwd=REPO, env={"PYTHONPATH": str(REPO), "MUJOCO_GL": "osmesa"},
    )
    if result["state"] != "VERIFIED" or result["outcome"] != "completed":
        print(f"{RUN_ID}: {result['state']} {result['outcome']}", file=sys.stderr)
        return 1
    shutil.copyfile(registry.run_dir(RUN_ID) / "artifacts" / "raw_rows.jsonl", OUT / "raw_rows.jsonl")
    return cmd_analyze(argparse.Namespace(rows=OUT / "raw_rows.jsonl", summary=OUT / "summary.json"))


_COLLECT = """
import json, sys
from benchmarks.heb import mujoco_backend
from benchmarks.heb.metrics import grade_run
from benchmarks.heb.systems import system_by_name
from benchmarks.stale_resume_population import e19_population
rows = []
for s in e19_population():
    real = mujoco_backend.build_realization(s)
    run = system_by_name("haplous_mediated").run(real, s)
    g, ts, b, v, m = grade_run(s, real.bundle, run)
    rows.append({"scenario_id": s.id, "reported_disposition": run.disposition, "reason": run.reason,
                 "graded_disposition": g, "bounded_success": b, "peak_abs_force_n": m.get("peak_abs_force_n"),
                 "applied": [float(t) for t in m.get("applied_command_times", ())]})
json.dump(rows, open(sys.argv[1], "w"))
"""


def cmd_parity_mediated(args) -> int:
    """haplous_mediated rows of raw_rows.jsonl vs Haplous at --before on the standard machine."""
    before = subprocess.check_output(["git", "rev-parse", args.before], cwd=REPO, text=True).strip()
    ours = {r["scenario_id"]: r for r in load_rows(OUT / "raw_rows.jsonl")
            if r["arm"] == "haplous_mediated" and r["realization"] == "zoh_wrench"}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "before"
        root.mkdir()
        archive = subprocess.run(["git", "archive", before], cwd=REPO, check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(root)], input=archive, check=True)
        env = dict(os.environ, PYTHONPATH=str(root), MUJOCO_GL="osmesa")
        subprocess.run([sys.executable, "-c", _COLLECT, str(Path(tmp) / "b.json")], cwd=root, env=env, check=True)
        theirs = json.loads((Path(tmp) / "b.json").read_text())
    fields = ("reported_disposition", "reason", "graded_disposition", "bounded_success", "peak_abs_force_n")
    diffs = []
    for t in theirs:
        o = ours[t["scenario_id"]]
        bad = [f for f in fields if o[f] != t[f]]
        if o["applied_commands_sha256"] != sha(t["applied"]):
            bad.append("applied_commands_sha256")
        if bad:
            diffs.append({"scenario_id": t["scenario_id"], "fields": bad})
    report = {"before_commit": before, "n_trials": len(theirs), "n_identical": len(theirs) - len(diffs),
              "compared": [*fields, "applied_commands_sha256"], "differences": diffs}
    (OUT / "parity_mediated.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(f"{report['n_identical']}/{report['n_trials']} identical")
    return 0 if not diffs else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    ex = sub.add_parser("execute")
    ex.add_argument("--out", type=Path, default=OUT / "scratch")
    an = sub.add_parser("analyze")
    an.add_argument("--rows", type=Path, default=OUT / "raw_rows.jsonl")
    an.add_argument("--summary", type=Path, default=OUT / "summary.json")
    pm = sub.add_parser("parity-mediated")
    pm.add_argument("--before", default="73db19d")
    args = ap.parse_args()
    return {"run": cmd_run, "execute": cmd_execute, "analyze": cmd_analyze,
            "parity-mediated": cmd_parity_mediated}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
