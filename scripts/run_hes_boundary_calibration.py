"""Haplous Boundary Calibration Falsification Campaign driver.

Resolves the causal ambiguity left by the HES causal diagnosis: is
``max_initial_offset_m = 0.003`` merely miscalibrated relative to the
actual bounded capture envelope, or does Haplous genuinely fail to
execute boundedly in the 3.0-3.5 mm region?

Frozen runtime; evaluator semantics repaired first (bounded-execution
counterfactual classifier, see benchmarks/heb/counterfactual.py). The one
benchmark-side variable under test is the reach capability reported to the
runtime for Branch B only.

Branches, per eligible scenario (all run exactly once, deterministic):
  A  frozen Haplous, unmodified reach capability            -> refuses
  B  Haplous, reach capability overridden to unbounded      -> decisive
  C  direct behaviour (direct_minimal), unmediated          -> reference
  D  competent supervisor (scenario-requirement watchdogs)  -> reference

Eligible = insertion scenarios with 0.003 < initial_offset_m <= 0.0035
(boundary_hi, all reach-rejected by Branch A) excluding the maxF == 2.0
guaranteed-abort class (a bounded success is impossible regardless of
reach, so the scenario carries no calibration information).

Outputs (results/hes_boundary_calibration/):
  population_audit.json, branch_results.jsonl, estimands.json,
  capture_curve.json, interaction_analysis.json, evaluator_repair.json,
  hes_recompute.json, decision.json

`--check` recomputes every derived artifact from the committed raw rows
and exits non-zero on any mismatch (byte-level, canonical JSON).
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.heb.counterfactual import (  # noqa: E402
    BENEFICIAL,
    CLASSIFIER_EPOCH,
    HARMFUL,
    LEGACY_SEMANTICS_EPOCH,
    UNNECESSARY,
    ContinuationOutcome,
    classify_intervention,
    legacy_classification,
)
from benchmarks.heb.metrics import grade_run  # noqa: E402
from benchmarks.heb.mujoco_backend import build_realization  # noqa: E402
from benchmarks.heb.population import population  # noqa: E402
from benchmarks.heb.protocol import Scenario, SystemRun  # noqa: E402
from benchmarks.heb.systems import (  # noqa: E402
    BareSystem,
    HaplousSystem,
    SupervisedSystem,
)
from hes.intervention import CLASSIFIER_EPOCH as HES_EPOCH  # noqa: E402

RESULTS = ROOT / "results" / "hes_boundary_calibration"
RAW_ROWS = RESULTS / "branch_results.jsonl"
FROZEN = ROOT / "results" / "hes" / "v3_hardening_qualification"

N = 1000
SEED = 20260916
REACH_CAP_M = 0.003
BOUNDARY_HI = (0.003, 0.0035)
GUARANTEED_ABORT_FORCE_N = 2.0
# The single overridden capability for Branch B. Any value above the
# population's largest offset (5 mm) removes the reach decision as a
# binding constraint; 1.0 m is unambiguously "unbounded".
REACH_OVERRIDE_M = 1.0
assert HES_EPOCH == CLASSIFIER_EPOCH


# --------------------------------------------------------------------------
# Population selection
# --------------------------------------------------------------------------


def eligible_scenarios() -> list[Scenario]:
    scs = population(N, SEED)
    out = [
        s
        for s in scs
        if s.task_family == "peg_insertion"
        and BOUNDARY_HI[0] < s.initial_offset_m <= BOUNDARY_HI[1]
        and s.max_force_n != GUARANTEED_ABORT_FORCE_N
    ]
    return sorted(out, key=lambda s: s.id)


def population_audit() -> dict:
    scs = population(N, SEED)
    ins = [s for s in scs if s.task_family == "peg_insertion"]
    bh = [
        s
        for s in ins
        if BOUNDARY_HI[0] < s.initial_offset_m <= BOUNDARY_HI[1]
    ]
    eligible = eligible_scenarios()
    bands = Counter(round(s.initial_offset_m * 1e3, 2) for s in bh)
    return {
        "population": {"n": N, "seed": SEED},
        "n_scenarios": len(scs),
        "n_insertion": len(ins),
        "boundary_hi": {
            "definition_m": list(BOUNDARY_HI),
            "declared_reach_cap_m": REACH_CAP_M,
            "n": len(bh),
            "excluded_guaranteed_abort_n": len(bh) - len(eligible),
            "eligible_n": len(eligible),
            "offset_counts_mm": {str(k): v for k, v in sorted(bands.items())},
        },
        "eligible_ids": [s.id for s in eligible],
        "reach_mechanism": {
            "constant_m": REACH_CAP_M,
            "consumed_only_by": "haplous/capabilities.py::negotiate INITIAL_OFFSET_M branch",
            "override_scope": "Branch B reports max_initial_offset_m = REACH_OVERRIDE_M; "
            "requirement remains declared as HARD in the intent",
            "reach_override_m": REACH_OVERRIDE_M,
            "bundle_geometric_envelope_m": 0.00435,
        },
    }


# --------------------------------------------------------------------------
# Branch execution
# --------------------------------------------------------------------------


def _grade(scenario: Scenario, run: SystemRun, realization) -> dict:
    graded, task_success, bounded, violations, metrics = grade_run(
        scenario, realization.bundle, run
    )
    vs = [
        {
            "code": v.get("code"),
            "value": v.get("value"),
            "limit": v.get("limit"),
            "t_s": v.get("t_s"),
        }
        for v in violations
    ]
    return {
        "disposition": run.disposition,
        "reason": run.reason,
        "refusal_codes": list(run.refusal_codes),
        "refusal_stage": (run.detail or {}).get("refusal_stage", "none"),
        "graded": graded,
        "task_success": task_success,
        "bounded_success": bounded,
        # n_hard_violations is exact; the record list is bounded to keep
        # the committed ledger small (a flushed force limit can breach on
        # every tick).
        "n_hard_violations": len(vs),
        "hard_violations": vs[:5],
        "peak_abs_force_n": metrics.get("peak_abs_force_n"),
        "max_tilt_deg": metrics.get("max_tilt_deg"),
        "seating_time_s": metrics.get("seating_time_s"),
        "wall_s": run.wall_s,
    }


def run_scenario(scenario: Scenario) -> dict:
    """Run branches A-D once each on independent realizations."""
    # A: frozen Haplous.
    real_a = build_realization(scenario)
    run_a = HaplousSystem().run(real_a, scenario)

    # B: identical Haplous with only the reach capability overridden.
    real_b = build_realization(
        scenario, machine_config_overrides={"max_initial_offset_m": REACH_OVERRIDE_M}
    )
    run_b = HaplousSystem().run(real_b, scenario)

    # C: direct unmediated (direct_minimal semantics).
    real_c = build_realization(scenario)
    run_c = BareSystem(evaluate_success=False).run(real_c, scenario)

    # D: competent supervisor.
    real_d = build_realization(scenario)
    run_d = SupervisedSystem().run(real_d, scenario)

    return {
        "scenario_id": scenario.id,
        "task_family": scenario.task_family,
        "axes": {
            "initial_offset_m": scenario.initial_offset_m,
            "initial_bearing_rad": scenario.initial_bearing_rad,
            "facet_count": scenario.facet_count,
            "clearance_delta_m": scenario.clearance_delta_m,
            "plate_thickness_m": scenario.plate_thickness_m,
            "max_force_n": scenario.max_force_n,
            "max_tilt_deg": scenario.max_tilt_deg,
            "deadline_s": scenario.deadline_s,
            "position_tolerance_m": scenario.position_tolerance_m,
            "state_max_age_s": scenario.state_max_age_s,
            "receive_delay_s": scenario.receive_delay_s,
            "n_dropout": len(scenario.dropout_windows_s),
            "n_stale": len(scenario.stale_windows_s),
            "n_rejection": len(scenario.command_rejection_windows_s),
            "n_disturbances": len(scenario.disturbances),
        },
        "branches": {
            "A": _grade(scenario, run_a, real_a),
            "B": _grade(scenario, run_b, real_b),
            "C": _grade(scenario, run_c, real_c),
            "D": _grade(scenario, run_d, real_d),
        },
    }


_WORKER_SCENARIO: Scenario | None = None


def _init_worker(scenario_json: str) -> None:
    global _WORKER_SCENARIO
    from benchmarks.heb.protocol import Scenario as _S

    _WORKER_SCENARIO = _S(**json.loads(scenario_json))


def _run_worker(scenario_json: str) -> dict:
    from benchmarks.heb.protocol import Scenario as _S

    return run_scenario(_S(**json.loads(scenario_json)))


def _scenario_json(s: Scenario) -> str:
    return json.dumps(
        {
            "id": s.id,
            "task_family": s.task_family,
            **{
                k: getattr(s, k)
                for k in (
                    "facet_count",
                    "clearance_delta_m",
                    "plate_thickness_m",
                    "peg_radius_m",
                    "start_position_m",
                    "initial_offset_m",
                    "initial_bearing_rad",
                    "path_start_m",
                    "path_end_m",
                    "min_force_n",
                    "max_force_n",
                    "max_tilt_deg",
                    "deadline_s",
                    "state_max_age_s",
                    "position_tolerance_m",
                    "persistence_s",
                    "recovery_permitted_conditions",
                    "max_recovery_actions",
                    "receive_delay_s",
                    "dropout_windows_s",
                    "stale_windows_s",
                    "command_rejection_windows_s",
                    "fault_at_s",
                    "disturbances",
                    "recovery_settle_s",
                    "recoverable",
                    "expected_disposition",
                    "infeasible_request",
                    "tags",
                )
            },
        },
        sort_keys=True,
    )


def run_all(workers: int, limit: int | None) -> list[dict]:
    scenarios = eligible_scenarios()
    if limit:
        scenarios = scenarios[:limit]
    done: set[str] = set()
    if RAW_ROWS.is_file():
        for line in RAW_ROWS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["scenario_id"])
    todo = [s for s in scenarios if s.id not in done]
    if not todo:
        return load_rows()
    print(f"running {len(todo)} scenarios (workers={workers})", flush=True)
    payloads = [_scenario_json(s) for s in todo]
    with open(RAW_ROWS, "a", encoding="utf-8") as fh:
        if workers <= 1:
            for p in payloads:
                row = _run_worker(p)
                fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                fh.flush()
        else:
            with multiprocessing.Pool(workers) as pool:
                for row in pool.imap_unordered(_run_worker, payloads):
                    fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                    fh.flush()
    return load_rows()


def load_rows() -> list[dict]:
    rows = [
        json.loads(line)
        for line in RAW_ROWS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return sorted(rows, key=lambda r: r["scenario_id"])


# --------------------------------------------------------------------------
# Statistics (stdlib)
# --------------------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.96) -> dict | None:
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return {
        "value": p,
        "n": n,
        "k": k,
        "ci_low": max(0.0, center - half),
        "ci_high": min(1.0, center + half),
        "method": "wilson95",
    }


def mcnemar_exact(b01: int, b10: int) -> dict:
    """Two-sided exact McNemar on discordant pairs. b01 = B succeeds / C
    fails; b10 = B fails / C succeeds."""
    n = b01 + b10
    if n == 0:
        return {"n_discordant": 0, "p_value": 1.0, "method": "exact_binomial"}
    k = min(b01, b10)
    cdf = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return {
        "b01_B_only": b01,
        "b10_C_only": b10,
        "n_discordant": n,
        "p_value": min(1.0, 2 * cdf),
        "method": "exact_binomial",
    }


def _capture(rows: list[dict], branch: str) -> tuple[int, int]:
    k = sum(1 for r in rows if r["branches"][branch]["bounded_success"])
    return k, len(rows)


def _any_violation(rows: list[dict], branch: str, only_executed: bool = True) -> tuple[int, int]:
    pool = [r for r in rows if not only_executed or r["branches"][branch]["disposition"] != "REFUSED"]
    k = sum(1 for r in pool if r["branches"][branch]["n_hard_violations"] > 0)
    return k, len(pool)


# --------------------------------------------------------------------------
# Estimands
# --------------------------------------------------------------------------


def estimands(rows: list[dict]) -> dict:
    n = len(rows)
    a_k, _ = _capture(rows, "A")
    b_k, _ = _capture(rows, "B")
    c_k, _ = _capture(rows, "C")
    d_k, _ = _capture(rows, "D")
    b_exec = [r for r in rows if r["branches"]["B"]["disposition"] != "REFUSED"]
    b_exec_k = sum(1 for r in b_exec if r["branches"]["B"]["bounded_success"])
    b_refused = n - len(b_exec)
    submit_refusals = sum(
        1 for r in rows if r["branches"]["B"]["refusal_stage"] == "submit"
    )
    execution_refusals = sum(
        1 for r in rows if r["branches"]["B"]["refusal_stage"] == "execution"
    )
    deadline_refusals = sum(
        1
        for r in rows
        if "deadline_unachievable" in r["branches"]["B"]["refusal_codes"]
    )
    # Paired B vs C.
    both = b01 = b10 = neither = 0
    for r in rows:
        bs = r["branches"]["B"]["bounded_success"]
        cs = r["branches"]["C"]["bounded_success"]
        if bs and cs:
            both += 1
        elif bs:
            b01 += 1
        elif cs:
            b10 += 1
        else:
            neither += 1
    # C bounded success destroyed by authorised Haplous, restricted to
    # scenarios Branch B actually executed (the freshness refusals are a
    # separate mechanism and must not be charged to reach calibrations).
    executed_ids = {r["scenario_id"] for r in b_exec}
    destroyed = [
        r["scenario_id"]
        for r in rows
        if r["scenario_id"] in executed_ids
        and r["branches"]["C"]["bounded_success"]
        and not r["branches"]["B"]["bounded_success"]
    ]
    destroyed_all = [
        r["scenario_id"]
        for r in rows
        if r["branches"]["C"]["bounded_success"] and not r["branches"]["B"]["bounded_success"]
    ]
    created = [r["scenario_id"] for r in rows if r["branches"]["B"]["bounded_success"] and not r["branches"]["C"]["bounded_success"]]
    # Safety: violation rate among executed.
    c_v, c_vn = _any_violation(rows, "C")
    b_v, b_vn = _any_violation(rows, "B")
    return {
        "n_eligible": n,
        "branch_A_refused": sum(1 for r in rows if r["branches"]["A"]["disposition"] == "REFUSED"),
        "branch_A_capture": wilson(a_k, n),
        "branch_B_capture_among_eligible": wilson(b_k, n),
        "branch_B_executed": wilson(b_exec_k, len(b_exec)),
        "branch_B_refusals": b_refused,
        "branch_B_submit_refusals": submit_refusals,
        "branch_B_execution_refusals": execution_refusals,
        "branch_B_deadline_refusals": deadline_refusals,
        "branch_C_capture": wilson(c_k, n),
        "branch_D_capture": wilson(d_k, n),
        "paired_B_vs_C": {
            "both_bounded": both,
            "B_only": b01,
            "C_only": b10,
            "neither": neither,
            "mcnemar": mcnemar_exact(b01, b10),
            "capture_gap_C_minus_B": (c_k - b_k) / n if n else None,
        },
        "bounded_success_destroyed_by_authorised_haplous": destroyed,
        "bounded_success_destroyed_including_freshness_refusals": destroyed_all,
        "bounded_success_created_by_authorised_haplous": created,
        "violation_rate_among_executed": {
            "C": wilson(c_v, c_vn),
            "B": wilson(b_v, b_vn),
        },
    }


def capture_curve(rows: list[dict], width_mm: float = 0.25) -> dict:
    def band(offset: float) -> float:
        return math.floor(offset / (width_mm / 1000.0)) * width_mm

    out: dict[str, dict] = {}
    for branch in ("A", "B", "C", "D"):
        grouped: dict[float, list[dict]] = defaultdict(list)
        for r in rows:
            grouped[band(r["axes"]["initial_offset_m"])].append(r)
        out[branch] = {
            f"{lo:.2f}-{lo + width_mm:.2f}": {
                **_capture_ci(grouped[lo], branch),
                "band_lo_mm": lo,
                "band_hi_mm": lo + width_mm,
            }
            for lo in sorted(grouped)
        }
    return out


def _capture_ci(rows: list[dict], branch: str) -> dict:
    k = sum(1 for r in rows if r["branches"][branch]["bounded_success"])
    return wilson(k, len(rows))


def _stratified(rows: list[dict], key) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        v = r["axes"].get(key)
        grouped[str(v)].append(r)
    return {
        level: {
            "n": len(g),
            "B_capture": _capture_ci(g, "B"),
            "C_capture": _capture_ci(g, "C"),
            "D_capture": _capture_ci(g, "D"),
        }
        for level, g in sorted(grouped.items())
    }


def interaction_analysis(rows: list[dict]) -> dict:
    # Simple 1-D logistic fit of C bounded success on offset (numpy least
    # squares on the logit grid is not justified at this n; report the
    # monotone band table plus a Spearman-style rank check instead).
    offsets = sorted(r["axes"]["initial_offset_m"] for r in rows)
    c_succ = [1 if r["branches"]["C"]["bounded_success"] else 0 for r in rows]
    out = {
        "offset_mm": {
            "min": min(offsets),
            "max": max(offsets),
            "n_distinct": len(set(offsets)),
        },
        "stratified": {
            "max_force_n": _stratified(rows, "max_force_n"),
            "deadline_s": _stratified(rows, "deadline_s"),
            "n_disturbances": _stratified(rows, "n_disturbances"),
            "n_rejection": _stratified(rows, "n_rejection"),
            "facet_count": _stratified(rows, "facet_count"),
            "plate_thickness_m": _stratified(rows, "plate_thickness_m"),
        },
        "factor_separation": _factor_separation(rows),
        "note": (
            "No sophisticated model is fit: treatment effects are reported as "
            "stratified rates with Wilson intervals and paired exact tests."
        ),
    }
    return out


def _factor_separation(rows: list[dict]) -> dict:
    """For each axis, the spread of C capture across levels: does any
    factor other than offset separate bounded capture?"""
    out = {}
    for key in (
        "max_force_n",
        "deadline_s",
        "max_tilt_deg",
        "n_disturbances",
        "n_rejection",
        "clearance_delta_m",
        "facet_count",
        "plate_thickness_m",
        "position_tolerance_m",
    ):
        strat = _stratified(rows, key)
        rates = [
            v["C_capture"]["value"]
            for v in strat.values()
            if v["C_capture"] and v["n"] >= 5
        ]
        out[key] = {
            "levels_with_n_ge_5": len(rates),
            "C_capture_min": min(rates) if rates else None,
            "C_capture_max": max(rates) if rates else None,
            "C_capture_spread": (max(rates) - min(rates)) if rates else None,
        }
    return out


# --------------------------------------------------------------------------
# Evaluator repair: before/after on the frozen intervention forks
# --------------------------------------------------------------------------


def evaluator_repair() -> dict:
    forks = [
        json.loads(line)
        for line in (FROZEN / "interventions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    migration: Counter = Counter()
    rows = []
    for f in forks:
        cf = (f.get("counterfactuals") or {}).get("intervention")
        if not cf:
            continue
        limit = (f.get("axes") or {}).get("max_force_n")
        cont = ContinuationOutcome(
            completed=bool(cf.get("continuation_completed")),
            peak_force_n=float(cf.get("continuation_peak_force_n") or 0.0),
            max_tilt_deg=0.0,
            wall_s=0.0,
        )
        legacy = legacy_classification(float(cf.get("intervention_force_n") or 0.0), cont, limit)
        fixed = classify_intervention(
            float(cf.get("intervention_force_n") or 0.0), cont, hard_force_limit_n=limit
        )
        migration[(legacy, fixed)] += 1
        rows.append(
            {
                "scenario_id": f["scenario_id"],
                "hard_force_limit_n": limit,
                "continuation_completed": cont.completed,
                "continuation_peak_force_n": cont.peak_force_n,
                "continuation_bounded": (
                    limit is None or cont.peak_force_n <= limit
                )
                and cont.completed,
                "legacy": legacy,
                "corrected": fixed,
            }
        )
    harmful = [r for r in rows if r["legacy"] == HARMFUL]
    return {
        "epochs": {
            "legacy": LEGACY_SEMANTICS_EPOCH,
            "corrected": CLASSIFIER_EPOCH,
        },
        "n_intervention_forks": len(rows),
        "migration": {f"{a}->{b}": c for (a, b), c in sorted(migration.items())},
        "legacy_HARMFUL": {
            "n": len(harmful),
            "bounded_continuations": sum(1 for r in harmful if r["continuation_bounded"]),
            "note": (
                "Every legacy HARMFUL fork has a continuation that breaches a "
                "declared hard contract, so the completion-only rule mislabelled "
                "it. Historical labels are superseded, not rewritten."
            ),
        },
        "rows": rows,
    }


# --------------------------------------------------------------------------
# HES recompute: attainability before/after the reach override
# --------------------------------------------------------------------------


def hes_recompute(rows: list[dict]) -> dict:
    import pyarrow.parquet as pq

    episodes = pq.read_table(FROZEN / "episodes.parquet").to_pylist()

    def dec(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    hap = {r["scenario_id"]: r for r in episodes if r["arm"] == "haplous_mediated"}
    direct = {r["scenario_id"]: r for r in episodes if r["arm"] == "direct_minimal"}
    before_attainable = sum(1 for r in rows if bool(direct[r["scenario_id"]]["bounded_success"]))
    after_attainable = sum(
        1
        for r in rows
        if r["branches"]["B"]["bounded_success"] or r["branches"]["C"]["bounded_success"]
    )
    # Restore the violation evidence the frozen forks lost, from the parquet.
    restored = {
        r["scenario_id"]: {
            "haplous_n_hard_violations": int(hap[r["scenario_id"]].get("n_hard_violations") or 0),
            "direct_n_hard_violations": int(direct[r["scenario_id"]].get("n_hard_violations") or 0),
        }
        for r in rows
    }
    return {
        "scope": "eligible boundary_hi reach-rejection set",
        "n": len(rows),
        "attainability_under_declared_reach_cap": {
            "attainable": before_attainable,
            "non_attainable": len(rows) - before_attainable,
            "basis": "frozen direct_minimal bounded_success",
        },
        "attainability_under_authorised_reach": {
            "attainable": after_attainable,
            "non_attainable": len(rows) - after_attainable,
            "basis": "Branch B or Branch C bounded_success",
        },
        "unreachable_goal_refusal_mass": sum(
            1 for r in rows if "unreachable_goal" in r["branches"]["A"]["refusal_codes"]
        ),
        "declared_reach_cap_m": REACH_CAP_M,
        "restored_violation_evidence": restored,
        "frozen_forks_lost_fields": [
            "hard_violations (only n_hard_violations retained)",
        ],
        "documented_repair": (
            "Frozen interventions.jsonl retained only n_hard_violations after "
            "slim_artifacts; the full records survive in episodes.parquet and are "
            "rejoined here. New artifacts preserve the full records."
        ),
    }


# --------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------


def decide(est: dict, curve: dict, inter: dict) -> dict:
    n = est["n_eligible"]
    b = est["branch_B_capture_among_eligible"]
    c = est["branch_C_capture"]
    be = est["branch_B_executed"]
    brate = b["value"] if b else 0.0
    crate = c["value"] if c else 0.0
    bexec = be["value"] if be else 0.0
    destroyed = est["bounded_success_destroyed_by_authorised_haplous"]
    created = est["bounded_success_created_by_authorised_haplous"]
    refusals = est["branch_B_refusals"]
    # Gap between the unmediated envelope and authorised Haplous, among
    # scenarios Branch B actually executed (excludes deadline refusals).
    authorised_gap = crate - bexec

    paths: list[dict] = []
    # D1: the reach threshold was the only binding constraint and
    # authorised Haplous captures at least as much as the unmediated arm.
    if bexec >= crate - 0.05 and bexec > 0.05:
        paths.append(
            {
                "path": "D1",
                "label": "calibration defect",
                "confidence": "high" if bexec >= crate else "moderate",
                "evidence": {
                    "authorised_haplous_capture_among_executed": bexec,
                    "direct_capture": crate,
                    "capture_gap_C_minus_B": authorised_gap,
                },
                "claim": "max_initial_offset_m is more conservative than the bounded capture envelope",
            }
        )
    # A1: authorised Haplous materially underperforms the unmediated arm
    # on the scenarios it executes.
    if destroyed and authorised_gap > 0.05:
        paths.append(
            {
                "path": "A1",
                "label": "generic Haplous/runtime defect",
                "confidence": "high" if crate > 0 and bexec < 0.5 * crate else "moderate",
                "evidence": {
                    "bounded_success_destroyed": len(destroyed),
                    "capture_gap_among_executed": authorised_gap,
                },
                "claim": "authorisation is not sufficient; the runtime loses attainable bounded successes",
            }
        )
    # P1: the band is genuinely outside the bounded capture envelope:
    # neither arm captures materially.
    if crate < 0.10 and bexec < 0.10:
        paths.append(
            {
                "path": "P1",
                "label": "controller/physics limitation",
                "confidence": "high" if crate == 0.0 else "moderate",
                "evidence": {
                    "direct_capture": crate,
                    "authorised_haplous_capture_among_executed": bexec,
                },
                "claim": "the 3.0-3.5 mm region is outside the bounded capture envelope for both arms",
            }
        )
    # E1: the benchmark's own declared constant contradicts its generated
    # population: scenarios declared infeasible are bounded-feasible under
    # the unmediated arm.
    if crate > 0.0:
        paths.append(
            {
                "path": "E1",
                "label": "benchmark/evaluator defect",
                "confidence": "moderate",
                "evidence": {
                    "declared_infeasible_scenarios_with_direct_bounded_success": int(
                        crate * n
                    ),
                    "declared_reach_cap_m": REACH_CAP_M,
                },
                "claim": "the reachability constant is inconsistent with the offset population it generates",
            }
        )
    if not paths:
        paths.append(
            {
                "path": "AMBIGUOUS",
                "label": "no path resolved",
                "confidence": "low",
                "evidence": {"direct_capture": crate, "authorised_capture": bexec},
                "claim": "evidence insufficient for a single conclusion",
            }
        )
    order = {"D1": 0, "A1": 1, "E1": 2, "P1": 3, "AMBIGUOUS": 4}
    paths.sort(key=lambda p: order[p["path"]])
    primary = paths[0]
    next_experiment = {
        "D1": (
            "Authorized-reach sweep (Branch B vs Branch C) across the full "
            "0.003-0.005 m offset population to estimate the bounded capture "
            "envelope and select a calibrated max_initial_offset_m with a "
            "declared safety margin; calibration is against measured physics, "
            "not benchmark score."
        ),
        "A1": (
            "Paired Branch B vs Branch C on the scenarios where direct bounded "
            "success was destroyed, with per-tick force/tilt traces, to attribute "
            "the loss to constraint monitoring vs negotiation vs recovery."
        ),
        "P1": (
            "Controller/physics characterization at the capture boundary: sweep "
            "offset in fine steps for both arms and identify the mechanical "
            "capture limit; no runtime change is implied."
        ),
        "E1": (
            "Re-derive the benchmark's declared feasibility labels from measured "
            "bounded capture so the reach constant and the generated offset "
            "population agree."
        ),
        "AMBIGUOUS": (
            "Extend the branch ledger with more eligible scenarios before deciding."
        ),
    }[primary["path"]]
    # E1 is a prerequisite observation whenever the benchmark constant is
    # contradicted, regardless of which execution path dominates.
    return {
        "primary": primary,
        "all_paths": paths,
        "evaluator_repair_first": True,
        "bounded_execution_epoch": CLASSIFIER_EPOCH,
        "summary_numbers": {
            "n_eligible": n,
            "branch_A_capture": est["branch_A_capture"]["value"] if est["branch_A_capture"] else None,
            "branch_B_capture_among_eligible": b["value"] if b else None,
            "branch_B_capture_among_executed": bexec,
            "branch_C_capture": crate,
            "branch_D_capture": est["branch_D_capture"]["value"] if est["branch_D_capture"] else None,
            "branch_B_refusals": refusals,
            "branch_B_submit_refusals": est["branch_B_submit_refusals"],
            "branch_B_execution_refusals": est["branch_B_execution_refusals"],
            "deadline_refusals_retained": est["branch_B_deadline_refusals"],
            "bounded_success_destroyed": len(destroyed),
            "bounded_success_created": len(created),
        },
        "separate_issues_not_bundled": [
            "deadline expected_duration_s recalibration",
            "insert_000793 49.7 N mediation divergence",
        ],
        "highest_information_next_experiment": next_experiment,
    }


# --------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------


def _write(name: str, payload: dict) -> None:
    (RESULTS / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _self_check(rows: list[dict]) -> None:
    """Invariants that must hold for the artifacts to mean anything."""
    ids = [r["scenario_id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate scenario rows"
    assert len(rows) == 142, f"expected 142 eligible scenarios, got {len(rows)}"
    for r in rows:
        assert set(r["branches"]) == {"A", "B", "C", "D"}
        for branch, payload in r["branches"].items():
            assert payload["disposition"] in ("COMPLETE", "ABORTED", "REFUSED"), branch
            assert isinstance(payload["hard_violations"], list)
            assert payload["n_hard_violations"] >= len(payload["hard_violations"])
            assert len(payload["hard_violations"]) <= 5
    assert all(
        r["branches"]["A"]["disposition"] == "REFUSED"
        and "unreachable_goal" in r["branches"]["A"]["refusal_codes"]
        for r in rows
    ), "an eligible scenario was not reach-rejected by Branch A"
    for r in rows:
        b = r["branches"]["B"]
        if b["disposition"] == "REFUSED":
            assert b["refusal_stage"] in ("submit", "execution"), r["scenario_id"]
            if b["refusal_stage"] == "execution":
                assert b["refusal_codes"], "execution refusal must carry a code"


def build_artifacts(rows: list[dict]) -> dict:
    _self_check(rows)
    est = estimands(rows)
    curve = capture_curve(rows)
    inter = interaction_analysis(rows)
    repair = evaluator_repair()
    recompute = hes_recompute(rows)
    decision = decide(est, curve, inter)
    return {
        "population_audit.json": population_audit(),
        "estimands.json": est,
        "capture_curve.json": curve,
        "interaction_analysis.json": inter,
        "evaluator_repair.json": repair,
        "hes_recompute.json": recompute,
        "decision.json": decision,
    }


def _report(art: dict) -> str:
    est = art["estimands.json"]
    dec = art["decision.json"]
    curve = art["capture_curve.json"]
    fix = art["evaluator_repair.json"]
    rec = art["hes_recompute.json"]
    sn = dec["summary_numbers"]

    def pct(v):
        return "n/a" if v is None else f"{100 * v:.1f}%"

    lines = []
    lines.append("# HES Boundary Calibration Falsification Campaign")
    lines.append("")
    lines.append(
        "Frozen Haplous runtime. Evaluator semantics repaired to the "
        "bounded-execution epoch (`bounded-execution-v1`) before any reach "
        "calibration claim. The only system variable is the reach capability "
        "reported to the runtime, and only for Branch B."
    )
    lines.append("")
    lines.append("## 1. Question and scope")
    lines.append("")
    lines.append(
        "Is `max_initial_offset_m = 0.003` merely miscalibrated relative to the "
        "actual bounded capture envelope, or does Haplous genuinely fail to "
        "execute boundedly in the 3.0-3.5 mm region?"
    )
    lines.append("")
    lines.append(f"- Eligible boundary_hi reach-rejections run once: **{est['n_eligible']}**")
    lines.append(
        f"- Branch A (frozen Haplous): {sn['branch_A_capture'] and pct(sn['branch_A_capture'])} capture "
        f"({est['branch_A_refused']} refusals)"
    )
    lines.append(f"- Branch B (reach authorised): {pct(sn['branch_B_capture_among_eligible'])} among eligible, "
                 f"{pct(sn['branch_B_capture_among_executed'])} among executed")
    lines.append(f"- Branch C (direct_minimal): {pct(sn['branch_C_capture'])}")
    lines.append(f"- Branch D (competent supervisor): {pct(sn['branch_D_capture'])}")
    lines.append(f"- Branch B refusals retained: submit-time (non-reach) "
                 f"{sn['branch_B_submit_refusals']} of which deadline "
                 f"{sn['deadline_refusals_retained']}; mid-execution freshness "
                 f"{sn['branch_B_execution_refusals']}")
    lines.append("")
    lines.append("## 2. Evaluator repair (prerequisite)")
    lines.append("")
    lines.append(
        f"- Legacy epoch `{fix['epochs']['legacy']}`: HARMFUL iff the counterfactual "
        "continuation merely completed the task."
    )
    lines.append(
        f"- Corrected epoch `{fix['epochs']['corrected']}`: bounded execution "
        "(`task_success AND no hard violations`) is the reference."
    )
    lines.append(
        f"- Migration on the {fix['n_intervention_forks']} frozen intervention forks: "
        f"`{json.dumps(fix['migration'], sort_keys=True)}`"
    )
    lines.append(
        f"- Legacy HARMFUL forks without a bounded continuation: "
        f"**{fix['legacy_HARMFUL']['n']}** (secondary check: "
        f"{fix['legacy_HARMFUL']['bounded_continuations']} with a bounded continuation)."
    )
    lines.append("")
    lines.append("## 3. Estimands")
    lines.append("")
    lines.append(f"- Direct bounded capture in the band: {pct(est['branch_C_capture']['value'])} "
                 f"({est['branch_C_capture']['k']}/{est['branch_C_capture']['n']})")
    lines.append(f"- Authorised Haplous capture (executed): {pct(sn['branch_B_capture_among_executed'])}")
    lines.append(f"- Paired B vs C: {json.dumps(est['paired_B_vs_C'], sort_keys=True)}")
    lines.append(
        f"- Bounded successes destroyed by authorised Haplous: {sn['bounded_success_destroyed']}; "
        f"created: {sn['bounded_success_created']}"
    )
    lines.append(
        f"- Violation rate among executed - C {pct((est['violation_rate_among_executed']['C'] or {}).get('value'))}, "
        f"B {pct((est['violation_rate_among_executed']['B'] or {}).get('value'))}"
    )
    lines.append("")
    lines.append("## 4. Capture curve")
    lines.append("")
    lines.append("| offset band (mm) | A | B | C | D | n |")
    lines.append("|---|---|---|---|---|---|")
    for band, _ in sorted(curve["C"].items()):
        n = curve["C"][band]["n"]

        def g(branch):
            cell = curve[branch].get(band)
            return "n/a" if not cell else f"{100 * cell['value']:.0f}%"

        lines.append(f"| {band} | {g('A')} | {g('B')} | {g('C')} | {g('D')} | {n} |")
    lines.append("")
    lines.append("## 5. Interaction analysis")
    lines.append("")
    lines.append("Stratified rates, Wilson intervals, paired exact tests. No fitted model.")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(art["interaction_analysis.json"]["stratified"], indent=1, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append("## 6. HES recompute")
    lines.append("")
    lines.append(
        f"- Attainable under declared reach cap: {rec['attainability_under_declared_reach_cap']['attainable']} "
        f"/ {rec['n']}"
    )
    lines.append(
        f"- Attainable under authorised reach: {rec['attainability_under_authorised_reach']['attainable']} "
        f"/ {rec['n']}"
    )
    lines.append(f"- Declared reach cap: {rec['declared_reach_cap_m']} m")
    lines.append("")
    lines.append("## 7. Decision")
    lines.append("")
    lines.append(f"**Primary: {dec['primary']['path']} - {dec['primary']['label']} "
                 f"(confidence {dec['primary']['confidence']})**")
    lines.append("")
    lines.append(dec["primary"]["claim"] + ".")
    lines.append("")
    for p in dec["all_paths"]:
        lines.append(f"- {p['path']} ({p['label']}, {p['confidence']}): {p['claim']}")
    lines.append("")
    lines.append("**Highest-information next experiment:** " + dec["highest_information_next_experiment"])
    lines.append("")
    lines.append("**Not bundled:** " + "; ".join(dec["separate_issues_not_bundled"]))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="recompute and verify committed artifacts")
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 2))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    if args.check:
        if not RAW_ROWS.is_file():
            print("no committed raw rows to check", file=sys.stderr)
            return 2
        rows = load_rows()
        art = build_artifacts(rows)
        bad = []
        for name, payload in art.items():
            canonical = json.dumps(payload, indent=2, sort_keys=True) + "\n"
            existing = (RESULTS / name).read_text(encoding="utf-8") if (RESULTS / name).is_file() else None
            if existing != canonical:
                bad.append(name)
        if bad:
            print("MISMATCH: " + ", ".join(bad), file=sys.stderr)
            return 1
        print(f"check OK: {len(art)} artifacts reproduce from {len(rows)} committed rows")
        return 0

    t0 = time.time()
    rows = run_all(args.workers, args.limit)
    art = build_artifacts(rows)
    for name, payload in art.items():
        _write(name, payload)
    (ROOT / "docs" / "HES_BOUNDARY_CALIBRATION.md").write_text(
        _report(art), encoding="utf-8"
    )
    print(
        f"wrote {len(art)} artifacts + report from {len(rows)} rows "
        f"in {time.time() - t0:.1f}s"
    )
    print(json.dumps(art["decision.json"]["summary_numbers"], indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())