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

# Depth below the flat table (z=0) that means "genuinely engaging the
# sloped chamfer/bore", not settling noise -- measured flat-table settling
# noise is <0.001mm, so this has ~50x margin while still catching
# engagement early (see run_episode's search-pause comment for why a
# force-based gate was tried first and rejected).
DEPTH_ENGAGE_MARGIN = 0.00005

# How far BELOW the peg's own flat-table baseline (z_flat, tracked live --
# see run_episode) counts as "committed to the seating ramp". Replaces an
# earlier absolute threshold (z_tip < -chamfer_width) that silently assumed
# the search-phase reference could always push the peg down chamfer_width
# (1mm) below the table -- false whenever f_search/k_z < chamfer_width (an
# f_search of 4N against k_z=2000 gives exactly chamfer_width; anything
# less physically cannot reach the old trigger at all, and the seating
# phase never fires, no matter how well the search finds the hole).
# 0.2mm is a few times the solver's flat-table settling noise (<0.001mm),
# so it's not tripped by noise, but small enough to fire on real partial
# engagement rather than requiring near-full chamfer depth.
DELTA_Z_TRIGGER = 0.0002


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


def assert_seating_trigger_reachable(insertion: InsertionProfile, gains: ImpedanceGains) -> None:
    """The search-phase descent allowance (how far past the table surface
    the search reference can pull the tip once unsupported) must clear
    DELTA_Z_TRIGGER with margin, or the seating-phase transition can never
    fire regardless of how well the lateral search finds the hole."""
    descent_allowance = insertion.f_search / gains.k_z
    assert descent_allowance > 2.0 * DELTA_Z_TRIGGER, (
        f"search-phase descent allowance f_search/k_z = "
        f"{descent_allowance * 1000:.4f}mm does not clear "
        f"2*DELTA_Z_TRIGGER = {2.0 * DELTA_Z_TRIGGER * 1000:.4f}mm -- the "
        "peg cannot descend far enough past its own flat-table baseline to "
        "ever trigger the seating phase"
    )


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
    plate_tilt_deg: float = 0.0  # fixture-misalignment scenario, see scene.build_mjcf

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


def hole_center_xy(z: float, hole_tilt_deg: float) -> np.ndarray:
    """True (x, y) of the hole's own centerline at world height z, for a
    hole tilted about world Y by hole_tilt_deg (see scene.build_mjcf's
    plate_tilt_deg). The tilt rotates about the mouth (world origin at
    z=0), so the mouth itself never moves -- only depth away from it does,
    by z*tan(tilt). At hole_tilt_deg=0 this is always (0, 0)."""
    return np.array([z * math.tan(math.radians(hole_tilt_deg)), 0.0])


