import math

import numpy as np
import pytest

from pegsim.control import ImpedanceGains, assert_gains_stable
from pegsim.rollout import (
    VALIDATED_LOOSE_V1,
    EpisodeConfig,
    InsertionProfile,
    assert_seating_trigger_reachable,
    axis_xy_at_z,
    ground_truth_seated,
    hole_center_xy,
    run_episode,
)
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf
from pegsim.search import DELTA_MAX_SPEC_M, SpiralSpec, assert_search_force_budget

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
GAINS = ImpedanceGains(k_xy=500.0, k_rx=1.0, k_ry=1.0)
INSERTION = InsertionProfile()


class TestValidatedLooseV1:
    """Milestone 4 G4-escalated: VALIDATED_LOOSE_V1 bundles every parameter
    that must move together (see its docstring). Guards against silent
    drift in the bundle itself -- if any of its own asserts start failing,
    that's a real regression, not a config-mismatch symptom."""

    def test_gains_stable(self):
        import mujoco
        model = mujoco.MjModel.from_xml_string(
            build_mjcf(VALIDATED_LOOSE_V1.task, VALIDATED_LOOSE_V1.solver)
        )
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        assert_gains_stable(model, data, VALIDATED_LOOSE_V1.gains, VALIDATED_LOOSE_V1.solver.timestep)

    def test_seating_trigger_reachable(self):
        assert_seating_trigger_reachable(VALIDATED_LOOSE_V1.insertion, VALIDATED_LOOSE_V1.gains)

    def test_search_force_budget(self):
        r_cap_dyn = 0.0016  # measured, scripts/measure_c2a_dynamic_capture_radius.py
        spiral_spec = SpiralSpec(task=VALIDATED_LOOSE_V1.task, delta_max=DELTA_MAX_SPEC_M,
                                  v_t=0.005)
        assert_search_force_budget(
            VALIDATED_LOOSE_V1.task, VALIDATED_LOOSE_V1.gains.k_xy,
            VALIDATED_LOOSE_V1.insertion.f_search, spiral_spec, r_cap_dyn,
        )


class TestInsertionProfileConstraints:
    def test_rejects_f_target_at_or_above_f_yield(self):
        with pytest.raises(AssertionError, match="f_target"):
            InsertionProfile(f_target=25.0, f_yield=25.0)

    def test_rejects_nonpositive_v_descent(self):
        with pytest.raises(AssertionError, match="v_descent"):
            InsertionProfile(v_descent=0.0)


class TestEpisodeConfigConstraints:
    def test_rejects_nonpositive_z_start(self):
        with pytest.raises(AssertionError, match="z_start"):
            EpisodeConfig(TASK, SOLVER, GAINS, INSERTION, z_start=-0.01,
                          initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=1.0)

    def test_default_target_depth_is_derived_from_plate_thickness(self):
        cfg = EpisodeConfig(TASK, SOLVER, GAINS, INSERTION, z_start=0.005,
                             initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=1.0)
        assert cfg.target_depth == pytest.approx(TASK.plate_thickness - 0.001)


class TestAxisXyAtZ:
    def test_untilted_axis_reads_tip_xy_everywhere(self):
        tip_pos = np.array([0.001, -0.002, -0.03])
        axis_dir = np.array([0.0, 0.0, 1.0])  # straight up, no tilt
        xy = axis_xy_at_z(tip_pos, axis_dir, 0.0)
        np.testing.assert_allclose(xy, tip_pos[:2])

    def test_tilted_axis_shifts_reading_at_the_mouth(self):
        # Axis tilted 1deg about y: tip at z=-0.04, mouth 40mm higher at z=0.
        tilt = math.radians(1.0)
        axis_dir = np.array([math.sin(tilt), 0.0, math.cos(tilt)])
        tip_pos = np.array([0.0, 0.0, -0.04])
        xy = axis_xy_at_z(tip_pos, axis_dir, 0.0)
        expected_x = 0.04 * math.tan(tilt)  # lever arm * tan(tilt)
        assert xy[0] == pytest.approx(expected_x, abs=1e-9)


