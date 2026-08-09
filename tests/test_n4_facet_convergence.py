import math
from dataclasses import replace

import numpy as np
import pytest
from matplotlib import pyplot as plt

from pegsim.rollout import VALIDATED_LOOSE_V1
import scripts.measure_n4_facet_convergence as n4
from scripts.measure_n4_facet_convergence import (
    GOLDEN_ANGLE_RAD,
    N_COARSE,
    N_FINE,
    N_PER_SEGMENT_COUNT,
    N_SEGMENTS_SWEEP,
    N_TOTAL,
    _base_record,
    _excluded_record,
    analyze,
    assert_preflight,
    build_trial_specs,
    distribution,
    estimate_hole_xy,
    facet_phase_rad,
    fit_asymptote,
    fit_harmonic,
    load_n2_reference,
    make_episode,
    penetration_from_min_radius_m,
    predicted_vertex_radius_m,
    scene_key,
    summarize_population,
)


def test_stratified_sampling_design_is_exact():
    specs = build_trial_specs()

    assert len(specs) == N_TOTAL == 288
    assert [spec.trial_index for spec in specs] == list(range(N_TOTAL))
    for n_segments in N_SEGMENTS_SWEEP:
        selected = [spec for spec in specs if spec.n_segments == n_segments]
        coarse = [spec for spec in selected if spec.population == "coarse"]
        fine = [spec for spec in selected if spec.population == "fine"]

        assert len(selected) == N_PER_SEGMENT_COUNT == 96
        assert len(coarse) == N_COARSE == 64
        assert len(fine) == N_FINE == 32
        np.testing.assert_allclose(
            [spec.commanded_bearing_rad for spec in coarse],
            np.arange(N_COARSE) * GOLDEN_ANGLE_RAD,
            rtol=0.0,
            atol=8.0 * np.finfo(float).eps,
        )

        facet_period = 2.0 * math.pi / n_segments
        expected_fine = (
            np.arange(N_FINE, dtype=float) + 0.5
        ) * facet_period / N_FINE
        np.testing.assert_allclose(
            [spec.commanded_bearing_rad for spec in fine],
            expected_fine,
            rtol=0.0,
            atol=8.0 * np.finfo(float).eps,
        )
        assert all(0.0 < value < facet_period for value in expected_fine)
        np.testing.assert_allclose(
            np.diff(expected_fine),
            facet_period / N_FINE,
            rtol=0.0,
            atol=8.0 * np.finfo(float).eps,
        )


def test_only_n_segments_changes_and_each_n_has_one_scene_key():
    specs = build_trial_specs()
    assert_preflight(specs)
    canonical = VALIDATED_LOOSE_V1
    assert canonical.task.n_segments == 16

    keys = set()
    for n_segments in N_SEGMENTS_SWEEP:
        first = next(spec for spec in specs if spec.n_segments == n_segments)
        last = [spec for spec in specs if spec.n_segments == n_segments][-1]
        episodes = [
            make_episode(n_segments, first.commanded_bearing_rad),
            make_episode(n_segments, last.commanded_bearing_rad),
        ]
        assert len({scene_key(episode) for episode in episodes}) == 1
        keys.add(scene_key(episodes[0]))
        for episode in episodes:
            assert replace(episode.task, n_segments=16) == canonical.task
            assert episode.solver == canonical.solver
            assert episode.gains == canonical.gains
            assert episode.insertion == canonical.insertion
            assert episode.spiral_spec is None
            assert episode.initial_offset_r == 2.0 * canonical.task.radial_clearance
    assert len(keys) == len(N_SEGMENTS_SWEEP)
    assert canonical.task.n_segments == 16


def test_estimator_and_facet_phase_use_commanded_geometry():
    clearance = VALIDATED_LOOSE_V1.task.radial_clearance
    bearing = GOLDEN_ANGLE_RAD
    true_hole = np.array([0.3e-3, -0.2e-3])
    direction = np.array([math.cos(bearing), math.sin(bearing)])
    tip = true_hole + clearance * direction

    np.testing.assert_allclose(
        estimate_hole_xy(tip, bearing, clearance),
        true_hole,
        rtol=0.0,
        atol=8.0 * np.finfo(float).eps,
    )
    period = 2.0 * math.pi / 64
    assert facet_phase_rad(period, 64) == pytest.approx(0.0, abs=1e-15)
    assert facet_phase_rad(0.75 * period, 64, centered=True) == pytest.approx(
        -0.25 * period,
        abs=1e-15,
    )


