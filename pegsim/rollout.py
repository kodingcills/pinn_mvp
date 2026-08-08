"""Episode runner: drives the peg through a two-phase axial insertion
attempt (gentle force-referenced search, then a rate-limited and
force-leashed ramp to seating depth) with an optional Archimedean spiral
overlaid on the lateral setpoint. Returns one typed TrialResult per episode.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional

import mujoco
import numpy as np

from pegsim.control import ImpedanceGains, assert_gains_stable, compute_and_apply_qfrc, min_contact_dist, peg_contact_force_world
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf
from pegsim.search import DELTA_MAX_SPEC_M, ArchimedeanSpiral, SpiralSpec

# ONE gate for ONE physical event: the peg has committed to the hole.
#
# This replaces two separate constants (DEPTH_ENGAGE_MARGIN = 0.05mm, which
# paused the spiral, and DELTA_Z_TRIGGER = 0.2mm, which started the seating
# ramp). They described the same event at two different depths, and they
# drifted apart: at 0.05mm the spiral froze for 82.8% of steps in the slow
# H1 trial, because 0.05mm sits BELOW the 0.06-0.10mm partial-overhang
# settling band the peg reaches merely by resting near the hole. Every one
# of the ten H1 failures shows the same signature -- spiral_exhausted=False
# after burning the entire episode budget, i.e. the search never advanced.
#
# DEPTH_COMMIT is DERIVED, not chosen. It must sit strictly between two
# measured bounds, both asserted in assert_commit_gate_reachable:
#
#   lower: 3 * the deepest measured settling depth on the flat table near
#          the hole (partial overhang), so resting is never mistaken for
#          committing. Measured, not assumed -- see
#          pegsim.characterize.measure_settling_depths.
#   upper: 0.5 * the search-phase descent allowance f_search / k_z_search,
#          the furthest the search reference can pull the tip past the
#          table before the axial spring goes slack. A threshold above this
#          is physically unreachable -- that was bug 4 (the old absolute
#          z_tip < -chamfer_width trigger against a 0.5mm allowance), and
#          the same class of error as the pause gate above.
#
# Both bounds move when f_search or k_z_search move, which is exactly why
# they are asserted against MEASUREMENTS at config-validation time rather
# than checked once by hand.
DEPTH_COMMIT = 0.0005

# Hysteresis: once committed, stay committed until the tip comes back up
# past this fraction of DEPTH_COMMIT. Contact chatter around a bare
# threshold would otherwise stutter the spiral on and off (and, worse,
# stutter the axial stiffness between k_z_search and k_z_seating) at
# physics rate.
COMMIT_RELEASE_FRACTION = 0.7

# How far the peg's measured resting depth may sit from the running
# flat-table baseline and still be treated as "genuinely resting on the
# table" for the purpose of refining that baseline. Derived from
# DEPTH_COMMIT rather than free-standing, so the two cannot drift apart:
# it must be WIDE enough to admit real settling (else the baseline can
# never refine off its CAD-nominal starting value) and NARROW enough that a
# committed descent never pollutes it. assert_commit_gate_reachable checks
# the first condition against the measured settling depth; the second holds
# by construction at any fraction below 1.
Z_FLAT_BAND = 0.5 * DEPTH_COMMIT

# Time constant of the flat-table baseline tracker, in SECONDS -- converted
# to a per-step EMA coefficient inside run_episode using that episode's dt.
Z_FLAT_TAU_S = 0.5

# The tip stops within about 0.07mm of the nominal blind-hole floor at 15N.
# The success threshold admits twice that measured residual: the second copy
# is an explicit 100% margin for the variation across approach angles, while
# still being an order of magnitude tighter than the unexplained 1mm margin
# it replaces.
MEASURED_Z_STOP_RESIDUAL_M = 70e-6
TARGET_DEPTH_MARGIN_M = 2.0 * MEASURED_Z_STOP_RESIDUAL_M


@dataclass(frozen=True)
class InsertionProfile:
    """Two-phase axial setpoint strategy.

    Replaces a single fixed ramp target (z_start -> z_end at z_rate), which
    was a stale constant left over from before the blind-hole floor
    existed: z_end=-45mm against a floor at -10mm produced a constant 70N
    overdrive force (K_z * 35mm), matched to three digits by simple
    arithmetic -- a bug, not a discovered hazard.

    Phase 1 (search): z_des referenced to the plate top (z=0) at a gentle
    f_search AND a soft k_z_search, so lateral search isn't fighting
    friction from a large, arbitrary axial force (a fixed deep setpoint
    mid-ramp was measured generating tens of N against the table purely by
    chance of ramp timing).
    Phase 2 (seating): once real FK depth shows the tip committed to the
    hole, ramp (never step) toward a seating target so the commanded force
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

    # k_z_search and f_search have NO defaults, for the same reason
    # ImpedanceGains.k_xy/k_rx/k_ry don't (see that class): together they
    # fix the search-phase descent allowance f_search/k_z_search, which is
    # the upper bound on DEPTH_COMMIT. A default for either silently
    # reconstitutes bug 4 (a depth threshold the axial reference cannot
    # physically reach) the moment the other one moves. f_search's old
    # default of 4.0 was exactly such a constant: correct when written,
    # then superseded by 1.0 in VALIDATED_LOOSE_V1 while every bare
    # InsertionProfile() in the repo silently kept 4.0.
    k_z_search: float
    f_search: float  # N, axial reference force during lateral search
    f_target: float = 15.0  # N, steady seating force (12-18N precision-insertion range)
    f_yield: float = 25.0  # N, fixture failure limit
    v_descent: float = 0.01  # m/s, rate limit on the seating ramp

    def __post_init__(self) -> None:
        assert self.k_z_search > 0, "k_z_search must be positive"
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

    @property
    def descent_allowance(self) -> float:
        """How far past the flat table the search-phase axial reference can
        pull the tip before its spring goes slack: f_search / k_z_search.
        Every depth threshold in the search phase is bounded by this."""
        return self.f_search / self.k_z_search


