"""Episode runner: drives the peg through a two-phase axial insertion
attempt (gentle force-referenced search, then a rate-limited and
force-leashed ramp to seating depth) with an optional Archimedean spiral
overlaid on the lateral setpoint. Returns a flat per-episode log dict --
one flat .npz per episode, no database.
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
class InsertionProfile:
    """Two-phase axial setpoint strategy.

    Replaces a single fixed ramp target (z_start -> z_end at z_rate), which
    was a stale constant left over from before the blind-hole floor
    existed: z_end=-45mm against a floor at -10mm produced a constant 70N
    overdrive force (K_z * 35mm), matched to three digits by simple
    arithmetic -- a bug, not a discovered hazard.

    Phase 1 (search): z_des referenced to the plate top (z=0) at a gentle
    f_search, so lateral search isn't fighting friction from a large,
    arbitrary axial force (a fixed deep setpoint mid-ramp was measured
    generating tens of N against the table purely by chance of ramp
    timing).
    Phase 2 (seating): once real FK depth shows the tip past the chamfer,
    ramp (never step) toward a seating target so the commanded force
    settles at f_target once bottomed out.

    Two governors, both required -- verified empirically that neither
    alone is sufficient:
      - v_descent rate-limits the raw setpoint ramp. This is what actually
        bounds impact velocity and therefore peak force: measured 2.85 m/s
        impact / 160N peak at a 3.5mm-offset tip-and-lever entry (peg
        tips past the overhang and free-falls) versus ~10mm/s / ~70N at
        zero offset on the old design -- the large-offset entry is exactly
        the regime that needs the rate limit.
      - the leash (z_des clamped to at most f_target/K_z ahead of the
        actual tip -- see run_episode) bounds STEADY-STATE force once in
        contact, independent of the rate limit. Without the rate limit,
        the leash alone still lets the peg free-accelerate through the
        bore under a constant f_target push.
    """

    f_search: float = 4.0  # N, axial reference force during lateral search
    f_target: float = 15.0  # N, steady seating force (12-18N precision-insertion range)
    f_yield: float = 25.0  # N, fixture failure limit
    v_descent: float = 0.01  # m/s, rate limit on the seating ramp

    def __post_init__(self) -> None:
        assert self.f_search > 0, "f_search must be positive"
        assert self.f_target > 0, "f_target must be positive"
        assert self.f_yield > 0, "f_yield must be positive"
        assert self.f_target < self.f_yield, (
            "f_target must stay under f_yield -- nominal operation must not "
            "risk the fixture; a wrench clamp is a backstop for a policy "
            "mistake, not the thing standing between every rollout and a "
            "broken part"
        )
        assert self.v_descent > 0, "v_descent must be positive"


def _rate_limited_advance(current: float, target: float, max_step: float) -> float:
    delta = target - current
    if abs(delta) <= max_step:
        return target
    return current + math.copysign(max_step, delta)


@dataclass(frozen=True)
class EpisodeConfig:
    task: TaskSpec
    solver: SolverSpec
    gains: ImpedanceGains
    insertion: InsertionProfile
    z_start: float
    initial_offset_r: float
    initial_offset_theta: float  # radians
    max_seconds: float
    spiral_spec: SpiralSpec | None = None  # None => straight drop, no search
    target_depth: float = None  # default set in __post_init__: task.plate_thickness - margin
    seat_persist_s: float = 0.1  # ground_truth_seated must hold continuously this long

    def __post_init__(self) -> None:
        assert self.z_start > 0, "z_start must be above the plate (z=0)"
        assert self.initial_offset_r >= 0, "initial_offset_r must be non-negative"
        assert self.max_seconds > 0, "max_seconds must be positive"
        assert self.seat_persist_s > 0, "seat_persist_s must be positive"
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
    pen_allow: float,
) -> bool:
    """Sim-only success check: uses the true hole position (world origin),
    valid here because this grades the benchmark offline -- it is never used
    to drive control or train a policy (Rule Zero). Tilt-robust by geometric
    containment: checks BOTH ends of the engaged segment (the tip, and where
    the axis crosses the plate's mouth at z=0).

    clearance + pen_allow, not clearance alone: a peg resting against the
    bore wall settles AT clearance by definition (that is the wall), so
    `<= clearance` tests a measure-zero condition and rejects legitimate
    seats by fractions of a micron. pen_allow is 1.5x a measured
    interpenetration at the relevant load, passed in by the caller (not a
    hardcoded fudge factor) so the tolerance stays traceable to a
    measurement.

    lateral force vs F_wedge guards against a wedged/jammed "success" where
    the tip and mouth both read centered but the peg is jammed diagonally
    under load. F_wedge is currently math.inf (deferred, see rollout.py's
    module note) -- this sweep's failures are flat-table non-engagements,
    not diagonal wedging, so there is nothing to calibrate against yet.
    """
    z_tip = tip_pos[2]
    tip_lateral = float(np.linalg.norm(tip_pos[:2]))
    mouth_lateral = float(np.linalg.norm(axis_xy_at_z(tip_pos, axis_dir, 0.0)))
    lateral_force = float(np.linalg.norm(contact_force_world[:2]))
    axial_force = abs(float(contact_force_world[2]))
    f_wedge = math.inf  # deferred -- see module docstring and PHASE2.md
    tol = task.radial_clearance + pen_allow
    return (
        z_tip <= -target_depth
        and tip_lateral <= tol
        and mouth_lateral <= tol
        and lateral_force < f_wedge
    )


def run_episode(cfg: EpisodeConfig, pen_allow: float = 0.0) -> dict[str, object]:
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

    # Seating target: derived from a MEASURED z_stop (see
    # scripts/stage_a_close.py's z_stop probe), not assumed to sit exactly at
    # -plate_thickness. The measured residual (~0.07mm on this task) is small
    # relative to f_target/K_z (7.5mm at defaults) and the leash below
    # re-references to the LIVE tip position every step regardless, so this
    # approximation only sets the ramp's nominal destination, not the actual
    # force cap.
    z_stop_nominal = -cfg.task.plate_thickness
    z_seating_target = z_stop_nominal - cfg.insertion.f_target / cfg.gains.k_z
    z_search_target = -cfg.insertion.f_search / cfg.gains.k_z

    time_log = np.zeros(n_steps)
    tip_pos = np.zeros((n_steps, 3))
    setpoint_xy = np.zeros((n_steps, 2))
    z_des_log = np.zeros(n_steps)
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
    #    finishes.
    #  - STOP (permanent): once actually centered within clearance (checked
    #    at the mouth, not the tip), stop for good.
    xy_des = nominal_xy
    search_done = False
    z_des_raw = cfg.z_start  # matches actual start -- zero initial spring error

    seated_step = None
    consecutive_ok = 0
    required_consecutive = max(1, int(round(cfg.seat_persist_s / dt)))

    for i in range(n_steps):
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        axis_dir_now = data.site_xmat[site_id].reshape(3, 3)[:, 2]
        mouth_now = axis_xy_at_z(tip_now, axis_dir_now, 0.0)
        if not search_done and np.linalg.norm(mouth_now) <= cfg.task.radial_clearance:
            search_done = True
        in_contact = math.isfinite(min_contact_dist(data))
        in_seating_phase = tip_now[2] < -cfg.task.chamfer_width  # real FK depth, past the chamfer
        phase_target = z_seating_target if in_seating_phase else z_search_target
        z_des_raw = _rate_limited_advance(z_des_raw, phase_target, cfg.insertion.v_descent * dt)
        # Force leash: never command more than f_target/K_z ahead of the
        # LIVE tip position, in either phase.
        z_des_leashed = max(z_des_raw, tip_now[2] - cfg.insertion.f_target / cfg.gains.k_z)

        if not search_done and spiral is not None and tip_now[2] <= 0.001 and not in_contact:
            # The spiral only starts advancing once the peg is actually
            # near the plate surface -- otherwise it wanders past clearance
            # well before the peg is physically at the hole's entrance.
            xy_des = nominal_xy + spiral.step(dt)
        x_des = np.array([xy_des[0], xy_des[1], z_des_leashed])

        compute_and_apply_qfrc(model, data, cfg.gains, x_des)
        mujoco.mj_step(model, data)

        tip = data.site_xpos[site_id]
        axis_dir = data.site_xmat[site_id].reshape(3, 3)[:, 2]
        force = peg_contact_force_world(model, data)

        time_log[i] = data.time
        tip_pos[i] = tip
        setpoint_xy[i] = xy_des
        z_des_log[i] = z_des_leashed
        min_dist[i] = min_contact_dist(data)
        contact_force[i] = force
        tip_lateral_log[i] = np.linalg.norm(tip[:2])
        mouth_lateral_log[i] = np.linalg.norm(axis_xy_at_z(tip, axis_dir, 0.0))
        tilt_deg_log[i] = math.degrees(math.acos(min(1.0, max(-1.0, axis_dir[2]))))

        if ground_truth_seated(tip, axis_dir, force, cfg.task, cfg.target_depth, pen_allow):
            consecutive_ok += 1
        else:
            consecutive_ok = 0
        if seated_step is None and consecutive_ok >= required_consecutive:
            seated_step = i - required_consecutive + 1  # onset of the confirmed streak

    return {
        "time": time_log,
        "tip_pos": tip_pos,
        "setpoint_xy": setpoint_xy,
        "z_des": z_des_log,
        "min_contact_dist": min_dist,
        "contact_force": contact_force,
        "tip_lateral": tip_lateral_log,
        "mouth_lateral": mouth_lateral_log,
        "tilt_deg": tilt_deg_log,
        "max_tilt_deg": float(tilt_deg_log.max()),
        "seated": seated_step is not None,
        "time_to_seat_s": time_log[seated_step] if seated_step is not None else math.inf,
    }
