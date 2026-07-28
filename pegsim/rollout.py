"""Episode runner: drives the peg through a ramped-z insertion attempt with
an optional Archimedean spiral overlaid on the lateral setpoint, and returns
a flat per-episode log dict -- one flat .npz per episode, no database.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

from pegsim.control import ImpedanceGains, assert_gains_stable, compute_and_apply_qfrc, min_contact_dist, peg_contact_force_world
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf
from pegsim.search import ArchimedeanSpiral, SpiralSpec


@dataclass(frozen=True)
class EpisodeConfig:
    task: TaskSpec
    solver: SolverSpec
    gains: ImpedanceGains
    z_start: float
    z_end: float
    z_rate: float  # m/s
    initial_offset_r: float
    initial_offset_theta: float  # radians
    max_seconds: float
    spiral_spec: SpiralSpec | None = None  # None => straight drop, no search
    target_depth: float = None  # default set in __post_init__: task.plate_thickness - margin

    def __post_init__(self) -> None:
        assert self.z_start > self.z_end, "z_start must be above z_end"
        assert self.z_rate > 0, "z_rate must be positive"
        assert self.initial_offset_r >= 0, "initial_offset_r must be non-negative"
        assert self.max_seconds > 0, "max_seconds must be positive"
        if self.target_depth is None:
            # "Seated" for a blind hole means bottomed out on the floor at
            # z=-plate_thickness, not an arbitrary depth -- a through-hole
            # has no such event (Item 0).
            object.__setattr__(self, "target_depth", self.task.plate_thickness - 0.001)


def axis_xy_at_z(tip_pos: np.ndarray, axis_dir: np.ndarray, z_target: float) -> np.ndarray:
    """World (x, y) where the peg's central axis crosses world height
    z_target, given the tip position (body origin) and the world direction
    of the body's local z axis (the site_xmat third column)."""
    s = (z_target - tip_pos[2]) / axis_dir[2]
    return tip_pos[:2] + axis_dir[:2] * s


def ground_truth_seated(
    tip_pos: np.ndarray,
    axis_dir: np.ndarray,
    contact_force_world: np.ndarray,
    task: TaskSpec,
    target_depth: float,
) -> bool:
    """Sim-only success check: uses the true hole position (world origin),
    valid here because this grades the benchmark offline -- it is never used
    to drive control or train a policy (Rule Zero). Tilt-robust by geometric
    containment: checks BOTH ends of the engaged segment (the tip, and where
    the axis crosses the plate's mouth at z=0) rather than a single tilt
    tolerance, since the tip alone is a poor proxy for centering once tilted
    (a ~0.15deg tilt, well inside the 10deg bound, shifted the previous
    tip-only reading by more than a full clearance width over the ~40mm
    tip-to-mouth lever arm).

    lateral force vs F_wedge guards against a wedged/jammed "success" where
    the tip and mouth both read centered but the peg is jammed diagonally
    under load: two-sided contact resists axial motion with up to 2*mu*N, so
    jamming becomes possible once the lateral (normal) force N reaches
    F_axial/(2*mu). This is the derived starting value, NOT YET calibrated
    against a known-good/known-jammed force distribution.
    """
    z_tip = tip_pos[2]
    tip_lateral = float(np.linalg.norm(tip_pos[:2]))
    mouth_lateral = float(np.linalg.norm(axis_xy_at_z(tip_pos, axis_dir, 0.0)))
    lateral_force = float(np.linalg.norm(contact_force_world[:2]))
    axial_force = abs(float(contact_force_world[2]))
    f_wedge = axial_force / (2.0 * task.friction) if axial_force > 0 else math.inf
    return (
        z_tip <= -target_depth
        and tip_lateral <= task.radial_clearance
        and mouth_lateral <= task.radial_clearance
        and lateral_force < f_wedge
    )