def assert_commit_gate_reachable(
    insertion: InsertionProfile,
    gains: ImpedanceGains,
    settling_depth: float,
    depth_commit: float = DEPTH_COMMIT,
) -> None:
    """Bracket DEPTH_COMMIT between its two bounds, the lower one MEASURED.

    The upper bound (depth_commit < 0.5 * f_search/k_z_search) is checked in
    EpisodeConfig.__post_init__ -- it needs no simulation.

    The lower bound does: settling_depth is how deep the tip actually rests
    below the flat table when it is merely SITTING near the hole, partially
    overhanging the chamfer. Pass a value from
    pegsim.characterize.measure_settling_depths, never a literal -- the
    whole point is that a hardcoded 0.10mm is the next stale constant.

    Z_FLAT_BAND is checked against the same measurement: the baseline can
    only refine off its CAD-nominal starting value if genuine resting
    contact falls inside the band.
    """
    assert settling_depth > 0, (
        "settling_depth must be a positive measured depth below the table"
    )
    z_flat_band = 0.5 * depth_commit
    assert depth_commit > 3.0 * settling_depth, (
        f"depth_commit = {depth_commit * 1000:.4f}mm does not clear 3x the "
        f"measured near-hole settling depth "
        f"({3.0 * settling_depth * 1000:.4f}mm) -- the peg reaches this depth "
        "just by resting against the chamfer, so the spiral would pause and "
        "the seating ramp would fire without the peg having committed to "
        "anything (this is the H1 pause-gate failure)"
    )
    assert z_flat_band > settling_depth, (
        f"z_flat_band = {z_flat_band * 1000:.4f}mm is narrower than the "
        f"measured settling depth {settling_depth * 1000:.4f}mm -- the "
        "flat-table baseline could never update off its CAD-nominal value"
    )
    assert gains.k_z > insertion.k_z_search, (
        f"k_z_seating ({gains.k_z}) must exceed k_z_search "
        f"({insertion.k_z_search}) -- the split exists to make the search "
        "phase softer (a wide descent allowance at a small contact force) "
        "than the seating phase (a stiff, accurate force reference)"
    )


