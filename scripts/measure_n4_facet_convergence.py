"""N4: measure estimator convergence as bore faceting is refined.

This is an offline characterization driver, not a production probe cycle.
Physics is stepped only by :func:`pegsim.rollout.run_episode`. Each segment
count gets a separate process pool, and every worker reuses one compatible
MjModel/MjData pair. Only the main process writes JSONL, summary, and figures.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

import matplotlib
import mujoco
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter

from pegsim.control import MAX_TILT_RAD, peg_dof_ids
from pegsim.rollout import (
    COMMIT_RELEASE_FRACTION,
    TARGET_DEPTH_MARGIN_M,
    Z_FLAT_BAND,
    Z_FLAT_TAU_S,
    EpisodeConfig,
    VALIDATED_LOOSE_V1,
    build_episode_model_data,
    run_episode,
)
from pegsim.scene import TaskSpec


ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"
FIGURES_DIR = ROOT / "docs" / "figures"
RAW_PATH = RESULTS_DIR / "n4_facet_convergence_raw.jsonl"
SUMMARY_PATH = RESULTS_DIR / "n4_facet_convergence_summary.json"
N2_RAW_PATH = RESULTS_DIR / "n2_probe_raw.jsonl"
N2_SUMMARY_PATH = RESULTS_DIR / "n2_probe_summary.json"

ESTIMATOR_FIGURE_STEM = FIGURES_DIR / "n4_estimator_error_asymptote"
BEARING_FIGURE_STEM = FIGURES_DIR / "n4_bearing_deflection_facet_phase"
RADIUS_FIGURE_STEM = FIGURES_DIR / "n4_settled_tip_radius_facet_phase"
RESIDUAL_FIGURE_STEM = FIGURES_DIR / "n4_asymptote_residuals"

N_SEGMENTS_SWEEP = (32, 64, 128)  # N4 work order; canonical n=16 is not changed.
N_COARSE = 64  # N4 work order: global golden-angle coverage.
N_FINE = 32  # N4 work order: half-bin samples over one facet.
N_PER_SEGMENT_COUNT = N_COARSE + N_FINE
N_TOTAL = len(N_SEGMENTS_SWEEP) * N_PER_SEGMENT_COUNT
GOLDEN_ANGLE_RAD = math.pi * (3.0 - math.sqrt(5.0))
MEASURED_R_PASSIVE_M = 1.5e-3  # L2 n=16 bound; retained only as probe preflight evidence.
FAULT_THRESHOLD_M = 0.5e-3  # Product special-cause threshold, independent of bore geometry.
N2_SEGMENTS = 16
N2_EVIDENCE_COMMIT = "7081111"

# Progress at quarter-population intervals avoids 288 lines of worker noise.
PROGRESS_INTERVAL = N_PER_SEGMENT_COUNT // 4

_WORKER_MODEL: Optional[mujoco.MjModel] = None
_WORKER_DATA: Optional[mujoco.MjData] = None
_WORKER_N_SEGMENTS: Optional[int] = None


@dataclass(frozen=True)
class TrialSpec:
    trial_index: int
    n_segments: int
    population: str
    population_index: int
    commanded_bearing_rad: float


def wrap_angle_rad(angle: float) -> float:
    """Wrap an angle to the shortest signed representation."""
    return math.atan2(math.sin(angle), math.cos(angle))


def facet_phase_rad(
    bearing_rad: float,
    n_segments: int,
    *,
    centered: bool = False,
) -> float:
    """Bearing phase within one facet, optionally centered on its face."""
    assert n_segments > 0, "n_segments must be positive"
    period = 2.0 * math.pi / n_segments
    phase = bearing_rad % period
    if centered:
        phase = (phase + 0.5 * period) % period - 0.5 * period
    return phase


def build_trial_specs() -> list[TrialSpec]:
    """Build the exact stratified 288-trial N4 design."""
    specs: list[TrialSpec] = []
    trial_index = 0
    for n_segments in N_SEGMENTS_SWEEP:
        for population_index in range(N_COARSE):
            specs.append(TrialSpec(
                trial_index=trial_index,
                n_segments=n_segments,
                population="coarse",
                population_index=population_index,
                commanded_bearing_rad=population_index * GOLDEN_ANGLE_RAD,
            ))
            trial_index += 1

        facet_period = 2.0 * math.pi / n_segments
        fine_spacing = facet_period / N_FINE
        for population_index in range(N_FINE):
            specs.append(TrialSpec(
                trial_index=trial_index,
                n_segments=n_segments,
                population="fine",
                population_index=population_index,
                commanded_bearing_rad=(population_index + 0.5) * fine_spacing,
            ))
            trial_index += 1
    return specs


def probe_radius_m() -> float:
    """The N2/N4 commanded probe radius: exactly twice clearance."""
    return 2.0 * VALIDATED_LOOSE_V1.task.radial_clearance


def task_for_segments(n_segments: int) -> TaskSpec:
    """Return the canonical task with only the sweep parameter replaced."""
    assert n_segments in N_SEGMENTS_SWEEP, "unsupported N4 segment count"
    return replace(VALIDATED_LOOSE_V1.task, n_segments=n_segments)


def make_episode(n_segments: int, commanded_bearing_rad: float) -> EpisodeConfig:
    cfg = VALIDATED_LOOSE_V1
    return EpisodeConfig(
        task=task_for_segments(n_segments),
        solver=cfg.solver,
        gains=cfg.gains,
        insertion=cfg.insertion,
        z_start=cfg.z_start,
        initial_offset_r=probe_radius_m(),
        initial_offset_theta=commanded_bearing_rad,
        max_seconds=cfg.max_seconds,
        spiral_spec=None,
        depth_commit=cfg.depth_commit,
        control_decimation=cfg.control_decimation,
        control_delay_steps=cfg.control_delay_steps,
    )


def scene_key(episode: EpisodeConfig) -> tuple[object, ...]:
    """Exactly the EpisodeConfig fields consumed by MJCF construction."""
    return episode.task, episode.solver, episode.plate_tilt_deg


def assert_preflight(specs: list[TrialSpec]) -> None:
    """Reject sampling, geometry-reuse, or canonical-config drift before work."""
    cfg = VALIDATED_LOOSE_V1
    assert cfg.task.n_segments == N2_SEGMENTS, (
        "VALIDATED_LOOSE_V1 must remain at the n=16 measurement geometry"
    )
    assert len(specs) == N_TOTAL, "N4 sampling design has the wrong trial count"
    assert [spec.trial_index for spec in specs] == list(range(N_TOTAL)), (
        "N4 trial indices must be unique and contiguous"
    )
    assert cfg.task.radial_clearance < probe_radius_m() < MEASURED_R_PASSIVE_M, (
        "probe must reach the wall while remaining inside measured R_passive"
    )
    assert probe_radius_m() < cfg.r_cap_dyn, (
        "probe must remain inside the measured dynamic capture radius"
    )

    all_scene_keys: set[tuple[object, ...]] = set()
    for n_segments in N_SEGMENTS_SWEEP:
        selected = [spec for spec in specs if spec.n_segments == n_segments]
        coarse = [spec for spec in selected if spec.population == "coarse"]
        fine = [spec for spec in selected if spec.population == "fine"]
        assert len(coarse) == N_COARSE and len(fine) == N_FINE

        np.testing.assert_allclose(
            [spec.commanded_bearing_rad for spec in coarse],
            np.arange(N_COARSE, dtype=float) * GOLDEN_ANGLE_RAD,
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
        assert all(
            0.0 < spec.commanded_bearing_rad < facet_period for spec in fine
        ), "fine samples must lie strictly inside one facet period"

        episodes = [
            make_episode(n_segments, spec.commanded_bearing_rad)
            for spec in selected
        ]
        keys = {scene_key(episode) for episode in episodes}
        assert len(keys) == 1, (
            "one n_segments batch must have one worker-compatible scene"
        )
        all_scene_keys.update(keys)
        assert all(episode.spiral_spec is None for episode in episodes), (
            "N4 radial probes must disable spiral search"
        )
        assert all(
            replace(episode.task, n_segments=N2_SEGMENTS) == cfg.task
            for episode in episodes
        ), "n_segments must be the only task-field sweep"
        assert all(
            episode.solver == cfg.solver
            and episode.gains == cfg.gains
            and episode.insertion == cfg.insertion
            for episode in episodes
        ), "solver, gains, and axial profile must remain canonical"
    assert len(all_scene_keys) == len(N_SEGMENTS_SWEEP), (
        "N4 must build one distinct scene for each segment count"
    )


def init_worker(n_segments: int) -> None:
    """Build one geometry-compatible model/data pair per worker process."""
    global _WORKER_MODEL, _WORKER_DATA, _WORKER_N_SEGMENTS
    _WORKER_N_SEGMENTS = n_segments
    _WORKER_MODEL, _WORKER_DATA = build_episode_model_data(
        make_episode(n_segments, 0.0)
    )


def estimate_hole_xy(
    settled_tip_xy: np.ndarray,
    commanded_bearing_rad: float,
    clearance_m: float,
) -> np.ndarray:
    """Apply the unchanged N2 commanded-direction estimator."""
    assert settled_tip_xy.shape == (2,), "settled tip position must be xy"
    assert clearance_m > 0.0, "clearance must be positive"
    direction = np.array([
        math.cos(commanded_bearing_rad),
        math.sin(commanded_bearing_rad),
    ])
    return settled_tip_xy - clearance_m * direction


def _base_record(spec: TrialSpec) -> dict[str, object]:
    period = 2.0 * math.pi / spec.n_segments
    return {
        "trial_index": int(spec.trial_index),
        "n_segments": int(spec.n_segments),
        "population": spec.population,
        "population_index": int(spec.population_index),
        "commanded_bearing_rad": float(spec.commanded_bearing_rad),
        "commanded_bearing_wrapped_rad": float(
            spec.commanded_bearing_rad % (2.0 * math.pi)
        ),
        "facet_period_rad": float(period),
        "facet_phase_rad": float(facet_phase_rad(
            spec.commanded_bearing_rad, spec.n_segments
        )),
        "facet_phase_centered_rad": float(facet_phase_rad(
            spec.commanded_bearing_rad, spec.n_segments, centered=True
        )),
        "initial_offset_m": float(probe_radius_m()),
        "spiral_enabled": False,
        "pen_allow_m": float(VALIDATED_LOOSE_V1.pen_allow),
    }


def _excluded_record(
    spec: TrialSpec,
    reason: str,
    rollout_wall_time_s: float,
) -> dict[str, object]:
    return {
        **_base_record(spec),
        "success": False,
        "exclusion_reason": reason,
        "settled_tip_xy_m": None,
        "settled_tip_radius_m": None,
        "measured_tip_bearing_rad": None,
        "bearing_deflection_rad": None,
        "tilt_at_settle_rad": None,
        "true_hole_xy_m": None,
        "hole_est_xy_m": None,
        "estimator_error_xy_m": None,
        "estimator_error_norm_m": None,
        "time_to_seat_s": None,
        "rollout_wall_time_s": float(rollout_wall_time_s),
    }


def run_probe_trial(spec: TrialSpec) -> dict[str, object]:
    assert _WORKER_MODEL is not None and _WORKER_DATA is not None, (
        "worker MuJoCo state was not initialized"
    )
    assert _WORKER_N_SEGMENTS == spec.n_segments, (
        "worker model geometry does not match the submitted trial"
    )
    cfg = VALIDATED_LOOSE_V1
    episode = make_episode(spec.n_segments, spec.commanded_bearing_rad)
    started = time.perf_counter()
    try:
        result = run_episode(
            episode,
            pen_allow=cfg.pen_allow,
            model=_WORKER_MODEL,
            data=_WORKER_DATA,
        )
    except AssertionError as exc:
        return _excluded_record(
            spec,
            f"assertion: {exc}",
            time.perf_counter() - started,
        )
    rollout_wall_time_s = time.perf_counter() - started

    if not result.success:
        return _excluded_record(
            spec,
            "not seated before max_seconds",
            rollout_wall_time_s,
        )

    assert result.settled_tip_xy is not None
    assert result.tilt_at_settle_rad is not None
    assert result.true_hole_xy is not None
    tip_xy = result.settled_tip_xy
    true_hole_xy = result.true_hole_xy
    tip_from_hole = tip_xy - true_hole_xy
    measured_bearing = math.atan2(
        float(tip_from_hole[1]), float(tip_from_hole[0])
    )
    bearing_deflection = wrap_angle_rad(
        measured_bearing - spec.commanded_bearing_rad
    )
    hole_est_xy = estimate_hole_xy(
        tip_xy,
        spec.commanded_bearing_rad,
        cfg.task.radial_clearance,
    )
    estimator_error_xy = hole_est_xy - true_hole_xy

    return {
        **_base_record(spec),
        "success": True,
        "exclusion_reason": None,
        "settled_tip_xy_m": tip_xy.tolist(),
        "settled_tip_radius_m": float(np.linalg.norm(tip_from_hole)),
        "measured_tip_bearing_rad": float(measured_bearing),
        "bearing_deflection_rad": float(bearing_deflection),
        "tilt_at_settle_rad": float(result.tilt_at_settle_rad),
        "true_hole_xy_m": true_hole_xy.tolist(),
        "hole_est_xy_m": hole_est_xy.tolist(),
        "estimator_error_xy_m": estimator_error_xy.tolist(),
        "estimator_error_norm_m": float(np.linalg.norm(estimator_error_xy)),
        "time_to_seat_s": float(result.time_to_seat_s),
        "rollout_wall_time_s": float(rollout_wall_time_s),
    }


def distribution(values: np.ndarray) -> dict[str, float]:
    """Population statistics; all N4 standard deviations use ddof=0."""
    values = np.asarray(values, dtype=float)
    assert values.ndim == 1 and values.size > 0, (
        "distribution requires a non-empty vector"
    )
    assert np.all(np.isfinite(values)), "distribution values must be finite"
    return {
        "mean": float(values.mean()),
        "std_population": float(values.std(ddof=0)),
        "min": float(values.min()),
        "p95": float(np.percentile(values, 95)),
        "max": float(values.max()),
    }


def penetration_from_min_radius_m(
    clearance_m: float,
    minimum_settled_tip_radius_m: float,
) -> float:
    """Signed solver penetration; a negative result means radial separation."""
    assert clearance_m > 0.0
    assert math.isfinite(minimum_settled_tip_radius_m)
    return float(clearance_m - minimum_settled_tip_radius_m)


def predicted_vertex_radius_m(clearance_m: float, n_segments: int) -> float:
    """Peg-center radius at a vertex of a polygon with apothem clearance."""
    assert clearance_m > 0.0
    assert type(n_segments) is int and n_segments >= 3
    return float(clearance_m / math.cos(math.pi / n_segments))


def facet_relief_m(clearance_m: float, n_segments: int) -> float:
    """Polygon vertex relief above its clearance-radius apothem."""
    return predicted_vertex_radius_m(clearance_m, n_segments) - clearance_m


def decompose_estimator_error_m(
    estimator_error_xy_m: np.ndarray,
    commanded_bearing_rad: float,
) -> tuple[float, float]:
    """Return signed radial/tangential error in the commanded-probe frame."""
    error_xy = np.asarray(estimator_error_xy_m, dtype=float)
    assert error_xy.shape == (2,) and np.all(np.isfinite(error_xy))
    radial_axis = np.array([
        math.cos(commanded_bearing_rad), math.sin(commanded_bearing_rad),
    ])
    tangential_axis = np.array([-radial_axis[1], radial_axis[0]])
    return (
        float(error_xy @ radial_axis),
        float(error_xy @ tangential_axis),
    )


def _optional_distribution(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return distribution(np.asarray(values, dtype=float))


def _optional_rms(values: list[float]) -> float | None:
    if not values:
        return None
    vector = np.asarray(values, dtype=float)
    assert np.all(np.isfinite(vector))
    return float(math.sqrt(float(np.mean(vector ** 2))))


def summarize_population(records: list[dict[str, object]]) -> dict[str, object]:
    """Summarize one named design population without admitting failures."""
    successful = [record for record in records if bool(record["success"])]
    excluded = [record for record in records if not bool(record["success"])]

    estimator_error = [
        float(record["estimator_error_norm_m"]) for record in successful
    ]
    bearing = [float(record["bearing_deflection_rad"]) for record in successful]
    radius = [float(record["settled_tip_radius_m"]) for record in successful]
    components = [
        decompose_estimator_error_m(
            np.asarray(record["estimator_error_xy_m"], dtype=float),
            float(record["commanded_bearing_rad"]),
        )
        for record in successful
    ]
    radial_error = [component[0] for component in components]
    tangential_error = [component[1] for component in components]
    for norm, radial, tangential in zip(
        estimator_error, radial_error, tangential_error
    ):
        assert math.isclose(
            norm ** 2,
            radial ** 2 + tangential ** 2,
            rel_tol=2e-12,
            abs_tol=np.finfo(float).tiny,
        ), "radial/tangential components must reconstruct estimator error"
    time_to_seat = [float(record["time_to_seat_s"]) for record in successful]
    rollout_wall = [
        float(record["rollout_wall_time_s"])
        for record in records
        if record.get("rollout_wall_time_s") is not None
    ]
    return {
        "n_trials": len(records),
        "n_success": len(successful),
        "n_excluded": len(excluded),
        "capture_rate": len(successful) / len(records) if records else None,
        "exclusion_reasons": dict(sorted(Counter(
            str(record["exclusion_reason"]) for record in excluded
        ).items())),
        "estimator_error_norm_m": _optional_distribution(estimator_error),
        "signed_radial_estimator_error_m": _optional_distribution(radial_error),
        "absolute_radial_estimator_error_m": _optional_distribution(
            [abs(value) for value in radial_error]
        ),
        "radial_estimator_error_rms_m": _optional_rms(radial_error),
        "maximum_inward_radial_error_m": (
            None if not radial_error else float(max(0.0, -min(radial_error)))
        ),
        "signed_tangential_estimator_error_m": _optional_distribution(
            tangential_error
        ),
        "absolute_tangential_estimator_error_m": _optional_distribution(
            [abs(value) for value in tangential_error]
        ),
        "tangential_estimator_error_rms_m": _optional_rms(tangential_error),
        "signed_bearing_deflection_rad": _optional_distribution(bearing),
        "absolute_bearing_deflection_rad": _optional_distribution(
            [abs(value) for value in bearing]
        ),
        "settled_tip_radius_m": _optional_distribution(radius),
        "time_to_seat_s": _optional_distribution(time_to_seat),
        "rollout_wall_time_s": _optional_distribution(rollout_wall),
    }


def fit_harmonic(
    commanded_bearing_rad: np.ndarray,
    signed_deflection_rad: np.ndarray,
    order: int,
) -> dict[str, object]:
    """Fit offset + amplitude*sin(order*bearing + phase) by least squares."""
    commanded = np.asarray(commanded_bearing_rad, dtype=float)
    deflection = np.asarray(signed_deflection_rad, dtype=float)
    assert commanded.ndim == deflection.ndim == 1
    assert commanded.size == deflection.size and commanded.size >= 3
    assert type(order) is int and order > 0
    assert np.all(np.isfinite(commanded)) and np.all(np.isfinite(deflection))

    design = np.column_stack([
        np.ones_like(commanded),
        np.sin(order * commanded),
        np.cos(order * commanded),
    ])
    assert np.linalg.matrix_rank(design) == 3, "harmonic design is rank deficient"
    coefficients, *_ = np.linalg.lstsq(design, deflection, rcond=None)
    fitted = design @ coefficients
    residual = deflection - fitted
    sse = float(residual @ residual)
    centered = deflection - deflection.mean()
    sst = float(centered @ centered)
    r_squared = None if sst == 0.0 else float(1.0 - sse / sst)

    offset, sin_coefficient, cos_coefficient = map(float, coefficients)
    amplitude = float(math.hypot(sin_coefficient, cos_coefficient))
    # Below sqrt(eps) of the signal scale, phase is numerically meaningless.
    amplitude_floor = math.sqrt(np.finfo(float).eps) * max(
        1.0, float(np.max(np.abs(deflection)))
    )
    if amplitude <= amplitude_floor:
        phase_rad = None
        positive_peak_phase_rad = None
    else:
        phase_rad = wrap_angle_rad(math.atan2(
            cos_coefficient, sin_coefficient
        ))
        positive_peak_phase_rad = float(
            ((0.5 * math.pi - phase_rad) / order)
            % (2.0 * math.pi / order)
        )

    return {
        "model": "offset + amplitude*sin(order*bearing + phase)",
        "order": order,
        "n_observations": int(commanded.size),
        "offset_rad": offset,
        "sin_coefficient_rad": sin_coefficient,
        "cos_coefficient_rad": cos_coefficient,
        "amplitude_rad": amplitude,
        "phase_rad_harmonic_argument": phase_rad,
        "phase_rad_bearing_axis": (
            None if phase_rad is None else float(phase_rad / order)
        ),
        "positive_peak_facet_phase_rad": positive_peak_phase_rad,
        "sse_rad2": sse,
        "r_squared": r_squared,
    }


def fit_asymptote(points_m: dict[int, float]) -> dict[str, object]:
    """Fit max/mean error = A/n + B and a formal 95% CI on B."""
    assert len(points_m) == 4, "N4 asymptote fit requires exactly four points"
    n_segments = np.array(sorted(points_m), dtype=float)
    assert np.all(n_segments > 0.0) and np.unique(n_segments).size == 4
    error_m = np.array([points_m[int(n)] for n in n_segments], dtype=float)
    assert np.all(np.isfinite(error_m)) and np.all(error_m >= 0.0)

    design = np.column_stack([1.0 / n_segments, np.ones_like(n_segments)])
    assert np.linalg.matrix_rank(design) == 2, "asymptote design is rank deficient"
    coefficients, *_ = np.linalg.lstsq(design, error_m, rcond=None)
    a_m, b_m = map(float, coefficients)
    fitted_m = design @ coefficients
    residual_m = error_m - fitted_m
    sse_m2 = float(residual_m @ residual_m)
    centered = error_m - error_m.mean()
    sst_m2 = float(centered @ centered)
    r_squared = None if sst_m2 == 0.0 else float(1.0 - sse_m2 / sst_m2)

    degrees_of_freedom = int(error_m.size - design.shape[1])
    assert degrees_of_freedom == 2
    residual_variance_m2 = sse_m2 / degrees_of_freedom
    covariance = residual_variance_m2 * np.linalg.inv(design.T @ design)
    b_standard_error_m = float(math.sqrt(max(0.0, covariance[1, 1])))
    # Two-sided 95% Student-t critical value at df=4 observations-2 parameters.
    t_critical_95_df2 = 4.302652729911275
    ci_half_width_m = t_critical_95_df2 * b_standard_error_m

    return {
        "model": "error_m = A_m / n_segments + B_m",
        "population_provenance": None,
        "n_segments": [int(value) for value in n_segments],
        "observed_error_m": error_m.tolist(),
        "fitted_error_m": fitted_m.tolist(),
        "residual_m": residual_m.tolist(),
        "A_m": a_m,
        "B_m": b_m,
        "sse_m2": sse_m2,
        "r_squared": r_squared,
        "degrees_of_freedom": degrees_of_freedom,
        "B_standard_error_m": b_standard_error_m,
        "confidence_level": 0.95,
        "t_critical": t_critical_95_df2,
        "B_confidence_interval_m": [
            b_m - ci_half_width_m,
            b_m + ci_half_width_m,
        ],
    }


def _successful(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return [record for record in records if bool(record["success"])]


def _harmonic_or_unavailable(
    records: list[dict[str, object]],
    order: int,
) -> dict[str, object]:
    successful = _successful(records)
    if len(successful) < 3:
        return {
            "available": False,
            "reason": "fewer than three successful coarse trials",
            "order": order,
        }
    fitted = fit_harmonic(
        np.array([
            float(record["commanded_bearing_wrapped_rad"])
            for record in successful
        ]),
        np.array([
            float(record["bearing_deflection_rad"])
            for record in successful
        ]),
        order,
    )
    return {"available": True, **fitted}


def load_n2_reference() -> dict[str, object]:
    """Normalize committed N2 evidence into the N4 comparison schema."""
    summary = json.loads(N2_SUMMARY_PATH.read_text(encoding="utf-8"))
    raw_records = [
        json.loads(line)
        for line in N2_RAW_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ordered = sorted(raw_records, key=lambda record: int(record["trial_index"]))
    assert len(ordered) == 128  # The committed N2 golden-angle population.
    assert [int(record["trial_index"]) for record in ordered] == list(range(128))
    assert int(summary["config"]["n_segments"]) == N2_SEGMENTS

    normalized: list[dict[str, object]] = []
    for record in ordered:
        normalized.append({
            **record,
            "estimator_error_norm_m": record["error_norm_m"],
            "estimator_error_xy_m": record["error_xy_m"],
            "bearing_deflection_rad": record["bearing_error_rad"],
        })
    population = summarize_population(normalized)
    estimator = population["estimator_error_norm_m"]
    assert estimator is not None
    committed_estimator = summary["estimator_error_norm_m"]
    assert math.isclose(
        float(estimator["mean"]),
        float(committed_estimator["mean"]),
        rel_tol=0.0,
        abs_tol=1e-15,
    )
    assert math.isclose(
        float(estimator["max"]),
        float(committed_estimator["max"]),
        rel_tol=0.0,
        abs_tol=1e-15,
    )

    successful = _successful(normalized)
    harmonic = fit_harmonic(
        np.array([
            float(record["commanded_bearing_wrapped_rad"])
            for record in successful
        ]),
        np.array([
            float(record["bearing_deflection_rad"])
            for record in successful
        ]),
        N2_SEGMENTS,
    )
    radius = population["settled_tip_radius_m"]
    assert radius is not None
    clearance = VALIDATED_LOOSE_V1.task.radial_clearance
    predicted_vertex = predicted_vertex_radius_m(clearance, N2_SEGMENTS)
    return {
        "n_segments": N2_SEGMENTS,
        "population": "N2 golden-angle, 128 trials; no fine population",
        "source_commit": N2_EVIDENCE_COMMIT,
        "source_raw_path": str(N2_RAW_PATH.relative_to(ROOT)),
        "source_summary_path": str(N2_SUMMARY_PATH.relative_to(ROOT)),
        "metrics": population,
        "penetration_m": penetration_from_min_radius_m(
            clearance, float(radius["min"])
        ),
        "predicted_vertex_radius_m": float(predicted_vertex),
        "measured_vertex_radius_m": float(radius["max"]),
        "vertex_radius_difference_m": float(radius["max"] - predicted_vertex),
        "harmonic": {"available": True, **harmonic},
        "rollout_wall_time_note": "not recorded by N2",
    }


def _full_config(worker_count: int) -> dict[str, object]:
    """Capture every explicit N4 input and the coupled derived quantities."""
    cfg = VALIDATED_LOOSE_V1
    episode = make_episode(N_SEGMENTS_SWEEP[0], 0.0)
    compiled_model, compiled_data = build_episode_model_data(episode)
    bore_id = mujoco.mj_name2id(
        compiled_model, mujoco.mjtObj.mjOBJ_GEOM, "bore_0"
    )
    dof_ids = peg_dof_ids(compiled_model)
    spiral = cfg.spiral
    theta_exhaust = (spiral.r_spiral - spiral.a) / spiral.b
    tangential_path_m = (
        spiral.r_spiral ** 2 - spiral.a ** 2
    ) / (2.0 * spiral.b)
    return {
        "canonical_name": cfg.name,
        "canonical_n_segments_unchanged": cfg.task.n_segments,
        "swept_parameter": {
            "name": "task.n_segments",
            "values": list(N_SEGMENTS_SWEEP),
        },
        "task": {
            "peg_radius_m": cfg.task.peg_radius,
            "radial_clearance_m": cfg.task.radial_clearance,
            "hole_radius_m": cfg.task.hole_radius,
            "chamfer_width_m": cfg.task.chamfer_width,
            "plate_thickness_m": cfg.task.plate_thickness,
            "plate_outer_face_radius_m": cfg.task.plate_outer_radius,
            "peg_length_m": cfg.task.peg_length,
            "peg_mass_kg": cfg.task.peg_mass,
            "friction_sliding": cfg.task.friction,
            "blind_hole": cfg.task.blind_hole,
            # Ring tiles overlap by 15% at their tangential reference radius.
            "ring_tangential_overlap_factor": 1.15,
            "geom_count_by_n_segments": {
                str(n): 3 * n + 2 for n in N_SEGMENTS_SWEEP
            },
        },
        "solver": {
            "timestep_s": cfg.solver.timestep,
            "cone": cfg.solver.cone,
            "integrator": cfg.solver.integrator,
            "solref": list(cfg.solver.solref),
            "solimp": list(cfg.solver.solimp),
            "compiled_at_n32": {
                "n_geom": compiled_model.ngeom,
                "gravity_m_per_s2": compiled_model.opt.gravity.tolist(),
                "bore_friction_sliding_torsional_rolling": (
                    compiled_model.geom_friction[bore_id].tolist()
                ),
                "bore_margin_m": float(compiled_model.geom_margin[bore_id]),
                "bore_contact_dimension": int(compiled_model.geom_condim[bore_id]),
                "bore_solref": compiled_model.geom_solref[bore_id].tolist(),
                "bore_solimp_full": compiled_model.geom_solimp[bore_id].tolist(),
                "peg_dof_damping": compiled_model.dof_damping[dof_ids].tolist(),
                "peg_dof_frictionloss": (
                    compiled_model.dof_frictionloss[dof_ids].tolist()
                ),
                "peg_dof_armature": compiled_model.dof_armature[dof_ids].tolist(),
                "forward_time_s": float(compiled_data.time),
            },
        },
        "controller": {
            "k_xy_N_per_m": cfg.gains.k_xy,
            "k_z_search_N_per_m": cfg.insertion.k_z_search,
            "k_z_seating_N_per_m": cfg.gains.k_z,
            "k_rx_Nm_per_rad": cfg.gains.k_rx,
            "k_ry_Nm_per_rad": cfg.gains.k_ry,
            "k_rz_Nm_per_rad": cfg.gains.k_rz,
            "damping": "D_i = 2*sqrt(K_i*M_ii), recomputed from live mass matrix",
            "desired_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
            "bias_compensation": True,
            "tilt_guard_rad": MAX_TILT_RAD,
        },
        "axial_profile": {
            "f_search_N": cfg.insertion.f_search,
            "f_target_N": cfg.insertion.f_target,
            "f_yield_N": cfg.insertion.f_yield,
            "v_descent_m_per_s": cfg.insertion.v_descent,
            "search_descent_allowance_m": cfg.insertion.descent_allowance,
            "search_raw_target_z_m": -cfg.insertion.descent_allowance,
            "seating_raw_target_z_m": (
                -cfg.task.plate_thickness
                - cfg.insertion.f_target / cfg.gains.k_z
            ),
            "force_ramp_rate_N_per_s": cfg.insertion.v_descent * cfg.gains.k_z,
            "force_continuous_handoff": episode.force_continuous_handoff,
        },
        "gate": {
            "depth_commit_m": cfg.depth_commit,
            "commit_release_fraction": COMMIT_RELEASE_FRACTION,
            "release_depth_m": COMMIT_RELEASE_FRACTION * cfg.depth_commit,
            "z_flat_band_m": Z_FLAT_BAND,
            "z_flat_tau_s": Z_FLAT_TAU_S,
            "z_flat_alpha_per_control_tick": min(
                1.0,
                cfg.control_decimation * cfg.solver.timestep / Z_FLAT_TAU_S,
            ),
            "seed_z_flat_from_cad": episode.seed_z_flat_from_cad,
        },
        "episode": {
            "z_start_m": cfg.z_start,
            "initial_offset_r_m": probe_radius_m(),
            "max_seconds": cfg.max_seconds,
            "physics_steps": int(cfg.max_seconds / cfg.solver.timestep),
            "seat_persist_s": episode.seat_persist_s,
            "seat_persist_steps": max(
                1, int(round(episode.seat_persist_s / cfg.solver.timestep))
            ),
            "plate_tilt_deg": episode.plate_tilt_deg,
            "target_depth_m": episode.target_depth,
            "target_depth_margin_m": TARGET_DEPTH_MARGIN_M,
        },
        "grading": {
            "pen_allow_m": cfg.pen_allow,
            "lateral_limit_m": cfg.task.radial_clearance + cfg.pen_allow,
            "tip_and_mouth_checked": True,
            "f_wedge_N": None,
            "f_wedge_note": "infinite/deferred; JSON null avoids non-finite data",
        },
        "control_timing": {
            "control_decimation": cfg.control_decimation,
            "control_period_s": cfg.control_decimation * cfg.solver.timestep,
            "control_rate_Hz": 1.0 / (
                cfg.control_decimation * cfg.solver.timestep
            ),
            "control_delay_steps": cfg.control_delay_steps,
        },
        "probe": {
            "estimator": "settled_tip_xy - clearance*u(commanded_bearing)",
            "probe_radius_m": probe_radius_m(),
            "spiral_spec": None,
            "spiral_enabled": False,
            "measured_R_passive_m_at_n16": MEASURED_R_PASSIVE_M,
            "measured_R_cap_dyn_m_at_n16": cfg.r_cap_dyn,
            "fault_threshold_m": FAULT_THRESHOLD_M,
        },
        "canonical_spiral_inactive": {
            "v_t_m_per_s": cfg.v_t,
            "a_m": spiral.a,
            "b_m": spiral.b,
            "pitch_m": 2.0 * math.pi * spiral.b,
            "outer_radius_m": spiral.r_spiral,
            "theta_exhaust_rad": theta_exhaust,
            "integrated_tangential_path_m": tangential_path_m,
            "continuous_coverage_time_s": tangential_path_m / cfg.v_t,
        },
        "sampling": {
            "coarse_per_n": N_COARSE,
            "coarse_sequence": "k*pi*(3-sqrt(5)), k=0..63",
            "fine_per_n": N_FINE,
            "fine_sequence": "(k+0.5)*(2*pi/n)/32, k=0..31",
            "trials_per_n": N_PER_SEGMENT_COUNT,
            "total_trials": N_TOTAL,
            "statistics_std_ddof": 0,
            "percentile_method": "numpy.percentile default linear method",
        },
        "execution": {
            "process_pool_workers": worker_count,
            "one_pool_per_n_segments": True,
            "worker_model_build": "one build_episode_model_data call per worker",
            "raw_writer": "main process only",
        },
    }


def _validate_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    ordered = sorted(records, key=lambda record: int(record["trial_index"]))
    specs = build_trial_specs()
    assert len(ordered) == N_TOTAL, "N4 result set is incomplete"
    assert [int(record["trial_index"]) for record in ordered] == list(range(N_TOTAL)), (
        "N4 result indices are duplicated or missing"
    )
    for record, spec in zip(ordered, specs):
        assert int(record["n_segments"]) == spec.n_segments
        assert str(record["population"]) == spec.population
        assert int(record["population_index"]) == spec.population_index
        assert math.isclose(
            float(record["commanded_bearing_rad"]),
            spec.commanded_bearing_rad,
            rel_tol=0.0,
            abs_tol=8.0 * np.finfo(float).eps,
        )
        success = bool(record["success"])
        settled_keys = (
            "settled_tip_xy_m",
            "settled_tip_radius_m",
            "measured_tip_bearing_rad",
            "bearing_deflection_rad",
            "tilt_at_settle_rad",
            "true_hole_xy_m",
            "hole_est_xy_m",
            "estimator_error_xy_m",
            "estimator_error_norm_m",
            "time_to_seat_s",
        )
        if success:
            assert record["exclusion_reason"] is None
            assert all(record[key] is not None for key in settled_keys)
        else:
            assert record["exclusion_reason"] is not None
            assert all(record[key] is None for key in settled_keys)
    return ordered


def _fit_if_complete(
    points_m: dict[int, float | None],
    population_provenance: str,
) -> dict[str, object]:
    missing = [n for n, value in points_m.items() if value is None]
    if missing:
        return {
            "available": False,
            "reason": f"missing successful measurement at n={missing}",
            "population_provenance": population_provenance,
        }
    fit = fit_asymptote({n: float(value) for n, value in points_m.items()})
    fit["population_provenance"] = population_provenance
    return {"available": True, **fit}


def prepare_asymptote_fit_for_summary(
    fit: dict[str, object],
) -> dict[str, object]:
    """Suppress an invalid intercept while retaining auditable fit diagnostics."""
    if not bool(fit["available"]):
        return fit
    observed = np.asarray(fit["observed_error_m"], dtype=float)
    monotonic_nonincreasing = bool(np.all(np.diff(observed) <= 0.0))
    fit["monotonic_nonincreasing"] = monotonic_nonincreasing
    fit["valid_for_floor_inference"] = monotonic_nonincreasing
    if not monotonic_nonincreasing:
        fit["invalidity_reason"] = (
            "observed error is non-monotonic, so the A/n+B intercept is a "
            "fitting artifact rather than a physical asymptote"
        )
        # Retain A, fitted values, residuals, SSE, and R² so the rejected fit
        # remains auditable without publishing its non-physical intercept.
        for key in (
            "B_m",
            "B_standard_error_m",
            "B_confidence_interval_m",
            "confidence_level",
            "t_critical",
        ):
            fit.pop(key, None)
    return fit


def _round_up_to_whole_micrometer(value_m: float) -> float:
    """Conservative external-claim rounding for a positive measured envelope."""
    assert value_m >= 0.0 and math.isfinite(value_m)
    return math.ceil(value_m * 1e6) * 1e-6


def analyze(records: list[dict[str, object]], worker_count: int) -> dict[str, object]:
    ordered = _validate_records(records)
    cfg = VALIDATED_LOOSE_V1
    n2_reference = load_n2_reference()
    by_n: dict[str, object] = {}

    for n_segments in N_SEGMENTS_SWEEP:
        selected = [
            record for record in ordered
            if int(record["n_segments"]) == n_segments
        ]
        coarse = [record for record in selected if record["population"] == "coarse"]
        fine = [record for record in selected if record["population"] == "fine"]
        population_metrics = {
            "coarse": summarize_population(coarse),
            "fine": summarize_population(fine),
            "pooled": summarize_population(selected),
        }
        pooled_radius = population_metrics["pooled"]["settled_tip_radius_m"]
        fine_radius = population_metrics["fine"]["settled_tip_radius_m"]
        predicted_vertex = predicted_vertex_radius_m(
            cfg.task.radial_clearance, n_segments
        )
        penetration = None if pooled_radius is None else (
            penetration_from_min_radius_m(
                cfg.task.radial_clearance, float(pooled_radius["min"])
            )
        )
        measured_max_radius = (
            None if pooled_radius is None else float(pooled_radius["max"])
        )
        fine_max_radius = None if fine_radius is None else float(fine_radius["max"])

        batch_wall_values = {
            float(record["n_batch_wall_time_s"]) for record in selected
        }
        worker_values = {int(record["process_pool_workers"]) for record in selected}
        assert len(batch_wall_values) == 1, "batch wall time must be stable within n"
        assert worker_values == {worker_count}, "raw worker count disagrees with analysis"
        by_n[str(n_segments)] = {
            "n_segments": n_segments,
            "populations": population_metrics,
            "penetration_m_from_pooled_minimum": penetration,
            "facet_relief_m": facet_relief_m(
                cfg.task.radial_clearance, n_segments
            ),
            "predicted_vertex_radius_m": float(predicted_vertex),
            "measured_max_settled_tip_radius_m_pooled": measured_max_radius,
            "fine_max_settled_tip_radius_m": fine_max_radius,
            "pooled_max_minus_predicted_vertex_radius_m": (
                None if measured_max_radius is None
                else float(measured_max_radius - predicted_vertex)
            ),
            "coarse_nth_harmonic": _harmonic_or_unavailable(
                coarse, n_segments
            ),
            "wall_clock": {
                "batch_total_s_including_pool_startup_and_worker_model_build": (
                    batch_wall_values.pop()
                ),
                "sum_of_individual_rollout_wall_time_s": float(sum(
                    float(record["rollout_wall_time_s"]) for record in selected
                )),
                "individual_rollout_wall_time_s": population_metrics[
                    "pooled"
                ]["rollout_wall_time_s"],
            },
        }

    n2_estimator = n2_reference["metrics"]["estimator_error_norm_m"]
    assert n2_estimator is not None
    max_points: dict[int, float | None] = {
        N2_SEGMENTS: float(n2_estimator["max"]),
    }
    fine_mean_points: dict[int, float | None] = {
        N2_SEGMENTS: float(n2_estimator["mean"]),
    }
    pooled_mean_points: dict[int, float | None] = {
        N2_SEGMENTS: float(n2_estimator["mean"]),
    }
    for n_segments in N_SEGMENTS_SWEEP:
        cell = by_n[str(n_segments)]
        fine_metrics = cell["populations"]["fine"]
        pooled_metrics = cell["populations"]["pooled"]
        fine_error = fine_metrics["estimator_error_norm_m"]
        pooled_error = pooled_metrics["estimator_error_norm_m"]
        # A missing peak-phase sample can bias max_error and B downward. The
        # formal fits therefore describe only the complete designed grids,
        # never a survivor-conditioned subset after a capture falsification.
        fine_complete = fine_metrics["n_success"] == N_FINE
        pooled_complete = pooled_metrics["n_success"] == N_PER_SEGMENT_COUNT
        max_points[n_segments] = (
            None if fine_error is None or not fine_complete
            else float(fine_error["max"])
        )
        fine_mean_points[n_segments] = (
            None if fine_error is None or not fine_complete
            else float(fine_error["mean"])
        )
        pooled_mean_points[n_segments] = (
            None if pooled_error is None or not pooled_complete
            else float(pooled_error["mean"])
        )

    max_fit = prepare_asymptote_fit_for_summary(_fit_if_complete(
        max_points,
        "n=16 N2 golden-128 maximum; n>=32 N4 fine-population maximum",
    ))
    fine_mean_fit = prepare_asymptote_fit_for_summary(_fit_if_complete(
        fine_mean_points,
        "n=16 N2 golden-128 mean; n>=32 N4 fine-population mean",
    ))
    pooled_mean_fit = prepare_asymptote_fit_for_summary(_fit_if_complete(
        pooled_mean_points,
        "sensitivity only: n=16 N2 golden-128 mean; n>=32 N4 pooled mean",
    ))

    plateau_n = (64, 128)
    plateau_per_n = {}
    for n_segments in plateau_n:
        pooled_error = by_n[str(n_segments)]["populations"][
            "pooled"
        ]["estimator_error_norm_m"]
        assert pooled_error is not None
        plateau_per_n[str(n_segments)] = {
            "pooled_mean_error_m": float(pooled_error["mean"]),
            "pooled_max_error_m": float(pooled_error["max"]),
        }
    plateau_mean_envelope_m = max(
        cell["pooled_mean_error_m"] for cell in plateau_per_n.values()
    )
    plateau_max_envelope_m = max(
        cell["pooled_max_error_m"] for cell in plateau_per_n.values()
    )
    rounded_plateau_mean_m = _round_up_to_whole_micrometer(
        plateau_mean_envelope_m
    )
    rounded_plateau_max_m = _round_up_to_whole_micrometer(
        plateau_max_envelope_m
    )

    crossover_by_n = {
        str(N2_SEGMENTS): {
            "facet_relief_m": facet_relief_m(
                cfg.task.radial_clearance, N2_SEGMENTS
            ),
            "penetration_m": float(n2_reference["penetration_m"]),
            "nth_harmonic_r_squared": float(
                n2_reference["harmonic"]["r_squared"]
            ),
        },
    }
    for n_segments in N_SEGMENTS_SWEEP:
        cell = by_n[str(n_segments)]
        crossover_by_n[str(n_segments)] = {
            "facet_relief_m": float(cell["facet_relief_m"]),
            "penetration_m": float(cell["penetration_m_from_pooled_minimum"]),
            "nth_harmonic_r_squared": float(
                cell["coarse_nth_harmonic"]["r_squared"]
            ),
        }

    batch_totals = [
        float(by_n[str(n)]["wall_clock"][
            "batch_total_s_including_pool_startup_and_worker_model_build"
        ])
        for n in N_SEGMENTS_SWEEP
    ]
    return {
        "config": _full_config(worker_count),
        "n_trials": len(ordered),
        "n_success": sum(bool(record["success"]) for record in ordered),
        "n_excluded": sum(not bool(record["success"]) for record in ordered),
        "by_n": by_n,
        "n2_reference": n2_reference,
        "asymptote_fits": {
            "headline_max_error": max_fit,
            "mean_error": fine_mean_fit,
            "pooled_mean_error_sensitivity": pooled_mean_fit,
        },
        "measured_plateau": {
            "definition": (
                "conservative envelope of pooled error at n_segments=64 and 128"
            ),
            "per_n": plateau_per_n,
            "measured_mean_envelope_m": plateau_mean_envelope_m,
            "measured_max_envelope_m": plateau_max_envelope_m,
            "external_claim_rounded_mean_m": rounded_plateau_mean_m,
            "external_claim_rounded_max_m": rounded_plateau_max_m,
            "fault_threshold_m": FAULT_THRESHOLD_M,
            "fault_threshold_margin_over_rounded_mean": (
                FAULT_THRESHOLD_M / rounded_plateau_mean_m
            ),
            "fault_threshold_margin_over_rounded_max": (
                FAULT_THRESHOLD_M / rounded_plateau_max_m
            ),
        },
        "facet_to_penetration_crossover": {
            "facet_relief_formula": "clearance*(sec(pi/n_segments)-1)",
            "by_n": crossover_by_n,
            "relief_below_penetration_between_n_segments": [16, 32],
            "nth_harmonic_collapse_between_n_segments": [32, 64],
            "measured_mechanism": (
                "penetration first exceeds facet relief by n=32 while the facet "
                "harmonic remains strong; contact-solver behavior dominates by "
                "n=64, where the nth-harmonic fit collapses"
            ),
            "unresolved": "n=128 signed tangential DC offset",
        },
        "wall_clock": {
            "measurement_total_s_sum_of_n_batches": float(sum(batch_totals)),
            "per_n_batch_total_s": {
                str(n): batch_totals[index]
                for index, n in enumerate(N_SEGMENTS_SWEEP)
            },
        },
    }


def _save_figure(fig: plt.Figure, stem: Path) -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        stem.with_suffix(".png"), dpi=180, bbox_inches="tight",
        metadata={"Software": "matplotlib"},
    )
    fig.savefig(
        stem.with_suffix(".svg"), bbox_inches="tight", metadata={"Date": None}
    )
    svg_path = stem.with_suffix(".svg")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_path.read_text().splitlines()) + "\n",
        encoding="utf-8",
    )
    plt.close(fig)


def _available_fit(summary: dict[str, object], key: str) -> dict[str, object]:
    fit = summary["asymptote_fits"][key]
    assert bool(fit["available"]), f"{key} fit unavailable: {fit.get('reason')}"
    return fit


def _save_facet_diagnostics(ordered: list[dict[str, object]]) -> None:
    """Always save falsification diagnostics, even when fits are unavailable."""
    fig, axes = plt.subplots(1, 3, figsize=(14.5, 4.3), constrained_layout=True)
    for axis, n_segments in zip(axes, N_SEGMENTS_SWEEP):
        fine = [
            record for record in ordered
            if int(record["n_segments"]) == n_segments
            and record["population"] == "fine"
            and bool(record["success"])
        ]
        phase_deg = np.degrees([
            float(record["facet_phase_centered_rad"]) for record in fine
        ])
        deflection_deg = np.degrees([
            float(record["bearing_deflection_rad"]) for record in fine
        ])
        axis.scatter(phase_deg, deflection_deg, s=28, alpha=0.85)
        axis.axhline(0.0, color="#333333", linewidth=1)
        axis.set(
            xlabel="Commanded facet phase (deg)",
            title=f"n={n_segments}; seated {len(fine)}/{N_FINE}",
        )
        axis.grid(True, alpha=0.2)
    axes[0].set_ylabel("Settled − commanded bearing (deg)")
    fig.suptitle("N4 fine population: bearing deflection within one facet")
    _save_figure(fig, BEARING_FIGURE_STEM)

    fig, axes = plt.subplots(1, 3, figsize=(14.5, 4.3), constrained_layout=True)
    clearance_um = 1e6 * VALIDATED_LOOSE_V1.task.radial_clearance
    for axis, n_segments in zip(axes, N_SEGMENTS_SWEEP):
        fine = [
            record for record in ordered
            if int(record["n_segments"]) == n_segments
            and record["population"] == "fine"
            and bool(record["success"])
        ]
        phase_deg = np.degrees([
            facet_phase_rad(
                float(record["measured_tip_bearing_rad"]),
                n_segments,
                centered=True,
            )
            for record in fine
        ])
        radius_um = 1e6 * np.array([
            float(record["settled_tip_radius_m"]) for record in fine
        ])
        vertex_um = 1e6 * predicted_vertex_radius_m(
            VALIDATED_LOOSE_V1.task.radial_clearance, n_segments
        )
        axis.scatter(phase_deg, radius_um, s=28, alpha=0.85)
        axis.axhline(
            clearance_um, color="#C84A42", linestyle="--", label="c"
        )
        axis.axhline(
            vertex_um, color="#3569A8", linestyle=":", label="c/cos(π/n)"
        )
        axis.set(
            xlabel="Settled-bearing facet phase (deg)",
            title=f"n={n_segments}; seated {len(fine)}/{N_FINE}",
        )
        axis.grid(True, alpha=0.2)
    axes[0].set_ylabel("Settled tip radius (µm)")
    axes[-1].legend(loc="best")
    fig.suptitle("N4 fine population: settled radius within one facet")
    _save_figure(fig, RADIUS_FIGURE_STEM)


def _save_unavailable_fit_figure(
    stem: Path,
    title: str,
    summary: dict[str, object],
) -> None:
    reasons = []
    for key in ("headline_max_error", "mean_error"):
        fit = summary["asymptote_fits"][key]
        if not bool(fit["available"]):
            reasons.append(f"{key}: {fit['reason']}")
    fig, ax = plt.subplots(figsize=(7.6, 4.8), constrained_layout=True)
    ax.axis("off")
    ax.text(
        0.5,
        0.5,
        "Asymptote fit unavailable\n" + "\n".join(reasons),
        ha="center",
        va="center",
        transform=ax.transAxes,
    )
    ax.set_title(title)
    _save_figure(fig, stem)


def _fit_intercept_for_plot_m(fit: dict[str, object]) -> float:
    """Recover the rejected intercept only to draw the mandated fit overlay."""
    n0 = float(fit["n_segments"][0])
    fitted0 = float(fit["fitted_error_m"][0])
    return fitted0 - float(fit["A_m"]) / n0


def save_figures(
    records: list[dict[str, object]],
    summary: dict[str, object],
) -> None:
    ordered = _validate_records(records)
    _save_facet_diagnostics(ordered)
    fit_entries = summary["asymptote_fits"]
    if not (
        bool(fit_entries["headline_max_error"]["available"])
        and bool(fit_entries["mean_error"]["available"])
    ):
        _save_unavailable_fit_figure(
            ESTIMATOR_FIGURE_STEM,
            "N4 estimator-error asymptote",
            summary,
        )
        _save_unavailable_fit_figure(
            RESIDUAL_FIGURE_STEM,
            "N4 asymptote residuals",
            summary,
        )
        return
    max_fit = _available_fit(summary, "headline_max_error")
    mean_fit = _available_fit(summary, "mean_error")

    n_values = np.array(max_fit["n_segments"], dtype=float)
    max_error_um = 1e6 * np.array(max_fit["observed_error_m"], dtype=float)
    mean_error_um = 1e6 * np.array(mean_fit["observed_error_m"], dtype=float)
    fit_n = np.geomspace(n_values.min(), n_values.max(), 300)
    max_intercept_m = _fit_intercept_for_plot_m(max_fit)
    mean_intercept_m = _fit_intercept_for_plot_m(mean_fit)
    max_curve_um = 1e6 * (
        float(max_fit["A_m"]) / fit_n + max_intercept_m
    )
    mean_curve_um = 1e6 * (
        float(mean_fit["A_m"]) / fit_n + mean_intercept_m
    )

    fig, ax = plt.subplots(figsize=(7.6, 5.2), constrained_layout=True)
    ax.loglog(n_values, max_error_um, "o", color="#C84A42", label="Fine maximum")
    ax.loglog(fit_n[max_curve_um > 0.0], max_curve_um[max_curve_um > 0.0],
              color="#C84A42", linestyle="--", label="Formal maximum fit (invalid)")
    ax.loglog(n_values, mean_error_um, "s", color="#3569A8", label="Fine mean")
    ax.loglog(fit_n[mean_curve_um > 0.0], mean_curve_um[mean_curve_um > 0.0],
              color="#3569A8", linestyle="--", label="Formal mean fit (invalid)")
    plateau = summary["measured_plateau"]
    ax.plot(
        [64, 128],
        [1e6 * float(plateau["external_claim_rounded_max_m"])] * 2,
        color="#C84A42",
        linestyle=":",
        label="Measured max plateau ≈7 µm",
    )
    ax.plot(
        [64, 128],
        [1e6 * float(plateau["external_claim_rounded_mean_m"])] * 2,
        color="#3569A8",
        linestyle=":",
        label="Measured mean plateau ≈3 µm",
    )
    ax.set(
        xlabel="Bore segments n",
        ylabel="Estimator error |hole_est − true_hole_xy| (µm)",
        xticks=n_values,
    )
    ax.get_xaxis().set_major_formatter(ScalarFormatter())
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()
    ax.set_title("N4 crossover: formal fit rejected, measured plateau retained")
    _save_figure(fig, ESTIMATOR_FIGURE_STEM)

    fig, ax = plt.subplots(figsize=(7.6, 4.8), constrained_layout=True)
    max_residual_um = 1e6 * np.array(max_fit["residual_m"], dtype=float)
    mean_residual_um = 1e6 * np.array(mean_fit["residual_m"], dtype=float)
    ax.axhline(0.0, color="#333333", linewidth=1)
    ax.plot(n_values, max_residual_um, "o-", color="#C84A42", label="Fine maximum")
    ax.plot(n_values, mean_residual_um, "s-", color="#3569A8", label="Fine mean")
    ax.set(
        xlabel="Bore segments n",
        ylabel="Observed − fitted error (µm)",
        xticks=n_values,
        title="Residuals of rejected N4 A/n + B fits",
    )
    ax.grid(True, alpha=0.25)
    ax.legend()
    _save_figure(fig, RESIDUAL_FIGURE_STEM)


def run_measurement(
    specs: list[TrialSpec],
    worker_count: int,
) -> list[dict[str, object]]:
    assert worker_count >= 1, "worker_count must be positive"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    with RAW_PATH.open("w", encoding="utf-8") as raw_file:
        for n_segments in N_SEGMENTS_SWEEP:
            selected = [spec for spec in specs if spec.n_segments == n_segments]
            batch_records: list[dict[str, object]] = []
            batch_started = time.perf_counter()
            with ProcessPoolExecutor(
                max_workers=worker_count,
                initializer=init_worker,
                initargs=(n_segments,),
            ) as executor:
                futures = {
                    executor.submit(run_probe_trial, spec): spec.trial_index
                    for spec in selected
                }
                for completed, future in enumerate(as_completed(futures), start=1):
                    expected_index = futures[future]
                    record = future.result()
                    assert int(record["trial_index"]) == expected_index
                    batch_records.append(record)
                    if (
                        completed % PROGRESS_INTERVAL == 0
                        or completed == N_PER_SEGMENT_COUNT
                    ):
                        print(
                            f"n={n_segments}: completed "
                            f"{completed}/{N_PER_SEGMENT_COUNT}"
                        )
            batch_wall_time_s = time.perf_counter() - batch_started
            for record in batch_records:
                record["n_batch_wall_time_s"] = float(batch_wall_time_s)
                record["process_pool_workers"] = int(worker_count)
                raw_file.write(json.dumps(record, allow_nan=False) + "\n")
            raw_file.flush()
            records.extend(batch_records)
            print(f"n={n_segments}: batch wall {batch_wall_time_s:.3f}s")
    return records


def load_raw_records() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in RAW_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def print_config(worker_count: int) -> None:
    cfg = VALIDATED_LOOSE_V1
    episode = make_episode(N_SEGMENTS_SWEEP[0], 0.0)
    print(
        f"config={cfg.name}; canonical_n={cfg.task.n_segments}; "
        f"n_sweep={N_SEGMENTS_SWEEP}"
    )
    print(
        f"task: peg_r={cfg.task.peg_radius}m clearance={cfg.task.radial_clearance}m "
        f"chamfer={cfg.task.chamfer_width}m plate={cfg.task.plate_thickness}m "
        f"peg_length={cfg.task.peg_length}m mass={cfg.task.peg_mass}kg "
        f"friction={cfg.task.friction} blind_hole={cfg.task.blind_hole}"
    )
    print(
        f"solver: dt={cfg.solver.timestep}s cone={cfg.solver.cone} "
        f"integrator={cfg.solver.integrator} solref={cfg.solver.solref} "
        f"solimp={cfg.solver.solimp}"
    )
    print(
        f"control: k_xy={cfg.gains.k_xy} k_z_search={cfg.insertion.k_z_search} "
        f"k_z_seating={cfg.gains.k_z} k_rx={cfg.gains.k_rx} "
        f"k_ry={cfg.gains.k_ry} k_rz={cfg.gains.k_rz}"
    )
    print(
        f"episode: z_start={cfg.z_start}m target_depth={episode.target_depth}m "
        f"pen_allow={cfg.pen_allow}m max_seconds={cfg.max_seconds}s "
        f"decimation={cfg.control_decimation} delay={cfg.control_delay_steps}"
    )
    print(
        f"probe: radius={probe_radius_m()}m spiral=None; sampling="
        f"{N_COARSE} coarse + {N_FINE} fine per n; workers={worker_count}"
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("worker count must be >= 1")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="regenerate summary and figures from the committed raw JSONL",
    )
    parser.add_argument(
        "--workers",
        type=_positive_int,
        default=os.cpu_count() or 1,
        help="process-pool worker count (default: logical CPU count)",
    )
    args = parser.parse_args()

    specs = build_trial_specs()
    assert_preflight(specs)
    if args.analyze_only:
        records = load_raw_records()
        raw_worker_counts = {
            int(record["process_pool_workers"]) for record in records
        }
        assert len(raw_worker_counts) == 1
        measured_worker_count = raw_worker_counts.pop()
    else:
        measured_worker_count = args.workers
        print_config(measured_worker_count)
        records = run_measurement(specs, measured_worker_count)
    if args.analyze_only:
        print_config(measured_worker_count)

    summary = analyze(records, measured_worker_count)
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    save_figures(records, summary)
    print(
        f"success={summary['n_success']}/{summary['n_trials']} "
        f"excluded={summary['n_excluded']}"
    )
    print(f"raw={RAW_PATH.relative_to(ROOT)}")
    print(f"summary={SUMMARY_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
