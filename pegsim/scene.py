"""Parameterized MJCF generator for peg-in-hole insertion.

MuJoCo takes the convex hull of any mesh, so a plate-with-a-hole imported as a
mesh has its hole silently filled in. The hole is therefore built entirely from
radial box primitives -- a valid convex decomposition -- which keeps
box-cylinder contact exact.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TaskSpec:
    """Peg-in-hole task geometry. In the real product these come from customer
    CAD, so every field here is a first-class parameter -- never hardcoded
    downstream."""

    peg_radius: float
    radial_clearance: float
    chamfer_width: float
    plate_thickness: float
    peg_length: float
    peg_mass: float
    n_segments: int = 16
    friction: float = 0.4
    blind_hole: bool = True

    def __post_init__(self) -> None:
        assert self.peg_radius > 0, "peg_radius must be positive"
        assert self.radial_clearance > 0, "radial_clearance must be positive"
        assert self.chamfer_width > 0, "chamfer_width must be positive"
        assert self.plate_thickness > 0, "plate_thickness must be positive"
        assert self.peg_length > 0, "peg_length must be positive"
        assert self.peg_mass > 0, "peg_mass must be positive"
        assert self.n_segments >= 8, (
            "n_segments < 8 leaves visible angular gaps even with the 1.15x "
            "overlap margin"
        )
        assert self.friction > 0, "friction must be positive"
        assert self.plate_thickness > self.chamfer_width, (
            "chamfer must fit inside the plate: plate_thickness must exceed "
            "chamfer_width"
        )

    @property
    def hole_radius(self) -> float:
        return self.peg_radius + self.radial_clearance

    @property
    def capture_radius(self) -> float:
        """Total lateral error the chamfer can catch: clearance + chamfer lead-in."""
        return self.radial_clearance + self.chamfer_width

    @property
    def plate_outer_radius(self) -> float:
        """Outer edge of the flat plate top surface.

        Must clear the largest offset used in the Milestone 4 capture-radius
        sweep (1.25 * capture_radius) plus the peg's own radius, so the peg
        body never hangs off the physical plate edge during spiral search.
        """
        return self.hole_radius + self.chamfer_width + 1.25 * self.capture_radius + self.peg_radius


@dataclass(frozen=True)
class SolverSpec:
    """Solver / contact parameters, kept separate from TaskSpec so they can be
    swept independently (see Milestone 3 contact-model audit)."""

    # No default: dt is part of the validated (k_xy, k_rx, k_ry, dt) gains
    # combination (see ImpedanceGains), not independently choosable -- 0.001
    # and 0.0005 are both used deliberately elsewhere in this project for
    # DIFFERENT, non-interchangeable gains combinations.
    timestep: float
    solref: tuple[float, float] = (0.002, 1.0)
    solimp: tuple[float, float, float] = (0.9, 0.95, 0.0001)
    cone: str = "elliptic"
    integrator: str = "implicitfast"

    def __post_init__(self) -> None:
        assert self.cone == "elliptic", (
            "pyramidal cone approximates the friction circle with a square, "
            "making sliding friction magnitude direction-dependent relative to "
            "world X/Y -- this silently breaks spiral-search angular symmetry"
        )
        assert self.timestep > 0, "timestep must be positive"
        assert self.solref[0] > 0, "solref timeconst must be positive"


def _mat_to_quat(rot: np.ndarray) -> np.ndarray:
    """Convert a proper 3x3 rotation matrix to a MuJoCo quaternion (w, x, y, z).

    Standard trace/largest-diagonal method, numerically stable near all
    rotation angles including the pi-rotation singularities of the naive
    trace-only formula.
    """
    trace = np.trace(rot)
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (rot[2, 1] - rot[1, 2]) / s
        y = (rot[0, 2] - rot[2, 0]) / s
        z = (rot[1, 0] - rot[0, 1]) / s
    elif rot[0, 0] > rot[1, 1] and rot[0, 0] > rot[2, 2]:
        s = math.sqrt(1.0 + rot[0, 0] - rot[1, 1] - rot[2, 2]) * 2.0
        w = (rot[2, 1] - rot[1, 2]) / s
        x = 0.25 * s
        y = (rot[0, 1] + rot[1, 0]) / s
        z = (rot[0, 2] + rot[2, 0]) / s
    elif rot[1, 1] > rot[2, 2]:
        s = math.sqrt(1.0 + rot[1, 1] - rot[0, 0] - rot[2, 2]) * 2.0
        w = (rot[0, 2] - rot[2, 0]) / s
        x = (rot[0, 1] + rot[1, 0]) / s
        y = 0.25 * s
        z = (rot[1, 2] + rot[2, 1]) / s
    else:
        s = math.sqrt(1.0 + rot[2, 2] - rot[0, 0] - rot[1, 1]) * 2.0
        w = (rot[1, 0] - rot[0, 1]) / s
        x = (rot[0, 2] + rot[2, 0]) / s
        y = (rot[1, 2] + rot[2, 1]) / s
        z = 0.25 * s
    return np.array([w, x, y, z])


@dataclass(frozen=True)
class RingTile:
    """One box geom of a radial ring, fully resolved in world coordinates."""

    pos: np.ndarray
    quat: np.ndarray
    size: np.ndarray
    face_center: np.ndarray
    e_normal: np.ndarray
    e_tan: np.ndarray
    e_along: np.ndarray


def _ring_tiles(
    n_seg: int,
    face_r0: float,
    face_z0: float,
    along_dr: float,
    along_dz: float,
    along_half_len: float,
    thickness_half: float,
    tangential_ref_radius: float,
    outward_hint: np.ndarray,
) -> list[RingTile]:
    """Build n_seg box tiles forming a ring around the z axis.

    Each tile's contact face (the local -x face, at distance thickness_half
    behind the tile center along -e_normal) runs through the point
    (face_r0, face_z0) in the meridional (r, z) plane at that tile's azimuth,
    extended +/- along_half_len along direction (along_dr, along_dz).

    outward_hint fixes which of the two directions perpendicular to the face
    is "away from the contact side" -- the cross product e_tan x e_along is
    otherwise sign-ambiguous depending on how (along_dr, along_dz) happened to
    be given. Flipping (along_dr, along_dz) instead of negating e_normal keeps
    (e_normal, e_tan, e_along) a proper (det = +1) rotation, which negating
    only e_normal would not.
    """
    along_norm = math.hypot(along_dr, along_dz)
    u_dr, u_dz = along_dr / along_norm, along_dz / along_norm

    e_r0 = np.array([1.0, 0.0, 0.0])
    e_tan0 = np.array([0.0, 1.0, 0.0])
    e_z_axis = np.array([0.0, 0.0, 1.0])
    e_along0 = u_dr * e_r0 + u_dz * e_z_axis
    if np.dot(np.cross(e_tan0, e_along0), outward_hint) < 0:
        u_dr, u_dz = -u_dr, -u_dz

    tangential_half = tangential_ref_radius * math.tan(math.pi / n_seg) * 1.15

    tiles = []
    for i in range(n_seg):
        theta = 2.0 * math.pi * i / n_seg
        e_r = np.array([math.cos(theta), math.sin(theta), 0.0])
        e_tan = np.array([-math.sin(theta), math.cos(theta), 0.0])
        e_along = u_dr * e_r + u_dz * e_z_axis
        e_normal = np.cross(e_tan, e_along)

        face_center = face_r0 * e_r + face_z0 * e_z_axis
        box_center = face_center + thickness_half * e_normal

        rot = np.column_stack([e_normal, e_tan, e_along])
        quat = _mat_to_quat(rot)
        size = np.array([thickness_half, tangential_half, along_half_len])
        tiles.append(RingTile(box_center, quat, size, face_center, e_normal, e_tan, e_along))
    return tiles


def _bore_wall_tiles(task: TaskSpec) -> list[RingTile]:
    z_lo, z_hi = -task.plate_thickness, -task.chamfer_width
    return _ring_tiles(
        n_seg=task.n_segments,
        face_r0=task.hole_radius,
        face_z0=(z_lo + z_hi) / 2.0,
        along_dr=0.0,
        along_dz=1.0,
        along_half_len=(z_hi - z_lo) / 2.0,
        thickness_half=task.chamfer_width / 2.0,
        tangential_ref_radius=task.hole_radius + task.chamfer_width,
        outward_hint=np.array([1.0, 0.0, 0.0]),
    )


def _chamfer_tiles(task: TaskSpec) -> list[RingTile]:
    r_outer = task.hole_radius + task.chamfer_width
    return _ring_tiles(
        n_seg=task.n_segments,
        face_r0=(r_outer + task.hole_radius) / 2.0,
        face_z0=-task.chamfer_width / 2.0,
        along_dr=1.0,
        along_dz=1.0,
        along_half_len=task.chamfer_width / math.sqrt(2.0),
        thickness_half=task.chamfer_width / 2.0,
        tangential_ref_radius=r_outer,
        outward_hint=np.array([1.0, 0.0, 0.0]),
    )


def _table_tiles(task: TaskSpec) -> list[RingTile]:
    r_inner = task.hole_radius + task.chamfer_width
    r_outer = task.plate_outer_radius
    return _ring_tiles(
        n_seg=task.n_segments,
        face_r0=(r_inner + r_outer) / 2.0,
        face_z0=0.0,
        along_dr=1.0,
        along_dz=0.0,
        along_half_len=(r_outer - r_inner) / 2.0,
        thickness_half=task.plate_thickness / 2.0,
        tangential_ref_radius=r_outer,
        outward_hint=np.array([0.0, 0.0, -1.0]),
    )


def _floor_geom_xml(task: TaskSpec) -> str:
    """Blind-hole floor: a plain solid disk (no hole to preserve, so a single
    cylinder primitive is exact -- no convex-hull concern) at the bottom of
    the bore. Radius = hole_radius so its edge lines up exactly with the bore
    wall's contact face, leaving no gap the peg could catch on.

    Without this, the hole is a through-hole and nothing axially stops the
    peg -- "seated" has no physical bottom-out event to detect, and Phase 2's
    reward design (a resistance-rise confirmation signal at seating) has
    nothing to be validated against.
    """
    half_height = task.chamfer_width / 2.0
    center_z = -task.plate_thickness - half_height
    return (
        f'    <geom name="floor" type="cylinder" pos="0 0 {center_z:.10g}" '
        f'size="{task.hole_radius:.10g} {half_height:.10g}"/>'
    )


def _fmt(values: np.ndarray) -> str:
    return " ".join(f"{v:.10g}" for v in values)


def _geom_defaults_xml(task: TaskSpec, solver: SolverSpec) -> str:
    solref = _fmt(np.array(solver.solref))
    solimp = _fmt(np.array(solver.solimp))
    friction = f"{task.friction:.10g} 0.005 0.0001"
    return (
        f'<default>\n'
        f'  <geom margin="0" condim="3" friction="{friction}" '
        f'solref="{solref}" solimp="{solimp}"/>\n'
        f'</default>'
    )


def _plate_geoms_xml(task: TaskSpec) -> str:
    lines = []
    for name_prefix, tiles in (
        ("bore", _bore_wall_tiles(task)),
        ("chamfer", _chamfer_tiles(task)),
        ("table", _table_tiles(task)),
    ):
        for i, tile in enumerate(tiles):
            lines.append(
                f'    <geom name="{name_prefix}_{i}" type="box" '
                f'pos="{_fmt(tile.pos)}" quat="{_fmt(tile.quat)}" '
                f'size="{_fmt(tile.size)}"/>'
            )
    if task.blind_hole:
        lines.append(_floor_geom_xml(task))
    return "\n".join(lines)


def build_mjcf(task: TaskSpec, solver: SolverSpec, plate_tilt_deg: float = 0.0) -> str:
    """Generate the full peg-in-hole MJCF model as an XML string.

    plate_tilt_deg: fixture-misalignment scenario, not a CAD parameter --
    tilts the whole plate body (and therefore the hole axis) about world Y
    by this angle, rotating about the body origin (world (0,0,0), the
    nominal hole mouth center), so the hole's (x,y) location is unchanged
    but its axis is no longer vertical. Used to test angular tolerance
    (Milestone 4 Stage B item B0c), never part of TaskSpec since a real
    customer CAD spec has no "how misaligned is my fixture" field.
    """
    plate_quat = _fmt(np.array([
        math.cos(math.radians(plate_tilt_deg) / 2.0), 0.0,
        math.sin(math.radians(plate_tilt_deg) / 2.0), 0.0,
    ]))
    return f"""<mujoco model="peg_in_hole">
  <option timestep="{solver.timestep:.10g}" cone="{solver.cone}" \
