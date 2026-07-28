"""One-off diagnostic: ray-cast straight down through the plate at a range of
radii and print the topmost solid z at each -- settles the Item 2 geometry
question empirically instead of by hand-derived box algebra.

Disposable: not imported by anything, safe to delete once the fix lands.
"""
from __future__ import annotations

import numpy as np
import mujoco

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


def main() -> None:
    model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, SolverSpec()))
    data = mujoco.MjData(model)
    # Move the peg far away -- it sits at the world origin by default and
    # would otherwise be the first thing every ray hits.
    data.qpos[2] = 1.0
    mujoco.mj_forward(model, data)

    start_z = 0.02
    vec = np.array([0.0, 0.0, -1.0])
    geomid = np.zeros(1, dtype=np.int32)
    peg_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "peg")

    print(f"{'r_mm':>8s} {'topmost_z_mm':>14s} {'geom'}")
    for r_mm in np.arange(0.0, 20.0 + 1e-9, 0.1):
        r = r_mm / 1000.0
        pnt = np.array([r, 0.0, start_z])
        dist = mujoco.mj_ray(model, data, pnt, vec, None, 1, peg_body_id, geomid)
        if dist < 0:
            print(f"{r_mm:8.1f} {'no hit':>14s}")
        else:
            hit_z = start_z - dist
            gname = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(geomid[0]))
            print(f"{r_mm:8.1f} {hit_z*1000:14.4f} {gname}")


if __name__ == "__main__":
    main()