def test_harmonic_fit_uses_requested_order_and_phase_convention():
    order = 64
    commanded = np.arange(N_COARSE, dtype=float) * GOLDEN_ANGLE_RAD
    offset = 0.002
    amplitude = 0.011
    phase = -0.37
    deflection = offset + amplitude * np.sin(order * commanded + phase)

    fit = fit_harmonic(commanded, deflection, order)

    assert fit["order"] == order
    assert fit["offset_rad"] == pytest.approx(offset, abs=1e-14)
    assert fit["amplitude_rad"] == pytest.approx(amplitude, abs=1e-14)
    assert fit["phase_rad_harmonic_argument"] == pytest.approx(phase, abs=1e-13)
    assert fit["phase_rad_bearing_axis"] == pytest.approx(
        phase / order,
        abs=1e-14,
    )
    expected_peak = ((0.5 * math.pi - phase) / order) % (2.0 * math.pi / order)
    assert fit["positive_peak_facet_phase_rad"] == pytest.approx(
        expected_peak,
        abs=1e-14,
    )
    assert fit["r_squared"] == pytest.approx(1.0, abs=1e-14)


def test_harmonic_phase_and_r_squared_are_none_for_constant_signal():
    commanded = np.arange(N_COARSE, dtype=float) * GOLDEN_ANGLE_RAD
    fit = fit_harmonic(commanded, np.full(N_COARSE, 0.002), 32)

    assert fit["amplitude_rad"] < math.sqrt(np.finfo(float).eps)
    assert fit["phase_rad_harmonic_argument"] is None
    assert fit["positive_peak_facet_phase_rad"] is None
    assert fit["r_squared"] is None


def test_asymptote_fit_recovers_exact_a_over_n_plus_b():
    expected_a_m = 640e-6  # Gives a 40 µm n-dependent term at n=16.
    expected_b_m = 2.5e-6
    points = {
        n_segments: expected_a_m / n_segments + expected_b_m
        for n_segments in (16, 32, 64, 128)
    }

    fit = fit_asymptote(points)

    assert fit["A_m"] == pytest.approx(expected_a_m, rel=1e-13)
    assert fit["B_m"] == pytest.approx(expected_b_m, rel=1e-13)
    assert fit["sse_m2"] == pytest.approx(0.0, abs=1e-35)
    assert fit["r_squared"] == pytest.approx(1.0, abs=1e-14)
    assert fit["B_confidence_interval_m"][0] == pytest.approx(
        expected_b_m,
        abs=1e-16,
    )
    assert fit["B_confidence_interval_m"][1] == pytest.approx(
        expected_b_m,
        abs=1e-16,
    )


def test_asymptote_fit_confidence_interval_matches_independent_reference():
    points = {
        n_segments: error_um * 1e-6
        for n_segments, error_um in zip(
            (16, 32, 64, 128),
            (43.0, 22.0, 12.0, 7.0),
        )
    }

    fit = fit_asymptote(points)

    assert 1e6 * fit["A_m"] == pytest.approx(658.9217391304348, rel=1e-12)
    assert 1e6 * fit["B_m"] == pytest.approx(1.6956521739130435, rel=1e-12)
    assert 1e12 * fit["sse_m2"] == pytest.approx(0.12173913043478261, rel=1e-11)
    assert fit["r_squared"] == pytest.approx(0.9998402373609313, rel=1e-12)
    assert 1e6 * fit["B_standard_error_m"] == pytest.approx(
        0.2121097550873574,
        rel=1e-10,
    )
    np.testing.assert_allclose(
        1e6 * np.array(fit["B_confidence_interval_m"]),
        [0.7830175570965904, 2.6082867907294967],
        rtol=1e-10,
        atol=0.0,
    )


def test_population_statistics_use_ddof_zero_and_exclude_failures():
    success = {
        "success": True,
        "exclusion_reason": None,
        "estimator_error_norm_m": 3e-6,
        "bearing_deflection_rad": -0.02,
        "settled_tip_radius_m": 499e-6,
        "time_to_seat_s": 1.5,
        "rollout_wall_time_s": 2.0,
    }
    second_success = {
        **success,
        "estimator_error_norm_m": 5e-6,
        "bearing_deflection_rad": 0.04,
        "settled_tip_radius_m": 503e-6,
        "rollout_wall_time_s": 4.0,
    }
    failure = {
        "success": False,
        "exclusion_reason": "not seated before max_seconds",
        "rollout_wall_time_s": 6.0,
    }

    summary = summarize_population([success, failure, second_success])

    assert summary["n_trials"] == 3
    assert summary["n_success"] == 2
    assert summary["n_excluded"] == 1
    assert summary["exclusion_reasons"] == {"not seated before max_seconds": 1}
    estimator = summary["estimator_error_norm_m"]
    assert estimator["mean"] == pytest.approx(4e-6)
    assert estimator["std_population"] == pytest.approx(1e-6)
    assert summary["signed_bearing_deflection_rad"]["mean"] == pytest.approx(0.01)
    assert summary["absolute_bearing_deflection_rad"]["mean"] == pytest.approx(0.03)
    assert summary["rollout_wall_time_s"]["mean"] == pytest.approx(4.0)
    assert distribution(np.array([1.0, 3.0]))["p95"] == pytest.approx(2.9)


