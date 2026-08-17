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


class FilterOutcome(str, Enum):
    """Disposition of one probe measurement."""

    UPDATED = "UPDATED"
    SALVAGE = "SALVAGE"


@dataclass(frozen=True)
class FilterUpdateRecord:
    """Immutable snapshots and diagnostics from one filter update."""

    outcome: FilterOutcome
    bearing_rad: float
    innovation_m: NDArray[np.float64]
    innovation_covariance_m2: NDArray[np.float64]
    normalized_innovation_squared: float
    gate_threshold: float
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
        self._observation_history: list[NDArray[np.float64]] = []

    @staticmethod
    def _nonnegative_finite(value: float, name: str) -> float:
        result = float(value)
        if not math.isfinite(result) or result < 0.0:
            raise ValueError(f"{name} must be finite and nonnegative")
        return result

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

        x_pred = self._x
        p_pred = self._p + self._q
        innovation_m = z_corrected_m - h_matrix @ x_pred
        innovation_covariance_m2 = h_matrix @ p_pred @ h_matrix.T + self._r
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
        self._observation_history.append(h_matrix)
        if normalized_innovation_squared > self._gate_threshold:
            return self._record(
                outcome=FilterOutcome.SALVAGE,
                bearing_rad=float(bearing_rad),
                innovation_m=innovation_m,
                innovation_covariance_m2=innovation_covariance_m2,
                normalized_innovation_squared=normalized_innovation_squared,
            )

        kalman_gain = np.linalg.solve(
            innovation_covariance_m2,
            h_matrix @ p_pred,
        ).T
        updated_x = x_pred + kalman_gain @ innovation_m
        joseph_left = np.eye(3) - kalman_gain @ h_matrix
        updated_p = (
            joseph_left @ p_pred @ joseph_left.T
            + kalman_gain @ self._r @ kalman_gain.T
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
        return self._record(
            outcome=FilterOutcome.UPDATED,
            bearing_rad=float(bearing_rad),
            innovation_m=innovation_m,
            innovation_covariance_m2=innovation_covariance_m2,
            normalized_innovation_squared=normalized_innovation_squared,
        )

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
            state_m=_read_only_copy(self._x),
            covariance_m2=_read_only_copy(self._p),
        )
