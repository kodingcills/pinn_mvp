"""Tests for pegsim.probing (F1c physics-integrated estimator session).

Synthetic tests cover gate/abort/reset semantics through feed_pair without
physics. Exactly one test runs real physics (the mandated lifecycle/reset
leak check at the N5 n=64 scene); it is the slowest test in the suite."""
import json
import math
from dataclasses import replace

import numpy as np
import pytest

from pegsim.error_injection import CorruptionModel, TcpCalibrationBias
from pegsim.filter import FilterOutcome
from pegsim.probing import (
    N5_SUMMARY_PATH,
    Q_C_M2_PER_CYCLE,
    Q_H_M2_PER_CYCLE,
    PairMeasurementModel,
    PairProbeSession,
    apply_sensor_corruption,
    load_n5_n64_pair_bias_m,
    load_n5_pair_measurement_model,
    probe_episode,
    reduce_measurement,
)
from pegsim.rollout import VALIDATED_LOOSE_V1


def n64_task():
    return replace(
        VALIDATED_LOOSE_V1.task,
        n_segments=64,
        radial_clearance=VALIDATED_LOOSE_V1.task.radial_clearance,
    )


def make_session(**kwargs):
    defaults = dict(
        task=n64_task(),
        penetration_m=load_n5_n64_pair_bias_m(),
        pair_measurement_model=load_n5_pair_measurement_model(),
    )
    defaults.update(kwargs)
    return PairProbeSession(**defaults)


def test_probe_episode_is_the_search_disabled_n2_primitive():
    episode = probe_episode(0.7, VALIDATED_LOOSE_V1.task)
    assert episode.spiral_spec is None
    assert episode.initial_offset_r == 2.0 * VALIDATED_LOOSE_V1.task.radial_clearance
    assert episode.initial_offset_theta == 0.7
    assert episode.task == VALIDATED_LOOSE_V1.task


def test_reduction_recovers_known_hole():
    bearing = 1.3
    hole = np.array([25e-6, -40e-6])
    clearance = 500e-6
    tip = hole + clearance * np.array([math.cos(bearing), math.sin(bearing)])
    np.testing.assert_allclose(
        reduce_measurement(tip, bearing, clearance), hole, rtol=1e-14
    )


def test_sensor_corruption_applies_bias_then_quantization():
    tip = np.array([500e-6 + 0.7e-6, 0.3e-6])
    bias = TcpCalibrationBias(translation_m=[1e-6, -1e-6], yaw_rad=0.0)
    corruption = CorruptionModel(tcp_bias=bias, encoder_resolution_m=1e-6)
    corrupted = apply_sensor_corruption(tip, corruption)
    biased = tip + np.array([1e-6, -1e-6])
    expected = np.array([round(biased[0] / 1e-6) * 1e-6, round(biased[1] / 1e-6) * 1e-6])
    np.testing.assert_allclose(corrupted, expected, rtol=1e-15)
    np.testing.assert_allclose(
        apply_sensor_corruption(tip, None), tip, rtol=0.0
    )


def test_pair_bias_constant_comes_from_committed_n5_evidence():
    summary = json.loads(N5_SUMMARY_PATH.read_text(encoding="utf-8"))
    expected = -0.5 * float(
        summary["by_level"]["+0um"]["pair_full_difference_along_u_m"]["mean"]
    )
    assert load_n5_n64_pair_bias_m() == pytest.approx(expected, rel=1e-12)
    assert 0.5e-6 < load_n5_n64_pair_bias_m() < 2.0e-6


def test_measured_pair_model_matches_n5_anticorrelation():
    model = load_n5_pair_measurement_model()
    radial_correlation = model.radial_side_covariance_m2 / model.radial_variance_m2
    tangential_correlation = (
        model.tangential_side_covariance_m2 / model.tangential_variance_m2
    )
    assert radial_correlation == pytest.approx(-0.86, abs=0.05)
    assert tangential_correlation == pytest.approx(-0.98, abs=0.02)
    # The pair covariance at any bearing is positive-definite with the
    # anticorrelated contrast direction carrying finite variance.
    covariance = model.covariance_at(0.7)
    assert covariance.shape == (4, 4)
    assert np.min(np.linalg.eigvalsh(covariance)) > 0.0
    np.testing.assert_allclose(covariance, covariance.T, rtol=1e-12)


def test_pair_model_rejects_unphysical_side_correlation():
    variance = 2.0e-12
    with pytest.raises(ValueError):
        PairMeasurementModel(
            radial_variance_m2=variance,
            tangential_variance_m2=variance,
            radial_side_covariance_m2=-variance,  # correlation exactly -1
            tangential_side_covariance_m2=0.0,
        )
    with pytest.raises(ValueError):
        PairMeasurementModel(
            radial_variance_m2=-1.0,
            tangential_variance_m2=variance,
            radial_side_covariance_m2=0.0,
            tangential_side_covariance_m2=0.0,
        )


def test_feed_pair_updates_state_and_recovers_dc():
    session = make_session()
    bearing = 0.0
    u = np.array([1.0, 0.0])
    hole = np.array([20e-6, -10e-6])
    dc = 5e-6
    penetration = load_n5_n64_pair_bias_m()
    # Perfect probes: est_plus = hole + (dc - penetration)*u etc.
    est_plus = hole + (dc - penetration) * u
    est_minus = hole - (dc - penetration) * u
    record = session.feed_pair(est_plus, est_minus, bearing)
    assert record.outcome is FilterOutcome.UPDATED
    state = session.state_m
    np.testing.assert_allclose(state[:2], hole, atol=1e-9)
    assert state[2] == pytest.approx(dc, rel=1e-6, abs=1e-12)


