"""N2: characterize the search-disabled radial probe measurement primitive.

This is an offline measurement driver, not a production probe cycle. Physics
is stepped only by pegsim.rollout.run_episode. Worker processes reuse one
MjModel/MjData pair; only the main process writes result files.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import mujoco
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
RAW_PATH = RESULTS_DIR / "n2_probe_raw.jsonl"
SUMMARY_PATH = RESULTS_DIR / "n2_probe_summary.json"
FIGURE_STEM = FIGURES_DIR / "n2_probe_primitive"

N_TRIALS = 128  # N2 work order: golden-angle coverage of all 16 facet phases.
PROGRESS_INTERVAL = 25  # N2 work order: progress without per-trial output noise.
GOLDEN_ANGLE_RAD = math.pi * (3.0 - math.sqrt(5.0))
MEASURED_R_PASSIVE_M = 1.5e-3  # L2: 8/8 capture through 1.5 mm, 0/8 at 1.75 mm.
FAULT_THRESHOLD_M = 0.5e-3  # Product special-cause threshold, independent of geometry.

_WORKER_MODEL: Optional[mujoco.MjModel] = None
_WORKER_DATA: Optional[mujoco.MjData] = None


def probe_radius_m() -> float:
    """The commanded probe radius: exactly twice the task clearance."""
    return 2.0 * VALIDATED_LOOSE_V1.task.radial_clearance


def make_episode(commanded_bearing_rad: float) -> EpisodeConfig:
    cfg = VALIDATED_LOOSE_V1
    return EpisodeConfig(
        task=cfg.task,
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
    """Exactly the EpisodeConfig fields that enter MJCF construction."""
    return episode.task, episode.solver, episode.plate_tilt_deg


def assert_preflight(commanded_bearings_rad: np.ndarray) -> None:
    cfg = VALIDATED_LOOSE_V1
    radius = probe_radius_m()
    assert cfg.task.radial_clearance < radius < MEASURED_R_PASSIVE_M, (
        "probe must exceed clearance to reach the wall and remain inside the "
        "measured passive-capture radius"
    )
    assert radius < cfg.r_cap_dyn, (
        "probe must remain inside the measured dynamic capture radius"
    )
    episodes = [make_episode(float(bearing)) for bearing in commanded_bearings_rad]
    assert len({scene_key(episode) for episode in episodes}) == 1, (
        "N2 trial parameters changed an MJCF-defining field; one model per "
        "worker would no longer be valid"
    )
    assert all(episode.spiral_spec is None for episode in episodes), (
        "N2 radial probes must disable spiral search"
    )


def init_worker() -> None:
    global _WORKER_MODEL, _WORKER_DATA
    _WORKER_MODEL, _WORKER_DATA = build_episode_model_data(make_episode(0.0))


def wrap_angle_rad(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def estimate_hole_xy(
    settled_tip_xy: np.ndarray,
    commanded_bearing_rad: float,
    clearance_m: float,
) -> np.ndarray:
    """Recover hole center from wall contact along a commanded direction."""
    assert settled_tip_xy.shape == (2,), "settled tip position must be an xy vector"
    assert clearance_m > 0.0, "clearance must be positive"
    u_probe = np.array([
        math.cos(commanded_bearing_rad), math.sin(commanded_bearing_rad),
    ])
    return settled_tip_xy - clearance_m * u_probe


def run_probe_trial(trial_index: int, commanded_bearing_rad: float) -> dict[str, object]:
    assert _WORKER_MODEL is not None and _WORKER_DATA is not None, (
        "worker MuJoCo state was not initialized"
    )
    cfg = VALIDATED_LOOSE_V1
    episode = make_episode(commanded_bearing_rad)
    base_record: dict[str, object] = {
        "trial_index": int(trial_index),
        "commanded_bearing_rad": float(commanded_bearing_rad),
        "commanded_bearing_wrapped_rad": float(commanded_bearing_rad % (2.0 * math.pi)),
        "initial_offset_m": float(episode.initial_offset_r),
        "spiral_enabled": False,
    }

    try:
        result = run_episode(
            episode,
            pen_allow=cfg.pen_allow,
            model=_WORKER_MODEL,
            data=_WORKER_DATA,
        )
    except AssertionError as exc:
        return {
            **base_record,
            "success": False,
            "exclusion_reason": f"assertion: {exc}",
            "settled_tip_xy_m": None,
            "settled_tip_radius_m": None,
            "measured_tip_bearing_rad": None,
            "bearing_error_rad": None,
            "tilt_at_settle_rad": None,
            "hole_est_xy_m": None,
            "error_xy_m": None,
            "error_norm_m": None,
            "time_to_seat_s": None,
        }

    if not result.success:
        return {
            **base_record,
            "success": False,
            "exclusion_reason": "not seated before max_seconds",
            "settled_tip_xy_m": None,
            "settled_tip_radius_m": None,
            "measured_tip_bearing_rad": None,
            "bearing_error_rad": None,
            "tilt_at_settle_rad": None,
            "hole_est_xy_m": None,
            "error_xy_m": None,
            "error_norm_m": None,
            "time_to_seat_s": None,
        }

    assert result.settled_tip_xy is not None
    assert result.tilt_at_settle_rad is not None
    assert result.true_hole_xy is not None
    tip_xy = result.settled_tip_xy
    measured_bearing = math.atan2(float(tip_xy[1]), float(tip_xy[0]))
    bearing_error = wrap_angle_rad(measured_bearing - commanded_bearing_rad)
    hole_est_xy = estimate_hole_xy(
        tip_xy, commanded_bearing_rad, cfg.task.radial_clearance,
    )
    error_xy = hole_est_xy - result.true_hole_xy

    return {
        **base_record,
        "success": True,
        "exclusion_reason": None,
        "settled_tip_xy_m": tip_xy.tolist(),
        "settled_tip_radius_m": float(np.linalg.norm(tip_xy - result.true_hole_xy)),
        "measured_tip_bearing_rad": float(measured_bearing),
        "bearing_error_rad": float(bearing_error),
        "tilt_at_settle_rad": float(result.tilt_at_settle_rad),
        "hole_est_xy_m": hole_est_xy.tolist(),
        "error_xy_m": error_xy.tolist(),
        "error_norm_m": float(np.linalg.norm(error_xy)),
        "time_to_seat_s": float(result.time_to_seat_s),
    }


def distribution(values: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(values.mean()),
        "std_population": float(values.std(ddof=0)),
        "min": float(values.min()),
        "p95": float(np.percentile(values, 95)),
        "max": float(values.max()),
    }


def analyze(records: list[dict[str, object]]) -> dict[str, object]:
    cfg = VALIDATED_LOOSE_V1
    ordered = sorted(records, key=lambda record: int(record["trial_index"]))
    successful = [record for record in ordered if bool(record["success"])]
    excluded = [record for record in ordered if not bool(record["success"])]
    assert successful, "N2 produced no successful measurements"

    commanded = np.array([
        float(record["commanded_bearing_wrapped_rad"]) for record in successful
    ])
    tip_xy = np.array([record["settled_tip_xy_m"] for record in successful], dtype=float)
    tip_radius = np.array([
        float(record["settled_tip_radius_m"]) for record in successful
    ])
    bearing_error = np.array([
        float(record["bearing_error_rad"]) for record in successful
    ])
    abs_bearing_error = np.abs(bearing_error)
    implied_lateral_error = cfg.task.radial_clearance * np.abs(np.sin(bearing_error))
    error_norm = np.array([float(record["error_norm_m"]) for record in successful])
    tilt = np.array([float(record["tilt_at_settle_rad"]) for record in successful])
    tilt_projection = cfg.task.peg_length * np.abs(np.sin(tilt))

    # A linear correlation catches broad frame drift. The fitted 16th harmonic
    # specifically prices the bore-facet periodicity that an angle grid would
    # alias away.
    linear_correlation = float(np.corrcoef(commanded, bearing_error)[0, 1])
    harmonic_design = np.column_stack([
        np.ones_like(commanded),
        np.sin(cfg.task.n_segments * commanded),
        np.cos(cfg.task.n_segments * commanded),
    ])
    harmonic_coefficients, *_ = np.linalg.lstsq(
        harmonic_design, bearing_error, rcond=None,
    )
    harmonic_amplitude = float(np.hypot(
        harmonic_coefficients[1], harmonic_coefficients[2],
    ))

    bin_index = np.floor(
        commanded / (2.0 * math.pi / cfg.task.n_segments)
    ).astype(int) % cfg.task.n_segments
    bearing_bins = []
    for index in range(cfg.task.n_segments):
        values = bearing_error[bin_index == index]
        bearing_bins.append({
            "bin": index,
            "count": int(values.size),
            "mean_bearing_error_rad": float(values.mean()),
            "std_population_bearing_error_rad": float(values.std(ddof=0)),
        })

    return {
        "config": {
            "name": cfg.name,
            "peg_radius_m": cfg.task.peg_radius,
            "clearance_m": cfg.task.radial_clearance,
            "chamfer_width_m": cfg.task.chamfer_width,
            "plate_thickness_m": cfg.task.plate_thickness,
            "peg_length_m": cfg.task.peg_length,
            "peg_mass_kg": cfg.task.peg_mass,
            "friction": cfg.task.friction,
            "n_segments": cfg.task.n_segments,
            "solver_timestep_s": cfg.solver.timestep,
            "solver_cone": cfg.solver.cone,
            "solver_integrator": cfg.solver.integrator,
            "solver_solref": list(cfg.solver.solref),
            "solver_solimp": list(cfg.solver.solimp),
            "k_xy_N_per_m": cfg.gains.k_xy,
            "k_z_search_N_per_m": cfg.insertion.k_z_search,
            "k_z_seating_N_per_m": cfg.gains.k_z,
            "k_rx_Nm_per_rad": cfg.gains.k_rx,
            "k_ry_Nm_per_rad": cfg.gains.k_ry,
            "k_rz_Nm_per_rad": cfg.gains.k_rz,
            "f_search_N": cfg.insertion.f_search,
            "f_target_N": cfg.insertion.f_target,
            "f_yield_N": cfg.insertion.f_yield,
            "v_descent_m_per_s": cfg.insertion.v_descent,
            "depth_commit_m": cfg.depth_commit,
            "z_start_m": cfg.z_start,
            "target_depth_margin_m": cfg.task.plate_thickness - make_episode(0.0).target_depth,
            "pen_allow_m": cfg.pen_allow,
            "max_seconds": cfg.max_seconds,
            "control_decimation": cfg.control_decimation,
            "control_delay_steps": cfg.control_delay_steps,
            "spiral_enabled": False,
            "probe_radius_m": probe_radius_m(),
            "measured_r_passive_m": MEASURED_R_PASSIVE_M,
            "r_cap_dyn_m": cfg.r_cap_dyn,
            "fault_threshold_m": FAULT_THRESHOLD_M,
        },
        "n_trials": len(ordered),
        "n_success": len(successful),
        "n_excluded": len(excluded),
        "exclusion_reasons": dict(Counter(
            str(record["exclusion_reason"]) for record in excluded
        )),
        "tip_radius_m": distribution(tip_radius),
        "signed_bearing_error_rad": distribution(bearing_error),
        "absolute_bearing_error_rad": distribution(abs_bearing_error),
        "implied_lateral_error_m": distribution(implied_lateral_error),
        "estimator_error_norm_m": distribution(error_norm),
        "tilt_at_settle_rad": distribution(tilt),
        "tilt_projection_m": distribution(tilt_projection),
        "commanded_bearing_vs_error_pearson_r": linear_correlation,
        "bearing_error_16th_harmonic_amplitude_rad": harmonic_amplitude,
        "bearing_error_16th_harmonic_offset_rad": float(harmonic_coefficients[0]),
        "bearing_bins": bearing_bins,
        "settled_tip_xy_m": tip_xy.tolist(),
    }


def save_figures(records: list[dict[str, object]], summary: dict[str, object]) -> None:
    cfg = VALIDATED_LOOSE_V1
    successful = sorted(
        (record for record in records if bool(record["success"])),
        key=lambda record: int(record["trial_index"]),
    )
    commanded_deg = np.degrees([
        float(record["commanded_bearing_wrapped_rad"]) for record in successful
    ])
    tip_radius_um = 1e6 * np.array([
        float(record["settled_tip_radius_m"]) for record in successful
    ])
    bearing_error_deg = np.degrees([
        float(record["bearing_error_rad"]) for record in successful
    ])
    error_um = 1e6 * np.array([
        float(record["error_norm_m"]) for record in successful
    ])
    tip_xy_mm = 1e3 * np.array([
        record["settled_tip_xy_m"] for record in successful
    ], dtype=float)

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    axes[0, 0].scatter(commanded_deg, tip_radius_um, s=18, alpha=0.8)
    axes[0, 0].axhline(
        cfg.task.radial_clearance * 1e6,
        color="#C84A42",
        linestyle="--",
        label="Nominal clearance",
    )
    axes[0, 0].set(xlabel="Commanded bearing (deg)", ylabel="Settled tip radius (µm)")
    axes[0, 0].set_title("Wall-contact radius")
    axes[0, 0].legend()

    axes[0, 1].scatter(commanded_deg, bearing_error_deg, s=18, alpha=0.8)
    axes[0, 1].axhline(0.0, color="#333333", linewidth=1)
    axes[0, 1].set(xlabel="Commanded bearing (deg)", ylabel="Measured − commanded (deg)")
    axes[0, 1].set_title("Search-disabled bearing error")

    axes[1, 0].hist(error_um, bins=20, color="#3569A8", alpha=0.85)
    axes[1, 0].set(xlabel="Estimator error |hole_est| (µm)", ylabel="Trials")
    axes[1, 0].set_title("Probe estimator error")

    circle_angle = np.linspace(0.0, 2.0 * math.pi, 361)
    clearance_mm = cfg.task.radial_clearance * 1e3
    axes[1, 1].plot(
        clearance_mm * np.cos(circle_angle),
        clearance_mm * np.sin(circle_angle),
        color="#C84A42",
        linestyle="--",
        label="Nominal clearance circle",
    )
    axes[1, 1].scatter(tip_xy_mm[:, 0], tip_xy_mm[:, 1], s=18, alpha=0.8)
    axes[1, 1].set_aspect("equal")
    axes[1, 1].set(xlabel="Settled tip x (mm)", ylabel="Settled tip y (mm)")
    axes[1, 1].set_title("Settled wall-contact positions")
    axes[1, 1].legend()

    absolute = summary["absolute_bearing_error_rad"]
    estimator = summary["estimator_error_norm_m"]
    fig.suptitle(
        "N2 radial probe: "
        f"p95 bearing error {math.degrees(float(absolute['p95'])):.3f}°, "
        f"p95 estimator error {float(estimator['p95']) * 1e3:.4f} mm",
        fontsize=14,
    )
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_STEM.with_suffix(".png"), dpi=180, bbox_inches="tight")
    svg_path = FIGURE_STEM.with_suffix(".svg")
    fig.savefig(svg_path, bbox_inches="tight")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_path.read_text().splitlines()) + "\n",
        encoding="utf-8",
    )
    plt.close(fig)


def print_config() -> None:
    cfg = VALIDATED_LOOSE_V1
    episode = make_episode(0.0)
    print(f"config={cfg.name}")
    print(
        f"task: peg_r={cfg.task.peg_radius}m clearance={cfg.task.radial_clearance}m "
        f"chamfer={cfg.task.chamfer_width}m plate={cfg.task.plate_thickness}m "
        f"peg_length={cfg.task.peg_length}m mass={cfg.task.peg_mass}kg "
        f"n_segments={cfg.task.n_segments} friction={cfg.task.friction}"
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
        f"insertion: f_search={cfg.insertion.f_search}N "
        f"f_target={cfg.insertion.f_target}N f_yield={cfg.insertion.f_yield}N "
        f"v_descent={cfg.insertion.v_descent}m/s depth_commit={cfg.depth_commit}m"
    )
    print(
        f"episode: z_start={cfg.z_start}m target_depth={episode.target_depth}m "
        f"pen_allow={cfg.pen_allow}m max_seconds={cfg.max_seconds}s "
        f"decimation={cfg.control_decimation} delay={cfg.control_delay_steps}"
    )
    print(
        f"probe: radius={probe_radius_m()}m spiral_enabled=False "
        f"R_passive={MEASURED_R_PASSIVE_M}m R_cap_dyn={cfg.r_cap_dyn}m"
    )


def main() -> None:
    bearings = np.arange(N_TRIALS, dtype=float) * GOLDEN_ANGLE_RAD
    assert_preflight(bearings)
    print_config()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    with RAW_PATH.open("w", encoding="utf-8") as raw_file:
        with ProcessPoolExecutor(initializer=init_worker) as executor:
            futures = {
                executor.submit(run_probe_trial, index, float(bearing)): index
                for index, bearing in enumerate(bearings)
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                expected_index = futures[future]
                record = future.result()
                assert record["trial_index"] == expected_index
                raw_file.write(json.dumps(record, allow_nan=False) + "\n")
                raw_file.flush()
                records.append(record)
                if completed % PROGRESS_INTERVAL == 0 or completed == N_TRIALS:
                    print(f"completed {completed}/{N_TRIALS}")

    summary = analyze(records)
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8",
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
