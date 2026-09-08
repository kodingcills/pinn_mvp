"""N5: antipodal-pair cancellation with injected part-tolerance clearance DC.

Locked specification (end of docs/N4_FACET_CONVERGENCE_REPORT.md):

    n=64, injected part-tolerance DC in {-10, -5, 0, +5, +10} um, 64
    antipodal pairs per level. Pre-registered targets: ~5 um single-probe
    norm, ~2.7 um antipodal-mean error, radial penetration cancellation in
    the mean, and a difference term of injected DC plus a constant of twice
    the measured n=64 penetration (2 x 2.963 um).

The injected DC is a PHYSICAL change: each level builds its scene with
radial_clearance = 0.5 mm + dc, while the estimator reduction keeps assuming
the nominal 0.5 mm and the controller is untouched. Each probe is one
search-disabled N2-style episode; the two probes of a pair are separate
episodes and the pair is formed only in analysis.

N5c is the geometric control condition, defined by this campaign (no prior
pre-registration exists): identical to N5b at dc=0 except the minus probe is
shifted by half a bore facet (pi/n), so opposite walls no longer share the
same local facet phase and relief must NOT cancel in the pair mean. It
discriminates "cancellation because antipodal walls are the same facet" from
"cancellation because anything averages".

Physics is stepped only by pegsim.rollout.run_episode. Worker processes reuse
one MjModel/MjData pair per level; only the main process writes result files.
Deterministic: no stochastic element exists, so records carry seed=null.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from pegsim.rollout import (
    EpisodeConfig,
    VALIDATED_LOOSE_V1,
    build_episode_model_data,
    run_episode,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"
FIGURES_DIR = ROOT / "docs" / "figures"
RAW_PATH = RESULTS_DIR / "n5_antipodal_raw.jsonl"
SUMMARY_PATH = RESULTS_DIR / "n5_antipodal_summary.json"
FIGURE_PAIRS_STEM = FIGURES_DIR / "n5_antipodal_pairs"
FIGURE_DIFFERENCE_STEM = FIGURES_DIR / "n5_antipodal_difference"

N4_SUMMARY_PATH = RESULTS_DIR / "n4_facet_convergence_summary.json"
N_PAIRS = 64
DC_LEVELS_M = (-10e-6, -5e-6, 0.0, 5e-6, 10e-6)
GOLDEN_ANGLE_RAD = math.pi * (3.0 - math.sqrt(5.0))
MEASURED_R_PASSIVE_M = 1.5e-3
FAULT_THRESHOLD_M = 0.5e-3
PROGRESS_INTERVAL = 50
SCHEMA_VERSION = "n5_antipodal_v1"

_WORKER_MODEL = None
_WORKER_DATA = None
_WORKER_TASK = None


@dataclass(frozen=True)
class TrialSpec:
    trial_id: int
    campaign: str  # "n5b" antipodal, "n5c" half-facet control
    dc_level_m: float
    pair_index: int
    probe_side: str  # "plus" | "minus"
    probe_axis_rad: float
    pair_bearing_rad: float


def nominal_clearance_m() -> float:
    return VALIDATED_LOOSE_V1.task.radial_clearance


def probe_radius_m() -> float:
    """Commanded probe radius: twice the NOMINAL clearance (N2 primitive)."""
    return 2.0 * nominal_clearance_m()


def scene_n_segments() -> int:
    return 64


def true_clearance_m(dc_level_m: float) -> float:
    return nominal_clearance_m() + dc_level_m


def level_task(dc_level_m: float):
    from dataclasses import replace

    return replace(
        VALIDATED_LOOSE_V1.task,
        n_segments=scene_n_segments(),
        radial_clearance=true_clearance_m(dc_level_m),
    )


def minus_probe_bearing_rad(pair_bearing_rad: float, campaign: str) -> float:
    """Minus-side commanded bearing. N5b: exactly antipodal. N5c: antipodal
    shifted by half a facet so the opposite wall sits at a different local
    facet phase."""
    if campaign == "n5b":
        return pair_bearing_rad + math.pi
    if campaign == "n5c":
        return pair_bearing_rad + math.pi + math.pi / scene_n_segments()
    raise ValueError(f"unknown campaign {campaign!r}")


def build_trial_specs() -> list[TrialSpec]:
    specs: list[TrialSpec] = []
    trial_id = 0
    for dc_level in DC_LEVELS_M:
        campaigns = ("n5b", "n5c") if dc_level == 0.0 else ("n5b",)
        for campaign in campaigns:
            for pair_index in range(N_PAIRS):
                pair_bearing = pair_index * GOLDEN_ANGLE_RAD
                for side, axis in (
                    ("plus", pair_bearing),
                    ("minus", minus_probe_bearing_rad(pair_bearing, campaign)),
                ):
                    specs.append(
                        TrialSpec(
                            trial_id=trial_id,
                            campaign=campaign,
                            dc_level_m=dc_level,
                            pair_index=pair_index,
                            probe_side=side,
                            probe_axis_rad=axis,
                            pair_bearing_rad=pair_bearing,
                        )
                    )
                    trial_id += 1
    return specs


@lru_cache(maxsize=1)
def load_penetration_m() -> float:
    """The filter's known observation bias: the MEASURED n=64 penetration
    from committed N4 evidence. Loaded, never hardcoded."""
    summary = json.loads(N4_SUMMARY_PATH.read_text(encoding="utf-8"))
    penetration = summary["by_n"]["64"]["penetration_m_from_pooled_minimum"]
    value = float(penetration)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"N4 penetration constant is not usable: {value}")
    return value


def make_episode(spec: TrialSpec) -> EpisodeConfig:
    cfg = VALIDATED_LOOSE_V1
    return EpisodeConfig(
        task=level_task(spec.dc_level_m),
        solver=cfg.solver,
        gains=cfg.gains,
        insertion=cfg.insertion,
        z_start=cfg.z_start,
        initial_offset_r=probe_radius_m(),
        initial_offset_theta=spec.probe_axis_rad,
        max_seconds=cfg.max_seconds,
        spiral_spec=None,
        depth_commit=cfg.depth_commit,
        control_decimation=cfg.control_decimation,
        control_delay_steps=cfg.control_delay_steps,
    )


def scene_key(episode: EpisodeConfig) -> tuple[object, ...]:
    return episode.task, episode.solver, episode.plate_tilt_deg


def assert_preflight(specs: list[TrialSpec]) -> None:
    cfg = VALIDATED_LOOSE_V1
    radius = probe_radius_m()
    clearances = [true_clearance_m(dc) for dc in DC_LEVELS_M]
    assert min(clearances) < radius < MEASURED_R_PASSIVE_M, (
        "probe must exceed every true clearance to reach the wall and remain "
        "inside the measured passive-capture radius"
    )
    assert radius < cfg.r_cap_dyn, (
        "probe must remain inside the measured dynamic capture radius"
    )
    assert cfg.task.n_segments == 16, "canonical task must remain n=16"
    assert len({level_task(dc) for dc in DC_LEVELS_M}) == len(DC_LEVELS_M)

    keys: set[tuple[object, ...]] = set()
    for spec in specs:
        episode = make_episode(spec)
        keys.add(scene_key(episode))
        assert episode.spiral_spec is None, "N5 probes disable spiral search"
        assert episode.initial_offset_theta == spec.probe_axis_rad
        expected_minus = minus_probe_bearing_rad(spec.pair_bearing_rad, spec.campaign)
        if spec.probe_side == "minus":
            assert spec.probe_axis_rad == expected_minus
        else:
            assert spec.probe_axis_rad == spec.pair_bearing_rad
    assert len(keys) == len(DC_LEVELS_M), (
        "one scene per DC level; worker model reuse would otherwise be invalid"
    )

    penetration = load_penetration_m()
    assert 0.0 < penetration < 0.1 * nominal_clearance_m(), (
        "the loaded N4 penetration constant must be a small fraction of the "
        "nominal clearance to serve as the known pair bias"
    )


def init_worker(dc_level_m: float) -> None:
    global _WORKER_MODEL, _WORKER_DATA, _WORKER_TASK
    _WORKER_MODEL, _WORKER_DATA = build_episode_model_data(make_episode(
        TrialSpec(
            trial_id=-1,
            campaign="n5b",
            dc_level_m=dc_level_m,
            pair_index=0,
            probe_side="plus",
            probe_axis_rad=0.0,
            pair_bearing_rad=0.0,
        )
    ))
    _WORKER_TASK = level_task(dc_level_m)


def estimate_hole_xy(
    settled_tip_xy: np.ndarray,
    commanded_bearing_rad: float,
    clearance_m: float,
) -> np.ndarray:
    assert settled_tip_xy.shape == (2,)
    assert clearance_m > 0.0
    u_probe = np.array([
        math.cos(commanded_bearing_rad), math.sin(commanded_bearing_rad),
    ])
    return settled_tip_xy - clearance_m * u_probe


def decompose_error(error_xy: np.ndarray, frame_bearing_rad: float) -> tuple[float, float]:
    """Radial (along u, positive outward) and tangential (positive CCW)
    components in the COMMANDED plus-bearing frame (N4 D7 convention)."""
    u = np.array([math.cos(frame_bearing_rad), math.sin(frame_bearing_rad)])
    u_perp = np.array([-math.sin(frame_bearing_rad), math.cos(frame_bearing_rad)])
    return float(error_xy @ u), float(error_xy @ u_perp)


def _failure_record(spec: TrialSpec, reason: str) -> dict[str, object]:
    return {
        "trial_id": spec.trial_id,
        "campaign": spec.campaign,
        "dc_level_m": spec.dc_level_m,
        "true_clearance_m": true_clearance_m(spec.dc_level_m),
        "pair_index": spec.pair_index,
        "probe_side": spec.probe_side,
        "probe_axis_rad": spec.probe_axis_rad,
        "pair_bearing_rad": spec.pair_bearing_rad,
        "success": False,
        "exclusion_reason": reason,
        "settled_tip_xy_m": None,
        "settled_tip_radius_m": None,
        "measured_tip_bearing_rad": None,
        "bearing_error_rad": None,
        "tilt_at_settle_rad": None,
        "hole_est_xy_m": None,
        "true_hole_xy_m": None,
        "error_xy_m": None,
        "error_norm_m": None,
        "error_radial_m": None,
        "error_tangential_m": None,
        "time_to_seat_s": None,
        "settled_contact_geoms": None,
        "seed": None,
        "schema_version": SCHEMA_VERSION,
    }


def run_probe_trial(spec: TrialSpec) -> dict[str, object]:
    assert _WORKER_MODEL is not None and _WORKER_DATA is not None, (
        "worker MuJoCo state was not initialized"
    )
    assert _WORKER_TASK == level_task(spec.dc_level_m), (
        "worker scene does not match this trial's DC level"
    )
    cfg = VALIDATED_LOOSE_V1
    episode = make_episode(spec)

    try:
        result = run_episode(
            episode,
            pen_allow=cfg.pen_allow,
            model=_WORKER_MODEL,
            data=_WORKER_DATA,
        )
    except AssertionError as exc:
        return _failure_record(spec, f"assertion: {exc}")

    if not result.success:
        return _failure_record(spec, "not seated before max_seconds")

    assert result.settled_tip_xy is not None
    assert result.true_hole_xy is not None
    tip_xy = result.settled_tip_xy
    hole_est = estimate_hole_xy(
        tip_xy, spec.probe_axis_rad, nominal_clearance_m(),
    )
    error_xy = hole_est - result.true_hole_xy
    radial, tangential = decompose_error(error_xy, spec.pair_bearing_rad)
    measured_bearing = math.atan2(float(tip_xy[1]), float(tip_xy[0]))
    bearing_error = measured_bearing - spec.probe_axis_rad
    bearing_error = math.atan2(math.sin(bearing_error), math.cos(bearing_error))

    return {
        "trial_id": spec.trial_id,
        "campaign": spec.campaign,
        "dc_level_m": spec.dc_level_m,
        "true_clearance_m": true_clearance_m(spec.dc_level_m),
        "pair_index": spec.pair_index,
        "probe_side": spec.probe_side,
        "probe_axis_rad": spec.probe_axis_rad,
        "pair_bearing_rad": spec.pair_bearing_rad,
        "success": True,
        "exclusion_reason": None,
        "settled_tip_xy_m": tip_xy.tolist(),
        "settled_tip_radius_m": float(np.linalg.norm(tip_xy - result.true_hole_xy)),
        "measured_tip_bearing_rad": float(measured_bearing),
        "bearing_error_rad": float(bearing_error),
        "tilt_at_settle_rad": (
            float(result.tilt_at_settle_rad)
            if result.tilt_at_settle_rad is not None else None
        ),
        "hole_est_xy_m": hole_est.tolist(),
        "true_hole_xy_m": result.true_hole_xy.tolist(),
        "error_xy_m": error_xy.tolist(),
        "error_norm_m": float(np.linalg.norm(error_xy)),
        "error_radial_m": radial,
        "error_tangential_m": tangential,
        "time_to_seat_s": float(result.time_to_seat_s),
        "settled_contact_geoms": (
            [list(pair) for pair in result.settled_contact_geoms]
            if result.settled_contact_geoms is not None else None
        ),
        "seed": None,
        "schema_version": SCHEMA_VERSION,
    }


def distribution(values: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(values.mean()),
        "std_population": float(values.std(ddof=0)),
        "min": float(values.min()),
        "p95": float(np.percentile(values, 95)),
        "max": float(values.max()),
    }


@dataclass(frozen=True)
class PairMeasurement:
    campaign: str
    dc_level_m: float
    pair_index: int
    pair_bearing_rad: float
    hole_est_plus_xy_m: np.ndarray
    hole_est_minus_xy_m: np.ndarray
    error_plus_xy_m: np.ndarray
    error_minus_xy_m: np.ndarray


def assemble_pairs(
    records: list[dict[str, object]],
) -> tuple[list[PairMeasurement], list[dict[str, object]]]:
    """Group successful records into complete pairs; excluded trials are
    returned separately, never silently dropped."""
    by_key: dict[tuple[str, float, int], dict[str, dict[str, object]]] = {}
    for record in records:
        key = (record["campaign"], float(record["dc_level_m"]), int(record["pair_index"]))
        by_key.setdefault(key, {})[str(record["probe_side"])] = record

    pairs: list[PairMeasurement] = []
    incomplete: list[dict[str, object]] = []
    for key in sorted(by_key, key=lambda k: (k[0], k[1], k[2])):
        sides = by_key[key]
        campaign, dc_level, pair_index = key
        if "plus" not in sides or "minus" not in sides:
            missing = "plus" if "plus" not in sides else "minus"
            incomplete.append({
                "campaign": campaign,
                "dc_level_m": dc_level,
                "pair_index": pair_index,
                "reason": f"missing {missing} side",
            })
            continue
        plus, minus = sides["plus"], sides["minus"]
        bearing = float(plus["pair_bearing_rad"])
        pairs.append(
            PairMeasurement(
                campaign=campaign,
                dc_level_m=dc_level,
                pair_index=pair_index,
                pair_bearing_rad=bearing,
                hole_est_plus_xy_m=np.asarray(plus["hole_est_xy_m"], dtype=float),
                hole_est_minus_xy_m=np.asarray(minus["hole_est_xy_m"], dtype=float),
                error_plus_xy_m=np.asarray(plus["error_xy_m"], dtype=float),
                error_minus_xy_m=np.asarray(minus["error_xy_m"], dtype=float),
            )
        )
    return pairs, incomplete


def pair_quantities(pair: PairMeasurement) -> dict[str, float]:
    """Antipodal-mean error and the half-difference along the pair axis u.

    hole_est(+u) = h + (r_plus - c_nom) u, hole_est(-u) = h - (r_minus - c_nom) u
    (N5b), so pair_mean - h = ((r_plus - r_minus)/2) u and the half-difference
    is (r_plus + r_minus - 2 c_nom)/2 = dc - penetration + relief terms. For
    N5c the minus bearing is rotated by half a facet, so both quantities carry
    an explicit cos(pi/n) projection on the minus side; the pair axis remains
    the plus commanded bearing."""
    u = np.array([
        math.cos(pair.pair_bearing_rad), math.sin(pair.pair_bearing_rad),
    ])
    u_perp = np.array([-u[1], u[0]])
    mean_error = (pair.error_plus_xy_m + pair.error_minus_xy_m) / 2.0
    half_difference = (
        (pair.hole_est_plus_xy_m - pair.hole_est_minus_xy_m) @ u
    ) / 2.0
    return {
        "pair_mean_error_radial_m": float(mean_error @ u),
        "pair_mean_error_tangential_m": float(mean_error @ u_perp),
        "pair_mean_error_norm_m": float(np.linalg.norm(mean_error)),
        "pair_half_difference_along_u_m": float(half_difference),
        "pair_full_difference_along_u_m": float(2.0 * half_difference),
        "dc_estimate_m": float(
            half_difference + load_penetration_m()
        ),
    }


def _campaign_level_pairs(
    pairs: list[PairMeasurement], campaign: str
) -> dict[float, list[PairMeasurement]]:
    grouped: dict[float, list[PairMeasurement]] = {}
    for pair in pairs:
        if pair.campaign == campaign:
            grouped.setdefault(pair.dc_level_m, []).append(pair)
    return grouped


def analyze(records: list[dict[str, object]]) -> dict[str, object]:
    ordered = sorted(records, key=lambda record: int(record["trial_id"]))
    expected = build_trial_specs()
    assert len(ordered) == len(expected) == len(DC_LEVELS_M) * N_PAIRS * 2 + N_PAIRS * 2, (
        "N5 result set is incomplete"
    )
    assert [int(record["trial_id"]) for record in ordered] == [
        spec.trial_id for spec in expected
    ], "N5 trial ids are duplicated or missing"

    successful = [record for record in ordered if bool(record["success"])]
    excluded = [record for record in ordered if not bool(record["success"])]
    assert successful, "N5 produced no successful measurements"
    penetration = load_penetration_m()

    pairs, incomplete = assemble_pairs(successful)
    n5b_pairs = [pair for pair in pairs if pair.campaign == "n5b"]
    n5c_pairs = [pair for pair in pairs if pair.campaign == "n5c"]

    by_level: dict[str, dict[str, object]] = {}
    difference_fit_points_x = []
    difference_fit_points_y = []
    for dc_level in DC_LEVELS_M:
        level_singles = [
            record for record in successful
            if record["campaign"] == "n5b" and float(record["dc_level_m"]) == dc_level
        ]
        level_pairs = [p for p in n5b_pairs if p.dc_level_m == dc_level]
        if not level_singles or not level_pairs:
            continue
        error_norm = np.array([float(r["error_norm_m"]) for r in level_singles])
        radial = np.array([float(r["error_radial_m"]) for r in level_singles])
        tangential = np.array([float(r["error_tangential_m"]) for r in level_singles])
        pair_radial = np.array([
            pair_quantities(p)["pair_mean_error_radial_m"] for p in level_pairs
        ])
        pair_tangential = np.array([
            pair_quantities(p)["pair_mean_error_tangential_m"] for p in level_pairs
        ])
        pair_norm = np.array([
            pair_quantities(p)["pair_mean_error_norm_m"] for p in level_pairs
        ])
        full_difference = np.array([
            pair_quantities(p)["pair_full_difference_along_u_m"] for p in level_pairs
        ])
        dc_estimate = np.array([
            pair_quantities(p)["dc_estimate_m"] for p in level_pairs
        ])
        difference_fit_points_x.append(dc_level)
        difference_fit_points_y.append(float(full_difference.mean()))
        by_level[f"{dc_level * 1e6:+.0f}um"] = {
            "dc_level_m": dc_level,
            "n_probes": len(level_singles),
            "n_pairs": len(level_pairs),
            "single_probe_error_norm_m": distribution(error_norm),
            "single_probe_error_radial_m": distribution(radial),
            "single_probe_error_tangential_m": distribution(tangential),
            "pair_mean_error_norm_m": distribution(pair_norm),
            "pair_mean_error_radial_m": distribution(pair_radial),
            "pair_mean_error_tangential_m": distribution(pair_tangential),
            "pair_full_difference_along_u_m": distribution(full_difference),
            "dc_estimate_m": distribution(dc_estimate),
            "dc_estimate_error_m": distribution(dc_estimate - dc_level),
        }

    dc_points = np.asarray(difference_fit_points_x)
    difference_means = np.asarray(difference_fit_points_y)
    slope, intercept = np.polyfit(dc_points, difference_means, 1)
    predicted_intercept_m = -2.0 * penetration

    n5c_by_quantity: dict[str, dict[str, float]] = {}
    n5c_pair_radial_phase_rows = []
    if n5c_pairs:
        quantities = [pair_quantities(pair) for pair in n5c_pairs]
        facet_period = 2.0 * math.pi / scene_n_segments()
        for pair, quantity in zip(n5c_pairs, quantities):
            facet_phase = (
                pair.pair_bearing_rad + 0.5 * facet_period
            ) % facet_period - 0.5 * facet_period
            n5c_pair_radial_phase_rows.append({
                "facet_phase_rad": facet_phase,
                "pair_mean_error_radial_m": quantity["pair_mean_error_radial_m"],
            })
        for quantity_name in (
            "pair_mean_error_radial_m",
            "pair_mean_error_tangential_m",
            "pair_mean_error_norm_m",
            "pair_half_difference_along_u_m",
        ):
            values = np.array([q[quantity_name] for q in quantities])
            n5c_by_quantity[quantity_name] = distribution(values)

    def n5b_dc0(name: str) -> dict[str, float]:
        return by_level["+0um"][name]

    return {
        "schema_version": SCHEMA_VERSION,
        "config": {
            "name": cfg_name(),
            "scene_n_segments": scene_n_segments(),
            "nominal_clearance_m": nominal_clearance_m(),
            "dc_levels_m": list(DC_LEVELS_M),
            "probe_radius_m": probe_radius_m(),
            "n_pairs_per_level": N_PAIRS,
            "bearing_schedule": "theta_k = k * golden_angle, minus = +pi (n5b) "
                                "or +pi + pi/n (n5c control)",
            "penetration_bias_m_from_n4": penetration,
            "predicted_full_difference_intercept_m": predicted_intercept_m,
            "measured_r_passive_m": MEASURED_R_PASSIVE_M,
            "fault_threshold_m": FAULT_THRESHOLD_M,
            "seed": None,
            "determinism_note": (
                "no stochastic element: bearings, levels, and controller are "
                "deterministic; seed is null by design"
            ),
            "n5c_provenance": (
                "geometric control condition defined by this campaign; no "
                "prior pre-registration exists"
            ),
        },
        "n_trials": len(ordered),
        "n_success": len(successful),
        "n_excluded": len(excluded),
        "exclusion_reasons": dict(Counter(
            str(record["exclusion_reason"]) for record in excluded
        )),
        "incomplete_pairs": incomplete,
        "by_level": by_level,
        "difference_fit": {
            "slope_m_per_m": float(slope),
            "intercept_m": float(intercept),
            "predicted_intercept_m": predicted_intercept_m,
            "dc_points_m": dc_points.tolist(),
            "measured_difference_means_m": difference_means.tolist(),
        },
        "pre_registered_checks": {
            "single_probe_norm_at_dc0_max_m": n5b_dc0("single_probe_error_norm_m")["max"],
            "antipodal_mean_error_dc0_max_m": n5b_dc0("pair_mean_error_norm_m")["max"],
            "antipodal_mean_error_dc0_mean_m": n5b_dc0("pair_mean_error_norm_m")["mean"],
            "pair_mean_radial_dc0_mean_m": n5b_dc0("pair_mean_error_radial_m")["mean"],
            "difference_slope_predicted": 2.0,
            "difference_slope_measured": float(slope),
            "difference_intercept_measured_m": float(intercept),
            "difference_intercept_predicted_m": predicted_intercept_m,
        },
        "n5c_control": {
            "quantities": n5c_by_quantity,
            "pair_radial_vs_facet_phase": n5c_pair_radial_phase_rows,
        },
    }


def cfg_name() -> str:
    return VALIDATED_LOOSE_V1.name


def save_figures(summary: dict[str, object]) -> None:
    by_level = summary["by_level"]
    levels = [float(level["dc_level_m"]) for level in by_level.values()]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), constrained_layout=True)
    single_max = [
        1e6 * level["single_probe_error_norm_m"]["max"] for level in by_level.values()
    ]
    pair_mean_max = [
        1e6 * level["pair_mean_error_norm_m"]["max"] for level in by_level.values()
    ]
    pair_radial_mean = [
        1e6 * level["pair_mean_error_radial_m"]["mean"] for level in by_level.values()
    ]
    axes[0].plot(np.array(levels) * 1e6, single_max, "o-", label="Single-probe max |error|")
    axes[0].plot(np.array(levels) * 1e6, pair_mean_max, "s-", label="Pair-mean max |error|")
    axes[0].plot(np.array(levels) * 1e6, pair_radial_mean, "^-", label="Pair-mean radial (signed)")
    axes[0].axhline(0.0, color="#333333", linewidth=1)
    axes[0].set(xlabel="Injected clearance DC (µm)", ylabel="Error (µm)")
    axes[0].set_title("Pair mean cancels DC, relief, and penetration")
    axes[0].legend()

    fit = summary["difference_fit"]
    predicted = 2.0 * np.asarray(levels) + fit["predicted_intercept_m"]
    axes[1].plot(
        np.array(levels) * 1e6, 1e6 * np.asarray(fit["measured_difference_means_m"]),
        "o-", label="Measured full difference",
    )
    axes[1].plot(
        np.array(levels) * 1e6, 1e6 * predicted, "--",
        label="Predicted 2·DC − 2·penetration",
    )
    axes[1].set(xlabel="Injected clearance DC (µm)", ylabel="Full difference along u (µm)")
    axes[1].set_title("Difference term tracks DC with penetration constant")
    axes[1].legend()

    fig.suptitle("N5 antipodal cancellation, injected part-tolerance DC", fontsize=14)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_PAIRS_STEM.with_suffix(".png"), dpi=180, bbox_inches="tight")
    fig.savefig(FIGURE_PAIRS_STEM.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)

    n5c = summary["n5c_control"]
    if n5c["pair_radial_vs_facet_phase"]:
        rows = n5c["pair_radial_vs_facet_phase"]
        phases_deg = np.degrees([row["facet_phase_rad"] for row in rows])
        radial_um = 1e6 * np.array([row["pair_mean_error_radial_m"] for row in rows])
        order = np.argsort(phases_deg)
        fig_c, ax_c = plt.subplots(figsize=(7, 4.2), constrained_layout=True)
        ax_c.scatter(phases_deg, radial_um, s=18, alpha=0.8, label="N5c half-facet control")
        dc0_radial = float(by_level["+0um"]["pair_mean_error_radial_m"]["mean"])
        ax_c.axhline(1e6 * dc0_radial, color="#C84A42", linestyle="--",
                     label="N5b (antipodal) pair-mean radial")
        ax_c.axhline(0.0, color="#333333", linewidth=1)
        ax_c.set(xlabel="Facet phase of pair bearing (deg)",
                 ylabel="Pair-mean radial error (µm)")
        ax_c.set_title("Relief cancellation requires same-facet antipodal walls")
        ax_c.legend()
        fig_c.savefig(FIGURE_DIFFERENCE_STEM.with_suffix(".png"), dpi=180,
                      bbox_inches="tight")
        fig_c.savefig(FIGURE_DIFFERENCE_STEM.with_suffix(".svg"), bbox_inches="tight")
        plt.close(fig_c)


def print_config() -> None:
    cfg = VALIDATED_LOOSE_V1
    print(f"config={cfg.name} scene_n={scene_n_segments()} "
          f"nominal_clearance={nominal_clearance_m()}m")
    print(f"dc_levels={[f'{dc * 1e6:+.0f}um' for dc in DC_LEVELS_M]} "
          f"pairs_per_level={N_PAIRS} probe_radius={probe_radius_m()}m "
          f"spiral_enabled=False")
    print(f"penetration_bias={load_penetration_m() * 1e6:.6f}um (from committed N4)")
    print(f"n5c_control=minus bearing +pi + pi/{scene_n_segments()} at dc=0 only")


def run_campaign(workers: int) -> list[dict[str, object]]:
    specs = build_trial_specs()
    assert_preflight(specs)
    print_config()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    with RAW_PATH.open("w", encoding="utf-8") as raw_file:
        for dc_level in DC_LEVELS_M:
            level_specs = [s for s in specs if s.dc_level_m == dc_level]
            completed_in_level = 0
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=init_worker,
                initargs=(dc_level,),
            ) as executor:
                futures = {
                    executor.submit(run_probe_trial, spec): spec
                    for spec in level_specs
                }
                for future in as_completed(futures):
                    record = future.result()
                    assert record["trial_id"] == futures[future].trial_id
                    raw_file.write(json.dumps(record, allow_nan=False) + "\n")
                    raw_file.flush()
                    records.append(record)
                    completed_in_level += 1
                    total_done = len(records)
                    if (
                        completed_in_level % PROGRESS_INTERVAL == 0
                        or completed_in_level == len(level_specs)
                    ):
                        print(
                            f"dc={dc_level * 1e6:+.0f}um: "
                            f"{completed_in_level}/{len(level_specs)} "
                            f"(total {total_done}/{len(specs)})"
                        )
    return records


def load_raw_records() -> list[dict[str, object]]:
    records = [
        json.loads(line)
        for line in RAW_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records:
        raise RuntimeError(f"{RAW_PATH} contains no records")
    return records


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=14)
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="analyze the committed raw JSONL without running physics",
    )
    args = parser.parse_args()

    if args.analyze_only:
        records = load_raw_records()
    else:
        records = run_campaign(args.workers)

    summary = analyze(records)
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    save_figures(summary)
    print(f"success={summary['n_success']}/{summary['n_trials']} "
          f"excluded={summary['n_excluded']}")
    checks = summary["pre_registered_checks"]
    print(
        "dc=0: single-probe max {sp:.3f}um | pair-mean max {pm:.3f}um | "
        "pair-mean radial mean {pr:+.4f}um".format(
            sp=checks["single_probe_norm_at_dc0_max_m"] * 1e6,
            pm=checks["antipodal_mean_error_dc0_max_m"] * 1e6,
            pr=checks["pair_mean_radial_dc0_mean_m"] * 1e6,
        )
    )
    print(
        "difference: slope={sl:.6f} (predicted 2.0) "
        "intercept={ic:+.3f}um (predicted {ip:+.3f}um)".format(
            sl=checks["difference_slope_measured"],
            ic=checks["difference_intercept_measured_m"] * 1e6,
            ip=checks["difference_intercept_predicted_m"] * 1e6,
        )
    )
    print(f"raw={RAW_PATH.relative_to(ROOT)}")
    print(f"summary={SUMMARY_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