def _max_step_near_edges(signal: np.ndarray, flag: np.ndarray, half_width: int = 20) -> float:
    """Largest single-step change in `signal` within half_width steps of any
    rising or falling edge of `flag`. Returns 0.0 if there are no edges.

    Scoped deliberately: the search->seating handoff is the one place the
    CONTROL LAW can inject a discontinuity, and mixing it with contact
    transients elsewhere in the episode would hide exactly the thing being
    checked.
    """
    edges = np.flatnonzero(flag[:-1] != flag[1:])
    if edges.size == 0 or signal.size < 2:
        return 0.0
    steps = np.abs(np.diff(signal))
    worst = 0.0
    for e in edges:
        lo, hi = max(0, e - half_width), min(steps.size, e + half_width)
        if hi > lo:
            worst = max(worst, float(steps[lo:hi].max()))
    return worst


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
    target_depth: float | None = None  # derived in __post_init__ from measured z-stop residual
    seat_persist_s: float = 0.1  # ground_truth_seated must hold continuously this long
    plate_tilt_deg: float = 0.0  # fixture-misalignment scenario, see scene.build_mjcf
    depth_commit: float = DEPTH_COMMIT
    seed_z_flat_from_cad: bool = True
    force_continuous_handoff: bool = True
    control_decimation: int = 1
    control_delay_steps: int = 0

    def __post_init__(self) -> None:
        assert self.z_start > 0, "z_start must be above the plate (z=0)"
        assert self.initial_offset_r >= 0, "initial_offset_r must be non-negative"
        assert self.max_seconds > 0, "max_seconds must be positive"
        assert self.seat_persist_s > 0, "seat_persist_s must be positive"
        assert type(self.control_decimation) is int and self.control_decimation >= 1, (
            "control_decimation must be an integer >= 1"
        )
        assert type(self.control_delay_steps) is int and self.control_delay_steps >= 0, (
            "control_delay_steps must be an integer >= 0"
        )
        assert self.depth_commit > 0, "depth_commit must be positive"
        assert self.depth_commit < 0.5 * self.insertion.descent_allowance, (
            f"depth_commit = {self.depth_commit * 1000:.4f}mm does not clear "
            f"0.5 * (f_search / k_z_search) = "
            f"{0.5 * self.insertion.descent_allowance * 1000:.4f}mm -- the "
            "search-phase axial reference cannot pull the tip that far past "
            "the table, so the commit gate is physically unreachable no "
            "matter how well the lateral search finds the hole"
        )
        if self.target_depth is None:
            # "Seated" for a blind hole means bottomed out on the floor at
            # z=-plate_thickness within the measured 15N stop residual, not an
            # arbitrary depth. A through-hole has no such event (Item 0).
            object.__setattr__(
                self,
                "target_depth",
                self.task.plate_thickness - TARGET_DEPTH_MARGIN_M,
            )
        assert 0 < self.target_depth <= self.task.plate_thickness, (
            "target_depth must lie within the blind-hole plate thickness"
        )


def assert_pen_allow_meaningful(task: TaskSpec, pen_allow: float) -> None:
    """Keep solver interpenetration tolerance small relative to clearance."""
    assert pen_allow >= 0, "pen_allow must be non-negative"
    assert pen_allow < 0.2 * task.radial_clearance, (
        f"pen_allow ({pen_allow * 1e6:.3f}um) must be < 0.2 * clearance "
        f"({0.2 * task.radial_clearance * 1e6:.3f}um); the geometric seating "
        "criterion goes vacuous otherwise"
    )


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


@dataclass
class TrialResult:
    """Complete telemetry from one episode.

    The six settled-state fields are defined only when ``success`` is true.
    Consumers must gate on ``success`` before using them; a failed episode has
    no physically meaningful estimator measurement instant.

    This dataclass is intentionally mutable. Freezing it would prevent field
    rebinding while leaving every NumPy array mutable, creating a false
    immutability guarantee without protecting the data that matters.
    """

    time: np.ndarray
    tip_pos: np.ndarray
    setpoint_xy: np.ndarray
    z_des: np.ndarray
    min_contact_dist: np.ndarray
    contact_force: np.ndarray
    tip_lateral: np.ndarray
    mouth_lateral: np.ndarray
    tilt_deg: np.ndarray
    max_tilt_deg: float
    f_cmd_z: np.ndarray
    committed: np.ndarray
    spiral_theta: np.ndarray
    z_flat: np.ndarray
    control_tick: np.ndarray
    qfrc_applied: np.ndarray
    searching: np.ndarray
    spiral_advanced: np.ndarray
    paused_fraction: float
    max_f_cmd_z_step: float
    max_f_cmd_z_step_global: float
    success: bool
    time_to_seat_s: float
    capture_step: Optional[int]
    capture_tip_lateral: Optional[float]
    capture_mouth_lateral: Optional[float]
    spiral_exhausted: Optional[bool]
    z_flat_final: Optional[float]
    settled_tip_xy: Optional[np.ndarray] = None
    settled_setpoint_xy: Optional[np.ndarray] = None
    settled_descent: Optional[float] = None
    tilt_at_settle_rad: Optional[float] = None
    true_hole_xy: Optional[np.ndarray] = None
    nominal_xy: Optional[np.ndarray] = None


