import pytest

from pegsim.control import ImpedanceGains
from pegsim.rollout import EpisodeConfig, run_episode
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

    def test_default_seat_thresholds_are_derived(self):
        cfg = EpisodeConfig(TASK, SOLVER, GAINS, z_start=0.005, z_end=-0.045, z_rate=0.01,
                             initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=1.0)
        assert cfg.seat_z_threshold == pytest.approx(-0.043)
        assert cfg.seat_lateral_threshold == pytest.approx(TASK.capture_radius)


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
