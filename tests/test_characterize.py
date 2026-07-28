import math

from pegsim.characterize import find_r_passive, run_offset_sweep, success_rate_by_offset
from pegsim.control import ImpedanceGains
from pegsim.scene import SolverSpec, TaskSpec

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


class TestRunOffsetSweep:
    def test_returns_one_row_per_offset_angle_pair(self):
        results = run_offset_sweep(
            TASK, SolverSpec(), ImpedanceGains(),
            offsets_m=[0.0, 0.001], angles_rad=[0.0, math.pi / 2],
            z_start=0.005, z_end=-0.045, z_rate=0.01, max_seconds=6.0,
        )
        assert len(results) == 4
        assert all("seated" in r and "max_tilt_deg" in r for r in results)

    def test_unstable_trial_is_recorded_not_raised(self):
        # k_rz=5 exceeds its stability cap for this peg at dt=0.001 (see
        # control.py) -- confirms the sweep survives a trial that asserts.
        results = run_offset_sweep(
            TASK, SolverSpec(), ImpedanceGains(k_rz=5.0),
            offsets_m=[0.002], angles_rad=[0.0],
            z_start=0.005, z_end=-0.045, z_rate=0.01, max_seconds=1.0,
        )
        assert len(results) == 1
        assert results[0]["unstable"]
        assert not results[0]["seated"]


class TestAggregation:
    def test_success_rate_by_offset(self):
        results = [
            {"offset_m": 0.001, "seated": True},
            {"offset_m": 0.001, "seated": False},
            {"offset_m": 0.002, "seated": False},
        ]
        rates = success_rate_by_offset(results)
        assert rates[0.001] == 0.5
        assert rates[0.002] == 0.0

    def test_find_r_passive_picks_largest_offset_at_or_above_half(self):
        rates = {0.001: 1.0, 0.002: 1.0, 0.003: 0.4, 0.004: 0.0}
        assert find_r_passive(rates) == 0.002

    def test_find_r_passive_none_if_nothing_passes(self):
        assert find_r_passive({0.001: 0.2, 0.002: 0.0}) is None
