"""Kalman state filter for fixture position and radial clearance drift.

This module is deliberately independent of the simulation.  Its observations
are already-reduced planar hole estimates, whether they came from synthetic
data, MuJoCo, or hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Final
import warnings

import numpy as np
from numpy.typing import ArrayLike, NDArray


# 99.9th percentile of chi-square with two innovation dimensions.
CHI2_2DOF_999: Final[float] = 13.816
# An antipodal measurement batch contains exactly two physical probes.
PAIR_PROBE_COUNT: Final[int] = 2
# F1a-rev2 escalates repeated model mismatch on the third consecutive gate.
DEFAULT_CONSECUTIVE_GATE_LIMIT: Final[int] = 3


class FilterOutcome(str, Enum):
    """Disposition of one probe measurement."""

    UPDATED = "UPDATED"
    SALVAGE = "SALVAGE"
    ABORT = "ABORT"


@dataclass(frozen=True)
class FilterUpdateRecord:
    """Immutable snapshots and diagnostics from one filter update."""

    outcome: FilterOutcome
    bearing_rad: float
    innovation_m: NDArray[np.float64]
    innovation_covariance_m2: NDArray[np.float64]
    normalized_innovation_squared: float
    gate_threshold: float
    consecutive_gate_count: int
    last_good_state_m: NDArray[np.float64]
    gated_innovation_history_m: tuple[NDArray[np.float64], ...]
    state_m: NDArray[np.float64]
    covariance_m2: NDArray[np.float64]


def _finite_vector(
    value: ArrayLike,
    *,
    shape: tuple[int, ...],
    name: str,
) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=float)
    if array.shape != shape:
        raise ValueError(f"{name} must have shape {shape}, got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain only finite values")
    return np.array(array, dtype=float, copy=True)


def _positive_definite_matrix(
    value: ArrayLike,
    *,
    shape: tuple[int, int],
    name: str,
) -> NDArray[np.float64]:
    matrix = _finite_vector(value, shape=shape, name=name)
    if not np.array_equal(matrix, matrix.T):
        raise ValueError(f"{name} must be exactly symmetric")
    if np.min(np.linalg.eigvalsh(matrix)) <= 0.0:
        raise ValueError(f"{name} must be positive-definite")
    return matrix


def _read_only_copy(value: NDArray[np.float64]) -> NDArray[np.float64]:
    snapshot = np.array(value, dtype=float, copy=True)
    snapshot.setflags(write=False)
    return snapshot


class StateFilter:
    """Random-walk Kalman filter for ``x = [h_x, h_y, dc]`` in meters.

    ``penetration_m`` is a known observation bias.  It is zero on hardware and
    is supplied from contact characterization in simulation; it is never
    inferred as part of the state.
    """

    def __init__(
        self,
        *,
        initial_state_m: ArrayLike,
        initial_covariance_m2: ArrayLike,
        measurement_covariance_m2: ArrayLike,
        q_h_m2_per_cycle: float,
        q_c_m2_per_cycle: float,
        penetration_m: float,
        consecutive_gate_limit: int = DEFAULT_CONSECUTIVE_GATE_LIMIT,
    ) -> None:
        self._x = _finite_vector(
            initial_state_m,
            shape=(3,),
            name="initial_state_m",
        )
        self._p = _positive_definite_matrix(
            initial_covariance_m2,
            shape=(3, 3),
            name="initial_covariance_m2",
        )
        self._r = _positive_definite_matrix(
            measurement_covariance_m2,
            shape=(2, 2),
            name="measurement_covariance_m2",
        )

        q_h = self._nonnegative_finite(q_h_m2_per_cycle, "q_h_m2_per_cycle")
        q_c = self._nonnegative_finite(q_c_m2_per_cycle, "q_c_m2_per_cycle")
        self._q = np.diag([q_h, q_h, q_c])
        self._penetration_m = self._nonnegative_finite(
            penetration_m,
            "penetration_m",
        )
        self._gate_threshold = CHI2_2DOF_999
        self._consecutive_gate_limit = self._positive_integer(
            consecutive_gate_limit,
            "consecutive_gate_limit",
        )
        self._initial_h_covariance_m2 = np.array(
            self._p[:2, :2],
            dtype=float,
            copy=True,
        )
        self._consecutive_gate_count = 0
        self._gated_innovation_history_m: list[NDArray[np.float64]] = []
        self._observation_history: list[NDArray[np.float64]] = []

    @staticmethod
    def _nonnegative_finite(value: float, name: str) -> float:
        result = float(value)
        if not math.isfinite(result) or result < 0.0:
            raise ValueError(f"{name} must be finite and nonnegative")
        return result

    @staticmethod
    def _positive_integer(value: int, name: str) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, np.integer))
            or value <= 0
        ):
            raise ValueError(f"{name} must be a positive integer")
        return int(value)

    @property
    def state_m(self) -> NDArray[np.float64]:
        """Return a copy of the current ``[h_x, h_y, dc]`` estimate."""

        return np.array(self._x, copy=True)

    @property
    def covariance_m2(self) -> NDArray[np.float64]:
        """Return a copy of the current posterior covariance."""

        return np.array(self._p, copy=True)

    @property
    def process_covariance_m2_per_cycle(self) -> NDArray[np.float64]:
        """Return a copy of the configured random-walk covariance."""

        return np.array(self._q, copy=True)

    @property
    def measurement_covariance_m2(self) -> NDArray[np.float64]:
        """Return a copy of the configured observation covariance."""

        return np.array(self._r, copy=True)

    @property
    def penetration_m(self) -> float:
        return self._penetration_m

    @property
    def consecutive_gate_limit(self) -> int:
        return self._consecutive_gate_limit

    @staticmethod
    def observation_matrix(bearing_rad: float) -> NDArray[np.float64]:
        """Return the 2x3 observation matrix for a commanded probe bearing."""

        theta = float(bearing_rad)
        if not math.isfinite(theta):
            raise ValueError("bearing_rad must be finite")
        u_x = math.cos(theta)
        u_y = math.sin(theta)
        return np.array([
            [1.0, 0.0, u_x],
            [0.0, 1.0, u_y],
        ])

    @classmethod
    def pair_observation_matrix(
        cls,
        bearing_rad: float,
    ) -> NDArray[np.float64]:
        """Return the 4x3 observation matrix for one antipodal pair."""

        plus_matrix = cls.observation_matrix(bearing_rad)
        minus_matrix = np.array(plus_matrix, copy=True)
        minus_matrix[:, 2] *= -1.0
        return np.vstack((plus_matrix, minus_matrix))

    def inflate_clearance_variance(self, prior_variance_m2: float) -> None:
        """Raise ``P_dc`` to a commanded tool-change prior variance."""

        requested_variance_m2 = self._nonnegative_finite(
            prior_variance_m2,
            "prior_variance_m2",
        )
        if requested_variance_m2 <= 0.0:
            raise ValueError("prior_variance_m2 must be positive")
        if requested_variance_m2 > self._p[2, 2]:
            # Increasing one diagonal of a positive-definite covariance cannot
            # invalidate it, while retaining learned cross-covariances.
            self._p[2, 2] = requested_variance_m2

    def update(
        self,
        hole_estimate_xy_m: ArrayLike,
        bearing_rad: float,
    ) -> FilterUpdateRecord:
        """Predict, gate, and conditionally update from one planar probe."""

        z = _finite_vector(
            hole_estimate_xy_m,
            shape=(2,),
            name="hole_estimate_xy_m",
        )
        h_matrix = self.observation_matrix(bearing_rad)
        direction = h_matrix[:, 2]
        known_bias_m = -self._penetration_m * direction
        z_corrected_m = z - known_bias_m

        return self._conditionally_update(
            z_corrected_m=z_corrected_m,
            h_matrix=h_matrix,
            measurement_covariance_m2=self._r,
            bearing_rad=float(bearing_rad),
            process_step_count=1,
            fixture_reinitialization_m=(
                z_corrected_m - direction * self._x[2]
            ),
            observation_matrices=(h_matrix,),
        )

    def update_pair(
        self,
        hole_estimate_plus_xy_m: ArrayLike,
        hole_estimate_minus_xy_m: ArrayLike,
        bearing_rad: float,
        pair_measurement_covariance_m2: ArrayLike | None = None,
    ) -> FilterUpdateRecord:
        """Predict twice, then condition once on an antipodal probe pair.

        The first process step precedes the plus probe and the second lies
        between the plus and minus probes. The observations are conditioned in
        one four-row batch; an incomplete pair must use :meth:`update`.

        ``pair_measurement_covariance_m2`` is the optional full 4x4
        measurement covariance for this pair, overriding the default
        ``block_diag(R, R)``. The default encodes the F1a independent-side
        assumption, made because no side cross-correlation had been
        measured; N5 measured a strong anticorrelation, so evidence-backed
        callers supply the measured covariance instead.
        """

        plus_m = _finite_vector(
            hole_estimate_plus_xy_m,
            shape=(2,),
            name="hole_estimate_plus_xy_m",
        )
        minus_m = _finite_vector(
            hole_estimate_minus_xy_m,
            shape=(2,),
            name="hole_estimate_minus_xy_m",
        )
        h_matrix = self.pair_observation_matrix(bearing_rad)
        direction = h_matrix[:2, 2]
        known_bias_m = np.concatenate((
            -self._penetration_m * direction,
            self._penetration_m * direction,
        ))
        z_corrected_m = np.concatenate((plus_m, minus_m)) - known_bias_m
        if pair_measurement_covariance_m2 is None:
            pair_covariance = np.zeros((4, 4), dtype=float)
            pair_covariance[:2, :2] = self._r
            pair_covariance[2:, 2:] = self._r
        else:
            pair_covariance = _finite_vector(
                pair_measurement_covariance_m2,
                shape=(4, 4),
                name="pair_measurement_covariance_m2",
            )
            if np.min(np.linalg.eigvalsh(pair_covariance)) <= 0.0:
                raise ValueError(
                    "the supplied pair measurement covariance is not "
                    "positive-definite"
                )
        fixture_reinitialization_m = (
            z_corrected_m[:2] + z_corrected_m[2:]
        ) / PAIR_PROBE_COUNT

        return self._conditionally_update(
            z_corrected_m=z_corrected_m,
            h_matrix=h_matrix,
            measurement_covariance_m2=pair_covariance,
            bearing_rad=float(bearing_rad),
            process_step_count=PAIR_PROBE_COUNT,
            fixture_reinitialization_m=fixture_reinitialization_m,
            observation_matrices=(h_matrix[:2], h_matrix[2:]),
        )

    def _conditionally_update(
        self,
        *,
        z_corrected_m: NDArray[np.float64],
        h_matrix: NDArray[np.float64],
        measurement_covariance_m2: NDArray[np.float64],
        bearing_rad: float,
        process_step_count: int,
        fixture_reinitialization_m: NDArray[np.float64],
        observation_matrices: tuple[NDArray[np.float64], ...],
    ) -> FilterUpdateRecord:
        """Apply one atomic gated update after the represented predictions."""

        x_pred = self._x
        p_pred = self._p + process_step_count * self._q
        innovation_m = z_corrected_m - h_matrix @ x_pred
        innovation_covariance_m2 = (
            h_matrix @ p_pred @ h_matrix.T
            + measurement_covariance_m2
        )
        innovation_covariance_m2 = (
            innovation_covariance_m2 + innovation_covariance_m2.T
        ) / 2.0
        solved_innovation = np.linalg.solve(
            innovation_covariance_m2,
            innovation_m,
        )
        normalized_innovation_squared = float(
            innovation_m @ solved_innovation
        )

        # Probe geometry remains useful for schedule diagnostics even when its
        # measurement is rejected; estimator state and covariance do not move.
        self._observation_history.extend(observation_matrices)
        if normalized_innovation_squared > self._gate_threshold:
            self._consecutive_gate_count += 1
            self._gated_innovation_history_m.append(
                np.array(innovation_m, dtype=float, copy=True)
            )
            last_good_state_m = np.array(self._x, copy=True)
            gate_history_m = tuple(
                _read_only_copy(value)
                for value in self._gated_innovation_history_m
            )
            if self._consecutive_gate_count >= self._consecutive_gate_limit:
                abort_gate_count = self._consecutive_gate_count
                self._reinitialize_fixture(
                    fixture_reinitialization_m,
                )
                record = self._record(
                    outcome=FilterOutcome.ABORT,
                    bearing_rad=bearing_rad,
                    innovation_m=innovation_m,
                    innovation_covariance_m2=innovation_covariance_m2,
                    normalized_innovation_squared=(
                        normalized_innovation_squared
                    ),
                    consecutive_gate_count=abort_gate_count,
                    last_good_state_m=last_good_state_m,
                    gated_innovation_history_m=gate_history_m,
                )
                self._consecutive_gate_count = 0
                self._gated_innovation_history_m.clear()
                return record
            return self._record(
                outcome=FilterOutcome.SALVAGE,
                bearing_rad=bearing_rad,
                innovation_m=innovation_m,
                innovation_covariance_m2=innovation_covariance_m2,
                normalized_innovation_squared=normalized_innovation_squared,
                consecutive_gate_count=self._consecutive_gate_count,
                last_good_state_m=last_good_state_m,
                gated_innovation_history_m=gate_history_m,
            )

        kalman_gain = np.linalg.solve(
            innovation_covariance_m2,
            h_matrix @ p_pred,
        ).T
        updated_x = x_pred + kalman_gain @ innovation_m
        joseph_left = np.eye(3) - kalman_gain @ h_matrix
        updated_p = (
            joseph_left @ p_pred @ joseph_left.T
            + kalman_gain
            @ measurement_covariance_m2
            @ kalman_gain.T
        )
        updated_p = (updated_p + updated_p.T) / 2.0
        if (
            not np.all(np.isfinite(updated_x))
            or not np.all(np.isfinite(updated_p))
        ):
            raise FloatingPointError("filter update produced a non-finite result")
        if np.min(np.linalg.eigvalsh(updated_p)) <= 0.0:
            raise FloatingPointError("filter covariance lost positive-definiteness")

        self._x = updated_x
        self._p = updated_p
        self._consecutive_gate_count = 0
        self._gated_innovation_history_m.clear()
        return self._record(
            outcome=FilterOutcome.UPDATED,
            bearing_rad=bearing_rad,
            innovation_m=innovation_m,
            innovation_covariance_m2=innovation_covariance_m2,
            normalized_innovation_squared=normalized_innovation_squared,
            consecutive_gate_count=0,
            last_good_state_m=self._x,
            gated_innovation_history_m=(),
        )

    def _reinitialize_fixture(
        self,
        fixture_reinitialization_m: NDArray[np.float64],
    ) -> None:
        """Restart fixture tracking after repeated model mismatch."""

        self._x[:2] = fixture_reinitialization_m
        reset_covariance_m2 = np.zeros((3, 3), dtype=float)
        reset_covariance_m2[:2, :2] = (
            self._p[:2, :2] + self._initial_h_covariance_m2
        )
        reset_covariance_m2[2, 2] = self._p[2, 2]
        self._p = reset_covariance_m2

    def observability_condition_number(
        self,
        last_n_probes: int,
        *,
        warn: bool = True,
    ) -> float:
        """Condition number of the stacked design over recent probe bearings."""

        if (
            isinstance(last_n_probes, bool)
            or not isinstance(last_n_probes, (int, np.integer))
            or last_n_probes <= 0
        ):
            raise ValueError("last_n_probes must be a positive integer")
        selected = self._observation_history[-int(last_n_probes):]
        if not selected:
            return math.inf

        stacked = np.vstack(selected)
        singular_values = np.linalg.svd(stacked, compute_uv=False)
        rank_tolerance = (
            singular_values[0]
            * max(stacked.shape)
            * np.finfo(float).eps
        )
        if len(singular_values) < 3 or singular_values[-1] <= rank_tolerance:
            condition_number = math.inf
        else:
            condition_number = float(singular_values[0] / singular_values[-1])

        # Beyond this derived boundary, roundoff can consume at least half of
        # float64's significant digits even when formal rank remains three.
        warning_boundary = 1.0 / math.sqrt(np.finfo(float).eps)
        if warn and condition_number >= warning_boundary:
            warnings.warn(
                "recent probe bearings do not provide a well-conditioned "
                "three-state observation design",
                RuntimeWarning,
                stacklevel=2,
            )
        return condition_number

    def _record(
        self,
        *,
        outcome: FilterOutcome,
        bearing_rad: float,
        innovation_m: NDArray[np.float64],
        innovation_covariance_m2: NDArray[np.float64],
        normalized_innovation_squared: float,
        consecutive_gate_count: int,
        last_good_state_m: NDArray[np.float64],
        gated_innovation_history_m: tuple[NDArray[np.float64], ...],
    ) -> FilterUpdateRecord:
        return FilterUpdateRecord(
            outcome=outcome,
            bearing_rad=bearing_rad,
            innovation_m=_read_only_copy(innovation_m),
            innovation_covariance_m2=_read_only_copy(
                innovation_covariance_m2,
            ),
            normalized_innovation_squared=normalized_innovation_squared,
            gate_threshold=self._gate_threshold,
            consecutive_gate_count=consecutive_gate_count,
            last_good_state_m=_read_only_copy(last_good_state_m),
            gated_innovation_history_m=tuple(
                _read_only_copy(value)
                for value in gated_innovation_history_m
            ),
            state_m=_read_only_copy(self._x),
            covariance_m2=_read_only_copy(self._p),
        )