def test_three_consecutive_outlier_pairs_abort():
    session = make_session()
    bearing = 0.0
    u = np.array([1.0, 0.0])
    good_hole = np.zeros(2)
    good_est = good_hole + (0.0 - load_n5_n64_pair_bias_m()) * u
    session.feed_pair(good_est, -good_est, bearing)
    # A 200 um fixture step contradicts the learned state: 3 consecutive
    # gated pairs must escalate to ABORT (F1a-rev2 semantics unchanged).
    hole_outlier = np.array([200e-6, 0.0])
    outlier_plus = hole_outlier + (0.0 - load_n5_n64_pair_bias_m()) * u
    outlier_minus = hole_outlier - (0.0 - load_n5_n64_pair_bias_m()) * u
    outcomes = [
        session.feed_pair(outlier_plus, outlier_minus, bearing).outcome
        for _ in range(3)
    ]
    assert outcomes == [
        FilterOutcome.SALVAGE,
        FilterOutcome.SALVAGE,
        FilterOutcome.ABORT,
    ]
    # ABORT reinitializes fixture tracking from the corrected observation
    # and preserves dc.
    np.testing.assert_allclose(session.state_m[:2], hole_outlier, atol=1e-9)
    assert session.state_m[2] == pytest.approx(0.0, abs=1e-12)


def test_reset_returns_estimator_to_configured_prior():
    session = make_session()
    u = np.array([1.0, 0.0])
    est = np.array([50e-6, 0.0]) + (0.0 - load_n5_n64_pair_bias_m()) * u
    session.feed_pair(est, -est, 0.0)
    assert not np.allclose(session.state_m, session.filter.state_m * 0.0)
    reference = make_session()
    session.reset()
    np.testing.assert_allclose(session.state_m, reference.state_m, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(
        session.covariance_m2, reference.covariance_m2, rtol=0.0, atol=0.0
    )


def test_session_rejects_nonpositive_penetration():
    with pytest.raises(ValueError):
        PairProbeSession(task=n64_task(), penetration_m=0.0)
    with pytest.raises(ValueError):
        PairProbeSession(task=n64_task(), penetration_m=-1e-6)


def test_real_physics_pair_and_reset_leak_check():
    """The §14.1-mandated lifecycle test: run a pair, disturb the estimator,
    reset, run the same pair again — the result must equal a fresh session's
    result within determinism. Slow: four n=64 probe episodes."""
    bearing = 0.7
    fresh = make_session()
    fresh_record = fresh.probe_pair(bearing)

    reused = make_session()
    reused.probe_pair(bearing)
    # Disturb the estimator far from its prior.
    disturbed = reused.feed_pair(
        reused.state_m[:2] + np.array([300e-6, -200e-6]),
        reused.state_m[:2] + np.array([-300e-6, 200e-6]),
        bearing,
    )
    assert disturbed.outcome is not None
    reused.reset()

    reset_record = reused.probe_pair(bearing)

    np.testing.assert_allclose(
        reset_record.filter_record.state_m,
        fresh_record.filter_record.state_m,
        rtol=1e-12,
        atol=1e-15,
    )
    np.testing.assert_allclose(
        reset_record.filter_record.covariance_m2,
        fresh_record.filter_record.covariance_m2,
        rtol=1e-12,
        atol=1e-18,
    )
    np.testing.assert_allclose(
        reset_record.plus.hole_estimate_xy_m,
        fresh_record.plus.hole_estimate_xy_m,
        rtol=1e-12,
    )
    # Evaluation-only truth must not have leaked into the update: the filter
    # states are identical even though only estimates were fed.
    assert reset_record.plus.true_hole_xy_m is not None
    assert reset_record.filter_record.outcome is FilterOutcome.UPDATED


def test_real_physics_pair_estimates_are_n5_consistent():
    """One physics pair at the N5 scene: per-side settled radius must sit at
    the true clearance minus the population-mean penetration, and the pair
    dc estimate must recover dc=0 within the measured N5 scatter."""
    session = make_session()
    record = session.probe_pair(0.7)
    penetration = load_n5_n64_pair_bias_m()
    c_nom = VALIDATED_LOOSE_V1.task.radial_clearance
    plus_tip = record.plus.settled_tip_xy_m
    minus_tip = record.minus.settled_tip_xy_m
    plus_radius = float(np.linalg.norm(plus_tip)) - c_nom
    minus_radius = float(np.linalg.norm(minus_tip)) - c_nom
    assert -4.0e-6 < plus_radius < 1.0e-6
    assert -4.0e-6 < minus_radius < 1.0e-6
    # The dc estimate from this single pair must be within the measured
    # N5 residual scatter (max 3.34 um with the pooled-minimum bias; the
    # population-mean bias removes the 1.87 um constant).
    u = np.array([math.cos(record.bearing_rad), math.sin(record.bearing_rad)])
    half_difference = (
        (record.plus.hole_estimate_xy_m - record.minus.hole_estimate_xy_m) @ u
    ) / 2.0
    dc_estimate = half_difference + penetration
    assert abs(dc_estimate) < 2.0e-6
