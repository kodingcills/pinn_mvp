import ast
import math
from pathlib import Path

import numpy as np
import pytest

from pegsim.filter import CHI2_2DOF_999, FilterOutcome, StateFilter


# Fixture Q encodes the placeholder 200 um per 1,000-cycle design rate.
Q_H_M2_PER_CYCLE = (0.2e-6) ** 2
# Clearance Q encodes the placeholder 10 um per 10,000-cycle wear rate.
Q_C_M2_PER_CYCLE = (1e-9) ** 2
# F1a's synthetic sensor has one-micrometer independent axis noise.
R_M2 = np.eye(2) * (1e-6) ** 2
# N4 n=64 penetration from the committed pooled-minimum measurement.
SIM_PENETRATION_M = 2.9630093418717097e-6
# A broad prior makes the fixed-bearing null-space conservation measurable.
INITIAL_COVARIANCE_M2 = np.eye(3) * (1e-3) ** 2
GOLDEN_ANGLE_RAD = math.pi * (3.0 - math.sqrt(5.0))
H_DESIGN_RATE_M_PER_CYCLE = math.sqrt(Q_H_M2_PER_CYCLE)
C_DESIGN_RATE_M_PER_CYCLE = math.sqrt(Q_C_M2_PER_CYCLE)
# Ten times either design rate is the specified silent-adaptation boundary.
FAULT_RATE_MULTIPLIER = 10.0
# F1a-rev2 evaluates 2 um of realistic locating-pin wear.
WEAR_PAIR_COUNT = 2000
# A commanded tool change restores the deliberately broad synthetic dc prior.
TOOL_CHANGE_PRIOR_VARIANCE_M2 = INITIAL_COVARIANCE_M2[2, 2]
# F1a-rev2 retains the original 10 um tool-change discontinuity.
TOOL_CHANGE_STEP_M = 10e-6


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


def synthetic_pair_estimates(
    plus_truth_m: np.ndarray,
    minus_truth_m: np.ndarray,
    bearing_rad: float,
) -> tuple[np.ndarray, np.ndarray]:
    direction = StateFilter.observation_matrix(bearing_rad)[:, 2]
    plus_m = (
        plus_truth_m[:2]
        + plus_truth_m[2] * direction
        - SIM_PENETRATION_M * direction
    )
    minus_m = (
        minus_truth_m[:2]
        - minus_truth_m[2] * direction
        + SIM_PENETRATION_M * direction
    )
    return plus_m, minus_m


def update_pair_from_truth(
    state_filter: StateFilter,
    truth_m: np.ndarray,
    bearing_rad: float,
):
    plus_m, minus_m = synthetic_pair_estimates(
        truth_m,
        truth_m,
        bearing_rad,
    )
    return state_filter.update_pair(plus_m, minus_m, bearing_rad)


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
    """T2 applies only to the retained per-probe update path."""

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
    """T4 tracks the fixture rate from which q_h was derived."""

    state_filter = make_filter()
    truth_m = np.zeros(3)
    outcomes = []

    for probe_index in range(200):
        truth_m[0] = probe_index * H_DESIGN_RATE_M_PER_CYCLE
        bearing = probe_index * GOLDEN_ANGLE_RAD
        result = state_filter.update(
            synthetic_hole_estimate(truth_m, bearing),
            bearing,
        )
        outcomes.append(result.outcome)

    sigma_m = np.sqrt(np.diag(result.covariance_m2))
    assert FilterOutcome.SALVAGE not in outcomes
    assert np.all(np.abs(result.state_m - truth_m) <= 3.0 * sigma_m)


def test_fixture_ramp_above_design_rate_is_gated():
    """T4b makes fast fixture drift an explicit stop condition."""

    state_filter = make_filter()
    truth_m = np.zeros(3)
    outcomes = []

    for probe_index in range(200):
        truth_m[0] = (
            probe_index
            * FAULT_RATE_MULTIPLIER
            * H_DESIGN_RATE_M_PER_CYCLE
        )
        bearing = probe_index * GOLDEN_ANGLE_RAD
        outcomes.append(state_filter.update(
            synthetic_hole_estimate(truth_m, bearing),
            bearing,
        ).outcome)

    assert FilterOutcome.SALVAGE in outcomes


