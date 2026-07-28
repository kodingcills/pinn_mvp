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
