"""Chunk 1 L3 control-rate and one-cycle-latency sweep."""
from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from pegsim.rollout import VALIDATED_LOOSE_V1, EpisodeConfig, run_episode


OFFSET_M = 0.002
ANGLES_DEG = tuple(22.5 * i for i in range(16))
CELLS = ((1, 0), (2, 0), (4, 0), (4, 1))
FORCE_WINDOW_S = 0.2


def run_trial(args: tuple[int, int, float]) -> dict[str, object]:
    decimation, delay_steps, angle_deg = args
    cfg = VALIDATED_LOOSE_V1
    episode = EpisodeConfig(
        task=cfg.task,
        solver=cfg.solver,
        gains=cfg.gains,
        insertion=cfg.insertion,
        z_start=cfg.z_start,
        initial_offset_r=OFFSET_M,
        initial_offset_theta=math.radians(angle_deg),
        max_seconds=cfg.max_seconds,
        spiral_spec=cfg.spiral,
        depth_commit=cfg.depth_commit,
        control_decimation=decimation,
        control_delay_steps=delay_steps,
    )
    try:
        log = run_episode(episode, pen_allow=cfg.pen_allow)
    except AssertionError as exc:
        return {"angle_deg": angle_deg, "seated": False, "error": str(exc)}

    fz_abs = np.abs(log["contact_force"][:, 2])
    tail_steps = max(1, int(round(FORCE_WINDOW_S / cfg.solver.timestep)))
    settled_fz = float(fz_abs[-tail_steps:].mean())
    peak_fz = float(fz_abs.max())
    if log["seated"]:
        t_seat = float(log["time_to_seat_s"])
        window = (log["time"] >= t_seat) & (log["time"] < t_seat + FORCE_WINDOW_S)
        oscillation = float(np.ptp(fz_abs[window]))
    else:
        oscillation = math.nan
    return {
        "angle_deg": angle_deg,
        "seated": bool(log["seated"]),
        "settled_fz": settled_fz,
        "peak_fz": peak_fz,
        "oscillation": oscillation,
        "tts": float(log["time_to_seat_s"]),
        "error": None,
    }


def print_config() -> None:
    cfg = VALIDATED_LOOSE_V1
    spiral = cfg.spiral
    print(f"config={cfg.name} offset={OFFSET_M * 1000:.1f}mm angles=16")
    print(
        f"task: peg_r={cfg.task.peg_radius * 1000:.3f}mm "
        f"clearance={cfg.task.radial_clearance * 1000:.3f}mm "
        f"chamfer={cfg.task.chamfer_width * 1000:.3f}mm "
        f"plate={cfg.task.plate_thickness * 1000:.3f}mm "
        f"mu={cfg.task.friction} n_seg={cfg.task.n_segments}"
    )
    print(
        f"solver: dt={cfg.solver.timestep} cone={cfg.solver.cone} "
        f"integrator={cfg.solver.integrator} solref={cfg.solver.solref} "
        f"solimp={cfg.solver.solimp}"
    )
    print(
        f"gains: k_xy={cfg.gains.k_xy} k_z_search={cfg.insertion.k_z_search} "
        f"k_z_seating={cfg.gains.k_z} k_rx={cfg.gains.k_rx} "
        f"k_ry={cfg.gains.k_ry} k_rz={cfg.gains.k_rz}"
    )
    print(
        f"gate={cfg.depth_commit * 1000:.3f}mm "
        f"f_search={cfg.insertion.f_search}N f_target={cfg.insertion.f_target}N "
        f"v_descent={cfg.insertion.v_descent * 1000:.3f}mm/s "
        f"v_t={spiral.v_t * 1000:.3f}mm/s "
        f"max_seconds={cfg.max_seconds} pen_allow={cfg.pen_allow * 1e6:.3f}um"
    )
    print(
        f"metrics: settled_Fz=mean(abs(Fz)) over final {FORCE_WINDOW_S * 1000:.0f}ms; "
        f"oscillation=peak-to-peak(abs(Fz)) in first {FORCE_WINDOW_S * 1000:.0f}ms "
        "after confirmed seating onset"
    )


def main() -> None:
    print_config()
    print(
        "\ndecim delay rate_Hz capture settled_Fz_mean_N "
        "settled_Fz_range_N peak_Fz_N max_postseat_osc_N tts_range_s"
    )
    for decimation, delay_steps in CELLS:
        args = [(decimation, delay_steps, angle) for angle in ANGLES_DEG]
        with ProcessPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(run_trial, args))
        rate_hz = 1.0 / (VALIDATED_LOOSE_V1.solver.timestep * decimation)
        valid = [row for row in rows if row["error"] is None]
        seated = [row for row in valid if row["seated"]]
        errors = [row for row in rows if row["error"] is not None]
        if not valid:
            print(
                f"{decimation:5d} {delay_steps:5d} {rate_hz:7.0f} "
                f" 0/16 ASSERTIONS ({len(errors)}/16)"
            )
            for row in errors:
                print(f"  angle={row['angle_deg']:.1f} assertion={row['error']}")
            continue
        settled = np.array([row["settled_fz"] for row in valid], dtype=float)
        peaks = np.array([row["peak_fz"] for row in valid], dtype=float)
        oscillations = np.array([row["oscillation"] for row in seated], dtype=float)
        tts = np.array([row["tts"] for row in seated], dtype=float)
        print(
            f"{decimation:5d} {delay_steps:5d} {rate_hz:7.0f} "
            f"{len(seated):2d}/16 {settled.mean():18.6f} "
            f"[{settled.min():.6f},{settled.max():.6f}] "
            f"{peaks.max():9.6f} {oscillations.max():18.6f} "
            f"[{tts.min():.4f},{tts.max():.4f}]"
        )
        for row in errors:
            print(f"  angle={row['angle_deg']:.1f} assertion={row['error']}")


if __name__ == "__main__":
    main()
