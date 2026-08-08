"""Milestone 2: straight-down insertion at ZERO lateral offset.

No search, no learning, no reward -- confirms the impedance controller tracks
a ramped z setpoint stably and that per-step contact/force logging is wired up
correctly, before Milestone 3 sweeps solver settings on this same episode.

Usage:
    python scripts/step1_drop.py            # run, save data/step1_drop.npz
    python scripts/step1_drop.py --check     # fast sanity check, no file I/O
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import mujoco
import numpy as np

from pegsim.control import (
    ImpedanceGains,
    assert_gains_stable,
    compute_and_apply_qfrc,
    min_contact_dist,
    peg_contact_force_world,
)
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf

TASK = TaskSpec(
    peg_radius=0.005,
    radial_clearance=0.0005,
    chamfer_width=0.001,
    plate_thickness=0.01,
    peg_length=0.05,
    peg_mass=0.05,
    n_segments=16,
    friction=0.4,
)
GAINS = ImpedanceGains(k_xy=500.0, k_z=2000.0, k_rx=1.0, k_ry=1.0)

Z_START = 0.005
Z_END = -0.045
RAMP_RATE = 0.01  # m/s, per spec


def run_episode(task: TaskSpec, solver: SolverSpec, gains: ImpedanceGains) -> dict[str, np.ndarray]:
    model = mujoco.MjModel.from_xml_string(build_mjcf(task, solver))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    assert_gains_stable(model, data, gains, solver.timestep)

    dt = solver.timestep
    n_steps = int(abs(Z_END - Z_START) / (RAMP_RATE * dt)) + 1
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")

    log = {
        "time": np.zeros(n_steps),
        "tip_pos": np.zeros((n_steps, 3)),
        "tip_vel": np.zeros((n_steps, 3)),
        "f_cart": np.zeros((n_steps, 6)),
        "contact_force_world": np.zeros((n_steps, 3)),
        "min_contact_dist": np.zeros(n_steps),
        "z_des": np.zeros(n_steps),
    }

    for i in range(n_steps):
        z_des = max(Z_START - i * RAMP_RATE * dt, Z_END)
        x_des = np.array([0.0, 0.0, z_des])

        mujoco.mj_forward(model, data)
        f_cart = compute_and_apply_qfrc(model, data, gains, x_des)
        mujoco.mj_step(model, data)

        # Translational block is the identity at peg_tip (see control.py):
        # the hinges contribute no linear velocity there, so qvel[:3] IS the
        # tip's linear velocity, no Jacobian needed to log it.
        log["time"][i] = data.time
        log["tip_pos"][i] = data.site_xpos[site_id]
        log["tip_vel"][i] = data.qvel[:3]
        log["f_cart"][i] = f_cart
        log["contact_force_world"][i] = peg_contact_force_world(model, data)
        log["min_contact_dist"][i] = min_contact_dist(data)
        log["z_des"][i] = z_des

    return log


def _check(log: dict[str, np.ndarray]) -> bool:
    ok = True
    if not np.all(np.isfinite(log["tip_pos"])) or not np.all(np.isfinite(log["f_cart"])):
        print("FAIL: non-finite value in tip_pos or f_cart")
        ok = False
    # TASK.blind_hole=True by default (Item 0): the peg bottoms out on the
    # floor at z=-plate_thickness well before reaching the commanded Z_END,
    # which is intentionally deeper than reachable so the controller presses
    # into the floor (the bottom-out force signature this check also wants).
    final_z = log["tip_pos"][-1, 2]
    floor_z = -TASK.plate_thickness
    if abs(final_z - floor_z) > 1e-3:
        print(f"FAIL: final tip z={final_z:.6f}, expected ~{floor_z} (bottomed out on the blind-hole floor)")
        ok = False
    final_lateral = np.linalg.norm(log["tip_pos"][-1, :2])
    if final_lateral > 1e-3:
        print(f"FAIL: final lateral drift={final_lateral:.6f}, expected ~0 (zero-offset drop)")
        ok = False
    if ok:
        print(f"OK: tracked z from {Z_START} to {final_z:.6f} over {len(log['time'])} steps, no instability")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fast sanity check, no file I/O")
    parser.add_argument("--out", type=Path, default=Path("data/step1_drop.npz"))
    args = parser.parse_args()

    solver = SolverSpec(timestep=0.001)
    log = run_episode(TASK, solver, GAINS)

    if args.check:
        return 0 if _check(log) else 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.out, **log)
    print(f"saved {len(log['time'])} steps to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
