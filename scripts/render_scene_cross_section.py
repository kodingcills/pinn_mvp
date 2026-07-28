"""Render the plate's r-z cross section (raycast silhouette) to
docs/scene_cross_section.png. Committed alongside any geometry change --
this is what would have caught every Item-2-style bug in one look."""
from __future__ import annotations

import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mujoco
import numpy as np

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


def raycast_topmost_z(model, data, r, theta, peg_body_id, start_z=0.02):
    geomid = np.zeros(1, dtype=np.int32)
    pnt = np.array([r * math.cos(theta), r * math.sin(theta), start_z])
    vec = np.array([0.0, 0.0, -1.0])
    dist = mujoco.mj_ray(model, data, pnt, vec, None, 1, peg_body_id, geomid)
    return None if dist < 0 else start_z - dist


def _render(plate_tilt_deg: float, out_path: str, title: str) -> None:
    model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, SolverSpec(timestep=0.001), plate_tilt_deg=plate_tilt_deg))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    peg_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "peg")

    # Signed r along the tilt axis (world x), not |r|, so the asymmetric
    # profile a tilted hole produces is visible instead of folded over.
    r_mm = np.arange(-15.0, 15.0, 0.05)
    z_mm = []
    for r in r_mm:
        theta = 0.0 if r >= 0 else math.pi
        z = raycast_topmost_z(model, data, abs(r) / 1000.0, theta, peg_body_id)
        z_mm.append(np.nan if z is None else z * 1000.0)
    z_mm = np.array(z_mm)

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(r_mm, z_mm, "-", lw=1.5, color="tab:blue")
    ax.axvline(TASK.hole_radius * 1000, color="gray", ls="--", lw=0.8, label="hole_radius")
    ax.axvline(-TASK.hole_radius * 1000, color="gray", ls="--", lw=0.8)
    ax.axvline((TASK.hole_radius + TASK.chamfer_width) * 1000, color="gray", ls=":", lw=0.8, label="hole_radius+chamfer")
    ax.axvline(-(TASK.hole_radius + TASK.chamfer_width) * 1000, color="gray", ls=":", lw=0.8)
    ax.axhline(-TASK.plate_thickness * 1000, color="tab:red", ls="--", lw=0.8, label="floor (-plate_thickness)")
    ax.set_xlabel("signed radius along tilt axis, world x (mm)")
    ax.set_ylabel("topmost solid z (mm)")
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(f"saved {out_path}")


def main() -> None:
    _render(0.0, "docs/scene_cross_section.png", "Plate cross section (raycast silhouette, no tilt)")
    _render(10.0, "docs/scene_cross_section_tilted.png",
            "Plate cross section, plate_tilt_deg=10 (Stage B item B0c fixture-misalignment scenario)")


if __name__ == "__main__":
    main()
