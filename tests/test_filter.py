import ast
import math
from pathlib import Path

import numpy as np
import pytest

from pegsim.filter import CHI2_2DOF_999, FilterOutcome, StateFilter


# Working process-noise values specified for F1, pending hardware evidence.
Q_H_M2_PER_CYCLE = (20e-6) ** 2
Q_C_M2_PER_CYCLE = (1e-9) ** 2
# F1a's synthetic sensor has one-micrometer independent axis noise.
R_M2 = np.eye(2) * (1e-6) ** 2
# N4 n=64 penetration from the committed pooled-minimum measurement.
SIM_PENETRATION_M = 2.9630093418717097e-6
# A broad prior makes the fixed-bearing null-space conservation measurable.
INITIAL_COVARIANCE_M2 = np.eye(3) * (1e-3) ** 2
GOLDEN_ANGLE_RAD = math.pi * (3.0 - math.sqrt(5.0))


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
    return (
        observation @ truth_m
        - SIM_PENETRATION_M * direction
        + noise_m
    )


def test_filter_module_has_no_mujoco_import():
    source = Path("pegsim/filter.py").read_text()
    imported_roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".")[0])

    assert "mujoco" not in imported_roots


def test_static_truth_converges_at_golden_angle_bearings():
    rng = np.random.default_rng(481516)
    state_filter = make_filter()
    truth_m = np.array([42e-6, -31e-6, 11e-6])

    for probe_index in range(200):
        bearing = probe_index * GOLDEN_ANGLE_RAD
        noise = rng.multivariate_normal(np.zeros(2), R_M2)
        result = state_filter.update(
            synthetic_hole_estimate(truth_m, bearing, noise),
            bearing,
        )

    sigma_m = np.sqrt(np.diag(result.covariance_m2))
    assert result.outcome is FilterOutcome.UPDATED
    assert np.all(np.abs(result.state_m - truth_m) <= 3.0 * sigma_m)
    assert state_filter.observability_condition_number(200) < 2.0


def test_fixed_bearing_does_not_separate_clearance_from_along_bearing_fixture():
    state_filter = make_filter()
    fixed_bearing_rad = 0.0
    # h_x + dc is zero, leaving two individually nonzero truths unidentifiable.
    truth_m = np.array([50e-6, 0.0, -50e-6])
    null_direction = np.array([1.0, 0.0, -1.0]) / math.sqrt(2.0)
    prior_null_variance_m2 = float(
        null_direction @ INITIAL_COVARIANCE_M2 @ null_direction
    )

    for _ in range(200):
        state_filter.update(
            synthetic_hole_estimate(truth_m, fixed_bearing_rad),
            fixed_bearing_rad,
        )

    posterior_null_variance_m2 = float(
        null_direction @ state_filter.covariance_m2 @ null_direction
    )
    with pytest.warns(RuntimeWarning, match="three-state"):
        condition_number = state_filter.observability_condition_number(200)

    assert math.isinf(condition_number)
    assert np.linalg.norm(state_filter.state_m - truth_m) > 50e-6
    assert posterior_null_variance_m2 == pytest.approx(
        prior_null_variance_m2,
        rel=0.05,
    )


def test_fixture_step_is_gated_before_state_or_covariance_changes():
    state_filter = make_filter()
    truth_m = np.zeros(3)

    for probe_index in range(100):
        bearing = probe_index * GOLDEN_ANGLE_RAD
        state_filter.update(synthetic_hole_estimate(truth_m, bearing), bearing)

    state_before_step = state_filter.state_m
    covariance_before_step = state_filter.covariance_m2
    truth_m[0] = 200e-6  # F1's specified fixture-position discontinuity.
    first_step_bearing = 100 * GOLDEN_ANGLE_RAD
    result = state_filter.update(
        synthetic_hole_estimate(truth_m, first_step_bearing),
        first_step_bearing,
    )

    assert result.outcome is FilterOutcome.SALVAGE
    assert result.normalized_innovation_squared > CHI2_2DOF_999
    assert result.gate_threshold == CHI2_2DOF_999
    np.testing.assert_array_equal(state_filter.state_m, state_before_step)
    np.testing.assert_array_equal(
        state_filter.covariance_m2,
        covariance_before_step,
    )


def test_slow_fixture_ramp_tracks_without_gating():
    state_filter = make_filter()
    truth_m = np.zeros(3)
    outcomes = []

    for probe_index in range(200):
        truth_m[0] = probe_index * 5e-6  # Specified 5 um per-cycle drift ramp.
        bearing = probe_index * GOLDEN_ANGLE_RAD
        result = state_filter.update(
            synthetic_hole_estimate(truth_m, bearing),
            bearing,
        )
        outcomes.append(result.outcome)

    sigma_m = np.sqrt(np.diag(result.covariance_m2))
    assert FilterOutcome.SALVAGE not in outcomes
    assert np.all(np.abs(result.state_m - truth_m) <= 3.0 * sigma_m)


@pytest.mark.xfail(
    strict=True,
    reason="required q_h >> q_c assigns the clearance step to fixture drift",
)
def test_clearance_step_separates_from_constant_fixture_position():
    state_filter = make_filter()
    truth_m = np.zeros(3)
    maximum_fixture_excursion_m = 0.0

    for probe_index in range(200):
        if probe_index == 100:
            truth_m[2] = 10e-6  # F1's specified tool-change clearance step.
        bearing = probe_index * GOLDEN_ANGLE_RAD
        result = state_filter.update(
            synthetic_hole_estimate(truth_m, bearing),
            bearing,
        )
        if probe_index >= 100:
            maximum_fixture_excursion_m = max(
                maximum_fixture_excursion_m,
                float(np.linalg.norm(result.state_m[:2])),
            )

    clearance_sigma_m = math.sqrt(state_filter.covariance_m2[2, 2])
    assert abs(state_filter.state_m[2] - truth_m[2]) <= 3.0 * clearance_sigma_m
    assert maximum_fixture_excursion_m < 2e-6


def test_joseph_covariance_stays_symmetric_and_positive_definite():
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

    assert maximum_asymmetry_m2 <= 1e-15
    assert minimum_eigenvalue_m2 > 0.0


def test_nees_is_consistent_over_monte_carlo_population():
    rng = np.random.default_rng(8675309)
    nees_values = []
    generated_probe_count = 0

    for _ in range(100):
        state_filter = make_filter()
        truth_m = rng.multivariate_normal(np.zeros(3), INITIAL_COVARIANCE_M2)
        process_steps_m = rng.multivariate_normal(
            np.zeros(3),
            state_filter.process_covariance_m2_per_cycle,
            size=200,
        )
        measurement_noises_m = rng.multivariate_normal(
            np.zeros(2),
            R_M2,
            size=200,
        )
        generated_probe_count += len(process_steps_m)
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
            # SALVAGE is a terminal product outcome. Continuing to advance the
            # random-walk truth against deliberately frozen x and P would no
            # longer be a Kalman consistency experiment.
            if result.outcome is FilterOutcome.SALVAGE:
                break
            error_m = result.state_m - truth_m
            nees_values.append(float(
                error_m @ np.linalg.solve(result.covariance_m2, error_m)
            ))

    time_averaged_nees = float(np.mean(nees_values))
    # Two-sided 95% chi-square interval for a three-dimensional state.
    assert generated_probe_count == 100 * 200
    assert 0.215795 < time_averaged_nees < 9.348404
