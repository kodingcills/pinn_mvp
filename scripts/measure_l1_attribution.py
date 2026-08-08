"""Chunk 1 L1 attribution grid.

This script only constructs EpisodeConfig variants and calls run_episode;
the canonical rollout remains the sole physics loop.
"""
from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

from pegsim.rollout import VALIDATED_LOOSE_V1, EpisodeConfig, run_episode


ANGLES_DEG = tuple(i * 22.5 for i in range(16))
OFFSET_M = 0.002


def episode_config(
    depth_commit_m: float,
    k_z_search: float,
    angle_deg: float,
    seed_z_flat_from_cad: bool = True,
    force_continuous_handoff: bool = True,
) -> EpisodeConfig:
    cfg = VALIDATED_LOOSE_V1
    insertion = replace(cfg.insertion, k_z_search=k_z_search)
    return EpisodeConfig(
        task=cfg.task,
        solver=cfg.solver,
        gains=cfg.gains,
        insertion=insertion,
        z_start=cfg.z_start,
        initial_offset_r=OFFSET_M,
        initial_offset_theta=math.radians(angle_deg),
        max_seconds=cfg.max_seconds,
        spiral_spec=cfg.spiral,
        depth_commit=depth_commit_m,
        seed_z_flat_from_cad=seed_z_flat_from_cad,
        force_continuous_handoff=force_continuous_handoff,
    )


def run_trial(args: tuple[float, float, float, bool, bool]) -> tuple[float, bool, float]:
    depth_commit_m, k_z_search, angle_deg, seed_from_cad, force_continuous = args
    episode = episode_config(
        depth_commit_m,
        k_z_search,
        angle_deg,
        seed_from_cad,
        force_continuous,
    )
    log = run_episode(episode, pen_allow=VALIDATED_LOOSE_V1.pen_allow)
    return angle_deg, bool(log["seated"]), float(log["time_to_seat_s"])


def run_cell(
    depth_commit_m: float,
    k_z_search: float,
    seed_from_cad: bool = True,
    force_continuous: bool = True,
) -> list[tuple[float, bool, float]]:
    # Validate once in the parent so a rejected cell is a reported result,
    # not a worker failure. Do not weaken the physical construction assert.
    episode_config(
        depth_commit_m,
        k_z_search,
        ANGLES_DEG[0],
        seed_from_cad,
        force_continuous,
    )
    args = [
        (depth_commit_m, k_z_search, angle, seed_from_cad, force_continuous)
        for angle in ANGLES_DEG
    ]
    with ProcessPoolExecutor(max_workers=4) as pool:
        return list(pool.map(run_trial, args))


def capture_count(rows: list[tuple[float, bool, float]]) -> int:
    return sum(seated for _, seated, _ in rows)


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
        f"gains: k_xy={cfg.gains.k_xy} k_z_seating={cfg.gains.k_z} "
        f"k_rx={cfg.gains.k_rx} k_ry={cfg.gains.k_ry} k_rz={cfg.gains.k_rz}"
    )
    print(
        f"insertion: f_search={cfg.insertion.f_search}N "
        f"f_target={cfg.insertion.f_target}N f_yield={cfg.insertion.f_yield}N "
        f"v_descent={cfg.insertion.v_descent * 1000:.3f}mm/s"
    )
    print(
        f"spiral: v_t={spiral.v_t * 1000:.3f}mm/s "
        f"pitch={2 * math.pi * spiral.b * 1000:.3f}mm "
        f"R={spiral.r_spiral * 1000:.3f}mm max_seconds={cfg.max_seconds} "
        f"z_start={cfg.z_start * 1000:.3f}mm "
        f"pen_allow={cfg.pen_allow * 1e6:.3f}um"
    )


def main() -> None:
    print_config()
    print("\nL1a: depth_commit x k_z_search")
    results: dict[tuple[float, float], list[tuple[float, bool, float]]] = {}
    for depth_mm in (0.05, 0.5):
        for k_z_search in (500.0, 2000.0):
            key = (depth_mm, k_z_search)
            try:
                rows = run_cell(depth_mm / 1000.0, k_z_search)
            except AssertionError as exc:
                print(f"depth={depth_mm:.2f}mm k_z_search={k_z_search:.0f}: REJECTED: {exc}")
                continue
            results[key] = rows
            print(
                f"depth={depth_mm:.2f}mm k_z_search={k_z_search:.0f}: "
                f"{capture_count(rows)}/16"
            )

    canonical_pair = (
        VALIDATED_LOOSE_V1.depth_commit * 1000.0,
        VALIDATED_LOOSE_V1.insertion.k_z_search,
    )
    winner = max(
        results,
        key=lambda key: (capture_count(results[key]), key == canonical_pair),
    )
    depth_mm, k_z_search = winner
    print(
        f"\nwinning pair: depth={depth_mm:.2f}mm k_z_search={k_z_search:.0f} "
        f"({capture_count(results[winner])}/16)"
    )

    print("\nL1b: one toggle at a time")
    comparisons = (
        ("CAD nominal", True, "force-continuous on", True),
        ("contact-latched", False, "force-continuous on", True),
        ("CAD nominal", True, "force-continuous off", False),
    )
    for seed_label, seed_from_cad, force_label, force_continuous in comparisons:
        if seed_from_cad and force_continuous:
            rows = results[winner]
        else:
            rows = run_cell(
                depth_mm / 1000.0,
                k_z_search,
                seed_from_cad,
                force_continuous,
            )
        failed_angles = [angle for angle, seated, _ in rows if not seated]
        print(
            f"z_flat={seed_label}; {force_label}: {capture_count(rows)}/16; "
            f"failed_angles={failed_angles}"
        )

    print("\nInteraction diagnostic: historical all-old combination")
    old_rows = run_cell(0.00005, 2000.0, False, False)
    old_failed_angles = [angle for angle, seated, _ in old_rows if not seated]
    print(
        f"depth=0.05mm k_z_search=2000 z_flat=mid-fall-latched "
        f"force-continuous=off: {capture_count(old_rows)}/16; "
        f"failed_angles={old_failed_angles}"
    )
    interaction_cases = (
        ("old pair, mid-fall latch, continuity on", 0.00005, 2000.0, False, True),
        ("old pair, CAD seed, continuity off", 0.00005, 2000.0, True, False),
        ("winning pair, mid-fall latch, continuity off", 0.0005, 500.0, False, False),
    )
    for label, depth_commit, k_z, seed_from_cad, force_continuous in interaction_cases:
        rows = run_cell(
            depth_commit,
            k_z,
            seed_from_cad,
            force_continuous,
        )
        print(f"{label}: {capture_count(rows)}/16")


if __name__ == "__main__":
    main()