@pytest.mark.xfail(
    strict=True,
    reason="SUPERSEDED: Q asymmetry separates slow from fast, not step from step",
)
def test_clearance_step_separates_from_constant_fixture_position():
    # SUPERSEDED: the original 9.925 um fixture excursion was correct under
    # q_h/q_c=4e8. Both hypotheses explain a step, so the prior selects the
    # state with more process noise; pair batching supplies the missing rank.
    state_filter = make_filter()
    truth_m = np.zeros(3)
    maximum_fixture_excursion_m = 0.0

    for probe_index in range(200):
        if probe_index == 100:
            truth_m[2] = TOOL_CHANGE_STEP_M
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


def test_wear_ramp_tracks_with_pair_batching():
    """T5a tracks the q_c design-rate ramp without fixture leakage."""

    state_filter = make_filter()
    truth_m = np.zeros(3)
    maximum_fixture_excursion_m = 0.0

    for pair_index in range(WEAR_PAIR_COUNT):
        truth_m[2] += C_DESIGN_RATE_M_PER_CYCLE
        bearing = pair_index * GOLDEN_ANGLE_RAD
        result = update_pair_from_truth(state_filter, truth_m, bearing)
        direction = StateFilter.observation_matrix(bearing)[:, 2]
        dc_contrast = np.concatenate((direction, -direction)) / 2.0
        dc_innovation_sigma_m = math.sqrt(float(
            dc_contrast
            @ result.innovation_covariance_m2
            @ dc_contrast
        ))
        maximum_fixture_excursion_m = max(
            maximum_fixture_excursion_m,
            float(np.linalg.norm(result.state_m[:2])),
        )

        assert result.outcome is FilterOutcome.UPDATED
        assert abs(result.state_m[2] - truth_m[2]) <= (
            3.0 * dc_innovation_sigma_m
        )

    assert truth_m[2] == pytest.approx(2e-6)
    assert maximum_fixture_excursion_m < 2e-6


def test_commanded_clearance_step_separates_with_pair_batching():
    """T5b re-estimates a scheduled dc step within five complete pairs."""

    state_filter = make_filter()
    truth_m = np.zeros(3)
    for pair_index in range(100):
        bearing = pair_index * GOLDEN_ANGLE_RAD
        update_pair_from_truth(state_filter, truth_m, bearing)

    truth_m[2] = TOOL_CHANGE_STEP_M
    state_filter.inflate_clearance_variance(
        TOOL_CHANGE_PRIOR_VARIANCE_M2,
    )
    for post_step_pair in range(5):
        bearing = (100 + post_step_pair) * GOLDEN_ANGLE_RAD
        result = update_pair_from_truth(state_filter, truth_m, bearing)
        if (
            abs(result.state_m[2] - truth_m[2]) <= 1e-6
            and np.linalg.norm(result.state_m[:2]) < 2e-6
        ):
            break

    assert post_step_pair < 5
    assert abs(result.state_m[2] - truth_m[2]) <= 1e-6
    assert np.linalg.norm(result.state_m[:2]) < 2e-6


def test_commanded_clearance_step_stays_inside_pair_gate():
    """T5c compares first-step d2 on both operational update paths."""

    truth_m = np.zeros(3)
    per_probe_filter = make_filter()
    pair_filter = make_filter()
    for probe_index in range(100):
        bearing = probe_index * GOLDEN_ANGLE_RAD
        per_probe_filter.update(
            synthetic_hole_estimate(truth_m, bearing),
            bearing,
        )
        update_pair_from_truth(pair_filter, truth_m, bearing)

    truth_m[2] = TOOL_CHANGE_STEP_M
    per_probe_filter.inflate_clearance_variance(
        TOOL_CHANGE_PRIOR_VARIANCE_M2,
    )
    pair_filter.inflate_clearance_variance(
        TOOL_CHANGE_PRIOR_VARIANCE_M2,
    )
    step_bearing = 100 * GOLDEN_ANGLE_RAD
    per_probe_result = per_probe_filter.update(
        synthetic_hole_estimate(truth_m, step_bearing),
        step_bearing,
    )
    pair_result = update_pair_from_truth(
        pair_filter,
        truth_m,
        step_bearing,
    )

    assert per_probe_result.outcome is FilterOutcome.UPDATED
    assert pair_result.outcome is FilterOutcome.UPDATED
    assert pair_result.normalized_innovation_squared < CHI2_2DOF_999


