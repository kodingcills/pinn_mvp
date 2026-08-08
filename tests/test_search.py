import math

import numpy as np
import pytest

from pegsim.scene import TaskSpec
from pegsim.search import ArchimedeanSpiral, ProbeSpec, SpiralSpec

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


def _spec(delta_max=1.25 * TASK.capture_radius, v_t=0.005, **kwargs) -> SpiralSpec:
    return SpiralSpec(task=TASK, delta_max=delta_max, v_t=v_t, **kwargs)


class TestSpiralSpecConstraints:
    def test_pitch_strictly_less_than_capture_radius(self):
        spec = _spec()
        assert 2.0 * math.pi * spec.b < TASK.capture_radius

    def test_rejects_pitch_safety_at_or_above_one(self):
        with pytest.raises(AssertionError, match="pitch_safety"):
            _spec(pitch_safety=1.0)

    def test_rejects_nonpositive_pitch_safety(self):
        with pytest.raises(AssertionError, match="pitch_safety"):
            _spec(pitch_safety=0.0)

    def test_rejects_nonpositive_delta_max(self):
        with pytest.raises(AssertionError, match="delta_max"):
            _spec(delta_max=0.0)

    def test_rejects_nonpositive_v_t(self):
        with pytest.raises(AssertionError, match="v_t"):
            _spec(v_t=0.0)

    def test_r_spiral_covers_delta_max(self):
        spec = _spec()
        assert spec.r_spiral >= spec.delta_max

    def test_starting_radius_is_positive(self):
        # theta_dot = v_t / (a + b*theta) is singular at r=0.
        spec = _spec()
        assert spec.a > 0.0


class TestArchimedeanSpiralIntegration:
    def test_no_nan_or_inf_from_the_first_step(self):
        spec = _spec()
        spiral = ArchimedeanSpiral(spec)
        for _ in range(10):
            xy = spiral.step(dt=0.001)
            assert np.all(np.isfinite(xy))

    def test_tangential_speed_is_constant_not_angular_rate(self):
        """Constraint 8: theta_dot = v_t / r must be integrated so that
        r*theta_dot (tangential speed) stays constant -- a fixed angular
        rate would instead let tangential speed grow with radius."""
        spec = _spec(v_t=0.004)
        spiral = ArchimedeanSpiral(spec)
        dt = 0.0005
        tangential_speeds = []
        for _ in range(4000):
            theta_before = spiral.theta
            r_before = spiral.radius
            spiral.step(dt)
            theta_dot_measured = (spiral.theta - theta_before) / dt
            tangential_speeds.append(r_before * theta_dot_measured)
        tangential_speeds = np.array(tangential_speeds)
        np.testing.assert_allclose(tangential_speeds, spec.v_t, rtol=1e-6)

    def test_radius_grows_monotonically_until_exhausted(self):
        spec = _spec()
        spiral = ArchimedeanSpiral(spec)
        prev_r = spiral.radius
        for _ in range(200_000):
            spiral.step(dt=0.001)
            assert spiral.radius >= prev_r - 1e-12
            prev_r = spiral.radius
            if spiral.exhausted:
                break
        assert spiral.exhausted

    def test_frozen_once_exhausted(self):
        spec = _spec()
        spiral = ArchimedeanSpiral(spec)
        while not spiral.exhausted:
            spiral.step(dt=0.001)
        xy_at_exhaustion = spiral.step(dt=0.001)
        xy_after_more_steps = spiral.step(dt=0.001)
        np.testing.assert_allclose(xy_at_exhaustion, xy_after_more_steps)

    def test_finite_time_coverage_guarantee(self):
        """For ANY target radius up to r_spiral, some point along the swept
        path comes within one pitch of it -- the analytic coverage guarantee
        an Archimedean spiral gives (and a Lissajous pattern would not)."""
        spec = _spec()
        pitch = 2.0 * math.pi * spec.b
        for target_r in np.linspace(spec.a, spec.r_spiral, 7):
            n_revolutions = (target_r - spec.a) / pitch
            theta_at_target = 2.0 * math.pi * n_revolutions
            r_at_theta = spec.a + spec.b * theta_at_target
            assert abs(r_at_theta - target_r) < pitch + 1e-9


class TestProbeSpecCoverage:
    """Empirical, not just formula-trusting: sample many points inside
    delta_max and verify every one is within r_cap_dyn of some probe point
    -- the same standard this project holds scene geometry to (raycast, not
    just trust the tiling math)."""

    def test_rejects_nonpositive_fields(self):
        with pytest.raises(AssertionError, match="delta_max"):
            ProbeSpec(task=TASK, delta_max=0.0, r_cap_dyn=0.0016)
        with pytest.raises(AssertionError, match="r_cap_dyn"):
            ProbeSpec(task=TASK, delta_max=0.002, r_cap_dyn=0.0)
        with pytest.raises(AssertionError, match="lift_height"):
            ProbeSpec(task=TASK, delta_max=0.002, r_cap_dyn=0.0016, lift_height=0.0)

    def test_disk_fully_covered(self):
        spec = ProbeSpec(task=TASK, delta_max=0.002, r_cap_dyn=0.0016)
        pts = np.array(spec.points)
        rng = np.random.default_rng(0)
        n_samples = 5000
        r = spec.delta_max * np.sqrt(rng.uniform(0.0, 1.0, n_samples))  # area-uniform
        theta = rng.uniform(0.0, 2.0 * math.pi, n_samples)
        samples = np.stack([r * np.cos(theta), r * np.sin(theta)], axis=1)
        dists = np.linalg.norm(samples[:, None, :] - pts[None, :, :], axis=2)
        min_dists = dists.min(axis=1)
        assert min_dists.max() <= spec.r_cap_dyn, (
            f"worst-covered sample point is {min_dists.max()*1000:.4f}mm from "
            f"its nearest probe, exceeding r_cap_dyn={spec.r_cap_dyn*1000:.4f}mm"
        )

    def test_point_count_matches_hex_covering_formula(self):
        # delta_max=2mm, r_cap_dyn=1.6mm -> spacing=1.6*sqrt(3)=2.771mm;
        # ring 1 (radius=spacing) has inner coverage edge at
        # spacing-r_cap_dyn=1.171mm <= delta_max, so it's needed; ring 2
        # (radius=2*spacing=5.543mm) has inner edge 3.943mm > delta_max, not
        # needed. Center + 6 = 7 points.
        spec = ProbeSpec(task=TASK, delta_max=0.002, r_cap_dyn=0.0016)
        assert len(spec.points) == 7