class TestHoleCenterXy:
    def test_mouth_is_always_world_origin_regardless_of_tilt(self):
        for tilt_deg in (0.0, 5.0, -20.0):
            np.testing.assert_allclose(hole_center_xy(0.0, tilt_deg), [0.0, 0.0], atol=1e-12)

    def test_zero_tilt_is_world_origin_at_any_depth(self):
        np.testing.assert_allclose(hole_center_xy(-0.02, 0.0), [0.0, 0.0], atol=1e-12)

    def test_tilted_hole_shifts_center_at_depth(self):
        tilt_deg = 10.0
        z = -0.01
        expected_x = z * math.tan(math.radians(tilt_deg))
        xy = hole_center_xy(z, tilt_deg)
        assert xy[0] == pytest.approx(expected_x)
        assert xy[1] == 0.0

    def test_seated_check_uses_hole_center_not_world_origin_when_tilted(self):
        """A peg exactly ON the tilted hole's centerline at depth must read
        as centered, even though it is off world-origin there."""
        tilt_deg = 10.0
        z_tip = -0.02
        tip_pos = np.array([hole_center_xy(z_tip, tilt_deg)[0], 0.0, z_tip])
        axis_dir = np.array([math.sin(math.radians(tilt_deg)), 0.0, math.cos(math.radians(tilt_deg))])
        force = np.array([0.0, 0.0, -1.0])
        assert ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001, 0.0, tilt_deg)
        # Same physical peg, checked against world-origin (tilt=0 assumption)
        # instead, must NOT read as centered -- this is the bug being guarded.
        assert not ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001, 0.0, 0.0)


class TestGroundTruthSeated:
    def test_rejects_shallow_depth(self):
        tip_pos = np.array([0.0, 0.0, -0.001])  # nowhere near the floor
        axis_dir = np.array([0.0, 0.0, 1.0])
        force = np.zeros(3)
        assert not ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001, 0.0)

    def test_rejects_tip_lateral_beyond_clearance_plus_allowance(self):
        tip_pos = np.array([0.001, 0.0, -0.02])  # deep enough, but 1mm off axis
        axis_dir = np.array([0.0, 0.0, 1.0])
        force = np.zeros(3)
        assert not ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001, 0.0)

    def test_pen_allow_admits_a_reading_just_over_clearance(self):
        # 0.5005mm, 5 microns over clearance -- fails with pen_allow=0, passes
        # with a pen_allow measured from real interpenetration.
        tip_pos = np.array([0.0005005, 0.0, -0.02])
        axis_dir = np.array([0.0, 0.0, 1.0])
        force = np.zeros(3)
        assert not ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001, 0.0)
        assert ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001, 1e-5)

    def test_rejects_mouth_lateral_beyond_clearance_even_if_tip_centered(self):
        # Tip reads centered, but enough tilt puts the mouth crossing 1mm off.
        tip_pos = np.array([0.0, 0.0, -0.02])
        tilt = math.atan(0.001 / 0.02)
        axis_dir = np.array([math.sin(tilt), 0.0, math.cos(tilt)])
        force = np.zeros(3)
        assert not ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001, 0.0)

    def test_accepts_deep_centered_untilted_low_lateral_force(self):
        tip_pos = np.array([0.0, 0.0, -0.02])
        axis_dir = np.array([0.0, 0.0, 1.0])
        force = np.array([0.0, 0.0, -1.0])
        assert ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001, 0.0)


class TestRunEpisode:
    def test_zero_offset_no_spiral_seats(self):
        cfg = EpisodeConfig(
            TASK, SOLVER, GAINS, INSERTION, z_start=0.005,
            initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=6.0,
        )
        log = run_episode(cfg)
        assert log["seated"]
        assert log["time_to_seat_s"] < 6.0

    def test_zero_offset_with_spiral_still_seats(self):
        """The search overlay must not destabilize or prevent the trivial
        (already-aligned) case from seating."""
        spiral_spec = SpiralSpec(task=TASK, delta_max=DELTA_MAX_SPEC_M)
        cfg = EpisodeConfig(
            TASK, SOLVER, GAINS, INSERTION, z_start=0.005,
            initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=10.0,
            spiral_spec=spiral_spec,
        )
        log = run_episode(cfg)
        assert log["seated"]

    def test_settled_force_stays_near_f_target(self):
        """A1 fix: settled axial force should track f_target (default 15N),
        not the old stale-ramp-driven 70N."""
        cfg = EpisodeConfig(
            TASK, SOLVER, GAINS, INSERTION, z_start=0.005,
            initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=6.0,
        )
        log = run_episode(cfg)
        settled_fz = abs(log["contact_force"][-200:, 2].mean())
        assert settled_fz == pytest.approx(INSERTION.f_target, rel=0.1)

    def test_peak_force_stays_under_f_yield(self):
        cfg = EpisodeConfig(
            TASK, SOLVER, GAINS, INSERTION, z_start=0.005,
            initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=6.0,
        )
        log = run_episode(cfg)
        peak_fz = float(np.abs(log["contact_force"][:, 2]).max())
        assert peak_fz < INSERTION.f_yield
