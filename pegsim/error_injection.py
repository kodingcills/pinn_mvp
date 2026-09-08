"""Synthetic error-injection pipeline for probe observations (F1b).

This module generates per-probe hole observations from a TRUE plant state
through the four R03 corruption classes, in the physical order the errors
actually enter:

    part dimensional variation   (truth side: the bore is the size it is)
    bore out-of-roundness        (truth side: harmonic wall deviation)
    TCP calibration bias         (sensor side: fixed per-episode transform)
    encoder quantization         (sensor side: tip position resolution)
    -> hole-estimate reduction   (estimator side: NOMINAL clearance)

The output matches the observation model of ``pegsim.filter.StateFilter``:
the filter receives ``hole_estimate_xy = measured_tip_xy - c_nominal * u``
and applies its own known penetration-bias correction. Truth quantities are
returned only as evaluation fields; nothing here drives control.

Like ``pegsim.filter``, this module is deliberately independent of MuJoCo.
The encoder quantizer is a mid-rise quantizer using round-half-to-even
(NumPy's ``round``), the standard ADC tie-breaking convention; the tie
behavior is pinned by tests. Bore out-of-roundness is an explicit k-th
harmonic wall-radius deviation -- an elliptical bore for k=2 -- not
isotropic noise, and claims must stay scoped to that synthetic shape.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray


@dataclass(frozen=True)
class PartDimensionalVariation:
    """Truth-side bore size error: the radial clearance is the CAD value
    plus this offset. The estimator and controller keep assuming nominal."""

    clearance_offset_m: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.clearance_offset_m):
            raise ValueError("clearance_offset_m must be finite")


@dataclass(frozen=True)
class BoreOutOfRoundness:
    """Truth-side k-th harmonic wall-radius deviation.

    The wall radius deviates from the manufactured clearance by
    amplitude * cos(order * (bearing - phase_rad)). order=2 is an elliptical
    bore. For order >= 1 the deviation averages to zero over the full circle;
    this models ONE synthetic shape class, not general manufacturing
    variation."""

    harmonic_order: int
    amplitude_m: float
    phase_rad: float

    def __post_init__(self) -> None:
        if type(self.harmonic_order) is not int or self.harmonic_order < 1:
            raise ValueError("harmonic_order must be an integer >= 1")
        if not math.isfinite(self.amplitude_m) or self.amplitude_m < 0.0:
            raise ValueError("amplitude_m must be finite and nonnegative")
        if not math.isfinite(self.phase_rad):
            raise ValueError("phase_rad must be finite")

    def radius_deviation_m(self, bearing_rad: float) -> float:
        theta = float(bearing_rad)
        if not math.isfinite(theta):
            raise ValueError("bearing_rad must be finite")
        return self.amplitude_m * math.cos(
            self.harmonic_order * (theta - self.phase_rad)
        )


@dataclass(frozen=True)
class TcpCalibrationBias:
    """Sensor-side fixed per-episode calibration transform, planar to match
    the planar hole observation: a world-frame yaw rotation followed by a
    world-frame translation, applied to the tip position before reduction.
    Drawn ONCE per episode, never redrawn per measurement."""

    translation_m: ArrayLike
    yaw_rad: float

    def __post_init__(self) -> None:
        translation = np.asarray(self.translation_m, dtype=float)
        if translation.shape != (2,):
            raise ValueError(
                f"translation_m must have shape (2,), got {translation.shape}"
            )
        if not np.all(np.isfinite(translation)):
            raise ValueError("translation_m must be finite")
        if not math.isfinite(self.yaw_rad):
            raise ValueError("yaw_rad must be finite")

    @property
    def translation_xy_m(self) -> NDArray[np.float64]:
        return np.array(self.translation_m, dtype=float, copy=True)

    def apply(self, tip_xy_m: NDArray[np.float64]) -> NDArray[np.float64]:
        point = np.asarray(tip_xy_m, dtype=float)
        if point.shape != (2,):
            raise ValueError(f"tip_xy_m must have shape (2,), got {point.shape}")
        cos_yaw = math.cos(self.yaw_rad)
        sin_yaw = math.sin(self.yaw_rad)
        rotation = np.array([
            [cos_yaw, -sin_yaw],
            [sin_yaw, cos_yaw],
        ])
        return rotation @ point + self.translation_xy_m


def sample_tcp_bias(
    rng: np.random.Generator,
    *,
    max_translation_m: float,
    max_rotation_rad: float,
) -> TcpCalibrationBias:
    """Draw one episode's fixed calibration bias from a seeded generator."""
    if max_translation_m < 0.0 or max_rotation_rad < 0.0:
        raise ValueError("scales must be nonnegative")
    translation = rng.uniform(-max_translation_m, max_translation_m, size=2)
    yaw = rng.uniform(-max_rotation_rad, max_rotation_rad)
    return TcpCalibrationBias(translation_m=translation, yaw_rad=float(yaw))


@dataclass(frozen=True)
class CorruptionModel:
    """The four corruption classes in physical entry order. ``None`` disables
    a channel; every field is explicit so composition order cannot hide."""

    part_variation: PartDimensionalVariation | None = None
    out_of_roundness: BoreOutOfRoundness | None = None
    tcp_bias: TcpCalibrationBias | None = None
    encoder_resolution_m: float | None = None

    def __post_init__(self) -> None:
        if self.encoder_resolution_m is not None:
            resolution = float(self.encoder_resolution_m)
            if not math.isfinite(resolution) or resolution <= 0.0:
                raise ValueError("encoder_resolution_m must be finite and positive")


