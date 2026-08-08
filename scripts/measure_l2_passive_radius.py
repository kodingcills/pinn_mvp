"""Chunk 1 L2 passive-capture radius with lateral search disabled."""
from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor

from pegsim.rollout import VALIDATED_LOOSE_V1, EpisodeConfig, run_episode


OFFSETS_MM = tuple(1.0 + 0.25 * i for i in range(9))
ANGLES_DEG = tuple(45.0 * i for i in range(8))


def run_trial(args: tuple[float, float]) -> tuple[float, float, bool, str | None]:
    offset_mm, angle_deg = args
    cfg = VALIDATED_LOOSE_V1
    episode = EpisodeConfig(
        task=cfg.task,
        solver=cfg.solver,
        gains=cfg.gains,
        insertion=cfg.insertion,
        z_start=cfg.z_start,
        initial_offset_r=offset_mm / 1000.0,
        initial_offset_theta=math.radians(angle_deg),
        max_seconds=cfg.max_seconds,
        spiral_spec=None,
        depth_commit=cfg.depth_commit,
        control_decimation=1,
        control_delay_steps=0,
    )
    try:
        log = run_episode(episode, pen_allow=cfg.pen_allow)
    except AssertionError as exc:
        return offset_mm, angle_deg, False, str(exc)
    return offset_mm, angle_deg, bool(log["seated"]), None


def print_config() -> None:
    cfg = VALIDATED_LOOSE_V1
    print(f"config={cfg.name} search=OFF angles=8")
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
        f"f_search={cfg.insertion.f_search}N "
        f"f_target={cfg.insertion.f_target}N "
        f"v_descent={cfg.insertion.v_descent * 1000:.3f}mm/s "
        f"max_seconds={cfg.max_seconds} z_start={cfg.z_start * 1000:.3f}mm "
        f"pen_allow={cfg.pen_allow * 1e6:.3f}um"
    )


def main() -> None:
    print_config()
    args = [(offset, angle) for offset in OFFSETS_MM for angle in ANGLES_DEG]
    with ProcessPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(run_trial, args))

    by_key = {(offset, angle): (seated, error) for offset, angle, seated, error in rows}
    print("\nFull grid: 1=seated, 0=not seated, X=assertion")
    print("offset_mm\\deg " + " ".join(f"{angle:5.1f}" for angle in ANGLES_DEG))
    rates: dict[float, float] = {}
    for offset in OFFSETS_MM:
        cells = []
        successes = 0
        for angle in ANGLES_DEG:
            seated, error = by_key[(offset, angle)]
            cells.append("  X  " if error is not None else ("  1  " if seated else "  0  "))
            successes += seated
        rates[offset] = successes / len(ANGLES_DEG)
        print(f"{offset:13.2f} " + " ".join(cells) + f"  {successes}/8")

    first_below = next((offset for offset in OFFSETS_MM if rates[offset] < 0.5), None)
    r_passive = max((offset for offset in OFFSETS_MM if rates[offset] >= 0.5), default=None)
    print("\nRates")
    for offset in OFFSETS_MM:
        print(f"{offset:.2f}mm: {rates[offset] * 100:.1f}%")
    print(f"first_below_50_percent={first_below}mm")
    print(f"R_passive_grid={r_passive}mm")

    errors = [(offset, angle, error) for offset, angle, _, error in rows if error is not None]
    if errors:
        print("\nAssertions")
        for offset, angle, error in errors:
            print(f"offset={offset:.2f} angle={angle:.1f}: {error}")


if __name__ == "__main__":
    main()