integrator="{solver.integrator}" gravity="0 0 -9.81"/>

  {_geom_defaults_xml(task, solver)}

  <worldbody>
    <light pos="0 0 1" dir="0 0 -1" diffuse="1 1 1"/>
    <body name="plate" pos="0 0 0" quat="{plate_quat}">
{_plate_geoms_xml(task)}
    </body>

    <body name="peg" pos="0 0 0">
      <joint name="peg_x" type="slide" axis="1 0 0" pos="0 0 0" damping="0" frictionloss="0" armature="0"/>
      <joint name="peg_y" type="slide" axis="0 1 0" pos="0 0 0" damping="0" frictionloss="0" armature="0"/>
      <joint name="peg_z" type="slide" axis="0 0 1" pos="0 0 0" damping="0" frictionloss="0" armature="0"/>
      <joint name="peg_rx" type="hinge" axis="1 0 0" pos="0 0 0" damping="0" frictionloss="0" armature="0"/>
      <joint name="peg_ry" type="hinge" axis="0 1 0" pos="0 0 0" damping="0" frictionloss="0" armature="0"/>
      <joint name="peg_rz" type="hinge" axis="0 0 1" pos="0 0 0" damping="0" frictionloss="0" armature="0"/>
      <geom name="peg_geom" type="cylinder" pos="0 0 {task.peg_length / 2.0:.10g}" \
size="{task.peg_radius:.10g} {task.peg_length / 2.0:.10g}" mass="{task.peg_mass:.10g}"/>
      <site name="peg_tip" pos="0 0 0"/>
    </body>
  </worldbody>
</mujoco>
"""
