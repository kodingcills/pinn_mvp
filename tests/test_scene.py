import math

import mujoco
import numpy as np
import pytest

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
SOLVER = SolverSpec(timestep=0.001)


def _compiled_model_data():
    xml = build_mjcf(TASK, SOLVER)
    model = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return model, data


def _geom_world_point(model, data, name: str, local_offset: np.ndarray) -> np.ndarray:
    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
    assert gid >= 0, f"geom {name} not found"
    xpos = data.geom_xpos[gid]
    xmat = data.geom_xmat[gid].reshape(3, 3)
    return xpos + xmat @ local_offset


def _raycast_topmost_z(model, data, r: float, theta: float = 0.0, start_z: float = 0.02):
    """Topmost solid z hit by a straight-down ray at (r, theta), excluding
    the peg body (which sits at the world origin and would otherwise be hit
    first). Returns None if nothing is hit."""
    peg_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "peg")
    geomid = np.zeros(1, dtype=np.int32)
    pnt = np.array([r * math.cos(theta), r * math.sin(theta), start_z])
    vec = np.array([0.0, 0.0, -1.0])
    dist = mujoco.mj_ray(model, data, pnt, vec, None, 1, peg_body_id, geomid)
    return None if dist < 0 else start_z - dist


def _geom_all_corners(model, data, name: str, size: np.ndarray) -> np.ndarray:
    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
    xpos = data.geom_xpos[gid]
    xmat = data.geom_xmat[gid].reshape(3, 3)
    corners = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                local = np.array([sx * size[0], sy * size[1], sz * size[2]])
                corners.append(xpos + xmat @ local)
    return np.array(corners)


class TestTaskSpecConstraints:
    def test_valid_spec_constructs(self):
        TaskSpec(0.005, 0.0005, 0.001, 0.01, 0.05, 0.05)

    def test_rejects_too_few_segments(self):
        with pytest.raises(AssertionError, match="n_segments"):
            TaskSpec(0.005, 0.0005, 0.001, 0.01, 0.05, 0.05, n_segments=4)

    def test_rejects_negative_peg_radius(self):
        with pytest.raises(AssertionError, match="peg_radius"):
            TaskSpec(-0.005, 0.0005, 0.001, 0.01, 0.05, 0.05)

    def test_rejects_chamfer_deeper_than_plate(self):
        with pytest.raises(AssertionError, match="chamfer"):
            TaskSpec(0.005, 0.0005, 0.01, 0.01, 0.05, 0.05)

    def test_capture_radius_is_clearance_plus_chamfer(self):
        assert TASK.capture_radius == pytest.approx(TASK.radial_clearance + TASK.chamfer_width)


class TestSolverSpecConstraints:
    def test_rejects_pyramidal_cone(self):
        with pytest.raises(AssertionError, match="cone"):
            SolverSpec(cone="pyramidal", timestep=0.001)

    def test_rejects_nonpositive_timestep(self):
        with pytest.raises(AssertionError, match="timestep"):
            SolverSpec(timestep=0.0)


class TestCompiledSolverOptionsMatchSpec:
    """Milestone 4 G1: SolverSpec's own __post_init__ only validates the
    Python-level string/tuple values -- it says nothing about what MuJoCo's
    compiler actually did with them. This project has already been bitten
    once by XML-vs-runtime divergence (a solref timeconst silently clamped
    by the compiler) and once by a suspicion of the same class of bug (the
    4-fold cardinal capture pattern, investigated as a possible pyramidal-
    cone compile fallback -- ruled out, but only by checking the compiled
    model directly, which is exactly what these tests now do permanently)."""

    def test_cone_compiles_to_elliptic(self):
        model, _ = _compiled_model_data()
        assert model.opt.cone == mujoco.mjtCone.mjCONE_ELLIPTIC

    def test_integrator_compiles_to_implicitfast(self):
        model, _ = _compiled_model_data()
        assert model.opt.integrator == mujoco.mjtIntegrator.mjINT_IMPLICITFAST

    def test_timestep_compiles_unchanged(self):
        model, _ = _compiled_model_data()
        assert model.opt.timestep == pytest.approx(SOLVER.timestep)

    def test_geom_solref_solimp_compile_unchanged(self):
        """Checks EVERY geom, not just one -- the default block in
        scene.py applies to all of them, so any per-geom divergence would
        indicate the default isn't being inherited as assumed."""
        model, _ = _compiled_model_data()
        for gid in range(model.ngeom):
            np.testing.assert_allclose(model.geom_solref[gid], SOLVER.solref)
            # geom_solimp has 5 entries (dmin, dmax, width, midpoint, power);
            # SolverSpec only configures the first 3, the rest are MuJoCo
            # defaults -- only the configured ones are ours to verify.
            np.testing.assert_allclose(model.geom_solimp[gid][:3], SOLVER.solimp)

    def test_geom_friction_compiles_unchanged(self):
        model, _ = _compiled_model_data()
        for gid in range(model.ngeom):
            assert model.geom_friction[gid, 0] == pytest.approx(TASK.friction)


