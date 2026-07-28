"""Archimedean spiral compliant search, overlaid on the Cartesian impedance
law from control.py.

    r(theta) = a + b*theta
    x_spiral = r*cos(theta), y_spiral = r*sin(theta)
    setpoint(t) = x_hole_nominal + dx_res + x_spiral(t)   (dx_res = 0 for now)

Two physical constraints, both asserted at construction:

  1. Pitch bound: 2*pi*b MUST be strictly less than capture_radius. A larger
     pitch steps over the hole on every revolution, and no downstream policy
     can recover from a search pattern that never comes within catching
     distance.
  2. Constant TANGENTIAL speed: theta_dot = v_t / (a + b*theta), integrated
     step by step, never a fixed angular rate. At a fixed angular rate,
     linear velocity grows with radius, putting peak impact energy exactly
     at the outer radii most likely to catch an edge.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from pegsim.scene import TaskSpec

# Product spec: the max lateral pose uncertainty the search must cover is a
# property of the fixture/vision tolerance stack, not of the hole geometry --
# it must NOT be derived from capture_radius (a prior version used
# 1.25*capture_radius, which silently shrinks or grows delta_max every time
# the hole geometry changes, even though the robot's actual pose uncertainty
# hasn't).
DELTA_MAX_SPEC_M = 0.002


@dataclass(frozen=True)
class SpiralSpec:
    """Everything the spiral needs, derived from TaskSpec -- no hand-tuned
    magic constants beyond the two dimensionless safety factors, both
    documented below."""

    task: TaskSpec
    delta_max: float  # max lateral pose uncertainty this search must cover
    v_t: float = 0.005  # constant tangential search speed, m/s
    pitch_safety: float = 0.5  # pitch = pitch_safety * capture_radius; must be in (0, 1)

    def __post_init__(self) -> None:
        assert self.delta_max > 0, "delta_max must be positive"
        assert self.v_t > 0, "v_t must be positive"
        assert 0.0 < self.pitch_safety < 1.0, (
            "pitch_safety must be in (0, 1) -- at 1.0 the pitch bound below "
            "would only just fail to be strictly satisfied"
        )

        pitch = 2.0 * math.pi * self.b
        assert pitch < self.task.capture_radius, (
            f"spiral pitch {pitch} must be strictly less than capture_radius "
            f"{self.task.capture_radius} -- a larger pitch steps over the "
            "hole and no policy recovers"
        )
        assert self.r_spiral >= self.delta_max, (
            f"R_spiral {self.r_spiral} must cover delta_max {self.delta_max} "
            "(constraint 9/10: the search must be able to reach every offset "
            "it is asked to cover)"
        )

    @property
    def b(self) -> float:
        """Pitch coefficient: one full revolution grows the radius by
        pitch_safety * capture_radius, keeping the pitch strictly under the
        capture radius so consecutive loops always overlap the chamfer's
        catch zone -- no offset can fall in a gap between loops."""
        return self.pitch_safety * self.task.capture_radius / (2.0 * math.pi)

    @property
    def a(self) -> float:
        """Starting radius. theta_dot = v_t / (a + b*theta) is singular at
        r=0, so the spiral cannot start there. a=b keeps the start within
        one pitch of the true center without introducing a second free
        parameter to hand-tune."""
        return self.b

    @property
    def r_spiral(self) -> float:
        """Radius at which the search is considered exhausted: delta_max
        plus one extra pitch of margin, so the last loop fully clears the
        largest commanded offset rather than stopping exactly on it."""
        return self.delta_max + 2.0 * math.pi * self.b


class ArchimedeanSpiral:
    """Stateful theta integrator. Call step(dt) once per control tick."""

    def __init__(self, spec: SpiralSpec) -> None:
        self.spec = spec
        self.theta = 0.0

    @property
    def radius(self) -> float:
        return self.spec.a + self.spec.b * self.theta

    @property
    def exhausted(self) -> bool:
        return self.radius >= self.spec.r_spiral

    def step(self, dt: float) -> np.ndarray:
        """Advance theta by one control step (no-op once exhausted) and
        return the current (x, y) spiral offset."""
        if not self.exhausted:
            theta_dot = self.spec.v_t / self.radius
            self.theta += theta_dot * dt
        r = min(self.radius, self.spec.r_spiral)
        return np.array([r * math.cos(self.theta), r * math.sin(self.theta)])


def assert_search_force_budget(
    task: TaskSpec,
    k_xy: float,
    f_search: float,
    spiral: SpiralSpec,
    r_cap_dyn: float,
) -> None:
    """Breakaway-force check: whether the lateral spring can move the peg at
    all against friction, at any point in the spiral's own trajectory. Found
    via the C2a 16-angle cross-check: at f_search=4N, k_xy=500, mu=0.4, every
    failing start angle ran the spiral to full exhaustion (all ~3.3
    revolutions) while the tip never left a ~0.3mm tangle near its start --
    k_xy*R_spiral (1.31N) never exceeded mu*f_search (1.6N), so the spiral
    was too weak to overcome friction at ANY point in its own trajectory, at
    any starting angle.

    NOTE: an earlier version of this also asserted a steady-state LAG bound
    (mu*f_search/k_xy <= 0.25*r_cap_dyn), reasoning that tracking slop eats
    into the capture radius. That reasoning was wrong: the lag is TANGENTIAL
    (a phase delay along the spiral's direction of travel), not a radial
    loss -- its radial component is L*b/r, a couple percent of L, not L
    itself. Empirically, the config that violated that bound hardest
    (f_search=4N, k_xy=1000, lag=1.53mm vs a 0.4mm bound) captured 16/16.
    Removed.

    r_cap_dyn is a measured constant (see
    scripts/measure_c2a_dynamic_capture_radius.py), not derived from
    TaskSpec -- callers must pass in a value they actually measured for
    their (task, gains) pair, not a guess.
    """
    breakaway_force = task.friction * f_search
    max_lateral_force = k_xy * spiral.r_spiral
    assert max_lateral_force > 2.0 * breakaway_force, (
        f"max deliverable lateral spring force k_xy*R_spiral = "
        f"{max_lateral_force:.4f}N does not clear 2x the friction floor "
        f"2*mu*f_search = {2.0 * breakaway_force:.4f}N -- the spiral cannot "
        "reliably overcome friction even at its own maximum radius"
    )
    r_breakaway = breakaway_force / k_xy
    assert r_breakaway < 0.5 * r_cap_dyn, (
        f"breakaway radius mu*f_search/k_xy = {r_breakaway * 1000:.4f}mm "
        f"does not clear 0.5*r_cap_dyn = {0.5 * r_cap_dyn * 1000:.4f}mm -- "
        "holes inside the breakaway radius only ever get a single radial "
        "crossing (the peg starts moving only once the spiral has grown "
        "past r_breakaway), not full spiral coverage"
    )


@dataclass(frozen=True)
class ProbeSpec:
    """Lift-and-step lateral search: an alternative to the Archimedean
    spiral (Milestone 4 E6) that sidesteps the friction force budget
    entirely by translating with the peg lifted clear of the table, then
    lowering to check for capture -- no lateral spring force ever has to
    overcome table friction, unlike a dragged spiral search (see
    assert_search_force_budget's docstring for why that budget can fail).

    Probe points are laid out on a hexagonal covering lattice: circles of
    radius r_cap_dyn centered on a triangular lattice with spacing
    r_cap_dyn*sqrt(3) is the loosest packing that still covers the plane
    with no gaps (the deepest point between three adjacent lattice points
    sits at spacing/sqrt(3) = r_cap_dyn from each center) -- covered
    empirically, not just by the formula, in
    tests/test_search.py::TestProbeSpecCoverage.
    """

    task: TaskSpec
    delta_max: float  # max lateral pose uncertainty this search must cover
    r_cap_dyn: float  # measured dynamic capture radius -- see measure_c2a_dynamic_capture_radius.py
    lift_height: float = 0.001  # m, clears the table surface (z=0) before translating

    def __post_init__(self) -> None:
        assert self.delta_max > 0, "delta_max must be positive"
        assert self.r_cap_dyn > 0, "r_cap_dyn must be positive"
        assert self.lift_height > 0, "lift_height must be positive"

    @property
    def spacing(self) -> float:
        return self.r_cap_dyn * math.sqrt(3.0)

    @property
    def points(self) -> list[np.ndarray]:
        """Probe offsets (x, y) relative to the nominal search center,
        ring by ring outward, stopping once every point within delta_max is
        within r_cap_dyn of some ring already generated."""
        pts = [np.array([0.0, 0.0])]
        ring = 1
        while ring * self.spacing - self.r_cap_dyn <= self.delta_max:
            ring_radius = ring * self.spacing
            n_points = 6 * ring
            for k in range(n_points):
                angle = 2.0 * math.pi * k / n_points
                pts.append(np.array([ring_radius * math.cos(angle), ring_radius * math.sin(angle)]))
            ring += 1
        return pts
