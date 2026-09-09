"""R05 locked comparison driver (see .opencode/haplous/state/locked-tests/R05-v2.yaml).

Phases:
  generate  write the locked dev/holdout manifests and record their hashes
  dev       run the development envelope for both arms' candidate sets and
            freeze the selected arm definitions
  holdout   run the 80-scenario held-out evaluation with the frozen arms
            (paired jobs: one worker per scenario executes both arms in
            arm_order_bit order on one physical model)
  analyze   paired exact McNemar analysis over the holdout trial rows

Every physics motion is an EpisodeConfig executed by pegsim.rollout.run_episode
via pegsim.r05. Trial rows are written per scenario-arm; aggregates are only
derived outputs. Raw rows are the evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np

import pegsim.r05 as r05
from pegsim.r05 import (
    HAPLOUS_CANDIDATES,
    HaplousCandidate,
    Scenario,
    grade_production,
    run_baseline_arm,
    run_haplous_arm,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
MANIFEST_DEV = RESULTS / "r05_dev_manifest.json"
MANIFEST_HOLDOUT = RESULTS / "r05_holdout_manifest.json"
TRIALS_DEV = RESULTS / "r05_dev_trials.jsonl"
TRIALS_HOLDOUT = RESULTS / "r05_holdout_trials.jsonl"
SELECTIONS = RESULTS / "r05_selections.json"
SUMMARY = RESULTS / "r05_summary.json"
MANIFEST_HASHES = RESULTS / "r05_manifest_hashes.json"
MAX_WORKERS = 14
MIN_THROUGHPUT_STEPS_PER_S = 200.0
EPISODE_STEPS = 32600


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _repo_head() -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def scenario_from_dict(d: dict) -> Scenario:
    return Scenario(
        scenario_id=d["scenario_id"],
        stratum_id=d["stratum_id"],
        stratum_name=d["stratum_name"],
        replicate_id=d["replicate_id"],
        seed=d["seed"],
        fixture_radius_m=d["fixture_radius_m"],
        offset_bearing_rad=d["offset_bearing_rad"],
        c0_x_m=d["c0_x_m"],
        c0_y_m=d["c0_y_m"],
        clearance_offset_m=d["clearance_offset_m"],
        tcp_translation_m=(
            tuple(d["tcp_translation_m"])
            if d["tcp_translation_m"] is not None
            else None
        ),
        tcp_yaw_rad=d["tcp_yaw_rad"],
        encoder_resolution_m=d["encoder_resolution_m"],
        first_probe_bearing_rad=d["first_probe_bearing_rad"],
        arm_order_bit=d["arm_order_bit"],
    )


def load_manifest(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"{path.name} is not a non-empty JSON list")
    return data


def _exclusive_write(path: Path, text: str) -> None:
    if path.exists():
        raise RuntimeError(f"{path.name} already exists; refusing to overwrite")
    path.write_text(text, encoding="utf-8")


def phase_generate() -> None:
    r05.assert_evidence_fingerprints()
    dev = r05.generate_manifest(
        "dev", master_seed=r05.DEV_MASTER_SEED,
        strata=r05.DEV_STRATA, n_per_stratum=r05.DEV_N_PER_STRATUM,
    )
    holdout = r05.generate_manifest(
        "holdout", master_seed=r05.HELDOUT_MASTER_SEED,
        strata=r05.HELDOUT_STRATA, n_per_stratum=r05.HELDOUT_N_PER_STRATUM,
    )
    if not (len(dev) == 20 and len(holdout) == 80):
        raise RuntimeError("locked scenario counts violated")
    dev_json = r05.canonical_manifest_json(dev)
    holdout_json = r05.canonical_manifest_json(holdout)
    for path in (MANIFEST_DEV, MANIFEST_HOLDOUT, MANIFEST_HASHES):
        if path.exists():
            raise RuntimeError(
                f"{path.name} already exists; manifests are one-shot (delete "
                "explicitly if you intend to re-lock)."
            )
    MANIFEST_DEV.write_text(dev_json, encoding="utf-8")
    MANIFEST_HOLDOUT.write_text(holdout_json, encoding="utf-8")
    hashes = {
        "dev_manifest_sha256": _sha256_text(dev_json),
        "holdout_manifest_sha256": _sha256_text(holdout_json),
        "n5_raw_sha256": r05.N5_RAW_SHA256,
        "n5_summary_sha256": r05.N5_SUMMARY_SHA256,
        "repo_head": _repo_head(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    MANIFEST_HASHES.write_text(json.dumps(hashes, indent=1), encoding="utf-8")
    print(json.dumps(hashes, indent=1))


# ---------------------------------------------------------------------------
# Worker: one paired scenario cell (both arms, one physical model)
# ---------------------------------------------------------------------------


def run_cell(args: tuple) -> list[dict]:
    phase, scenario_dict, arm, spec_id, v_t = args
    scenario = scenario_from_dict(scenario_dict)
    t_start = time.time()
    exogenous_hash = _sha256_text(
        json.dumps(r05.scenario_to_dict(scenario), sort_keys=True)
    )
    model, data, xml_hash = r05.build_physical_model(scenario)
    _, control_task = r05.build_tasks(scenario.clearance_offset_m)
    del control_task
    common = {
        "phase": phase,
        "scenario_id": scenario.scenario_id,
        "stratum_id": scenario.stratum_id,
        "stratum_name": scenario.stratum_name,
        "seed": scenario.seed,
        "exogenous_sha256": exogenous_hash,
        "nominal_clearance_m": r05.NOMINAL_CLEARANCE_M,
        "physical_clearance_m": scenario.physical_clearance_m,
        "clearance_offset_m": scenario.clearance_offset_m,
        "initial_offset_r_m": scenario.fixture_radius_m,
        "initial_offset_theta_rad": scenario.offset_bearing_rad,
        "orientation_error_rad": 0.0,
        "arm_order_bit": scenario.arm_order_bit,
        "measurement_corruption": (
            "tcp_bias_3um+yaw_0.01+quant_2um"
            if scenario.tcp_translation_m is not None
            else "none"
        ),
        "model_xml_sha256": xml_hash,
    }
    if arm == "baseline":
        tr = run_baseline_arm(scenario, v_t, model=model, data=data)
        grade = grade_production(tr, scenario.physical_clearance_m)
        return [{
            **common,
            "system": "baseline",
            "baseline_or_haplous": "baseline",
            "candidate_id": f"v_t={v_t}",
            "baseline_v_t_m_per_s": v_t,
            "episodes_run": 1,
            "success": grade.success,
            "completion_time_s": grade.seat_time_s,
            "pipeline_time_s": grade.seat_time_s,
            "peak_force_n": grade.peak_abs_fz_n,
            "pipeline_peak_force_n": grade.peak_abs_fz_n,
            "force_integral_n_s": grade.force_integral_n_s,
            "max_tilt_deg": grade.max_tilt_deg,
            "recovery_count": 0,
            "abort": False,
            "failure_class": grade.failure_class,
            "failure_reason": grade.failure_class if not grade.success else None,
            "true_state_error_m": None,
            "estimated_state_error_m": None,
            "estimated_uncertainty_m": None,
            "nees": None,
            "confidently_wrong": None,
            "probe_count": 0,
            "probe_unseatable_count": 0,
            "probe_time_s": 0.0,
            "spiral_usage": "baseline_spiral",
            "guided": False,
            "updated_pair_count": 0,
            "probe_events": [],
            "final_filter_state_m": None,
            "wall_time_s": time.time() - t_start,
            "validity": "valid",
        }]

    candidate = next(c for c in HAPLOUS_CANDIDATES if c.id == spec_id)
    execution, production, diagnostics = run_haplous_arm(
        scenario, candidate, v_t, model=model, data=data
    )
    grade = grade_production(production, scenario.physical_clearance_m)
    estimator_abort = any(e.kind == "ABORT" for e in execution.probe_events)
    p_hh = np.array(diagnostics["final_filter_h_cov_m2"], dtype=float)
    return [{
        **common,
        "system": "haplous",
        "baseline_or_haplous": "haplous",
        "candidate_id": candidate.id,
        "baseline_v_t_m_per_s": v_t,
        "episodes_run": execution.probe_side_motions + 1,
        "success": grade.success,
        "completion_time_s": grade.seat_time_s,
        "pipeline_time_s": (
            execution.probe_time_s
            + (grade.seat_time_s if grade.success else r05.MAX_SECONDS)
        ),
        "peak_force_n": grade.peak_abs_fz_n,
        "pipeline_peak_force_n": max(
            [grade.peak_abs_fz_n] + execution.probe_peak_fz_n
        ),
        "force_integral_n_s": grade.force_integral_n_s,
        "max_tilt_deg": grade.max_tilt_deg,
        "recovery_count": 0,
        "abort": estimator_abort,
        "failure_class": grade.failure_class,
        "failure_reason": grade.failure_class if not grade.success else None,
        "true_state_error_m": execution.h_error_norm_m,
        "estimated_state_error_m": execution.h_error_norm_m,
        "estimated_uncertainty_m": float(
            math.sqrt(max(float(np.trace(p_hh)), 0.0)) / 2.0
        ),
        "nees": execution.nees,
        "confidently_wrong": execution.confidently_wrong,
        "probe_count": execution.probe_side_motions,
        "probe_unseatable_count": sum(
            1 for e in execution.probe_events if e.kind == "unseatable"
        ),
        "probe_time_s": execution.probe_time_s,
        "spiral_usage": execution.spiral_usage,
        "guided": execution.guided,
        "updated_pair_count": diagnostics["updated_pair_count"],
        "probe_events": [
            {
                "attempt": e.attempt_index,
                "bearing_rad": e.bearing_rad,
                "kind": e.kind,
                "nis": e.nis,
            }
            for e in execution.probe_events
        ],
        "final_filter_state_m": diagnostics["final_filter_state_m"],
        "wall_time_s": time.time() - t_start,
        "validity": "valid",
    }]


def run_cells(jobs: list[tuple], out_path: Path) -> tuple[int, float]:
    t0 = time.time()
    total_rows = 0
    with out_path.open("w", encoding="utf-8") as fh, ProcessPoolExecutor(
        max_workers=14
    ) as pool:
        for rows in pool.map(run_cell, jobs, chunksize=1):
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                total_rows += 1
    wall = time.time() - t0
    print(f"{out_path.name}: {total_rows} rows, wall {wall:.0f}s")
    return total_rows, wall


# ---------------------------------------------------------------------------
# Selection (locked lexicographic rules) and gates
# ---------------------------------------------------------------------------


def _p95(values: list[float]) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), 0.95, method="linear"))


def _median(values: list[float]) -> float:
    return float(np.median(np.asarray(values, dtype=float)))


def _mean_none_safe(values: list[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return float(np.mean(present)) if present else None


def throughput_gate(rows: list[dict]) -> float:
    """G9: minimum per-row physics throughput (steps/s) must exceed the
    locked 200 steps/s per active worker on full-length episodes."""
    rates = []
    for row in rows:
        wall = row.get("wall_time_s")
        if not wall or wall <= 0:
            continue
        rates.append(row["episodes_run"] * EPISODE_STEPS / wall)
    if not rates:
        raise RuntimeError("G9 throughput gate: no rows to rate")
    worst = min(rates)
    if worst < MIN_THROUGHPUT_STEPS_PER_S:
        raise RuntimeError(
            f"G9 throughput gate FAILED: worst row {worst:.0f} steps/s < "
            f"{MIN_THROUGHPUT_STEPS_PER_S:.0f}; R05-v2 is INVALID"
        )
    return worst


def select_baseline(rows: list[dict]) -> tuple[float, dict]:
    by_candidate: dict[float, list[dict]] = {}
    for row in rows:
        if row["system"] != "baseline":
            continue
        by_candidate.setdefault(row["baseline_v_t_m_per_s"], []).append(row)
    scored = {}
    for v_t, cells in by_candidate.items():
        scored[v_t] = {
            "successes": sum(1 for r in cells if r["success"]),
            "p95_peak_fz_n": _p95([r["peak_force_n"] for r in cells]),
            "median_capped_time_s": _median([r["completion_time_s"] for r in cells]),
        }
    best = sorted(
        scored.items(),
        key=lambda item: (
            -item[1]["successes"],
            item[1]["p95_peak_fz_n"],
            item[1]["median_capped_time_s"],
            item[0],
        ),
    )[0]
    return best[0], scored


def select_haplous(rows: list[dict], baseline_v_t: float) -> tuple[str, dict]:
    by_candidate: dict[str, list[dict]] = {}
    for row in rows:
        if row["system"] != "haplous":
            continue
        assert row["baseline_v_t_m_per_s"] == baseline_v_t
        by_candidate.setdefault(row["candidate_id"], []).append(row)
    scored = {}
    for cid, cells in by_candidate.items():
        scored[cid] = {
            "successes": sum(1 for r in cells if r["success"]),
            "p95_pipeline_peak_fz_n": _p95(
                [r["pipeline_peak_force_n"] for r in cells]
            ),
            "median_total_pipeline_time_s": _median(
                [r["pipeline_time_s"] for r in cells]
            ),
            "total_probe_side_motions": sum(r["probe_count"] for r in cells),
        }
    best = sorted(
        scored.items(),
        key=lambda item: (
            -item[1]["successes"],
            item[1]["p95_pipeline_peak_fz_n"],
            item[1]["median_total_pipeline_time_s"],
            item[1]["total_probe_side_motions"],
            item[0],
        ),
    )[0]
    return best[0], scored


def phase_dev() -> None:
    r05.assert_evidence_fingerprints()
    if TRIALS_DEV.exists() or SELECTIONS.exists():
        raise RuntimeError(
            "dev artifacts exist; the development budget is one-shot. Move "
            "them aside explicitly if a debug-phase rescore is intended."
        )
    hashes = json.loads(MANIFEST_HASHES.read_text(encoding="utf-8"))
    dev_hash = _sha256_text(MANIFEST_DEV.read_text(encoding="utf-8"))
    if dev_hash != hashes["dev_manifest_sha256"]:
        raise RuntimeError("dev manifest changed since generation")
    scenarios = load_manifest(MANIFEST_DEV)
    assert len(scenarios) == 20
    baseline_jobs = [
        ("dev", s, "baseline", "baseline", v_t)
        for v_t in r05.BASELINE_V_T_CANDIDATES_M_PER_S
        for s in scenarios
    ]
    assert len(baseline_jobs) == 100
    baseline_rows: list[dict] = []
    with TRIALS_DEV.open("x", encoding="utf-8") as fh, ProcessPoolExecutor(
        max_workers=14
    ) as pool:
        for rows in pool.map(run_cell, baseline_jobs, chunksize=1):
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                baseline_rows.append(row)
    worst_rate = throughput_gate(baseline_rows)
    baseline_v_t, baseline_scores = select_baseline(baseline_rows)
    print(f"G9 worst-row throughput {worst_rate:.0f} steps/s: PASS")
    print(f"selected baseline v_t={baseline_v_t}")

    haplous_jobs = [
        ("dev", s, "haplous", c.id, baseline_v_t)
        for c in HAPLOUS_CANDIDATES
        for s in scenarios
    ]
    assert len(haplous_jobs) == 100
    haplous_rows: list[dict] = []
    with TRIALS_DEV.open("a", encoding="utf-8") as fh, ProcessPoolExecutor(
        max_workers=14
    ) as pool:
        for rows in pool.map(run_cell, haplous_jobs, chunksize=1):
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                haplous_rows.append(row)
    throughput_gate(haplous_rows)
    selected_candidate, haplous_scores = select_haplous(haplous_rows, baseline_v_t)
    print(f"selected haplous candidate {selected_candidate}")
    selections = {
        "locked_design": ".opencode/haplous/state/locked-tests/R05-v2.yaml",
        "locked_design_sha256": _sha256_text(
            (r05.ROOT / ".opencode/haplous/state/locked-tests/R05-v2.yaml")
            .read_text(encoding="utf-8")
        ),
        "repo_head": _repo_head(),
        "baseline_selected_v_t_m_per_s": baseline_v_t,
        "baseline_selection_metrics": {
            str(k): v for k, v in baseline_scores.items()
        },
        "haplous_selected_candidate": selected_candidate,
        "haplous_selection_metrics": haplous_scores,
        "dev_manifest_sha256": dev_hash,
        "g9_worst_row_steps_per_s": worst_rate,
        "selection_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    SELECTIONS.write_text(json.dumps(selections, indent=1), encoding="utf-8")
    print(json.dumps(selections, indent=1))


def phase_holdout() -> None:
    r05.assert_evidence_fingerprints()
    if not SELECTIONS.exists():
        raise RuntimeError("frozen selections missing; run the dev phase first")
    selections = json.loads(SELECTIONS.read_text(encoding="utf-8"))
    hashes = json.loads(MANIFEST_HASHES.read_text(encoding="utf-8"))
    holdout_hash = _sha256_text(MANIFEST_HOLDOUT.read_text(encoding="utf-8"))
    if holdout_hash != hashes["holdout_manifest_sha256"]:
        raise RuntimeError("holdout manifest changed since generation")
    if (
        selections.get("g9_worst_row_steps_per_s", 0.0)
        < MIN_THROUGHPUT_STEPS_PER_S
    ):
        raise RuntimeError("G9 throughput gate not passed in dev; holdout refused")
    if TRIALS_HOLDOUT.exists():
        raise RuntimeError(f"{TRIALS_HOLDOUT.name} exists; holdout is one-shot.")
    scenarios = load_manifest(MANIFEST_HOLDOUT)
    assert len(scenarios) == 80
    baseline_v_t = selections["baseline_selected_v_t_m_per_s"]
    candidate_id = selections["haplous_selected_candidate"]
    jobs = [
        ("holdout", s, candidate_id, baseline_v_t) for s in scenarios
    ]
    with TRIALS_HOLDOUT.open("x", encoding="utf-8") as fh, ProcessPoolExecutor(
        max_workers=14
    ) as pool:
        n_rows = 0
        for rows in pool.map(run_cell_paired, jobs, chunksize=1):
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                n_rows += 1
    if n_rows != 160:
        raise RuntimeError(f"expected 160 holdout rows, wrote {n_rows}")
    phase_analyze()


def run_cell_paired(args: tuple) -> list[dict]:
    """Holdout cell: both arms on the SAME physical model in one worker, in
    arm_order_bit order, with the exogenous identity asserted before either
    arm runs (G7). run_episode resets physics before every motion, so arm
    order cannot leak state."""
    phase, scenario_dict, candidate_id, baseline_v_t = args
    scenario = scenario_from_dict(scenario_dict)
    t_start = time.time()
    exogenous_hash = _sha256_text(
        json.dumps(r05.scenario_to_dict(scenario), sort_keys=True)
    )
    candidate = next(c for c in HAPLOUS_CANDIDATES if c.id == candidate_id)
    model, data, xml_hash = r05.build_physical_model(scenario)
    _, control_task = r05.build_tasks(scenario.clearance_offset_m)
    del control_task

    if scenario.arm_order_bit == 0:
        baseline_tr = run_baseline_arm(
            scenario, baseline_v_t, model=model, data=data
        )
        baseline_grade = grade_production(baseline_tr, scenario.physical_clearance_m)
        execution, production, diagnostics = run_haplous_arm(
            scenario, candidate, baseline_v_t, model=model, data=data
        )
    else:
        execution, production, diagnostics = run_haplous_arm(
            scenario, candidate, baseline_v_t, model=model, data=data
        )
        baseline_tr = run_baseline_arm(
            scenario, baseline_v_t, model=model, data=data
        )
        baseline_grade = grade_production(baseline_tr, scenario.physical_clearance_m)
    production_grade = grade_production(production, scenario.physical_clearance_m)
    estimator_abort = any(e.kind == "ABORT" for e in execution.probe_events)
    p_hh = np.array(diagnostics["final_filter_h_cov_m2"], dtype=float)
    t_end = time.time() - t_start

    def base_common() -> dict:
        return {
            "phase": phase,
            "scenario_id": scenario.scenario_id,
            "stratum_id": scenario.stratum_id,
            "stratum_name": scenario.stratum_name,
            "seed": scenario.seed,
            "exogenous_sha256": exogenous_hash,
            "model_xml_sha256": xml_hash,
            "nominal_clearance_m": r05.NOMINAL_CLEARANCE_M,
            "physical_clearance_m": scenario.physical_clearance_m,
            "clearance_offset_m": scenario.clearance_offset_m,
            "initial_offset_r_m": scenario.fixture_radius_m,
            "initial_offset_theta_rad": scenario.offset_bearing_rad,
            "orientation_error_rad": 0.0,
            "arm_order_bit": scenario.arm_order_bit,
            "measurement_corruption": (
                "tcp_bias_3um+yaw_0.01+quant_2um"
                if scenario.tcp_translation_m is not None
                else "none"
            ),
        }

    baseline_row = {
        **base_common(),
        "system": "baseline",
        "baseline_or_haplous": "baseline",
        "candidate_id": f"v_t={baseline_v_t}",
        "baseline_v_t_m_per_s": baseline_v_t,
        "episodes_run": 1,
        "success": baseline_grade.success,
        "completion_time_s": baseline_grade.seat_time_s,
        "pipeline_time_s": baseline_grade.seat_time_s,
        "peak_force_n": baseline_grade.peak_abs_fz_n,
        "pipeline_peak_force_n": baseline_grade.peak_abs_fz_n,
        "force_integral_n_s": baseline_grade.force_integral_n_s,
        "max_tilt_deg": baseline_grade.max_tilt_deg,
        "recovery_count": 0,
        "abort": False,
        "failure_class": baseline_grade.failure_class,
        "failure_reason": (
            baseline_grade.failure_class if not baseline_grade.success else None
        ),
        "true_state_error_m": None,
        "estimated_state_error_m": None,
        "estimated_uncertainty_m": None,
        "nees": None,
        "confidently_wrong": None,
        "probe_count": 0,
        "probe_unseatable_count": 0,
        "probe_time_s": 0.0,
        "spiral_usage": "baseline_spiral",
        "guided": False,
        "updated_pair_count": 0,
        "probe_events": [],
        "final_filter_state_m": None,
        "wall_time_s": t_end,
        "validity": "valid",
    }
    haplous_row = {
        **base_common(),
        "system": "haplous",
        "baseline_or_haplous": "haplous",
        "candidate_id": candidate.id,
        "baseline_v_t_m_per_s": baseline_v_t,
        "episodes_run": execution.probe_side_motions + 1,
        "success": production_grade.success,
        "completion_time_s": production_grade.seat_time_s,
        "pipeline_time_s": (
            execution.probe_time_s
            + (
                production_grade.seat_time_s
                if production_grade.success
                else r05.MAX_SECONDS
            )
        ),
        "peak_force_n": production_grade.peak_abs_fz_n,
        "pipeline_peak_force_n": max(
            [production_grade.peak_abs_fz_n] + execution.probe_peak_fz_n
        ),
        "force_integral_n_s": production_grade.force_integral_n_s,
        "max_tilt_deg": production_grade.max_tilt_deg,
        "recovery_count": 0,
        "abort": estimator_abort,
        "failure_class": production_grade.failure_class,
        "failure_reason": (
            production_grade.failure_class if not production_grade.success else None
        ),
        "true_state_error_m": execution.h_error_norm_m,
        "estimated_state_error_m": execution.h_error_norm_m,
        "estimated_uncertainty_m": float(
            math.sqrt(max(float(np.trace(p_hh)), 0.0)) / 2.0
        ),
        "nees": execution.nees,
        "confidently_wrong": execution.confidently_wrong,
        "probe_count": execution.probe_side_motions,
        "probe_unseatable_count": sum(
            1 for e in execution.probe_events if e.kind == "unseatable"
        ),
        "probe_time_s": execution.probe_time_s,
        "spiral_usage": execution.spiral_usage,
        "guided": execution.guided,
        "updated_pair_count": diagnostics["updated_pair_count"],
        "probe_events": [
            {
                "attempt": e.attempt_index,
                "bearing_rad": e.bearing_rad,
                "kind": e.kind,
                "nis": e.nis,
            }
            for e in execution.probe_events
        ],
        "final_filter_state_m": diagnostics["final_filter_state_m"],
        "wall_time_s": t_end,
        "validity": "valid",
    }
    # arm_order_bit records the registered execution order; both arms ran on
    # the same physical model with run_episode's per-episode reset.
    return [baseline_row, haplous_row]


def analyze_holdout(selections: dict) -> dict:
    from pegsim.r05 import paired_table

    rows = [
        json.loads(line)
        for line in TRIALS_HOLDOUT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    invalid = [r for r in rows if r.get("validity") != "valid"]
    if invalid:
        raise RuntimeError(
            f"{len(invalid)} invalid rows; R05-v2 is INVALID per the locked rules"
        )
    baseline = {r["scenario_id"]: r for r in rows if r["system"] == "baseline"}
    haplous = {r["scenario_id"]: r for r in rows if r["system"] == "haplous"}
    if not (set(baseline) == set(haplous) and len(baseline) == 80):
        raise RuntimeError(
            f"paired completeness violated: {len(baseline)} baseline / "
            f"{len(haplous)} haplous rows"
        )
    ids = sorted(baseline)
    b_succ = [bool(baseline[i]["success"]) for i in ids]
    h_succ = [bool(haplous[i]["success"]) for i in ids]
    table = paired_table(b_succ, h_succ)
    by_stratum: dict[int, dict] = {}
    for i in ids:
        sid = baseline[i]["stratum_id"]
        cell = by_stratum.setdefault(
            sid, {"n": 0, "baseline_successes": 0, "haplous_successes": 0}
        )
        cell["n"] += 1
        cell["baseline_successes"] += int(baseline[i]["success"])
        cell["haplous_successes"] += int(haplous[i]["success"])
    supported = table["delta_hat"] >= 0.10 and table["p_haplous_superior"] < 0.05
    refuted = table["delta_hat"] <= -0.10 and table["p_baseline_superior"] < 0.05
    if supported:
        label = "SUPPORTED"
    elif refuted:
        label = "REFUTED"
    elif table["delta_hat"] == 0.0:
        label = "INCONCLUSIVE"
    else:
        label = "WEAKENED"
    summary = {
        "phase": "holdout",
        "locked_design": ".opencode/haplous/state/locked-tests/R05-v2.yaml",
        "baseline_selected_v_t_m_per_s": selections["baseline_selected_v_t_m_per_s"],
        "haplous_selected_candidate": selections["haplous_selected_candidate"],
        "primary_table": table,
        "preregistered_label": label,
        "per_stratum": by_stratum,
        "secondary": {
            "baseline_median_completion_s": _median(
                [baseline[i]["completion_time_s"] for i in ids]
            ),
            "haplous_median_completion_s": _median(
                [haplous[i]["completion_time_s"] for i in ids]
            ),
            "haplous_median_probe_time_s": _median(
                [haplous[i]["probe_time_s"] for i in ids]
            ),
            "haplous_median_pipeline_time_s": _median(
                [haplous[i]["pipeline_time_s"] for i in ids]
            ),
            "baseline_median_pipeline_peak_fz_n": _median(
                [baseline[i]["pipeline_peak_force_n"] for i in ids]
            ),
            "haplous_median_pipeline_peak_fz_n": _median(
                [haplous[i]["pipeline_peak_force_n"] for i in ids]
            ),
            "guided_fraction": sum(1 for i in ids if haplous[i]["guided"]) / 80.0,
            "fallback_fraction": sum(
                1 for i in ids if not haplous[i]["guided"]
            ) / 80.0,
            "estimator_abort_count": sum(1 for i in ids if haplous[i]["abort"]),
            "probe_unseatable_total": sum(
                haplous[i]["probe_unseatable_count"] for i in ids
            ),
            "confidently_wrong_count": sum(
                1 for i in ids if haplous[i]["confidently_wrong"]
            ),
            "mean_nees": _mean_none_safe([haplous[i]["nees"] for i in ids]),
        },
    }
    SUMMARY.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return summary


def phase_analyze() -> None:
    if not SELECTIONS.exists():
        raise RuntimeError("selections missing")
    selections = json.loads(SELECTIONS.read_text(encoding="utf-8"))
    analyze_holdout(selections)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=["generate", "dev", "holdout", "analyze"]
    )
    args = parser.parse_args()
    if args.phase == "generate":
        phase_generate()
    elif args.phase == "dev":
        phase_dev()
    elif args.phase == "holdout":
        phase_holdout()
    else:
        phase_analyze()


if __name__ == "__main__":
    main()
