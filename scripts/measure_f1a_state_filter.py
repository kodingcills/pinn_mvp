"""Generate deterministic synthetic evidence for the F1a state filter."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from pegsim.filter import CHI2_2DOF_999, FilterOutcome, StateFilter


RESULT_PATH = Path("results/f1a_filter_summary.json")
# F1 working values have no empirical basis; they are intentionally explicit.
Q_H_M2_PER_CYCLE = (20e-6) ** 2
Q_C_M2_PER_CYCLE = (1e-9) ** 2
# F1a specifies independent one-micrometer synthetic observation noise.
R_M2 = np.eye(2) * (1e-6) ** 2
# Read from by_n["64"].penetration_m_from_pooled_minimum in the N4 summary.
SIM_PENETRATION_M = 2.9630093418717097e-6
# Broad initialization exposes, rather than conceals, unobservable directions.
INITIAL_COVARIANCE_M2 = np.eye(3) * (1e-3) ** 2
GOLDEN_ANGLE_RAD = math.pi * (3.0 - math.sqrt(5.0))
N_STANDARD_PROBES = 200
STEP_PROBE_INDEX = 100


def make_filter() -> StateFilter:
    return StateFilter(
        initial_state_m=np.zeros(3),
        initial_covariance_m2=INITIAL_COVARIANCE_M2,
        measurement_covariance_m2=R_M2,
        q_h_m2_per_cycle=Q_H_M2_PER_CYCLE,
        q_c_m2_per_cycle=Q_C_M2_PER_CYCLE,
        penetration_m=SIM_PENETRATION_M,
    )


def synthetic_hole_estimate(
    truth_m: np.ndarray,
    bearing_rad: float,
    noise_m: np.ndarray | None = None,
) -> np.ndarray:
    observation = StateFilter.observation_matrix(bearing_rad)
    direction = observation[:, 2]
    if noise_m is None:
        noise_m = np.zeros(2)
    return observation @ truth_m - SIM_PENETRATION_M * direction + noise_m


def static_truth_measurement() -> dict[str, object]:
    rng = np.random.default_rng(481516)
    state_filter = make_filter()
    truth_m = np.array([42e-6, -31e-6, 11e-6])
    gated_count = 0
    for probe_index in range(N_STANDARD_PROBES):
        bearing = probe_index * GOLDEN_ANGLE_RAD
        noise_m = rng.multivariate_normal(np.zeros(2), R_M2)
        result = state_filter.update(
            synthetic_hole_estimate(truth_m, bearing, noise_m),
            bearing,
        )
        gated_count += result.outcome is FilterOutcome.SALVAGE

    error_m = result.state_m - truth_m
    sigma_m = np.sqrt(np.diag(result.covariance_m2))
    return {
        "truth_m": truth_m.tolist(),
        "estimate_m": result.state_m.tolist(),
        "error_m": error_m.tolist(),
        "posterior_sigma_m": sigma_m.tolist(),
        "within_three_sigma_by_state": (
            np.abs(error_m) <= 3.0 * sigma_m
        ).tolist(),
        "gated_count": int(gated_count),
        "condition_number_last_200": (
            state_filter.observability_condition_number(N_STANDARD_PROBES)
        ),
    }


def fixed_bearing_measurement() -> dict[str, object]:
    state_filter = make_filter()
    bearing_rad = 0.0
    truth_m = np.array([50e-6, 0.0, -50e-6])
    null_direction = np.array([1.0, 0.0, -1.0]) / math.sqrt(2.0)
    prior_null_variance_m2 = float(
        null_direction @ INITIAL_COVARIANCE_M2 @ null_direction
    )
    for _ in range(N_STANDARD_PROBES):
        state_filter.update(
            synthetic_hole_estimate(truth_m, bearing_rad),
            bearing_rad,
        )
    posterior_null_variance_m2 = float(
        null_direction @ state_filter.covariance_m2 @ null_direction
    )
    condition_number = state_filter.observability_condition_number(
        N_STANDARD_PROBES,
        warn=False,
    )
    return {
        "truth_m": truth_m.tolist(),
        "estimate_m": state_filter.state_m.tolist(),
        "estimation_error_norm_m": float(
            np.linalg.norm(state_filter.state_m - truth_m)
        ),
        "condition_number": None if math.isinf(condition_number) else condition_number,
        "condition_number_is_infinite": math.isinf(condition_number),
        "prior_null_variance_m2": prior_null_variance_m2,
        "posterior_null_variance_m2": posterior_null_variance_m2,
        "posterior_to_prior_null_variance_ratio": (
            posterior_null_variance_m2 / prior_null_variance_m2
        ),
    }


def fixture_step_measurement() -> dict[str, object]:
    state_filter = make_filter()
    truth_m = np.zeros(3)
    for probe_index in range(STEP_PROBE_INDEX):
        bearing = probe_index * GOLDEN_ANGLE_RAD
        state_filter.update(synthetic_hole_estimate(truth_m, bearing), bearing)

    state_before_m = state_filter.state_m
    covariance_before_m2 = state_filter.covariance_m2
    truth_m[0] = 200e-6
    bearing = STEP_PROBE_INDEX * GOLDEN_ANGLE_RAD
    result = state_filter.update(
        synthetic_hole_estimate(truth_m, bearing),
        bearing,
    )
    return {
        "step_probe_index": STEP_PROBE_INDEX,
        "step_h_m": truth_m[:2].tolist(),
        "outcome": result.outcome.value,
        "normalized_innovation_squared": result.normalized_innovation_squared,
        "gate_threshold": result.gate_threshold,
        "innovation_m": result.innovation_m.tolist(),
        "state_unchanged": bool(np.array_equal(state_before_m, state_filter.state_m)),
        "covariance_unchanged": bool(
            np.array_equal(covariance_before_m2, state_filter.covariance_m2)
        ),
    }


def fixture_ramp_measurement() -> dict[str, object]:
    state_filter = make_filter()
    truth_m = np.zeros(3)
    gated_count = 0
    for probe_index in range(N_STANDARD_PROBES):
        truth_m[0] = probe_index * 5e-6
        bearing = probe_index * GOLDEN_ANGLE_RAD
        result = state_filter.update(
            synthetic_hole_estimate(truth_m, bearing),
            bearing,
        )
        gated_count += result.outcome is FilterOutcome.SALVAGE

    error_m = result.state_m - truth_m
    sigma_m = np.sqrt(np.diag(result.covariance_m2))
    return {
        "ramp_rate_m_per_cycle": 5e-6,
        "truth_final_m": truth_m.tolist(),
        "estimate_final_m": result.state_m.tolist(),
        "error_final_m": error_m.tolist(),
        "posterior_sigma_m": sigma_m.tolist(),
        "within_three_sigma_by_state": (
            np.abs(error_m) <= 3.0 * sigma_m
        ).tolist(),
        "gated_count": int(gated_count),
    }


def _clearance_step_at_horizon(n_probes: int) -> dict[str, object]:
    state_filter = make_filter()
    truth_m = np.zeros(3)
    maximum_fixture_excursion_m = 0.0
    gated_count = 0
    for probe_index in range(n_probes):
        if probe_index == STEP_PROBE_INDEX:
            truth_m[2] = 10e-6
        bearing = probe_index * GOLDEN_ANGLE_RAD
        result = state_filter.update(
            synthetic_hole_estimate(truth_m, bearing),
            bearing,
        )
        gated_count += result.outcome is FilterOutcome.SALVAGE
        if probe_index >= STEP_PROBE_INDEX:
            maximum_fixture_excursion_m = max(
                maximum_fixture_excursion_m,
                float(np.linalg.norm(result.state_m[:2])),
            )

    sigma_m = np.sqrt(np.diag(result.covariance_m2))
    return {
        "n_probes": n_probes,
        "post_step_probes": n_probes - STEP_PROBE_INDEX,
        "truth_final_m": truth_m.tolist(),
        "estimate_final_m": result.state_m.tolist(),
        "posterior_sigma_m": sigma_m.tolist(),
        "maximum_fixture_excursion_m": maximum_fixture_excursion_m,
        "final_fixture_error_norm_m": float(np.linalg.norm(result.state_m[:2])),
        "final_clearance_error_m": float(result.state_m[2] - truth_m[2]),
        "clearance_within_three_sigma": bool(
            abs(result.state_m[2] - truth_m[2]) <= 3.0 * sigma_m[2]
        ),
        "maximum_fixture_excursion_below_2um": (
            maximum_fixture_excursion_m < 2e-6
        ),
        "gated_count": int(gated_count),
    }


def covariance_conservation_measurement() -> dict[str, object]:
    state_filter = make_filter()
    truth_m = np.array([7e-6, -9e-6, 3e-6])
    maximum_asymmetry_m2 = 0.0
    minimum_eigenvalue_m2 = math.inf
    for probe_index in range(5000):
        bearing = probe_index * GOLDEN_ANGLE_RAD
        state_filter.update(synthetic_hole_estimate(truth_m, bearing), bearing)
        covariance_m2 = state_filter.covariance_m2
        maximum_asymmetry_m2 = max(
            maximum_asymmetry_m2,
            float(np.max(np.abs(covariance_m2 - covariance_m2.T))),
        )
        minimum_eigenvalue_m2 = min(
            minimum_eigenvalue_m2,
            float(np.min(np.linalg.eigvalsh(covariance_m2))),
        )
    return {
        "n_probes": 5000,
        "maximum_absolute_asymmetry_m2": maximum_asymmetry_m2,
        "minimum_eigenvalue_m2": minimum_eigenvalue_m2,
    }


def nees_measurement() -> dict[str, object]:
    rng = np.random.default_rng(8675309)
    nees_values: list[float] = []
    gated_runs = 0
    completed_runs = 0
    n_runs = 100
    probes_per_run = 200

    for _ in range(n_runs):
        state_filter = make_filter()
        truth_m = rng.multivariate_normal(np.zeros(3), INITIAL_COVARIANCE_M2)
        process_steps_m = rng.multivariate_normal(
            np.zeros(3),
            state_filter.process_covariance_m2_per_cycle,
            size=probes_per_run,
        )
        measurement_noises_m = rng.multivariate_normal(
            np.zeros(2),
            R_M2,
            size=probes_per_run,
        )
        for probe_index, (process_step_m, measurement_noise_m) in enumerate(
            zip(process_steps_m, measurement_noises_m, strict=True)
        ):
            truth_m += process_step_m
            bearing = probe_index * GOLDEN_ANGLE_RAD
            result = state_filter.update(
                synthetic_hole_estimate(
                    truth_m,
                    bearing,
                    measurement_noise_m,
                ),
                bearing,
            )
            if result.outcome is FilterOutcome.SALVAGE:
                gated_runs += 1
                break
            error_m = result.state_m - truth_m
            nees_values.append(float(
                error_m @ np.linalg.solve(result.covariance_m2, error_m)
            ))
        else:
            completed_runs += 1

    time_averaged_nees = float(np.mean(nees_values))
    return {
        "seed": 8675309,
        "runs": n_runs,
        "probes_generated_per_run": probes_per_run,
        "accepted_pre_latch_samples": len(nees_values),
        "gated_runs": gated_runs,
        "completed_runs": completed_runs,
        "time_averaged_nees": time_averaged_nees,
        "chi_square_3dof_two_sided_95_interval": [0.215795, 9.348404],
        "inside_interval": 0.215795 < time_averaged_nees < 9.348404,
        "post_salvage_samples_excluded": True,
    }


def build_summary() -> dict[str, object]:
    return {
        "scope": "F1a synthetic measurements only; no physics",
        "config": {
            "state_order": ["h_x_m", "h_y_m", "dc_m"],
            "initial_state_m": [0.0, 0.0, 0.0],
            "initial_covariance_diagonal_m2": np.diag(
                INITIAL_COVARIANCE_M2
            ).tolist(),
            "q_h_m2_per_cycle": Q_H_M2_PER_CYCLE,
            "q_c_m2_per_cycle": Q_C_M2_PER_CYCLE,
            "q_variance_ratio_h_to_c": Q_H_M2_PER_CYCLE / Q_C_M2_PER_CYCLE,
            "measurement_covariance_m2": R_M2.tolist(),
            "penetration_m": SIM_PENETRATION_M,
            "penetration_source": (
                "results/n4_facet_convergence_summary.json "
                "by_n[64].penetration_m_from_pooled_minimum"
            ),
            "gate_threshold_chi_square_2dof_99_9_percent": CHI2_2DOF_999,
            "bearing_schedule": "theta_k = k*pi*(3-sqrt(5))",
        },
        "t1_static_truth": static_truth_measurement(),
        "t2_fixed_bearing": fixed_bearing_measurement(),
        "t3_fixture_step": fixture_step_measurement(),
        "t4_fixture_ramp": fixture_ramp_measurement(),
        "t5_clearance_step_200_probes": _clearance_step_at_horizon(200),
        "t5_clearance_step_1000_probe_diagnostic": _clearance_step_at_horizon(1000),
        "t6_joseph_conservation": covariance_conservation_measurement(),
        "t7_nees": nees_measurement(),
        "statistics": {
            "standard_deviation_ddof": None,
            "reason": (
                "F1a reports configured Gaussian sigma, state errors, extrema, "
                "and a mean NEES; no sample standard deviation is reported"
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=RESULT_PATH)
    args = parser.parse_args()
    summary = build_summary()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
