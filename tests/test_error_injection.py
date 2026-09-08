"""Unit tests for pegsim.error_injection (F1b corruption channels)."""
import ast
import math
from pathlib import Path

import numpy as np
import pytest

from pegsim.error_injection import (
    BoreOutOfRoundness,
    CorruptionModel,
    PartDimensionalVariation,
    TcpCalibrationBias,
    confidently_wrong_mask,
    probe_observation,
    quantize,
    sample_tcp_bias,
    true_wall_radius_m,
)


def test_quantizer_boundary_rounds_half_to_even():
    resolution = 10e-6
    assert quantize(5e-6, resolution) == 0.0  # 0.5 -> even 0
    assert quantize(15e-6, resolution) == 2.0e-5  # 1.5 -> even 2
    assert quantize(25e-6, resolution) == 2.0e-5  # 2.5 -> even 2
    assert quantize(45e-6, resolution) == 4.0e-5  # 4.5 -> even 4
    assert quantize(-5e-6, resolution) == 0.0
    assert quantize(123e-6, resolution) == pytest.approx(1.2e-4)


def test_quantizer_output_is_exact_multiple_within_half_resolution():
    resolution = 3e-6
    rng = np.random.default_rng(7)
    values = rng.uniform(-1e-3, 1e-3, size=500)
    for value in values:
        result = quantize(float(value), resolution)
        assert result / resolution == pytest.approx(round(result / resolution))
        assert abs(result - value) <= resolution / 2.0 + 1e-15


def test_quantizer_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        quantize(float("nan"), 1e-6)
    with pytest.raises(ValueError):
        quantize(1e-3, 0.0)
    with pytest.raises(ValueError):
        quantize(1e-3, -1e-6)


def test_tcp_bias_identity_and_known_transform():
    tip = np.array([0.5e-3, -0.25e-3])
    identity = TcpCalibrationBias(translation_m=[0.0, 0.0], yaw_rad=0.0)
    np.testing.assert_allclose(identity.apply(tip), tip, rtol=0.0, atol=0.0)

    # +90 deg yaw about world z then +1 mm x translation: R90*(x, y) = (-y, x),
    # so (0.5, -0.25) mm -> (0.25, 0.5) mm, then + (1, 0) mm.
    bias = TcpCalibrationBias(translation_m=[1e-3, 0.0], yaw_rad=math.pi / 2.0)
    expected = np.array([1.25e-3, 0.5e-3])
    np.testing.assert_allclose(bias.apply(tip), expected, rtol=1e-15)


