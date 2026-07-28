import math

import numpy as np
import pytest

from pegsim.control import ImpedanceGains
from pegsim.rollout import EpisodeConfig, axis_xy_at_z, ground_truth_seated, run_episode
from pegsim.scene import SolverSpec, TaskSpec
from pegsim.search import SpiralSpec

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
GAINS = ImpedanceGains()


class TestEpisodeConfigConstraints:
    def test_rejects_z_start_below_z_end(self):
        with pytest.raises(AssertionError, match="z_start"):
            EpisodeConfig(TASK, SOLVER, GAINS, z_start=-0.01, z_end=0.005, z_rate=0.01,
                          initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=1.0)

    def test_rejects_nonpositive_z_rate(self):
        with pytest.raises(AssertionError, match="z_rate"):
            EpisodeConfig(TASK, SOLVER, GAINS, z_start=0.005, z_end=-0.01, z_rate=0.0,
                          initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=1.0)

    def test_default_target_depth_is_derived_from_plate_thickness(self):
        cfg = EpisodeConfig(TASK, SOLVER, GAINS, z_start=0.005, z_end=-0.045, z_rate=0.01,
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


class TestGroundTruthSeated:
    def test_rejects_shallow_depth(self):
        tip_pos = np.array([0.0, 0.0, -0.001])  # nowhere near the floor
        axis_dir = np.array([0.0, 0.0, 1.0])
        force = np.zeros(3)
        assert not ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001)

    def test_rejects_tip_lateral_beyond_clearance(self):
        tip_pos = np.array([0.001, 0.0, -0.02])  # deep enough, but 1mm off axis
        axis_dir = np.array([0.0, 0.0, 1.0])
        force = np.zeros(3)
        assert not ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001)

    def test_rejects_mouth_lateral_beyond_clearance_even_if_tip_centered(self):
        # Tip reads centered, but enough tilt puts the mouth crossing 1mm off.
        tip_pos = np.array([0.0, 0.0, -0.02])
        tilt = math.atan(0.001 / 0.02)
        axis_dir = np.array([math.sin(tilt), 0.0, math.cos(tilt)])
        force = np.zeros(3)
        assert not ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001)

    def test_rejects_high_lateral_force_relative_to_axial_wedge_bound(self):
        tip_pos = np.array([0.0, 0.0, -0.02])
        axis_dir = np.array([0.0, 0.0, 1.0])
        axial = 10.0
        f_wedge = axial / (2.0 * TASK.friction)
        force = np.array([f_wedge * 1.1, 0.0, -axial])
        assert not ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001)

    def test_accepts_deep_centered_untilted_low_lateral_force(self):
        tip_pos = np.array([0.0, 0.0, -0.02])
        axis_dir = np.array([0.0, 0.0, 1.0])
        force = np.array([0.0, 0.0, -1.0])
        assert ground_truth_seated(tip_pos, axis_dir, force, TASK, TASK.plate_thickness - 0.001)


class TestRunEpisode:
    def test_zero_offset_no_spiral_seats(self):
        cfg = EpisodeConfig(
            TASK, SOLVER, GAINS, z_start=0.005, z_end=-0.045, z_rate=0.01,
            initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=6.0,
        )
        log = run_episode(cfg)
        assert log["seated"]
        assert log["time_to_seat_s"] < 6.0

    def test_zero_offset_with_spiral_still_seats(self):
        """The search overlay must not destabilize or prevent the trivial
        (already-aligned) case from seating."""
        spiral_spec = SpiralSpec(task=TASK, delta_max=1.25 * TASK.capture_radius)
        cfg = EpisodeConfig(
            TASK, SOLVER, GAINS, z_start=0.005, z_end=-0.045, z_rate=0.01,
            initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=10.0,
            spiral_spec=spiral_spec,
        )
        log = run_episode(cfg)
        assert log["seated"]