def test_wear_ramp_above_design_rate_is_gated():
    """T5d makes excessive tooling wear an explicit stop condition."""

    state_filter = make_filter()
    truth_m = np.zeros(3)
    outcomes = []

    for pair_index in range(WEAR_PAIR_COUNT):
        truth_m[2] += (
            FAULT_RATE_MULTIPLIER * C_DESIGN_RATE_M_PER_CYCLE
        )
        bearing = pair_index * GOLDEN_ANGLE_RAD
        outcomes.append(update_pair_from_truth(
            state_filter,
            truth_m,
            bearing,
        ).outcome)

    assert FilterOutcome.SALVAGE in outcomes


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


@pytest.mark.parametrize("bearing_deg", [0.0, 30.0, 45.0, 90.0])
def test_pair_observation_is_bearing_independent(bearing_deg: float):
    """T8 verifies one pair has full rank at representative bearings."""

    h_pair = StateFilter.pair_observation_matrix(math.radians(bearing_deg))

    assert np.linalg.matrix_rank(h_pair) == 3
    assert np.linalg.cond(h_pair) == pytest.approx(1.0)


@pytest.mark.parametrize("drift_m", [0.2e-6, 2e-6, 20e-6])
def test_intra_pair_fixture_drift_appears_as_half_clearance_error(
    drift_m: float,
):
    """T9 measures the dc contamination caused by motion within a pair."""

    state_filter = make_filter()
    bearing = math.radians(30.0)
    direction = StateFilter.observation_matrix(bearing)[:, 2]
    plus_truth_m = np.zeros(3)
    minus_truth_m = np.zeros(3)
    minus_truth_m[:2] = drift_m * direction
    plus_m, minus_m = synthetic_pair_estimates(
        plus_truth_m,
        minus_truth_m,
        bearing,
    )

    result = state_filter.update_pair(plus_m, minus_m, bearing)

    assert result.outcome is FilterOutcome.UPDATED
    assert result.state_m[2] == pytest.approx(
        -drift_m / 2.0,
        rel=1e-5,
        abs=1e-12,
    )


def test_repeated_gates_abort_and_reinitialize_fixture_tracking():
    """T10 escalates sustained model mismatch and recovers the h estimate."""

    state_filter = make_filter()
    truth_m = np.zeros(3)
    for pair_index in range(100):
        bearing = pair_index * GOLDEN_ANGLE_RAD
        update_pair_from_truth(state_filter, truth_m, bearing)

    fast_rate_m_per_pair = (
        FAULT_RATE_MULTIPLIER * H_DESIGN_RATE_M_PER_CYCLE
    )
    result = None
    covariance_before_abort_m2 = state_filter.covariance_m2
    for post_ramp_pair in range(5):
        truth_m[0] += fast_rate_m_per_pair
        bearing = (100 + post_ramp_pair) * GOLDEN_ANGLE_RAD
        covariance_before_abort_m2 = state_filter.covariance_m2
        result = update_pair_from_truth(state_filter, truth_m, bearing)
        if result.outcome is FilterOutcome.ABORT:
            break

    assert result is not None
    assert result.outcome is FilterOutcome.ABORT
    assert post_ramp_pair < 5
    assert result.consecutive_gate_count == state_filter.consecutive_gate_limit
    assert len(result.gated_innovation_history_m) == (
        state_filter.consecutive_gate_limit
    )
    assert np.linalg.norm(result.state_m[:2] - truth_m[:2]) < 1e-9
    assert np.all(
        np.diag(result.covariance_m2)[:2]
        > np.diag(covariance_before_abort_m2)[:2]
    )

    truth_m[0] += fast_rate_m_per_pair
    next_bearing = (101 + post_ramp_pair) * GOLDEN_ANGLE_RAD
    recovered = update_pair_from_truth(
        state_filter,
        truth_m,
        next_bearing,
    )
    pair_axis_sigma_m = math.sqrt(R_M2[0, 0] / 2.0)
    assert recovered.outcome is FilterOutcome.UPDATED
    assert np.all(
        np.abs(recovered.state_m[:2] - truth_m[:2])
        <= 3.0 * pair_axis_sigma_m
    )