def build_episode_model_data(
    cfg: EpisodeConfig,
) -> tuple[mujoco.MjModel, mujoco.MjData]:
    """Construct reusable MuJoCo state and validate the configured hole pose."""
    model = mujoco.MjModel.from_xml_string(
        build_mjcf(cfg.task, cfg.solver, cfg.plate_tilt_deg)
    )
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    bore_ids = np.array([
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, f"bore_{i}")
        for i in range(cfg.task.n_segments)
    ])
    assert np.all(bore_ids >= 0), "compiled scene is missing one or more bore segments"

    # The configured mouth is the world origin. For a tilted plate, compare
    # the bore centroid to the same centerline at the centroid's world z;
    # otherwise a correct rotation about the mouth would look like translation.
    bore_centroid = data.geom_xpos[bore_ids, :2].mean(axis=0)
    bore_world_z = float(data.geom_xpos[bore_ids, 2].mean())
    configured_hole_xy = hole_center_xy(bore_world_z, cfg.plate_tilt_deg)
    assert np.linalg.norm(bore_centroid - configured_hole_xy) < 1e-9, (
        "compiled bore centroid does not match the configured hole position"
    )
    return model, data


def run_episode(
    cfg: EpisodeConfig,
    pen_allow: float = 0.0,
    *,
    model: Optional[mujoco.MjModel] = None,
    data: Optional[mujoco.MjData] = None,
) -> TrialResult:
    assert_pen_allow_meaningful(cfg.task, pen_allow)
    assert (model is None) == (data is None), (
        "model and data must either both be supplied or both be omitted"
    )
    if model is None:
        model, data = build_episode_model_data(cfg)
    assert data is not None  # narrowed for type checkers after paired validation

    # Episode boundary: reset every piece of MuJoCo runtime state before
    # applying this episode's initial condition. Estimator state will live
    # outside MjData in later chunks and may persist deliberately; physics
    # state must not. mj_forward after qpos assignment populates contacts and
    # all derived quantities before the first control evaluation reads them.
    mujoco.mj_resetData(model, data)

    x0 = cfg.initial_offset_r * math.cos(cfg.initial_offset_theta)
    y0 = cfg.initial_offset_r * math.sin(cfg.initial_offset_theta)
    data.qpos[0] = x0
    data.qpos[1] = y0
    data.qpos[2] = cfg.z_start
    mujoco.mj_forward(model, data)
    # Both axial stiffnesses are live gains, so both get the stability check.
    # (k_z_search is softer and therefore never the binding one, but checking
    # only the seating value would be an assumption, not a check.)
    seating_gains = cfg.gains
    search_gains = replace(cfg.gains, k_z=cfg.insertion.k_z_search)
    assert_gains_stable(model, data, seating_gains, cfg.solver.timestep)
    assert_gains_stable(model, data, search_gains, cfg.solver.timestep)
    # depth_commit's upper bound is asserted in EpisodeConfig.__post_init__
    # (no simulation needed). Its lower bound needs a measured settling depth
    # and so belongs to config validation, not to every episode -- see
    # assert_commit_gate_reachable and pegsim.characterize.measure_settling_depths.

    # The spiral is centered on the peg's STARTING (nominal) position, not
    # world origin: it represents the controller's best guess at the hole
    # location, which is wrong by exactly initial_offset_r -- the true hole
    # is always at world (0,0). The search must sweep back around to there.
    nominal_xy = np.array([x0, y0])
    spiral = ArchimedeanSpiral(cfg.spiral_spec) if cfg.spiral_spec is not None else None

    dt = cfg.solver.timestep
    control_period = cfg.control_decimation * dt
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
    z_seating_target = z_stop_nominal - cfg.insertion.f_target / seating_gains.k_z
    z_search_target = -cfg.insertion.descent_allowance

    # Rate at which the commanded axial force is allowed to ramp, N/s.
    # DERIVED from v_descent so the force ramp and the position ramp move the
    # setpoint at the same speed: a setpoint travelling at v_descent against a
    # stationary tip changes the commanded force by exactly v_descent*k_z per
    # second. One rate, two equivalent statements of it -- they cannot drift.
    f_ramp_rate = cfg.insertion.v_descent * seating_gains.k_z

    time_log = np.zeros(n_steps)
    tip_pos = np.zeros((n_steps, 3))
    setpoint_xy = np.zeros((n_steps, 2))
    z_des_log = np.zeros(n_steps)
    min_dist = np.full(n_steps, np.inf)
    contact_force = np.zeros((n_steps, 3))
    tip_lateral_log = np.zeros(n_steps)
    mouth_lateral_log = np.zeros(n_steps)
    tilt_deg_log = np.zeros(n_steps)
    f_cmd_z_log = np.zeros(n_steps)  # commanded axial wrench, physics rate
    committed_log = np.zeros(n_steps, dtype=bool)
    spiral_theta_log = np.zeros(n_steps)
    z_flat_log = np.zeros(n_steps)
    control_tick_log = np.zeros(n_steps, dtype=bool)
    qfrc_applied_log = np.zeros((n_steps, model.nv))
    # searching: the peg is at the plate and has not yet captured -- the only
    # window in which "did the spiral advance?" is a meaningful question.
    # spiral_advanced: it actually advanced this step. The ratio of the two is
    # the pause duty cycle J1 exists to fix; measuring pauses against the WHOLE
    # episode instead just measures how long the peg sat seated at the bottom.
    searching_log = np.zeros(n_steps, dtype=bool)
    spiral_advanced_log = np.zeros(n_steps, dtype=bool)

    # The search has two stopping mechanisms, both driven by real
    # (FK/contact) signals, never simulator ground truth:
    #  - PAUSE (resumable): hold the setpoint instead of advancing theta once
    #    `committed` fires -- the peg is on its way into the hole and further
    #    lateral sweeping can only drag it back out. Gated on FK depth, not
    #    lateral force. Force was tried first (lateral_force > mu*axial_force,
    #    the largest lateral force pure friction against a vertical-normal
    #    flat surface could produce) and is WRONG: kinetic friction during
    #    genuine flat-table sliding sits right at mu*N by definition, so the
    #    gate flickered true/false every step right at the threshold and froze
    #    x_des just as badly as gating on any contact did (verified: a
    #    dead-center linear sweep still failed to capture with the force
    #    gate). Gating on ANY contact (the very first version) was also wrong:
    #    routine table contact is continuous while sliding, so "pause while in
    #    contact" froze the spiral after ~6 steps out of 12000 in a direct
    #    check. Gating on a SHALLOW depth (the old 0.05mm DEPTH_ENGAGE_MARGIN)
    #    was wrong for the same reason once measured: 0.05mm is inside the
    #    0.06-0.10mm band the tip reaches merely by resting with its rim over
    #    the chamfer, so the spiral froze for 82.8% of steps.
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
    f_cmd = cfg.insertion.f_search  # leash force reference, ramped, never stepped
    committed = False

    # z_flat: the flat-table baseline the commit gate is measured against.
    # Starts at the CAD-nominal plate top (z=0, a number a real robot has from
    # the part program, not simulator ground truth) and is refined by the
    # peg's OWN measured rest depth whenever it is genuinely in resting
    # contact within Z_FLAT_BAND of that baseline. Both conditions matter:
    # requiring contact stops a free-falling tip from latching a mid-air
    # value, and the band stops a partial-overhang settle (or a committed
    # descent) from dragging the baseline down after it.
    z_flat = 0.0 if cfg.seed_z_flat_from_cad else None
    # Expressed as a TIME CONSTANT, never as a bare per-step alpha. The old
    # alpha=0.001 was a 0.5s filter at dt=0.0005 and a 1.0s filter at
    # dt=0.001 -- same literal, different physics, which is precisely the
    # failure mode this audit exists to remove. 0.5s is slow relative to the
    # spiral's own timescale (one revolution takes 0.15-3.5s) so the baseline
    # cannot chase a transient dip, and fast relative to the episode.
    z_flat_alpha = min(1.0, control_period / Z_FLAT_TAU_S)

    seated_step = None
    consecutive_ok = 0
    required_consecutive = max(1, int(round(cfg.seat_persist_s / dt)))

    # Capture-time localization error (Phase 2 pre-flight P1): the peg's own
    # FK position, at the exact instant search_done first fires, relative to
    # the TRUE hole center. Sim-only ground truth (Rule Zero: grades the
    # search, never drives it) -- this is what decides whether Phase 2 needs
    # a learned residual or just an estimator on top of the capture event.
    capture_step = None
    capture_tip_lateral = None
    capture_mouth_lateral = None
    settled_tip_xy = None
    settled_setpoint_xy = None
    settled_descent = None
    tilt_at_settle_rad = None
    true_hole_xy = None
    settled_nominal_xy = None

    # Previous step's ACTIVE stiffness and commanded setpoint, needed to read
    # off the commanded force at the instant the phase flips (see the
    # force-continuity block in the loop). Seeded with the values the first
    # step would have used, so step 0 is not a special case.
    k_z_prev_active = cfg.insertion.k_z_search
    z_des_prev = cfg.z_start
    z_des_leashed = cfg.z_start
    searching = False
    f_cart = np.zeros(6)
    held_qfrc = np.zeros(model.nv)
    delayed_commands: list[np.ndarray] = []

    for i in range(n_steps):
        mujoco.mj_forward(model, data)
        control_tick = i % cfg.control_decimation == 0
        control_tick_log[i] = control_tick
        if control_tick:
            tip_now = data.site_xpos[site_id]
            axis_dir_now = data.site_xmat[site_id].reshape(3, 3)[:, 2]
            mouth_now = axis_xy_at_z(tip_now, axis_dir_now, 0.0)
            if not search_done and np.linalg.norm(mouth_now) <= cfg.task.radial_clearance:
                search_done = True
                capture_step = i
                capture_tip_lateral = float(np.linalg.norm(tip_now[:2]))
                capture_mouth_lateral = float(np.linalg.norm(mouth_now))
                xy_des = tip_now[:2].copy()
            z_flat_band = 0.5 * cfg.depth_commit
            if cfg.seed_z_flat_from_cad:
                depth_below_flat = z_flat - tip_now[2]
                if data.ncon > 0 and abs(depth_below_flat) < z_flat_band:
                    z_flat = (1.0 - z_flat_alpha) * z_flat + z_flat_alpha * tip_now[2]
            else:
                # Historical comparison mode for L1 attribution. The old code
                # began latching once the descending tip crossed z=+1mm,
                # without requiring contact, and kept updating during sliding.
                engaging = tip_now[2] < -cfg.depth_commit
                if tip_now[2] <= 0.001 and not engaging:
                    z_flat = tip_now[2] if z_flat is None else (
                        (1.0 - z_flat_alpha) * z_flat + z_flat_alpha * tip_now[2]
                    )
                depth_below_flat = 0.0 if z_flat is None else z_flat - tip_now[2]

            # ONE gate, with hysteresis. Both spiral pause and seating ramp
            # key off the same physical event.
            was_committed = committed
            release_depth = COMMIT_RELEASE_FRACTION * cfg.depth_commit
            committed = depth_below_flat > (
                release_depth if committed else cfg.depth_commit
            )

            k_z_active = seating_gains.k_z if committed else cfg.insertion.k_z_search
            active_gains = seating_gains if committed else search_gains

            if committed != was_committed and cfg.force_continuous_handoff:
                # Re-reference the setpoint so changing k_z preserves force.
                f_now = k_z_prev_active * (tip_now[2] - z_des_prev)
                f_cmd = f_now
                z_des_raw = tip_now[2] - f_now / k_z_active

            if cfg.force_continuous_handoff:
                f_goal = (
                    cfg.insertion.f_target if committed else cfg.insertion.f_search
                )
                f_cmd = _rate_limited_advance(
                    f_cmd, f_goal, f_ramp_rate * control_period,
                )

            phase_target = z_seating_target if committed else z_search_target
            z_des_raw = _rate_limited_advance(
                z_des_raw,
                phase_target,
                cfg.insertion.v_descent * control_period,
            )
            leash_force = (
                f_cmd if cfg.force_continuous_handoff else cfg.insertion.f_target
            )
            z_des_leashed = max(
                z_des_raw, tip_now[2] - leash_force / k_z_active,
            )

            at_plate = z_flat is not None and tip_now[2] <= z_flat + z_flat_band
            searching = (not search_done) and spiral is not None and at_plate
            if searching and not committed:
                xy_des = nominal_xy + spiral.step(control_period)
            x_des = np.array([xy_des[0], xy_des[1], z_des_leashed])

            f_cart = compute_and_apply_qfrc(model, data, active_gains, x_des)
            delayed_commands.append(data.qfrc_applied.copy())
            if len(delayed_commands) > cfg.control_delay_steps:
                held_qfrc = delayed_commands.pop(0)
            k_z_prev_active = k_z_active
            z_des_prev = z_des_leashed

        # Zero-order hold: the generalized-force vector, including bias
        # compensation, remains exactly constant until the next control tick.
        data.qfrc_applied[:] = held_qfrc
        searching_log[i] = searching
        # Decimation itself is not a search pause: this flag records whether
        # the held controller state intends the spiral to advance.
        spiral_advanced_log[i] = searching and not committed
        qfrc_applied_log[i] = held_qfrc
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
        f_cmd_z_log[i] = f_cart[2]
        committed_log[i] = committed
        spiral_theta_log[i] = spiral.theta if spiral is not None else 0.0
        z_flat_log[i] = z_flat if z_flat is not None else math.nan

        if ground_truth_seated(tip, axis_dir, force, cfg.task, cfg.target_depth, pen_allow, cfg.plate_tilt_deg):
            consecutive_ok += 1
        else:
            consecutive_ok = 0
        if seated_step is None and consecutive_ok >= required_consecutive:
            seated_step = i - required_consecutive + 1  # onset of the confirmed streak
            assert z_flat is not None, (
                "a successful episode must have a defined flat-table datum"
            )
            settled_tip_xy = tip[:2].copy()
            settled_setpoint_xy = xy_des.copy()
            settled_descent = float(z_flat - tip[2])
            tilt_at_settle_rad = float(
                math.acos(min(1.0, max(-1.0, axis_dir[2])))
            )
            true_hole_xy = hole_center_xy(0.0, cfg.plate_tilt_deg)
            settled_nominal_xy = nominal_xy.copy()

    return TrialResult(
        time=time_log,
        tip_pos=tip_pos,
        setpoint_xy=setpoint_xy,
        z_des=z_des_log,
        min_contact_dist=min_dist,
        contact_force=contact_force,
        tip_lateral=tip_lateral_log,
        mouth_lateral=mouth_lateral_log,
        tilt_deg=tilt_deg_log,
        max_tilt_deg=float(tilt_deg_log.max()),
        f_cmd_z=f_cmd_z_log,
        committed=committed_log,
        spiral_theta=spiral_theta_log,
        z_flat=z_flat_log,
        control_tick=control_tick_log,
        qfrc_applied=qfrc_applied_log,
        searching=searching_log,
        spiral_advanced=spiral_advanced_log,
        # Pause duty cycle: fraction of the SEARCH window (peg at the plate,
        # not yet captured) in which the spiral was held instead of advancing.
        # H1 measured 82.8% here on its slow trial with the old 0.05mm gate.
        paused_fraction=(
            float(1.0 - spiral_advanced_log.sum() / searching_log.sum())
            if searching_log.any() else 0.0
        ),
        # J2 asks for the jump THROUGH THE TRANSITION, which is what the
        # control law is responsible for. Reported separately from the
        # global max, which is dominated by contact events (floor impact
        # changes v_z in one step, and the derived damping term -D_z*v_z
        # moves with it) -- physics, not a commanded discontinuity.
        max_f_cmd_z_step=_max_step_near_edges(f_cmd_z_log, committed_log),
        max_f_cmd_z_step_global=(
            float(np.abs(np.diff(f_cmd_z_log)).max()) if n_steps > 1 else 0.0
        ),
        success=seated_step is not None,
        time_to_seat_s=(
            float(time_log[seated_step]) if seated_step is not None else math.inf
        ),
        capture_step=capture_step,
        capture_tip_lateral=capture_tip_lateral,
        capture_mouth_lateral=capture_mouth_lateral,
        spiral_exhausted=spiral.exhausted if spiral is not None else None,
        z_flat_final=float(z_flat) if z_flat is not None else None,
        settled_tip_xy=settled_tip_xy,
        settled_setpoint_xy=settled_setpoint_xy,
        settled_descent=settled_descent,
        tilt_at_settle_rad=tilt_at_settle_rad,
        true_hole_xy=true_hole_xy,
        nominal_xy=settled_nominal_xy,
    )


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
    # J3 constant audit: these four were bare literals repeated across every
    # script that used them, each one calibrated against this config and
    # each one free to drift out of step with it. A script that reads
    # cfg.v_t cannot silently disagree with the config the rest of its
    # numbers came from.
    v_t: float  # m/s, spiral tangential search speed
    r_cap_dyn: float  # m, MEASURED dynamic capture radius (feeds the breakaway assert)
    pen_allow: float  # m, MEASURED interpenetration allowance for the seat check
    max_seconds: float  # s, episode budget -- MEASURED (J5), not arc-length arithmetic
    z_start: float  # m, initial tip height above the plate
    depth_commit: float  # m, FK depth below z_flat that commits to seating
    control_decimation: int
    control_delay_steps: int

    def __post_init__(self) -> None:
        assert self.z_start > 0, "z_start must be above the plate (z=0)"
        assert_pen_allow_meaningful(self.task, self.pen_allow)

    @property
    def spiral(self) -> SpiralSpec:
        """The one spiral this config was validated with. delta_max is a
        product spec (fixture/vision tolerance), not a geometry derivative,
        so it comes from DELTA_MAX_SPEC_M, not from the task."""
        return SpiralSpec(task=self.task, delta_max=DELTA_MAX_SPEC_M, v_t=self.v_t)


