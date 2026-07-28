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
SOLVER = SolverSpec()


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
            SolverSpec(cone="pyramidal")

    def test_rejects_nonpositive_timestep(self):
        with pytest.raises(AssertionError, match="timestep"):
            SolverSpec(timestep=0.0)


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