def quantize(value: float, resolution_m: float) -> float:
    """Mid-rise quantizer: q_m = resolution * round(q / resolution).

    Uses round-half-to-even. The result is always an exact multiple of the
    resolution and within resolution/2 of the input."""
    if not math.isfinite(value):
        raise ValueError("value must be finite")
    if not math.isfinite(resolution_m) or resolution_m <= 0.0:
        raise ValueError("resolution_m must be finite and positive")
    return float(resolution_m * round(value / resolution_m))


def true_wall_radius_m(
    corruption: CorruptionModel,
    *,
    nominal_clearance_m: float,
    penetration_m: float,
    bearing_rad: float,
) -> float:
    """The radius at which the peg actually rests against the wall.

    r(bearing) = (nominal clearance + part offset) + ovality deviation
                 - penetration. Nominal clearance is the MANUFACTURED value
    here; the part offset moves it. The estimator never sees this radius,
    only its consequences through the tip position."""
    if nominal_clearance_m <= 0.0:
        raise ValueError("nominal_clearance_m must be positive")
    if penetration_m < 0.0:
        raise ValueError("penetration_m must be nonnegative")
    radius = nominal_clearance_m
    if corruption.part_variation is not None:
        radius += corruption.part_variation.clearance_offset_m
    if corruption.out_of_roundness is not None:
        radius += corruption.out_of_roundness.radius_deviation_m(bearing_rad)
    return radius - penetration_m


@dataclass(frozen=True)
class ProbeMeasurement:
    """One probe observation with every intermediate stage exposed.

    ``hole_estimate_xy_m`` is the filter observation (nominal-clearance
    reduction, WITHOUT the filter's penetration-bias correction). The truth
    fields exist only for evaluation."""

    commanded_bearing_rad: float
    true_tip_xy_m: NDArray[np.float64]
    biased_tip_xy_m: NDArray[np.float64]
    measured_tip_xy_m: NDArray[np.float64]
    hole_estimate_xy_m: NDArray[np.float64]
    true_wall_radius_m: float
    true_hole_xy_m: NDArray[np.float64]
    quantized: bool
    tcp_bias_applied: bool


def probe_observation(
    corruption: CorruptionModel,
    *,
    true_hole_xy_m: ArrayLike,
    commanded_bearing_rad: float,
    nominal_clearance_m: float,
    penetration_m: float,
) -> ProbeMeasurement:
    """Generate one probe observation through the full corruption pipeline.

    Physical order: the peg contacts the TRUE wall (part variation + ovality
    set the radius, penetration insets it), the tip position is transformed
    by the fixed TCP calibration bias, the encoder quantizes the tip
    position, and the estimator reduces with NOMINAL clearance."""
    hole = np.asarray(true_hole_xy_m, dtype=float)
    if hole.shape != (2,):
        raise ValueError(f"true_hole_xy_m must have shape (2,), got {hole.shape}")
    if not np.all(np.isfinite(hole)):
        raise ValueError("true_hole_xy_m must be finite")
    theta = float(commanded_bearing_rad)
    if not math.isfinite(theta):
        raise ValueError("commanded_bearing_rad must be finite")
    if nominal_clearance_m <= 0.0:
        raise ValueError("nominal_clearance_m must be positive")

    direction = np.array([math.cos(theta), math.sin(theta)])
    wall_radius = true_wall_radius_m(
        corruption,
        nominal_clearance_m=nominal_clearance_m,
        penetration_m=penetration_m,
        bearing_rad=theta,
    )
    true_tip = hole + wall_radius * direction

    biased_tip = (
        corruption.tcp_bias.apply(true_tip)
        if corruption.tcp_bias is not None
        else np.array(true_tip, dtype=float, copy=True)
    )

    quantized = corruption.encoder_resolution_m is not None
    if quantized:
        resolution = float(corruption.encoder_resolution_m)
        measured_tip = np.array([
            quantize(float(biased_tip[0]), resolution),
            quantize(float(biased_tip[1]), resolution),
        ])
    else:
        measured_tip = np.array(biased_tip, dtype=float, copy=True)

    hole_estimate = measured_tip - nominal_clearance_m * direction

    return ProbeMeasurement(
        commanded_bearing_rad=theta,
        true_tip_xy_m=true_tip,
        biased_tip_xy_m=biased_tip,
        measured_tip_xy_m=measured_tip,
        hole_estimate_xy_m=hole_estimate,
        true_wall_radius_m=wall_radius,
        true_hole_xy_m=np.array(hole, dtype=float, copy=True),
        quantized=quantized,
        tcp_bias_applied=corruption.tcp_bias is not None,
    )


def confidently_wrong_mask(
    prior_std_m: ArrayLike,
    actual_error_m: ArrayLike,
    n_sigma_threshold: float,
) -> NDArray[np.bool_]:
    """Flag the R03 confidently-wrong condition: estimator uncertainty is LOW
    while actual error is HIGH. Elementwise: error > threshold * prior std.

    This is the detection hook for corruption campaigns; it compares the
    filter's claimed uncertainty against evaluation-only truth error."""
    std = np.asarray(prior_std_m, dtype=float)
    error = np.asarray(actual_error_m, dtype=float)
    if std.shape != error.shape:
        raise ValueError(
            f"prior_std_m shape {std.shape} != actual_error_m shape {error.shape}"
        )
    if not np.all(np.isfinite(std)) or np.any(std < 0.0):
        raise ValueError("prior_std_m must be finite and nonnegative")
    if not np.all(np.isfinite(error)):
        raise ValueError("actual_error_m must be finite")
    if not math.isfinite(n_sigma_threshold) or n_sigma_threshold <= 0.0:
        raise ValueError("n_sigma_threshold must be finite and positive")
    return error > n_sigma_threshold * std
