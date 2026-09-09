"""Probe-based measurement sessions feeding the state filter (F1c).

Wires the N5-validated antipodal pair observation model to real physics:

    run_episode (search-disabled radial probe)
        -> settled tip position (measured, never commanded)
        -> fixed TCP calibration bias        (sensor side, per-episode)
        -> encoder quantization              (sensor side, tip resolution)
        -> nominal-clearance reduction       (hole estimate, no truth access)
        -> StateFilter.update_pair           (gated batch update)

The filter observes ONLY the reduced estimates. True hole position appears
solely in evaluation fields of the returned records, never in the update
path (Rule Zero). Lifecycle ownership is explicit: the session owns the
filter, the reusable MuJoCo model/data pair, and the reset semantics;
``run_episode`` still resets all physics state per episode (mj_resetData),
and ``reset()`` reinitializes the estimator to its configured prior.

The pair geometry is the N5b antipodal design (minus probe at +pi). The
penetration bias constant is scene-dependent and evidence-loaded: at n=64
the N5 campaign measured a population-mean settled radius deviation of
1.09 µm (half the full-difference intercept); the N4 pooled-minimum
2.963 µm is the facet-center extreme and REFUTED as the pair-difference
constant (see docs/N5_ANTIPODAL_CANCELLATION_REPORT.md §6).

Truth-side corruption (part variation, out-of-roundness) enters through the
scene itself, as in N5; sensor-side corruption enters here through
``error_injection`` primitives applied to the measured tip in physical
order (bias, then quantization). Estimator behavior is untouched: the
filter is used exactly as audited in F1a-rev2.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Optional

import mujoco
import numpy as np
from numpy.typing import ArrayLike, NDArray

from pegsim.error_injection import CorruptionModel, TcpCalibrationBias, quantize
from pegsim.filter import FilterUpdateRecord, StateFilter
from pegsim.rollout import (
    VALIDATED_LOOSE_V1,
    EpisodeConfig,
    TrialResult,
    build_episode_model_data,
    run_episode,
)

ROOT = Path(__file__).resolve().parents[1]
N5_SUMMARY_PATH: Final[Path] = ROOT / "results" / "n5_antipodal_summary.json"
N5_RAW_PATH: Final[Path] = ROOT / "results" / "n5_antipodal_raw.jsonl"

# F1a-rev2 process priors and placeholder measurement covariance, carried
# over unchanged: they are documented placeholders, not tuned values.
Q_H_M2_PER_CYCLE: Final[float] = 4.0e-14  # (0.2 um)^2
Q_C_M2_PER_CYCLE: Final[float] = 1.0e-18  # (0.001 um)^2
R_PAIR_M2: Final[float] = 1.0e-12  # (1 um)^2 placeholder from F1a
INITIAL_COVARIANCE_M2: Final[float] = 1.0e-6  # (1 mm)^2 broad prior


@dataclass(frozen=True)
class PairMeasurementModel:
    """Measured per-pair observation covariance in the COMMANDED frame.

    N5 measured the side errors of an antipodal pair to be strongly
    anticorrelated along the pair's own radial/tangential axes (the same
    world-frame facet deviation enters the two reductions with opposite
    signs). The anticorrelation is aligned with the commanded bearing, so
    the covariance must be assembled per pair and rotated into the world
    frame; a world-frame average over bearings washes the structure out.

    ``radial_side_covariance_m2`` and ``tangential_side_covariance_m2`` are
    Cov(e_plus, e_minus) per axis; both measured negative at n=64."""

    radial_variance_m2: float
    tangential_variance_m2: float
    radial_side_covariance_m2: float
    tangential_side_covariance_m2: float

    def __post_init__(self) -> None:
        for name in (
            "radial_variance_m2",
            "tangential_variance_m2",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        radial_block = np.array([
            [self.radial_variance_m2, self.radial_side_covariance_m2],
            [self.radial_side_covariance_m2, self.radial_variance_m2],
        ])
        tangential_block = np.array([
            [self.tangential_variance_m2, self.tangential_side_covariance_m2],
            [self.tangential_side_covariance_m2, self.tangential_variance_m2],
        ])
        for name, block in (
            ("radial", radial_block),
            ("tangential", tangential_block),
        ):
            if np.min(np.linalg.eigvalsh(block)) <= 0.0:
                raise ValueError(
                    f"measured {name} side covariance is not positive-"
                    "definite (side correlation at or beyond -1)"
                )

    def covariance_at(self, bearing_rad: float) -> NDArray[np.float64]:
        """The 4x4 world-frame pair measurement covariance at one bearing."""
        theta = float(bearing_rad)
        if not math.isfinite(theta):
            raise ValueError("bearing_rad must be finite")
        u = np.array([math.cos(theta), math.sin(theta)])
        u_perp = np.array([-math.sin(theta), math.cos(theta)])
        basis = np.column_stack((u, u_perp))
        sigma = np.diag([
            self.radial_variance_m2,
            self.tangential_variance_m2,
        ])
        cross = np.diag([
            self.radial_side_covariance_m2,
            self.tangential_side_covariance_m2,
        ])
        world_sigma = basis @ sigma @ basis.T
        world_cross = basis @ cross @ basis.T
        return np.block([[world_sigma, world_cross], [world_cross, world_sigma]])


@lru_cache(maxsize=1)
def load_n5_pair_measurement_model() -> PairMeasurementModel:
    """The measured N5 dc=0 antipodal population's pair covariance model.

    Residuals are bias-corrected per side (the per-side mean error is what
    the filter's known penetration bias represents) and measured in each
    probe's commanded radial/tangential frame, where the N5 anticorrelation
    lives (radial -0.86, tangential -0.98)."""
    radial: dict[str, dict[int, float]] = {"plus": {}, "minus": {}}
    tangential: dict[str, dict[int, float]] = {"plus": {}, "minus": {}}
    for line in N5_RAW_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if (
            record["campaign"] != "n5b"
            or float(record["dc_level_m"]) != 0.0
            or not record["success"]
        ):
            continue
        side = str(record["probe_side"])
        pair_index = int(record["pair_index"])
        radial[side][pair_index] = float(record["error_radial_m"])
        tangential[side][pair_index] = float(record["error_tangential_m"])

    pair_indices = sorted(set(radial["plus"]) & set(radial["minus"]))
    if len(pair_indices) < 2:
        raise ValueError(
            "committed N5 raw evidence does not contain a usable dc=0 pair "
            "population"
        )
    # The raw file is in completion order; the two sides of a pair must be
    # aligned by pair index before any cross-side statistic.
    plus_r = np.array([radial["plus"][k] for k in pair_indices])
    minus_r = np.array([radial["minus"][k] for k in pair_indices])
    plus_t = np.array([tangential["plus"][k] for k in pair_indices])
    minus_t = np.array([tangential["minus"][k] for k in pair_indices])

    def side_averaged_variance(first: NDArray[np.float64], second: NDArray[np.float64]) -> float:
        return float(
            (first.var(ddof=1) + second.var(ddof=1)) / 2.0
        )

    radial_variance = side_averaged_variance(plus_r, minus_r)
    tangential_variance = side_averaged_variance(plus_t, minus_t)
    radial_side = float(np.cov(plus_r, minus_r, ddof=1)[0, 1])
    tangential_side = float(np.cov(plus_t, minus_t, ddof=1)[0, 1])
    return PairMeasurementModel(
        radial_variance_m2=radial_variance,
        tangential_variance_m2=tangential_variance,
        radial_side_covariance_m2=radial_side,
        tangential_side_covariance_m2=tangential_side,
    )


def load_n5_n64_pair_bias_m() -> float:
    """The MEASURED population-mean settled radius deviation at n=64, dc=0,
    from the committed N5 summary: minus half the mean full difference.

    This is the admissible per-probe penetration bias for the pair
    observation model at the N5 scene. The N4 pooled-minimum (2.963 um) is
    the facet-center extreme and is NOT the pair-difference constant."""
    summary = json.loads(N5_SUMMARY_PATH.read_text(encoding="utf-8"))
    mean_full_difference = float(
        summary["by_level"]["+0um"]["pair_full_difference_along_u_m"]["mean"]
    )
    bias = -0.5 * mean_full_difference
    if not math.isfinite(bias) or bias <= 0.0:
        raise ValueError(f"N5 pair bias constant is not usable: {bias}")
    return bias


@dataclass(frozen=True)
class ProbeTelemetry:
    """One side of a completed probe pair. ``true_hole_xy_m`` and the error
    fields are EVALUATION-ONLY: the filter update never sees them."""

    bearing_rad: float
    success: bool
    exclusion_reason: Optional[str]
    settled_tip_xy_m: Optional[NDArray[np.float64]]
    hole_estimate_xy_m: Optional[NDArray[np.float64]]
    true_hole_xy_m: Optional[NDArray[np.float64]]
    evaluation_error_xy_m: Optional[NDArray[np.float64]]
    time_to_seat_s: Optional[float]


def probe_episode(bearing_rad: float, task) -> EpisodeConfig:
    """The N2/N5 search-disabled radial-probe episode for one bearing."""
    cfg = VALIDATED_LOOSE_V1
    theta = float(bearing_rad)
    if not math.isfinite(theta):
        raise ValueError("bearing_rad must be finite")
    return EpisodeConfig(
        task=task,
        solver=cfg.solver,
        gains=cfg.gains,
        insertion=cfg.insertion,
        z_start=cfg.z_start,
        initial_offset_r=2.0 * cfg.task.radial_clearance,
        initial_offset_theta=theta,
        max_seconds=cfg.max_seconds,
        spiral_spec=None,
        depth_commit=cfg.depth_commit,
        control_decimation=cfg.control_decimation,
        control_delay_steps=cfg.control_delay_steps,
    )


def reduce_measurement(
    settled_tip_xy: NDArray[np.float64],
    bearing_rad: float,
    nominal_clearance_m: float,
) -> NDArray[np.float64]:
    """Nominal-clearance hole reduction: hole_est = tip - c_nominal * u."""
    tip = np.asarray(settled_tip_xy, dtype=float)
    if tip.shape != (2,):
        raise ValueError(f"settled_tip_xy must have shape (2,), got {tip.shape}")
    if nominal_clearance_m <= 0.0:
        raise ValueError("nominal_clearance_m must be positive")
    u = np.array([math.cos(bearing_rad), math.sin(bearing_rad)])
    return tip - nominal_clearance_m * u


def apply_sensor_corruption(
    tip_xy: NDArray[np.float64],
    corruption: Optional[CorruptionModel],
) -> NDArray[np.float64]:
    """Sensor-side corruption in physical order: fixed TCP bias, then
    encoder quantization of the tip position. Truth-side corruption is not
    applied here (it enters through the scene)."""
    tip = np.asarray(tip_xy, dtype=float)
    if corruption is None:
        return np.array(tip, dtype=float, copy=True)
    biased = (
        corruption.tcp_bias.apply(tip)
        if corruption.tcp_bias is not None
        else np.array(tip, dtype=float, copy=True)
    )
    if corruption.encoder_resolution_m is None:
        return biased
    resolution = float(corruption.encoder_resolution_m)
    return np.array([
        quantize(float(biased[0]), resolution),
        quantize(float(biased[1]), resolution),
    ])


@dataclass(frozen=True)
class PairProbeRecord:
    """One antipodal pair: filter disposition plus per-side telemetry."""

    bearing_rad: float
    filter_record: FilterUpdateRecord
    plus: ProbeTelemetry
    minus: ProbeTelemetry


class PairProbeSession:
    """Owns the estimator, the reusable MuJoCo model/data pair, and the
    probe lifecycle. One session per scene/task; ``reset()`` returns the
    estimator to its configured prior without rebuilding physics."""

    def __init__(
        self,
        *,
        task,
        penetration_m: float,
        initial_hole_xy_m: ArrayLike = (0.0, 0.0),
        sensor_corruption: Optional[CorruptionModel] = None,
        q_h_m2_per_cycle: float = Q_H_M2_PER_CYCLE,
        q_c_m2_per_cycle: float = Q_C_M2_PER_CYCLE,
        measurement_variance_m2: float = R_PAIR_M2,
        pair_measurement_model: Optional[PairMeasurementModel] = None,
        initial_variance_m2: float = INITIAL_COVARIANCE_M2,
        consecutive_gate_limit: int = 3,
        model: Optional[mujoco.MjModel] = None,
        data: Optional[mujoco.MjData] = None,
    ) -> None:
        if penetration_m <= 0.0:
            raise ValueError(
                "penetration_m must be positive and evidence-loaded; the "
                "N5-measured n=64 constant is available via "
                "load_n5_n64_pair_bias_m()"
            )
        assert (model is None) == (data is None), (
            "model and data must either both be supplied or both be omitted"
        )
        self._task = task
        self._penetration_m = float(penetration_m)
        self._sensor_corruption = sensor_corruption
        self._nominal_clearance_m = float(task.radial_clearance)
        initial_hole = np.asarray(initial_hole_xy_m, dtype=float)
        if initial_hole.shape != (2,) or not np.all(np.isfinite(initial_hole)):
            raise ValueError("initial_hole_xy_m must be a finite (2,) vector")
        if model is not None:
            self._model, self._data = model, data
        else:
            self._model, self._data = build_episode_model_data(
                probe_episode(0.0, task)
            )
        self._initial_state_m = np.array([
            initial_hole[0], initial_hole[1], 0.0,
        ])
        self._pair_measurement_model = pair_measurement_model
        self._config = dict(
            initial_covariance_m2=np.diag([
                initial_variance_m2, initial_variance_m2, initial_variance_m2,
            ]),
            measurement_covariance_m2=np.diag([
                measurement_variance_m2, measurement_variance_m2,
            ]),
            q_h_m2_per_cycle=q_h_m2_per_cycle,
            q_c_m2_per_cycle=q_c_m2_per_cycle,
            penetration_m=self._penetration_m,
            consecutive_gate_limit=consecutive_gate_limit,
        )
        self._filter = self._fresh_filter()

    def _fresh_filter(self) -> StateFilter:
        return StateFilter(
            initial_state_m=self._initial_state_m,
            **self._config,
        )

    def reset(self) -> None:
        """Reinitialize the estimator to its configured prior. Physics state
        is reset per episode by run_episode; this resets estimator state."""
        self._filter = self._fresh_filter()

    @property
    def filter(self) -> StateFilter:
        return self._filter

    @property
    def state_m(self) -> NDArray[np.float64]:
        return self._filter.state_m

    @property
    def covariance_m2(self) -> NDArray[np.float64]:
        return self._filter.covariance_m2

    def _pair_covariance_m2(self, bearing_rad: float) -> Optional[NDArray[np.float64]]:
        if self._pair_measurement_model is None:
            return None
        return self._pair_measurement_model.covariance_at(bearing_rad)

    def feed_pair(
        self,
        hole_estimate_plus_xy_m: ArrayLike,
        hole_estimate_minus_xy_m: ArrayLike,
        bearing_rad: float,
    ) -> FilterUpdateRecord:
        """Update the filter from pre-reduced pair estimates with no physics.
        The gate/abort/reset semantics of F1a-rev2 apply unchanged."""
        return self._filter.update_pair(
            hole_estimate_plus_xy_m,
            hole_estimate_minus_xy_m,
            bearing_rad,
            pair_measurement_covariance_m2=self._pair_covariance_m2(bearing_rad),
        )

    def probe_pair(self, bearing_rad: float) -> PairProbeRecord:
        """Run one antipodal probe pair through real physics and update the
        filter with the measured (corrupted, reduced) estimates."""
        theta = float(bearing_rad)
        cfg = VALIDATED_LOOSE_V1
        plus_result = run_episode(
            probe_episode(theta, self._task),
            pen_allow=cfg.pen_allow,
            model=self._model,
            data=self._data,
        )
        minus_result = run_episode(
            probe_episode(theta + math.pi, self._task),
            pen_allow=cfg.pen_allow,
            model=self._model,
            data=self._data,
        )
        plus_telemetry = self._telemetry(plus_result, theta)
        minus_telemetry = self._telemetry(minus_result, theta + math.pi)
        if not (plus_result.success and minus_result.success):
            raise RuntimeError(
                "probe pair did not seat; the filter is left untouched and "
                f"the pair is invalid (plus={plus_result.success}, "
                f"minus={minus_result.success})"
            )
        filter_record = self._filter.update_pair(
            plus_telemetry.hole_estimate_xy_m,
            minus_telemetry.hole_estimate_xy_m,
            theta,
            pair_measurement_covariance_m2=self._pair_covariance_m2(theta),
        )
        return PairProbeRecord(
            bearing_rad=theta,
            filter_record=filter_record,
            plus=plus_telemetry,
            minus=minus_telemetry,
        )

    def _telemetry(
        self, result: TrialResult, bearing_rad: float
    ) -> ProbeTelemetry:
        if not result.success:
            return ProbeTelemetry(
                bearing_rad=bearing_rad,
                success=False,
                exclusion_reason="not seated before max_seconds",
                settled_tip_xy_m=None,
                hole_estimate_xy_m=None,
                true_hole_xy_m=None,
                evaluation_error_xy_m=None,
                time_to_seat_s=None,
            )
        assert result.settled_tip_xy is not None
        assert result.true_hole_xy is not None
        measured_tip = apply_sensor_corruption(
            result.settled_tip_xy, self._sensor_corruption
        )
        hole_estimate = reduce_measurement(
            measured_tip, bearing_rad, self._nominal_clearance_m,
        )
        return ProbeTelemetry(
            bearing_rad=bearing_rad,
            success=True,
            exclusion_reason=None,
            settled_tip_xy_m=measured_tip,
            hole_estimate_xy_m=hole_estimate,
            # Evaluation-only truth, never consumed by the update path.
            true_hole_xy_m=np.array(result.true_hole_xy, dtype=float, copy=True),
            evaluation_error_xy_m=(
                hole_estimate - np.asarray(result.true_hole_xy, dtype=float)
            ),
            time_to_seat_s=float(result.time_to_seat_s),
        )