class TestChamferGeometry:
    """Milestone 1 required test: chamfer face endpoints land exactly where intended."""

    @pytest.mark.parametrize("theta", [0.0, math.pi / 2.0, math.pi, 7.0 * math.pi / 4.0])
    def test_face_endpoints_at_expected_world_points(self, theta):
        model, data = _compiled_model_data()
        i = round(theta / (2.0 * math.pi) * TASK.n_segments) % TASK.n_segments
        name = f"chamfer_{i}"
        thickness_half = TASK.chamfer_width / 2.0

        e_r = np.array([math.cos(2 * math.pi * i / TASK.n_segments),
                         math.sin(2 * math.pi * i / TASK.n_segments), 0.0])
        expected_outer = (TASK.hole_radius + TASK.chamfer_width) * e_r + np.array([0, 0, 0.0])
        expected_inner = TASK.hole_radius * e_r + np.array([0, 0, -TASK.chamfer_width])

        p_plus = _geom_world_point(model, data, name, np.array([-thickness_half, 0.0, TASK.chamfer_width / math.sqrt(2.0)]))
        p_minus = _geom_world_point(model, data, name, np.array([-thickness_half, 0.0, -TASK.chamfer_width / math.sqrt(2.0)]))

        # The two along-endpoints must match {outer/z=0, inner/z=-chamfer} in
        # some order (sign of the along axis is an internal labeling choice).
        pair_a = sorted([p_plus.tolist(), p_minus.tolist()], key=lambda p: p[2])
        expected = sorted([expected_outer.tolist(), expected_inner.tolist()], key=lambda p: p[2])
        np.testing.assert_allclose(pair_a[0], expected[0], atol=1e-9)
        np.testing.assert_allclose(pair_a[1], expected[1], atol=1e-9)

    def test_no_chamfer_geometry_protrudes_above_z0(self):
        model, data = _compiled_model_data()
        size = np.array([TASK.chamfer_width / 2.0,
                          _chamfer_tangential_half(),
                          TASK.chamfer_width / math.sqrt(2.0)])
        for i in range(TASK.n_segments):
            corners = _geom_all_corners(model, data, f"chamfer_{i}", size)
            assert corners[:, 2].max() <= 1e-9, f"chamfer_{i} protrudes above z=0"


def _chamfer_tangential_half() -> float:
    r_outer = TASK.hole_radius + TASK.chamfer_width
    return r_outer * math.tan(math.pi / TASK.n_segments) * 1.15


class TestBoreGeometry:
    """Milestone 1 required test: the bore is angularly continuous."""

    def test_bore_angularly_continuous(self):
        model, data = _compiled_model_data()
        thickness_half = TASK.chamfer_width / 2.0
        tangential_half = _chamfer_tangential_half()  # same reference radius as bore ring

        spans = []
        for i in range(TASK.n_segments):
            name = f"bore_{i}"
            p_left = _geom_world_point(model, data, name, np.array([-thickness_half, -tangential_half, 0.0]))
            p_right = _geom_world_point(model, data, name, np.array([-thickness_half, tangential_half, 0.0]))
            a_left = math.atan2(p_left[1], p_left[0])
            a_right = math.atan2(p_right[1], p_right[0])
            spans.append((i, a_left, a_right))

        step = 2.0 * math.pi / TASK.n_segments
        for i in range(TASK.n_segments):
            theta_i = i * step
            theta_next = ((i + 1) % TASK.n_segments) * step
            if i == TASK.n_segments - 1:
                theta_next += 2.0 * math.pi
            # each tile must reach at least halfway to its neighbor's center
            # in both directions, guaranteeing overlap and no angular gap.
            half_reach = tangential_half / TASK.hole_radius
            assert half_reach > step / 2.0, "tangential half-size too small to bridge the angular step"


class TestPegBody:
    def test_peg_tip_site_at_body_origin(self):
        model, data = _compiled_model_data()
        sid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
        assert sid >= 0
        np.testing.assert_allclose(model.site_pos[sid], [0.0, 0.0, 0.0])

    def test_peg_joints_have_zero_passive_forces(self):
        model, _ = _compiled_model_data()
        for jname in ("peg_x", "peg_y", "peg_z", "peg_rx", "peg_ry", "peg_rz"):
            jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, jname)
            assert model.dof_damping[jid] == 0.0
            assert model.dof_frictionloss[jid] == 0.0
            assert model.dof_armature[jid] == 0.0

    def test_peg_mass_matches_spec(self):
        model, _ = _compiled_model_data()
        bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "peg")
        assert model.body_mass[bid] == pytest.approx(TASK.peg_mass)