def run_episode(cfg: EpisodeConfig) -> dict[str, object]:
    model = mujoco.MjModel.from_xml_string(build_mjcf(cfg.task, cfg.solver))
    data = mujoco.MjData(model)

    x0 = cfg.initial_offset_r * math.cos(cfg.initial_offset_theta)
    y0 = cfg.initial_offset_r * math.sin(cfg.initial_offset_theta)
    data.qpos[0] = x0
    data.qpos[1] = y0
    data.qpos[2] = cfg.z_start
    mujoco.mj_forward(model, data)
    assert_gains_stable(model, data, cfg.gains, cfg.solver.timestep)

    # The spiral is centered on the peg's STARTING (nominal) position, not
    # world origin: it represents the controller's best guess at the hole
    # location, which is wrong by exactly initial_offset_r -- the true hole
    # is always at world (0,0). The search must sweep back around to there.
    nominal_xy = np.array([x0, y0])
    spiral = ArchimedeanSpiral(cfg.spiral_spec) if cfg.spiral_spec is not None else None

    dt = cfg.solver.timestep
    n_steps = int(cfg.max_seconds / dt)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")

    time_log = np.zeros(n_steps)
    tip_pos = np.zeros((n_steps, 3))
    setpoint_xy = np.zeros((n_steps, 2))
    min_dist = np.full(n_steps, np.inf)
    contact_force = np.zeros((n_steps, 3))
    tip_lateral_log = np.zeros(n_steps)
    mouth_lateral_log = np.zeros(n_steps)
    tilt_deg_log = np.zeros(n_steps)

    # The search has two stopping mechanisms, both driven by real
    # (FK/contact) signals, never simulator ground truth:
    #  - PAUSE (resumable): while actively in contact with the chamfer/bore,
    #    hold the setpoint instead of advancing theta. Without this, the
    #    setpoint keeps sweeping outward while the chamfer is midway through
    #    funneling the peg to center, dragging it back out before funneling
    #    finishes -- observed directly: without pausing, 0.5*capture_radius
    #    only succeeded at 2/8 angles despite pitch < capture_radius.
    #  - STOP (permanent): once actually centered within clearance (checked
    #    at the mouth, not the tip -- see ground_truth_seated), stop for
    #    good rather than resuming if contact is later lost transiently.
    xy_des = nominal_xy
    search_done = False

    seated_step = None
    for i in range(n_steps):
        z_des = max(cfg.z_start - i * cfg.z_rate * dt, cfg.z_end)
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        axis_dir_now = data.site_xmat[site_id].reshape(3, 3)[:, 2]
        mouth_now = axis_xy_at_z(tip_now, axis_dir_now, 0.0)
        if not search_done and np.linalg.norm(mouth_now) <= cfg.task.radial_clearance:
            search_done = True
        in_contact = math.isfinite(min_contact_dist(data))
        if not search_done and spiral is not None and z_des <= 0.0 and not in_contact:
            # The spiral only starts advancing once the commanded descent
            # has reached the plate surface (z_des <= 0) -- otherwise, at
            # the spec'd z_start/z_rate, the search wanders past clearance
            # (~0.2s) before the peg is even physically at the hole's
            # entrance plane (~0.5s), so a zero-offset drop would search
            # itself away from a hole it was already aligned with.
            xy_des = nominal_xy + spiral.step(dt)
        x_des = np.array([xy_des[0], xy_des[1], z_des])

        compute_and_apply_qfrc(model, data, cfg.gains, x_des)
        mujoco.mj_step(model, data)

        tip = data.site_xpos[site_id]
        axis_dir = data.site_xmat[site_id].reshape(3, 3)[:, 2]
        force = peg_contact_force_world(model, data)

        time_log[i] = data.time
        tip_pos[i] = tip
        setpoint_xy[i] = xy_des
        min_dist[i] = min_contact_dist(data)
        contact_force[i] = force
        tip_lateral_log[i] = np.linalg.norm(tip[:2])
        mouth_lateral_log[i] = np.linalg.norm(axis_xy_at_z(tip, axis_dir, 0.0))
        tilt_deg_log[i] = math.degrees(math.acos(min(1.0, max(-1.0, axis_dir[2]))))

        if seated_step is None and ground_truth_seated(tip, axis_dir, force, cfg.task, cfg.target_depth):
            seated_step = i

    return {
        "time": time_log,
        "tip_pos": tip_pos,
        "setpoint_xy": setpoint_xy,
        "min_contact_dist": min_dist,
        "contact_force": contact_force,
        "tip_lateral": tip_lateral_log,
        "mouth_lateral": mouth_lateral_log,
        "tilt_deg": tilt_deg_log,
        "max_tilt_deg": float(tilt_deg_log.max()),
        "seated": seated_step is not None,
        "time_to_seat_s": time_log[seated_step] if seated_step is not None else math.inf,
    }