VALIDATED_LOOSE_V1 = ValidatedConfig(
    task=TaskSpec(
        peg_radius=0.005, radial_clearance=0.0005, chamfer_width=0.001,
        plate_thickness=0.01, peg_length=0.05, peg_mass=0.05, n_segments=16, friction=0.4,
    ),
    solver=SolverSpec(timestep=0.0005),
    gains=ImpedanceGains(k_xy=1000.0, k_z=2000.0, k_rx=5.0, k_ry=5.0, k_rz=0.05),
    # k_z_search=500 against f_search=1N gives a 2mm descent allowance at the
    # SAME 1N contact force (k_z_search=2000 gave only 0.5mm), which is what
    # opens a window wide enough to place DEPTH_COMMIT=0.5mm inside with the
    # 3x-settling and 0.5x-allowance margins both satisfied.
    insertion=InsertionProfile(k_z_search=500.0, f_search=1.0),
    name="VALIDATED_LOOSE_V1",
    validated_by=(
        "scripts/j4_capture_sweep.py (J-series re-sweep post gate unification): "
        "see the J4 table for per-offset capture rate and pause duty cycle. "
        "Supersedes the F5 measurement (13/16 at 1.8mm), which was taken with "
        "the split DEPTH_ENGAGE_MARGIN/DELTA_Z_TRIGGER gates."
    ),
    v_t=0.005,
    r_cap_dyn=0.0016,
    # 1.5x the measured ~8um SEATED LATERAL interpenetration. The previous
    # 37.035um value was derived from the unrelated 24.7um AXIAL floor
    # penetration and could silently make the lateral containment test lax.
    pen_allow=1.5 * 8e-6,
    max_seconds=16.3,
    z_start=0.005,
    depth_commit=DEPTH_COMMIT,
    control_decimation=1,
    control_delay_steps=0,
)
