"""F1c: physics-integrated filter session measurements.

Runs the N5-validated antipodal probe pairs through real physics and feeds
the reduced observations to the audited StateFilter via
pegsim.probing.PairProbeSession, with the evidence-loaded constants:

    penetration bias  = N5 dc=0 population-mean settled radius deviation
    pair covariance   = measured N5 side covariance model (anticorrelated)

Two sessions are measured:

    clean      six pairs at golden bearings, no corruption
    corrupted  same schedule with a fixed 3 um TCP calibration bias and
               2 um encoder quantization applied sensor-side

Every pair records the filter disposition (outcome, NIS, gate threshold),
the posterior state and covariance, the EVALUATION-ONLY ground-truth errors,
and the 2-DOF NEES of the fixture estimate. The R03 confidently-wrong
condition (low claimed uncertainty, high actual error) is evaluated
explicitly. No aggregate drops failures: unseated pairs abort the session
with the pair flagged invalid.

Deterministic: no stochastic element. Physics is stepped only by
run_episode; the filter is used exactly as audited in F1a-rev2 (the only
new estimator-path input is the measured pair covariance, which replaces
the independent-side placeholder the F1 report deferred).
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np

from pegsim.error_injection import CorruptionModel, TcpCalibrationBias
from pegsim.probing import (
    PairProbeSession,
    load_n5_n64_pair_bias_m,
    load_n5_pair_measurement_model,
)
from pegsim.rollout import VALIDATED_LOOSE_V1

ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "results" / "f1c_session_summary.json"

N_PAIRS = 6
GOLDEN_ANGLE_RAD = math.pi * (3.0 - math.sqrt(5.0))
TCP_BIAS_TRANSLATION_M = 3e-6
TCP_BIAS_YAW_RAD = 0.01
ENCODER_RESOLUTION_M = 2e-6
CONFIDENTLY_WRONG_SIGMA = 3.0
Q_H_M2_PER_CYCLE = 4.0e-14
Q_C_M2_PER_CYCLE = 1.0e-18


def n64_task():
    return replace(
        VALIDATED_LOOSE_V1.task,
        n_segments=64,
        radial_clearance=VALIDATED_LOOSE_V1.task.radial_clearance,
    )


def run_session(label: str, corrupted: bool) -> dict[str, object]:
    corruption = None
    if corrupted:
        corruption = CorruptionModel(
            tcp_bias=TcpCalibrationBias(
                translation_m=[TCP_BIAS_TRANSLATION_M, 0.0],
                yaw_rad=TCP_BIAS_YAW_RAD,
            ),
            encoder_resolution_m=ENCODER_RESOLUTION_M,
        )
    session = PairProbeSession(
        task=n64_task(),
        penetration_m=load_n5_n64_pair_bias_m(),
        pair_measurement_model=load_n5_pair_measurement_model(),
        sensor_corruption=corruption,
    )
    truth = np.zeros(2)
    rows = []
    for pair_index in range(N_PAIRS):
        bearing = pair_index * GOLDEN_ANGLE_RAD
        record = session.probe_pair(bearing)
        filter_record = record.filter_record
        state = filter_record.state_m
        posterior_cov = filter_record.covariance_m2
        error_xy = state[:2] - truth
        nees = float(error_xy @ np.linalg.solve(posterior_cov[:2, :2], error_xy))
        rows.append({
            "pair_index": pair_index,
            "bearing_rad": bearing,
            "outcome": filter_record.outcome.value,
            "normalized_innovation_squared": float(
                filter_record.normalized_innovation_squared
            ),
            "state_hx_m": float(state[0]),
            "state_hy_m": float(state[1]),
            "state_dc_m": float(state[2]),
            "state_error_norm_m": float(np.linalg.norm(error_xy)),
            "nees_2dof": nees,
            "posterior_h_sigma_m": math.sqrt(posterior_cov[0, 0]),
            "confidently_wrong": bool(
                np.linalg.norm(error_xy)
                > CONFIDENTLY_WRONG_SIGMA * math.sqrt(posterior_cov[0, 0])
            ),
            "evaluation_pair_mean_error_norm_m": float(
                np.linalg.norm(
                    (record.plus.evaluation_error_xy_m
                     + record.minus.evaluation_error_xy_m) / 2.0
                )
            ),
        })
    return {
        "label": label,
        "n_pairs": len(rows),
        "outcomes": [row["outcome"] for row in rows],
        "final_state_m": [float(v) for v in session.state_m],
        "pairs": rows,
    }


def main() -> None:
    clean = run_session("clean", corrupted=False)
    corrupted = run_session("corrupted", corrupted=True)
    summary = {
        "config": {
            "name": VALIDATED_LOOSE_V1.name,
            "scene": "canonical task at n_segments=64",
            "penetration_bias_m": load_n5_n64_pair_bias_m(),
            "pair_measurement_model_source": (
                "committed results/n5_antipodal_raw.jsonl dc=0 population"
            ),
            "q_h_m2_per_cycle": Q_H_M2_PER_CYCLE,
            "q_c_m2_per_cycle": Q_C_M2_PER_CYCLE,
            "tcp_bias_translation_m": TCP_BIAS_TRANSLATION_M,
            "tcp_bias_yaw_rad": TCP_BIAS_YAW_RAD,
            "encoder_resolution_m": ENCODER_RESOLUTION_M,
            "confidently_wrong_sigma": CONFIDENTLY_WRONG_SIGMA,
            "seed": None,
            "determinism_note": (
                "no stochastic element; deterministic bearing schedule"
            ),
        },
        "clean": clean,
        "corrupted": corrupted,
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    print(
        f"config={VALIDATED_LOOSE_V1.name} scene=n=64 "
        f"penetration_bias={load_n5_n64_pair_bias_m() * 1e6:.4f}um"
    )
    for label, result in (("clean", clean), ("corrupted", corrupted)):
        print(f"[{label}]")
        for row in result["pairs"]:
            print(
                f"  pair {row['pair_index']}: {row['outcome']:<8} "
                f"NIS={row['normalized_innovation_squared']:8.3f} "
                f"|h err|={row['state_error_norm_m'] * 1e6:7.3f}um "
                f"dc_state={row['state_dc_m'] * 1e6:+8.3f}um "
                f"NEES={row['nees_2dof']:8.3f}"
            )
        print(f"  final state: {result['final_state_m']}")
    print(f"summary={SUMMARY_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