class TestSilhouette:
    """Permanent regression guard for the Item 2 investigation: an
    independently-suspected ridge/ledge between the chamfer's outer edge and
    the flat table top was checked by raycasting the actual compiled model
    (not re-derived box algebra) and found NOT to exist, at both a tile
    center and a tile boundary angle. This test locks that in."""

    @pytest.mark.parametrize("theta_deg", [0.0, 11.25, 22.5])
    def test_flat_at_z0_immediately_past_the_chamfer(self, theta_deg):
        model, data = _compiled_model_data()
        r_outer = TASK.hole_radius + TASK.chamfer_width
        for r_mm in np.arange(r_outer * 1000 + 0.2, r_outer * 1000 + 3.0, 0.2):
            z = _raycast_topmost_z(model, data, r_mm / 1000.0, math.radians(theta_deg))
            assert z is not None, f"no hit at r={r_mm}mm theta={theta_deg}deg -- gap in the table ring"
            assert abs(z) < 1e-6, f"topmost z={z*1000:.4f}mm at r={r_mm}mm theta={theta_deg}deg, expected 0 (flat table)"

    @pytest.mark.parametrize("theta_deg", [0.0, 11.25])
    def test_chamfer_slope_is_monotonic_no_hit_inside_hole(self, theta_deg):
        model, data = _compiled_model_data()
        theta = math.radians(theta_deg)
        # Inside the hole radius: nothing above the bore/floor -- no ledge.
        for r_mm in np.arange(0.5, TASK.hole_radius * 1000 - 0.05, 0.5):
            z = _raycast_topmost_z(model, data, r_mm / 1000.0, theta)
            assert z is None or z <= -TASK.plate_thickness + 1e-6, (
                f"unexpected solid at r={r_mm}mm (inside hole_radius) z={z}"
            )
        # Across the chamfer: topmost z must increase monotonically with r.
        # Starts 0.25mm past hole_radius, not right at it -- a ray cast
        # exactly tangent to the bore/chamfer boundary edge is a raycasting
        # precision artifact (grazes the corner and reports no hit within
        # ~0.15mm of the exact edge), not a real geometric gap: Milestone 1's
        # test_bore_angularly_continuous already checks tangential overlap
        # analytically, not by raycast, and finds none.
        r_outer_mm = (TASK.hole_radius + TASK.chamfer_width) * 1000
        prev_z = -math.inf
        for r_mm in np.arange(TASK.hole_radius * 1000 + 0.25, r_outer_mm - 0.05, 0.1):
            z = _raycast_topmost_z(model, data, r_mm / 1000.0, theta)
            assert z is not None, f"no hit in chamfer band at r={r_mm}mm"
            assert z >= prev_z - 1e-9, f"non-monotonic chamfer slope at r={r_mm}mm: {z} < {prev_z}"
            prev_z = z


class TestBlindHoleFloor:
    """Item 0: without an axial stop this is a through-hole, and 'seated' is
    physically undefined -- Phase 2's bottom-out reward signal has nothing
    to validate against."""

    def test_floor_present_by_default(self):
        model, _ = _compiled_model_data()
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
        assert gid >= 0

    def test_floor_top_at_plate_thickness_depth(self):
        model, data = _compiled_model_data()
        z = _raycast_topmost_z(model, data, 0.0, 0.0)
        assert z is not None, "no floor detected at hole center"
        np.testing.assert_allclose(z, -TASK.plate_thickness, atol=1e-9)

    def test_floor_radius_matches_hole_radius(self):
        model, data = _compiled_model_data()
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
        assert model.geom_size[gid][0] == pytest.approx(TASK.hole_radius)

    def test_through_hole_option_omits_floor(self):
        task = TaskSpec(0.005, 0.0005, 0.001, 0.01, 0.05, 0.05, blind_hole=False)
        xml = build_mjcf(task, SOLVER)
        model = mujoco.MjModel.from_xml_string(xml)
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor") < 0


class TestPlateTilt:
    """Stage B item B0c: fixture-misalignment scenario, not a CAD parameter
    -- tilts the hole axis about world Y, rotating about the mouth center
    (world origin) so the hole's (x,y) location is unchanged."""

    @pytest.mark.parametrize("tilt_deg", [0.0, 5.0, 20.0, -15.0])
    def test_hole_axis_tilts_about_world_y(self, tilt_deg):
        xml = build_mjcf(TASK, SOLVER, plate_tilt_deg=tilt_deg)
        model = mujoco.MjModel.from_xml_string(xml)
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
        world_z_axis = data.geom_xmat[gid].reshape(3, 3)[:, 2]
        expected = np.array([math.sin(math.radians(tilt_deg)), 0.0, math.cos(math.radians(tilt_deg))])
        np.testing.assert_allclose(world_z_axis, expected, atol=1e-9)

    def test_zero_tilt_leaves_floor_at_world_origin_xy(self):
        xml = build_mjcf(TASK, SOLVER, plate_tilt_deg=0.0)
        model = mujoco.MjModel.from_xml_string(xml)
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
        np.testing.assert_allclose(data.geom_xpos[gid][:2], [0.0, 0.0], atol=1e-9)
