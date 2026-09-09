"""R05 locked comparison driver (see .opencode/haplous/state/locked-tests/R05-v1.yaml).

Phases:
  generate  write the locked dev/holdout manifests and record their hashes
  dev       run the development envelope for both arms' candidate sets and
            freeze the selected arm definitions
  holdout   run the 80-scenario held-out evaluation with the frozen arms
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

import mujoco
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
from pegsim.scene import build_mjcf

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


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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
    MANIFEST_DEV.write_text(dev_json, encoding="utf-8")
    MANIFEST_HOLDOUT.write_text(holdout_json, encoding="utf-8")
    hashes = {
        "dev_manifest_sha256": _sha256_text(dev_json),
        "holdout_manifest_sha256": _sha256_text(holdout_json),
        "n5_raw_sha256": r05.N5_RAW_SHA256,
        "n5_summary_sha256": r05.N5_SUMMARY_SHA256,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    MANIFEST_HASHES.write_text(json.dumps(hashes, indent=1), encoding="utf-8")
    print(json.dumps(hashes, indent=1))


# ---------------------------------------------------------------------------
# Worker: one (scenario, arm, candidate) cell
# ---------------------------------------------------------------------------


def _build_model(scenario: Scenario):
    physical, _ = r05.build_tasks(scenario.clearance_offset_m)
    model = mujoco.MjModel.from_xml_string(
        build_mjcf(physical, r05._SOLVER, r05.PLATE_TILT_DEG)
    )
    return model, mujoco.MjData(model)


def run_cell(args: tuple) -> list[dict]:
    phase, scenario_dict, arm, spec_id, v_t = args
    scenario = scenario_from_dict(scenario_dict)
    t_start = time.time()
    common = {
        "phase": phase,
        "scenario_id": scenario.scenario_id,
        "stratum_id": scenario.stratum_id,
        "stratum_name": scenario.stratum_name,
        "seed": scenario.seed,
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
    model, data = _build_model(scenario)
    if arm == "baseline":
        tr = run_baseline_arm(scenario, v_t, model=model, data=data)
        grade = grade_production(tr, scenario.physical_clearance_m)
        row = {
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
        }
        return [row]

    candidate = next(c for c in HAPLOUS_CANDIDATES if c.id == spec_id)
    execution, production, diagnostics = run_haplous_arm(
        scenario, candidate, v_t, model=model, data=data
    )
    grade = grade_production(production, scenario.physical_clearance_m)
    episodes = execution.probe_side_motions + 1
    p_hh = np.array(diagnostics["final_filter_h_cov_m2"], dtype=float)
    estimator_abort = any(e.kind == "ABORT" for e in execution.probe_events)
    row = {
        **common,
        "system": "haplous",
        "baseline_or_haplous": "haplous",
        "candidate_id": candidate.id,
        "baseline_v_t_m_per_s": v_t,
        "episodes_run": episodes,
        "success": grade.success,
        "completion_time_s": grade.seat_time_s,
        "pipeline_time_s": (
            execution.probe_time_s
            + (grade.seat_time_s if grade.success else r05.MAX_SECONDS)
        ),
        "peak_force_n": grade.peak_abs_fz_n,
        "force_integral_n_s": grade.force_integral_n_s,
        "max_tilt_deg": grade.max_tilt_deg,
        "recovery_count": 0,
        "abort": estimator_abort,
        "failure_class": grade.failure_class,
        "failure_reason": grade.failure_class if not grade.success else None,
        "true_state_error_m": execution.h_error_norm_m,
        "estimated_state_error_m": execution.h_error_norm_m,
        "estimated_uncertainty_m": float(math.sqrt(max(np.trace(p_hh), 0.0)) / 2.0),
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
    }
    return [row]


def run_cells(jobs: list[tuple], out_path: Path) -> None:
    t0 = time.time()
    total_rows = 0
    with out_path.open("w", encoding="utf-8") as fh, ProcessPoolExecutor(
        max_workers=MAX_WORKERS
    ) as pool:
        for rows in pool.map(run_cell, jobs, chunksize=1):
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                total_rows += 1
    print(f"{out_path.name}: {total_rows} rows, wall {time.time() - t0:.0f}s")


# ---------------------------------------------------------------------------
# Selection (locked lexicographic rules)
# ---------------------------------------------------------------------------


def _p95(values: list[float]) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), 0.95, method="linear"))


def _median(values: list[float]) -> float:
    return float(np.median(np.asarray(values, dtype=float)))


def _mean_none_safe(values: list[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return float(np.mean(present)) if present else None


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
            "p95_pipeline_peak_fz_n": _p95([r["peak_force_n"] for r in cells]),
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
    if TRIALS_DEV.exists():
        raise RuntimeError(
            f"{TRIALS_DEV.name} exists; the development budget is one-shot. "
            "Move it aside explicitly if a debug-phase rescore is intended."
        )
    scenarios = load_manifest(MANIFEST_DEV)
    baseline_jobs = [
        ("dev", s, "baseline", "baseline", v_t)
        for v_t in r05.BASELINE_V_T_CANDIDATES_M_PER_S
        for s in scenarios
    ]
    assert len(baseline_jobs) == 100
    baseline_rows: list[dict] = []
    with TRIALS_DEV.open("w", encoding="utf-8") as fh, ProcessPoolExecutor(
        max_workers=MAX_WORKERS
    ) as pool:
        for rows in pool.map(run_cell, baseline_jobs, chunksize=1):
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                baseline_rows.append(row)
    baseline_v_t, baseline_scores = select_baseline(baseline_rows)
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
    selected_candidate, haplous_scores = select_haplous(baseline_rows + haplous_rows, baseline_v_t)
    print(f"selected haplous candidate {selected_candidate}")
    selections = {
        "locked_design": ".opencode/haplous/state/locked-tests/R05-v1.yaml",
        "baseline_selected_v_t_m_per_s": baseline_v_t,
        "baseline_selection_metrics": {
            str(k): v for k, v in baseline_scores.items()
        },
        "haplous_selected_candidate": selected_candidate,
        "haplous_selection_metrics": haplous_scores,
        "dev_manifest_sha256": _sha256_text(
            MANIFEST_DEV.read_text(encoding="utf-8")
        ),
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
    if TRIALS_HOLDOUT.exists():
        raise RuntimeError(f"{TRIALS_HOLDOUT.name} exists; holdout is one-shot.")
    scenarios = load_manifest(MANIFEST_HOLDOUT)
    baseline_v_t = selections["baseline_selected_v_t_m_per_s"]
    candidate_id = selections["haplous_selected_candidate"]
    baseline_jobs = [
        ("holdout", s, "baseline", "baseline", baseline_v_t) for s in scenarios
    ]
    haplous_jobs = [
        ("holdout", s, "haplous", candidate_id, baseline_v_t) for s in scenarios
    ]
    assert len(baseline_jobs) == 80 and len(haplous_jobs) == 80
    with TRIALS_HOLDOUT.open("w", encoding="utf-8") as fh, ProcessPoolExecutor(
        max_workers=MAX_WORKERS
    ) as pool:
        for rows in pool.map(run_cell, baseline_jobs + haplous_jobs, chunksize=1):
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
    phase_analyze()


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
            f"{len(invalid)} invalid rows; R05-v1 is INVALID per the locked rules"
        )
    baseline = {r["scenario_id"]: r for r in rows if r["system"] == "baseline"}
    haplous = {r["scenario_id"]: r for r in rows if r["system"] == "haplous"}
    assert set(baseline) == set(haplous) and len(baseline) == 80
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