def test_tcp_bias_is_fixed_across_repeated_application():
    bias = TcpCalibrationBias(translation_m=[3e-6, -2e-6], yaw_rad=0.01)
    first = bias.apply(np.array([1e-3, 0.0]))
    second = bias.apply(np.array([1e-3, 0.0]))
    third = bias.apply(np.array([2e-3, 1e-3]))
    np.testing.assert_allclose(first, second, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(
        third, bias.apply(np.array([2e-3, 1e-3])), rtol=0.0, atol=0.0
    )


def test_part_variation_moves_true_wall_and_measurement_not_nominal():
    nominal = 500e-6
    offset = 10e-6
    corruption = CorruptionModel(part_variation=PartDimensionalVariation(offset))
    bearing = 0.7
    hole = np.array([20e-6, -15e-6])
    measurement = probe_observation(
        corruption,
        true_hole_xy_m=hole,
        commanded_bearing_rad=bearing,
        nominal_clearance_m=nominal,
        penetration_m=0.0,
    )
    direction = np.array([math.cos(bearing), math.sin(bearing)])
    assert measurement.true_wall_radius_m == pytest.approx(nominal + offset)
    np.testing.assert_allclose(measurement.true_tip_xy_m, hole + (nominal + offset) * direction)
    np.testing.assert_allclose(
        measurement.hole_estimate_xy_m - hole, offset * direction, rtol=1e-12
    )


def test_out_of_roundness_elliptical_values_and_zero_circle_mean():
    ovality = BoreOutOfRoundness(harmonic_order=2, amplitude_m=8e-6, phase_rad=0.3)
    assert ovality.radius_deviation_m(0.3) == pytest.approx(8e-6)
    assert ovality.radius_deviation_m(0.3 + math.pi / 2.0) == pytest.approx(-8e-6)
    assert ovality.radius_deviation_m(0.3 + math.pi / 4.0) == pytest.approx(
        0.0, abs=1e-15
    )
    bearings = np.linspace(0.0, 2.0 * math.pi, 4096, endpoint=False)
    mean_deviation = np.mean([
        ovality.radius_deviation_m(float(theta)) for theta in bearings
    ])
    assert mean_deviation == pytest.approx(0.0, abs=1e-12)


def test_wall_radius_composes_part_ovality_and_penetration():
    corruption = CorruptionModel(
        part_variation=PartDimensionalVariation(-5e-6),
        out_of_roundness=BoreOutOfRoundness(3, 4e-6, 0.0),
    )
    radius = true_wall_radius_m(
        corruption,
        nominal_clearance_m=500e-6,
        penetration_m=2.963e-6,
        bearing_rad=0.0,
    )
    assert radius == pytest.approx(500e-6 - 5e-6 + 4e-6 - 2.963e-6)


def test_pipeline_matches_filter_bias_correction_semantics():
    """The filter corrects z by +penetration*u for a plus-side probe; after
    that correction a clean probe with dc=0 must recover the true hole."""
    nominal = 500e-6
    penetration = 2.963e-6
    corruption = CorruptionModel()
    hole = np.array([-30e-6, 10e-6])
    measurement = probe_observation(
        corruption,
        true_hole_xy_m=hole,
        commanded_bearing_rad=1.1,
        nominal_clearance_m=nominal,
        penetration_m=penetration,
    )
    direction = np.array([math.cos(1.1), math.sin(1.1)])
    corrected = measurement.hole_estimate_xy_m + penetration * direction
    np.testing.assert_allclose(corrected, hole, rtol=1e-12, atol=1e-18)


def test_composition_order_bias_then_quantization_at_tip():
    """The encoder quantizes the TIP position, before the nominal-clearance
    reduction; quantizing the reduced estimate would be a different (wrong)
    physical model whenever u is not axis-aligned."""
    nominal = 500e-6
    resolution = 4e-6
    bias = TcpCalibrationBias(translation_m=[1e-6, -1e-6], yaw_rad=0.0)
    corruption = CorruptionModel(tcp_bias=bias, encoder_resolution_m=resolution)
    hole = np.zeros(2)
    bearing = 0.7
    direction = np.array([math.cos(bearing), math.sin(bearing)])
    true_tip = hole + nominal * direction
    biased = bias.apply(true_tip)
    expected_tip = np.array([
        quantize(float(biased[0]), resolution),
        quantize(float(biased[1]), resolution),
    ])
    measurement = probe_observation(
        corruption,
        true_hole_xy_m=hole,
        commanded_bearing_rad=bearing,
        nominal_clearance_m=nominal,
        penetration_m=0.0,
    )
    np.testing.assert_allclose(measurement.measured_tip_xy_m, expected_tip, rtol=0.0)
    np.testing.assert_allclose(
        measurement.hole_estimate_xy_m,
        expected_tip - nominal * direction,
        rtol=0.0,
    )
    assert measurement.quantized
    assert measurement.tcp_bias_applied


def test_sampled_tcp_bias_is_deterministic_under_seed():
    kwargs = {"max_translation_m": 10e-6, "max_rotation_rad": 0.005}
    first = sample_tcp_bias(np.random.default_rng(42), **kwargs)
    second = sample_tcp_bias(np.random.default_rng(42), **kwargs)
    third = sample_tcp_bias(np.random.default_rng(43), **kwargs)
    assert first.translation_xy_m.tolist() == second.translation_xy_m.tolist()
    assert first.yaw_rad == second.yaw_rad
    assert first.translation_xy_m.tolist() != third.translation_xy_m.tolist()
    with pytest.raises(ValueError):
        sample_tcp_bias(np.random.default_rng(0), max_translation_m=-1.0, max_rotation_rad=0.0)


def test_confidently_wrong_mask_flags_low_uncertainty_high_error():
    prior_std = np.array([1e-6, 1e-6, 100e-6])
    error = np.array([5e-6, 0.5e-6, 400e-6])
    mask = confidently_wrong_mask(prior_std, error, n_sigma_threshold=3.0)
    np.testing.assert_array_equal(mask, [True, False, True])
    with pytest.raises(ValueError):
        confidently_wrong_mask(np.array([1e-6]), np.array([1e-6, 2e-6]), 3.0)
    with pytest.raises(ValueError):
        confidently_wrong_mask(np.array([1e-6]), np.array([1e-6]), -1.0)


def test_invalid_channel_parameters_raise():
    with pytest.raises(ValueError):
        PartDimensionalVariation(float("nan"))
    with pytest.raises(ValueError):
        BoreOutOfRoundness(0, 1e-6, 0.0)
    with pytest.raises(ValueError):
        BoreOutOfRoundness(2.5, 1e-6, 0.0)
    with pytest.raises(ValueError):
        BoreOutOfRoundness(2, -1e-6, 0.0)
    with pytest.raises(ValueError):
        TcpCalibrationBias(translation_m=[0.0], yaw_rad=0.0)
    with pytest.raises(ValueError):
        CorruptionModel(encoder_resolution_m=0.0)
    with pytest.raises(ValueError):
        probe_observation(
            CorruptionModel(),
            true_hole_xy_m=np.zeros(3),
            commanded_bearing_rad=0.0,
            nominal_clearance_m=500e-6,
            penetration_m=0.0,
        )


def test_error_injection_module_has_no_mujoco_import():
    source = Path("pegsim/error_injection.py").read_text()
    imported_roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".")[0])

    assert "mujoco" not in imported_roots