def ground_truth_seated(
    tip_pos: np.ndarray,
    axis_dir: np.ndarray,
    contact_force_world: np.ndarray,
    task: TaskSpec,
    target_depth: float,
    pen_allow: float,
    hole_tilt_deg: float = 0.0,
) -> bool:
    """Sim-only success check: uses the true hole position, valid here
    because this grades the benchmark offline -- it is never used to drive
    control or train a policy (Rule Zero). Tilt-robust by geometric
    containment: checks BOTH ends of the engaged segment (the tip, and where
    the axis crosses the plate's mouth at z=0) against the HOLE's own
    (possibly tilted) centerline, not blindly against world (0,0) -- a
    tilted hole's centerline only passes through the origin at its mouth
    (z=0); at any other depth it has shifted by z*tan(hole_tilt_deg), and
    comparing against world (0,0) there would wrongly penalize a peg that is
    genuinely centered in the tilted hole.

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
    tip_lateral = float(np.linalg.norm(tip_pos[:2] - hole_center_xy(z_tip, hole_tilt_deg)))
    mouth_xy = axis_xy_at_z(tip_pos, axis_dir, 0.0)
    mouth_lateral = float(np.linalg.norm(mouth_xy - hole_center_xy(0.0, hole_tilt_deg)))
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
    model = mujoco.MjModel.from_xml_string(build_mjcf(cfg.task, cfg.solver, cfg.plate_tilt_deg))
    data = mujoco.MjData(model)

    x0 = cfg.initial_offset_r * math.cos(cfg.initial_offset_theta)
    y0 = cfg.initial_offset_r * math.sin(cfg.initial_offset_theta)
    data.qpos[0] = x0
    data.qpos[1] = y0
    data.qpos[2] = cfg.z_start
    mujoco.mj_forward(model, data)
    assert_gains_stable(model, data, cfg.gains, cfg.solver.timestep)
    assert_seating_trigger_reachable(cfg.insertion, cfg.gains)

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
    #  - PAUSE (resumable): hold the setpoint instead of advancing theta
    #    while engaging a SLOPED surface (chamfer/bore), not while merely
    #    resting on the flat table -- gated on FK depth (tip_now[2] below a
    #    small margin past the flat table's z=0), not lateral force. Force
    #    was tried first (lateral_force > mu*axial_force, the largest
    #    lateral force pure friction against a vertical-normal flat surface
    #    could produce) and is WRONG: kinetic friction during genuine flat-
    #    table sliding sits right at mu*N by definition, so the gate
    #    flickered true/false every step right at the threshold and froze
    #    x_des just as badly as gating on any contact did (verified: a
    #    dead-center linear sweep still failed to capture with the force
    #    gate). Depth is unambiguous: flat-table settling noise measured at
    #    <0.001mm, chamfer engagement drops the tip tens of microns or more
    #    almost immediately, so DEPTH_ENGAGE_MARGIN below has ~50x margin.
    #    Gating on ANY contact (the very first version) was also wrong:
    #    routine table contact is continuous while sliding, so "pause while
    #    in contact" froze the spiral after ~6 steps out of 12000 in a
    #    direct check -- theta never meaningfully advanced, and every
    #    earlier "search ON" result in this project needed re-checking
    #    against these fixes.
    #  - STOP (permanent): once actually centered within clearance (checked
    #    at the mouth, not the tip), stop for good -- SNAPPED to the peg's
    #    own current position, not left at whatever the setpoint last
    #    computed to. The commanded setpoint lags the actual tip during
    #    active search (measured ~1.6mm at v_t=5mm/s), so freezing at the
    #    commanded value froze it up to ~1.3mm away from where the peg
    #    genuinely was -- a persistent misaligned spring pull that wedged
    #    the peg partway into the bore instead of letting it settle
    #    (verified directly: tip_z plateaued at ~-1.9mm instead of
    #    continuing to the floor). Using the peg's own measured position is
    #    still RULE-ZERO compliant -- it never references the true hole
    #    location, only the peg's own FK.
    xy_des = nominal_xy
    search_done = False
    z_des_raw = cfg.z_start  # matches actual start -- zero initial spring error

    # z_flat: a running baseline of the peg's own resting depth while
    # genuinely on the flat table (not engaging a slope), updated only in
    # that regime so it doesn't drift down once real engagement begins.
    # in_seating_phase fires on descent RELATIVE to this baseline
    # (DELTA_Z_TRIGGER below it), not on an absolute depth -- an absolute
    # -chamfer_width threshold silently assumed the search-phase reference
    # could always push the tip that far down, which is false whenever
    # f_search/k_z < chamfer_width (assert_seating_trigger_reachable checks
    # this at construction, but the trigger itself must not depend on it
    # either).
    z_flat = None
    Z_FLAT_EMA_ALPHA = 0.001

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
            xy_des = tip_now[:2].copy()
        engaging_slope = tip_now[2] < -DEPTH_ENGAGE_MARGIN
        if tip_now[2] <= 0.001 and not engaging_slope:
            # Peg has arrived at the plate and is genuinely flat (not still
            # descending from z_start, not engaging a slope) -- safe to
            # update the baseline.
            z_flat = tip_now[2] if z_flat is None else (
                (1.0 - Z_FLAT_EMA_ALPHA) * z_flat + Z_FLAT_EMA_ALPHA * tip_now[2]
            )
        in_seating_phase = z_flat is not None and (z_flat - tip_now[2]) > DELTA_Z_TRIGGER
        phase_target = z_seating_target if in_seating_phase else z_search_target
        z_des_raw = _rate_limited_advance(z_des_raw, phase_target, cfg.insertion.v_descent * dt)
        # Force leash: never command more than f_target/K_z ahead of the
        # LIVE tip position, in either phase.
        z_des_leashed = max(z_des_raw, tip_now[2] - cfg.insertion.f_target / cfg.gains.k_z)

        if not search_done and spiral is not None and tip_now[2] <= 0.001 and not engaging_slope:
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

        if ground_truth_seated(tip, axis_dir, force, cfg.task, cfg.target_depth, pen_allow, cfg.plate_tilt_deg):
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


@dataclass(frozen=True)
class ValidatedConfig:
    """A single named, versioned bundle of every parameter that must move
    together for a validated result to stay valid (Milestone 4 G4-escalated).

    ImpedanceGains.k_xy/k_rx/k_ry and SolverSpec.timestep have no class
    defaults precisely so a caller can't silently assemble a plausible but
    never-tested combination from independent per-class defaults -- that is
    exactly how k_xy=1000 (validated only together with k_rx=k_ry=5.0 and
    dt=0.0005) broke test_holds_small_lateral_setpoint_in_free_space (which
    uses k_rx=k_ry=1.0 and dt=0.001, a DIFFERENT validated combination) when
    it was set as a shared default in isolation. Use one of the named
    bundles below, or construct your own ValidatedConfig and name what
    validated it.
    """

    task: TaskSpec
    solver: SolverSpec
    gains: ImpedanceGains
    insertion: InsertionProfile
    name: str
    validated_by: str


VALIDATED_LOOSE_V1 = ValidatedConfig(
    task=TaskSpec(
        peg_radius=0.005, radial_clearance=0.0005, chamfer_width=0.001,
        plate_thickness=0.01, peg_length=0.05, peg_mass=0.05, n_segments=16, friction=0.4,
    ),
    solver=SolverSpec(timestep=0.0005),
    gains=ImpedanceGains(k_xy=1000.0, k_rx=5.0, k_ry=5.0, k_rz=0.05),
    insertion=InsertionProfile(f_search=1.0),
    name="VALIDATED_LOOSE_V1",
    validated_by=(
        "scripts/measure_e3_search_phase_sweep.py (F5 re-sweep post seating-"
        "trigger fix): 13/16 capture at 1.8mm offset, breakaway and seating-"
        "trigger asserts both clear with margin."
    ),
)
