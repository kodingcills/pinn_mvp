"""Episode runner: drives the peg through a ramped-z insertion attempt with
an optional Archimedean spiral overlaid on the lateral setpoint, and returns
a flat per-episode log dict -- one flat .npz per episode, no database.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

from pegsim.control import ImpedanceGains, assert_gains_stable, compute_and_apply_qfrc, min_contact_dist
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
    seat_z_threshold: float = None  # default set in __post_init__
    seat_lateral_threshold: float = None

    def __post_init__(self) -> None:
        assert self.z_start > self.z_end, "z_start must be above z_end"
        assert self.z_rate > 0, "z_rate must be positive"
        assert self.initial_offset_r >= 0, "initial_offset_r must be non-negative"
        assert self.max_seconds > 0, "max_seconds must be positive"
        if self.seat_z_threshold is None:
            object.__setattr__(self, "seat_z_threshold", self.z_end + 0.002)
        if self.seat_lateral_threshold is None:
            # NOT radial_clearance: the peg_tip site sits at the BOTTOM of
            # the shaft, but the bore engagement zone is near z=0, tens of mm
            # above the tip once fully inserted. Even a tiny, safety-legal
            # tilt (well under the 10deg bound -- ~0.15deg was observed) is
            # amplified by that lever arm into a tip-vs-actual-shaft-position
            # discrepancy several times bigger than radial_clearance itself.
            # This threshold is a coarse "didn't end up somewhere wildly
            # wrong" sanity check, not a precision centering requirement --
            # depth (seat_z_threshold) is what actually distinguishes
            # "passed through the bore" from "stuck resting on the table".
            object.__setattr__(self, "seat_lateral_threshold", self.task.capture_radius)


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

    # The search has two stopping mechanisms, both driven by real
    # (FK/contact) signals, never simulator ground truth:
    #  - PAUSE (resumable): while actively in contact with the chamfer/bore,
    #    hold the setpoint instead of advancing theta. Without this, the
    #    setpoint keeps sweeping outward while the chamfer is midway through
    #    funneling the peg to center, dragging it back out before funneling
    #    finishes -- observed directly: without pausing, 0.5*capture_radius
    #    only succeeded at 2/8 angles despite pitch < capture_radius.
    #  - STOP (permanent): once actually centered within clearance, stop for
    #    good rather than resuming if contact is later lost transiently.
    xy_des = nominal_xy
    search_done = False

    seated_step = None
    for i in range(n_steps):
        z_des = max(cfg.z_start - i * cfg.z_rate * dt, cfg.z_end)
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        if not search_done and np.linalg.norm(tip_now[:2]) <= cfg.task.radial_clearance:
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
        time_log[i] = data.time
        tip_pos[i] = tip
        setpoint_xy[i] = xy_des
        min_dist[i] = min_contact_dist(data)

        if (
            seated_step is None
            and tip[2] <= cfg.seat_z_threshold
            and np.linalg.norm(tip[:2]) <= cfg.seat_lateral_threshold
        ):
            seated_step = i

    return {
        "time": time_log,
        "tip_pos": tip_pos,
        "setpoint_xy": setpoint_xy,
        "min_contact_dist": min_dist,
        "seated": seated_step is not None,
        "time_to_seat_s": time_log[seated_step] if seated_step is not None else math.inf,
    }
