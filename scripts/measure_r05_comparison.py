"""R05 locked comparison driver (see .opencode/haplous/state/locked-tests/R05-v3.yaml).

Phases:
  generate  write the locked dev/holdout manifests and record their hashes
  dev       run the development envelope for both arms' candidate sets and
            freeze the selected arm definitions
  freeze    write the exclusive execution freeze record (hashes of the lock,
            manifests, selections, runner sources; current clean HEAD)
  holdout   run the 80-scenario held-out evaluation with the frozen arms
            (paired jobs: one worker per scenario executes both arms in
            arm_order_bit order on one physical model), then analyze
  analyze   paired exact McNemar analysis over the holdout trial rows

G10 firewall: arm functions receive ONLY policy-visible inputs (the nominal
control task and a PolicyView). Hidden physical clearance dc and truth-side
diagnostics (NEES, h error, confidently-wrong) are computed offline here.

Every physics motion is an EpisodeConfig executed by pegsim.rollout.run_episode
via pegsim.r05. Trial rows and full trace sidecars are written per scenario;
aggregates are only derived outputs. Raw rows and traces are the evidence.
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
from pegsim.rollout import TrialResult

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
MANIFEST_DEV = RESULTS / "r05_dev_manifest.json"
MANIFEST_HOLDOUT = RESULTS / "r05_holdout_manifest.json"
TRIALS_DEV = RESULTS / "r05_dev_trials.jsonl"
TRIALS_HOLDOUT = RESULTS / "r05_holdout_trials.jsonl"
SELECTIONS = RESULTS / "r05_selections.json"
SUMMARY = RESULTS / "r05_summary.json"
MANIFEST_HASHES = RESULTS / "r05_manifest_hashes.json"
FREEZE = RESULTS / "r05_freeze.json"
TRACES_DEV = RESULTS / "r05_traces_dev"
TRACES_HOLDOUT = RESULTS / "r05_traces_holdout"
LOCK_YAML = ROOT / ".opencode/haplous/state/locked-tests/R05-v3.yaml"
MAX_WORKERS = 14
MIN_THROUGHPUT_STEPS_PER_S = 200.0
EPISODE_STEPS = 32600

_HASHED_SOURCES = (
    LOCK_YAML,
    Path("scripts/measure_r05_comparison.py"),
    Path("pegsim/r05.py"),
    Path("pegsim/filter.py"),
    Path("pegsim/probing.py"),
    Path("pegsim/rollout.py"),
    Path("pegsim/error_injection.py"),
    Path("tests/test_r05.py"),
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def filter_record_json(record) -> dict:
    return {
        "outcome": record.outcome.value,
        "bearing_rad": record.bearing_rad,
        "innovation_m": np.asarray(record.innovation_m).tolist(),
        "innovation_covariance_m2": np.asarray(
            record.innovation_covariance_m2
        ).tolist(),
        "nis": record.normalized_innovation_squared,
        "gate_threshold": record.gate_threshold,
        "consecutive_gate_count": record.consecutive_gate_count,
        "last_good_state_m": np.asarray(record.last_good_state_m).tolist(),
        "gated_innovation_history_m": [
            np.asarray(g).tolist() for g in record.gated_innovation_history_m
        ],
        "state_m": np.asarray(record.state_m).tolist(),
        "covariance_m2": np.asarray(record.covariance_m2).tolist(),
    }


def probe_event_json(event) -> dict:
    return {
        "attempt": event.attempt_index,
        "bearing_rad": event.bearing_rad,
        "kind": event.kind,
        "nis": event.nis,
        "plus_time_s": event.plus_time_s,
        "minus_time_s": event.minus_time_s,
        "plus_reduced_xy_m": (
            event.plus_reduced_xy_m.tolist()
            if event.plus_reduced_xy_m is not None
            else None
        ),
        "minus_reduced_xy_m": (
            event.minus_reduced_xy_m.tolist()
            if event.minus_reduced_xy_m is not None
            else None
        ),
    }


def _trace_arrays(prefix: str, tr: TrialResult) -> dict:
    return {
        f"{prefix}/{k}": v
        for k, v in {
            "time": tr.time,
            "tip_pos": tr.tip_pos,
            "setpoint_xy": tr.setpoint_xy,
            "z_des": tr.z_des,
            "z_flat": tr.z_flat,
            "contact_force": tr.contact_force,
            "qfrc_applied": tr.qfrc_applied,
            "tip_lateral": tr.tip_lateral,
            "mouth_lateral": tr.mouth_lateral,
            "min_contact_dist": tr.min_contact_dist,
            "tilt_deg": tr.tilt_deg,
            "f_cmd_z": tr.f_cmd_z,
            "committed": tr.committed,
            "spiral_theta": tr.spiral_theta,
            "control_tick": tr.control_tick,
            "searching": tr.searching,
            "spiral_advanced": tr.spiral_advanced,
        }.items()
    }


def persist_sidecar(
    trace_dir: Path,
    scenario_id: str,
    system: str,
    candidate_tag: str,
    *,
    production_tr: TrialResult,
    probe_traces: list[tuple[str, TrialResult]],
    execution: Optional[object],
) -> str:
    """Write the complete per-cell telemetry (production trace, every probe
    side trace, all filter records/events) to an exclusive .npz sidecar.
    Returns the relative path recorded in the row. The candidate tag keeps
    the per-candidate development cells distinct."""
    trace_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{scenario_id}_{system}_{candidate_tag}"
    path = trace_dir / f"{stem}.npz"
    if path.exists():
        raise RuntimeError(f"sidecar exists: {path.name}")
    arrays: dict = {}
    for key, val in _trace_arrays("production", production_tr).items():
        arrays[key] = val.astype(np.float32) if val.dtype.kind == "f" else val
    for i, (side, tr) in enumerate(probe_traces):
        for key, val in _trace_arrays(f"probe{int(i):02d}_{side}", tr).items():
            arrays[key] = val.astype(np.float32) if val.dtype.kind == "f" else val
    np.savez_compressed(path, **arrays)
    meta: dict = {"scenario_id": scenario_id, "system": system,
                  "candidate_tag": candidate_tag}
    if execution is not None:
        meta["filter_records"] = [
            filter_record_json(r) for r in execution.filter_records
        ]
        meta["probe_events"] = [probe_event_json(e) for e in execution.probe_events]
        meta["final_filter_state_m"] = np.asarray(
            execution.final_filter_state_m
        ).tolist()
        meta["final_filter_cov_m2"] = np.asarray(
            execution.final_filter_h_cov_m2
        ).tolist()
        meta["updated_pair_count"] = execution.updated_pair_count
        meta["spiral_usage"] = execution.spiral_usage
        meta["guided"] = execution.guided
    meta_path = trace_dir / f"{stem}_meta.json"
    if meta_path.exists():
        raise RuntimeError(f"sidecar meta exists: {meta_path.name}")
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return f"{trace_dir.name}/{path.name}"


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
# Offline evaluation diagnostics (G10: computed OUTSIDE the arms)
# ---------------------------------------------------------------------------


def offline_estimator_diagnostics(execution) -> dict:
    nees, h_err, cw = r05.nees_and_errors(
        np.asarray(execution.final_filter_state_m, dtype=float)[:2],
        np.asarray(execution.final_filter_h_cov_m2, dtype=float)[:2, :2],
        np.zeros(2),
        len(execution.filter_records) > 0,
    )
    return {"nees": nees, "h_error_norm_m": h_err, "confidently_wrong": cw}


# ---------------------------------------------------------------------------
# Worker: one (scenario, arm, candidate) dev cell
# ---------------------------------------------------------------------------


def run_cell(args: tuple) -> list[dict]:
    phase, scenario_dict, arm, spec_id, v_t = args
    scenario = scenario_from_dict(scenario_dict)
    policy = r05.policy_view(scenario)
    control_task = r05.build_control_task()
    t_start = time.time()
    exogenous_hash = _sha256_text(
        json.dumps(r05.scenario_to_dict(scenario), sort_keys=True)
    )
    model, data, xml_hash = r05.build_physical_model(scenario)
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
    trace_dir = TRACES_HOLDOUT  # dev rows carry no sidecars (G12)
    if arm == "baseline":
        tr = run_baseline_arm(policy, control_task, v_t, model=model, data=data)
        grade = grade_production(tr, scenario.physical_clearance_m)
        candidate_tag = f"vt{v_t}".replace(".", "p")
        sidecar = persist_sidecar(
            trace_dir, scenario.scenario_id, "baseline", candidate_tag,
            production_tr=tr, probe_traces=[], execution=None,
        )
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
            "sidecar": sidecar,
            "wall_time_s": time.time() - t_start,
            "validity": "valid",
        }]

    candidate = next(c for c in HAPLOUS_CANDIDATES if c.id == spec_id)
    execution, production, probe_traces = run_haplous_arm(
        policy, control_task, candidate, v_t, model=model, data=data
    )
    grade = grade_production(production, scenario.physical_clearance_m)
    estimator_abort = any(e.kind == "ABORT" for e in execution.probe_events)
    diag = offline_estimator_diagnostics(execution)
    p_hh = np.asarray(execution.final_filter_h_cov_m2, dtype=float)[:2, :2]
    sidecar = persist_sidecar(
        trace_dir, scenario.scenario_id, "haplous", candidate.id,
        production_tr=production, probe_traces=probe_traces, execution=execution,
    )
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
        "true_state_error_m": diag["h_error_norm_m"],
        "estimated_state_error_m": diag["h_error_norm_m"],
        "estimated_uncertainty_m": float(
            math.sqrt(max(float(np.trace(p_hh)), 0.0)) / 2.0
        ),
        "nees": diag["nees"],
        "confidently_wrong": diag["confidently_wrong"],
        "probe_count": execution.probe_side_motions,
        "probe_unseatable_count": sum(
            1 for e in execution.probe_events if e.kind == "unseatable"
        ),
        "probe_time_s": execution.probe_time_s,
        "spiral_usage": execution.spiral_usage,
        "guided": execution.guided,
        "updated_pair_count": execution.updated_pair_count,
        "probe_events": [probe_event_json(e) for e in execution.probe_events],
        "sidecar": sidecar,
        "wall_time_s": time.time() - t_start,
        "validity": "valid",
    }]


def run_cells(jobs: list[tuple], out_path: Path) -> tuple[int, float]:
    t0 = time.time()
    total_rows = 0
    with out_path.open("x", encoding="utf-8") as fh, ProcessPoolExecutor(
        max_workers=MAX_WORKERS
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


def _median(values: list[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return float(np.median(present)) if present else None


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
            f"{MIN_THROUGHPUT_STEPS_PER_S:.0f}; R05-v3 is INVALID"
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
        max_workers=MAX_WORKERS
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
        max_workers=MAX_WORKERS
    ) as pool:
        for rows in pool.map(run_cell, haplous_jobs, chunksize=1):
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                haplous_rows.append(row)
    throughput_gate(haplous_rows)
    selected_candidate, haplous_scores = select_haplous(haplous_rows, baseline_v_t)
    print(f"selected haplous candidate {selected_candidate}")
    selections = {
        "locked_design": str(LOCK_YAML.relative_to(ROOT)),
        "locked_design_sha256": _sha256_file(LOCK_YAML),
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


def phase_freeze() -> None:
    """Exclusive execution freeze record: the exact provenance hash set that
    phase_holdout validates before starting any held-out physics (G11)."""
    r05.assert_evidence_fingerprints()
    if not SELECTIONS.exists():
        raise RuntimeError("frozen selections missing; run the dev phase first")
    if TRIALS_HOLDOUT.exists():
        raise RuntimeError("holdout already ran; freeze record is pre-holdout only")
    selections_text = SELECTIONS.read_text(encoding="utf-8")
    selections = json.loads(selections_text)
    hashes = json.loads(MANIFEST_HASHES.read_text(encoding="utf-8"))
    dev_hash = _sha256_text(MANIFEST_DEV.read_text(encoding="utf-8"))
    holdout_hash = _sha256_text(MANIFEST_HOLDOUT.read_text(encoding="utf-8"))
    if dev_hash != hashes["dev_manifest_sha256"]:
        raise RuntimeError("dev manifest changed since generation")
    if holdout_hash != hashes["holdout_manifest_sha256"]:
        raise RuntimeError("holdout manifest changed since generation")
    if selections["dev_manifest_sha256"] != dev_hash:
        raise RuntimeError("selections reference a different dev manifest")
    record = {
        "locked_design": str(LOCK_YAML.relative_to(ROOT)),
        "locked_design_sha256": _sha256_file(LOCK_YAML),
        "repo_head": _repo_head(),
        "dev_manifest_sha256": dev_hash,
        "holdout_manifest_sha256": holdout_hash,
        "n5_raw_sha256": r05.N5_RAW_SHA256,
        "n5_summary_sha256": r05.N5_SUMMARY_SHA256,
        "selections_sha256": _sha256_text(selections_text),
        "dev_trials_sha256": _sha256_file(TRIALS_DEV),
        "baseline_selected_v_t_m_per_s": selections[
            "baseline_selected_v_t_m_per_s"
        ],
        "haplous_selected_candidate": selections["haplous_selected_candidate"],
        "source_sha256": {str(p): _sha256_file(p) for p in _HASHED_SOURCES},
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _exclusive_write(FREEZE, json.dumps(record, indent=1))
    print(json.dumps(record, indent=1))


def _sha256_dir(path: Path) -> str:
    import hashlib as _h

    digest = _h.sha256()
    for member in sorted(path.iterdir()):
        digest.update(member.name.encode("utf-8"))
        digest.update(member.read_bytes())
    return digest.hexdigest()


def _validate_freeze() -> dict:
    record = json.loads(FREEZE.read_text(encoding="utf-8"))
    problems = []
    if record["repo_head"] != _repo_head():
        problems.append("HEAD moved since freeze")
    if _sha256_file(LOCK_YAML) != record["locked_design_sha256"]:
        problems.append("locked design changed since freeze")
    hashes = json.loads(MANIFEST_HASHES.read_text(encoding="utf-8"))
    if _sha256_text(MANIFEST_DEV.read_text(encoding="utf-8")) != hashes[
        "dev_manifest_sha256"
    ]:
        problems.append("dev manifest changed since generation")
    if _sha256_text(MANIFEST_HOLDOUT.read_text(encoding="utf-8")) != hashes[
        "holdout_manifest_sha256"
    ]:
        problems.append("holdout manifest changed since generation")
    if _sha256_text(SELECTIONS.read_text(encoding="utf-8")) != record[
        "selections_sha256"
    ]:
        problems.append("selections changed since freeze")
    if _sha256_file(TRIALS_DEV) != record["dev_trials_sha256"]:
        problems.append("dev trials changed since freeze")
    for rel, digest in record["source_sha256"].items():
        if _sha256_file(ROOT / rel) != digest:
            problems.append(f"source changed since freeze: {rel}")
    if r05.N5_RAW_SHA256 != record["n5_raw_sha256"]:
        problems.append("N5 raw evidence changed since freeze")
    if r05.N5_SUMMARY_SHA256 != record["n5_summary_sha256"]:
        problems.append("N5 summary evidence changed since freeze")
    if problems:
        raise RuntimeError(
            "freeze validation failed: " + "; ".join(problems)
        )
    return record


def phase_holdout() -> None:
    r05.assert_evidence_fingerprints()
    freeze = _validate_freeze()
    print("freeze record validated:", freeze["repo_head"])
    selections = json.loads(SELECTIONS.read_text(encoding="utf-8"))
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
        max_workers=MAX_WORKERS
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
    policy = r05.policy_view(scenario)
    control_task = r05.build_control_task()
    t_start = time.time()
    exogenous_hash = _sha256_text(
        json.dumps(r05.scenario_to_dict(scenario), sort_keys=True)
    )
    candidate = next(c for c in HAPLOUS_CANDIDATES if c.id == candidate_id)
    model, data, xml_hash = r05.build_physical_model(scenario)

    if scenario.arm_order_bit == 0:
        baseline_tr = run_baseline_arm(
            policy, control_task, baseline_v_t, model=model, data=data
        )
        baseline_grade = grade_production(baseline_tr, scenario.physical_clearance_m)
        execution, production, probe_traces = run_haplous_arm(
            policy, control_task, candidate, baseline_v_t, model=model, data=data
        )
    else:
        execution, production, probe_traces = run_haplous_arm(
            policy, control_task, candidate, baseline_v_t, model=model, data=data
        )
        baseline_tr = run_baseline_arm(
            policy, control_task, baseline_v_t, model=model, data=data
        )
        baseline_grade = grade_production(baseline_tr, scenario.physical_clearance_m)
    production_grade = grade_production(production, scenario.physical_clearance_m)
    estimator_abort = any(e.kind == "ABORT" for e in execution.probe_events)
    diag = offline_estimator_diagnostics(execution)
    p_hh = np.asarray(execution.final_filter_h_cov_m2, dtype=float)[:2, :2]
    t_end = time.time() - t_start

    baseline_sidecar = persist_sidecar(
        TRACES_HOLDOUT, scenario.scenario_id, "baseline",
        f"vt{baseline_v_t}".replace(".", "p"),
        production_tr=baseline_tr, probe_traces=[], execution=None,
    )
    haplous_sidecar = persist_sidecar(
        TRACES_HOLDOUT, scenario.scenario_id, "haplous", candidate.id,
        production_tr=production, probe_traces=probe_traces, execution=execution,
    )

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
        "sidecar": baseline_sidecar,
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
        "true_state_error_m": diag["h_error_norm_m"],
        "estimated_state_error_m": diag["h_error_norm_m"],
        "estimated_uncertainty_m": float(
            math.sqrt(max(float(np.trace(p_hh)), 0.0)) / 2.0
        ),
        "nees": diag["nees"],
        "confidently_wrong": diag["confidently_wrong"],
        "probe_count": execution.probe_side_motions,
        "probe_unseatable_count": sum(
            1 for e in execution.probe_events if e.kind == "unseatable"
        ),
        "probe_time_s": execution.probe_time_s,
        "spiral_usage": execution.spiral_usage,
        "guided": execution.guided,
        "updated_pair_count": execution.updated_pair_count,
        "probe_events": [probe_event_json(e) for e in execution.probe_events],
        "sidecar": haplous_sidecar,
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
            f"{len(invalid)} invalid rows; R05-v3 is INVALID per the locked rules"
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
        "locked_design": str(LOCK_YAML.relative_to(ROOT)),
        "locked_design_sha256": _sha256_file(LOCK_YAML),
        "freeze_repo_head": json.loads(FREEZE.read_text(encoding="utf-8"))[
            "repo_head"
        ],
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
            "traces_holdout_sha256": _sha256_dir(TRACES_HOLDOUT),
        },
    }
    _exclusive_write(SUMMARY, json.dumps(summary, indent=1))
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
        "phase", choices=["generate", "dev", "freeze", "holdout", "analyze"]
    )
    args = parser.parse_args()
    if args.phase == "generate":
        phase_generate()
    elif args.phase == "dev":
        phase_dev()
    elif args.phase == "freeze":
        phase_freeze()
    elif args.phase == "holdout":
        phase_holdout()
    else:
        phase_analyze()


if __name__ == "__main__":
    main()