def test_penetration_is_signed_and_vertex_uses_clearance_apothem():
    clearance = 500e-6
    assert penetration_from_min_radius_m(clearance, 492e-6) == pytest.approx(8e-6)
    assert penetration_from_min_radius_m(clearance, 501e-6) == pytest.approx(-1e-6)
    assert predicted_vertex_radius_m(clearance, 32) == pytest.approx(
        clearance / math.cos(math.pi / 32)
    )


def _synthetic_record(spec, *, success=True):
    if not success:
        record = _excluded_record(spec, "synthetic exclusion", 2.0)
    else:
        clearance = VALIDATED_LOOSE_V1.task.radial_clearance
        amplitude = 0.012 if spec.population == "coarse" else 0.008
        deflection = 0.001 + amplitude * math.sin(
            spec.n_segments * spec.commanded_bearing_rad + 0.2
        )
        measured_bearing = spec.commanded_bearing_rad + deflection
        radius = clearance - 7e-6
        tip = radius * np.array([
            math.cos(measured_bearing), math.sin(measured_bearing),
        ])
        estimate = estimate_hole_xy(
            tip, spec.commanded_bearing_rad, clearance
        )
        record = {
            **_base_record(spec),
            "success": True,
            "exclusion_reason": None,
            "settled_tip_xy_m": tip.tolist(),
            "settled_tip_radius_m": float(radius),
            "measured_tip_bearing_rad": float(measured_bearing),
            "bearing_deflection_rad": float(deflection),
            "tilt_at_settle_rad": 0.0,
            "true_hole_xy_m": [0.0, 0.0],
            "hole_est_xy_m": estimate.tolist(),
            "estimator_error_xy_m": estimate.tolist(),
            "estimator_error_norm_m": float(np.linalg.norm(estimate)),
            "time_to_seat_s": 1.5,
            "rollout_wall_time_s": 2.0,
        }
    record["n_batch_wall_time_s"] = 20.0 + spec.n_segments
    record["process_pool_workers"] = 2
    return record


def test_analysis_keys_shuffled_results_and_keeps_populations_separate():
    specs = build_trial_specs()
    records = [
        _synthetic_record(spec, success=spec.trial_index != 0)
        for spec in specs
    ]

    summary = analyze(list(reversed(records)), worker_count=2)

    assert summary["n_trials"] == N_TOTAL
    assert summary["n_success"] == N_TOTAL - 1
    assert summary["n_excluded"] == 1
    n32 = summary["by_n"]["32"]
    assert n32["populations"]["coarse"]["n_success"] == N_COARSE - 1
    assert n32["populations"]["fine"]["n_success"] == N_FINE
    assert n32["populations"]["coarse"]["exclusion_reasons"] == {
        "synthetic exclusion": 1,
    }
    assert n32["coarse_nth_harmonic"]["order"] == 32
    assert summary["by_n"]["64"]["coarse_nth_harmonic"]["order"] == 64
    assert summary["by_n"]["128"]["coarse_nth_harmonic"]["order"] == 128
    assert summary["asymptote_fits"]["headline_max_error"]["available"]
    assert summary["asymptote_fits"]["mean_error"]["available"]
    assert summary["config"]["canonical_n_segments_unchanged"] == 16
    assert VALIDATED_LOOSE_V1.task.n_segments == 16


def test_fine_exclusion_invalidates_fits_but_preserves_diagnostic_figures(
    monkeypatch,
):
    specs = build_trial_specs()
    first_fine_index = N_COARSE
    records = [
        _synthetic_record(spec, success=spec.trial_index != first_fine_index)
        for spec in specs
    ]
    summary = analyze(records, worker_count=2)

    assert not summary["asymptote_fits"]["headline_max_error"]["available"]
    assert not summary["asymptote_fits"]["mean_error"]["available"]
    assert not summary["asymptote_fits"]["pooled_mean_error_sensitivity"]["available"]

    saved_stems = []

    def record_figure(fig, stem):
        saved_stems.append(stem)
        plt.close(fig)

    monkeypatch.setattr(n4, "_save_figure", record_figure)
    n4.save_figures(records, summary)

    assert set(saved_stems) == {
        n4.ESTIMATOR_FIGURE_STEM,
        n4.BEARING_FIGURE_STEM,
        n4.RADIUS_FIGURE_STEM,
        n4.RESIDUAL_FIGURE_STEM,
    }


def test_n2_reference_is_the_exact_committed_fit_anchor():
    reference = load_n2_reference()
    estimator = reference["metrics"]["estimator_error_norm_m"]

    assert reference["n_segments"] == 16
    assert reference["source_commit"] == "7081111"
    assert estimator["mean"] == pytest.approx(23.452960279920596e-6)
    assert estimator["max"] == pytest.approx(41.99369583830442e-6)
    assert reference["penetration_m"] == pytest.approx(7.386894131318099e-6)
    assert reference["harmonic"]["r_squared"] == pytest.approx(
        0.9829301302287917
    )
    assert reference["metrics"]["rollout_wall_time_s"] is None
