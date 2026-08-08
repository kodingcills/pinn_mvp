import math

import numpy as np

from pegsim.rollout import VALIDATED_LOOSE_V1
from scripts.measure_n2_probe_primitive import (
    GOLDEN_ANGLE_RAD,
    MEASURED_R_PASSIVE_M,
    assert_preflight,
    estimate_hole_xy,
    make_episode,
    probe_radius_m,
    scene_key,
    wrap_angle_rad,
)


def test_probe_geometry_and_scene_invariance():
    cfg = VALIDATED_LOOSE_V1
    bearings = np.arange(cfg.task.n_segments, dtype=float) * GOLDEN_ANGLE_RAD
    assert_preflight(bearings)

    assert probe_radius_m() == 2.0 * cfg.task.radial_clearance
    assert cfg.task.radial_clearance < probe_radius_m() < MEASURED_R_PASSIVE_M
    assert probe_radius_m() < cfg.r_cap_dyn
    episodes = [make_episode(float(bearing)) for bearing in bearings]
    assert all(episode.spiral_spec is None for episode in episodes)
    assert len({scene_key(episode) for episode in episodes}) == 1


def test_probe_estimator_recovers_known_hole_center():
    cfg = VALIDATED_LOOSE_V1
    bearing = GOLDEN_ANGLE_RAD
    direction = np.array([math.cos(bearing), math.sin(bearing)])
    true_hole_xy = np.array([
        cfg.task.radial_clearance,
        -cfg.task.radial_clearance,
    ])
    settled_tip_xy = true_hole_xy + cfg.task.radial_clearance * direction

    estimate = estimate_hole_xy(
        settled_tip_xy, bearing, cfg.task.radial_clearance,
    )

    np.testing.assert_allclose(
        estimate,
        true_hole_xy,
        atol=10.0 * np.finfo(float).eps,
    )


def test_bearing_error_wraps_to_shortest_signed_angle():
    quarter_turn = math.pi / 2.0
    np.testing.assert_allclose(
        wrap_angle_rad(3.0 * quarter_turn),
        -quarter_turn,
        atol=np.finfo(float).eps,
    )
    np.testing.assert_allclose(
        wrap_angle_rad(-3.0 * quarter_turn),
        quarter_turn,
        atol=np.finfo(float).eps,
    )
