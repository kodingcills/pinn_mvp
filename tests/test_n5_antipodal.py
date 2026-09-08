"""Permanent tests for the N5 antipodal campaign driver (R01).

Covers: exact locked design, per-level scene identity, opposite-direction
construction, commanded-frame decomposition, pair-mean/half-difference math
and ordering invariance, end-to-end analysis on synthetic perfect probes,
failure-record schema completeness, the committed-N4 penetration constant,
and the no-estimator scoping rule."""
import ast
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pegsim.rollout import VALIDATED_LOOSE_V1
import scripts.measure_n5_antipodal as n5
from scripts.measure_n5_antipodal import (
    DC_LEVELS_M,
    N_PAIRS,
    assemble_pairs,
    assert_preflight,
    build_trial_specs,
    decompose_error,
    estimate_hole_xy,
    load_penetration_m,
    make_episode,
    minus_probe_bearing_rad,
    pair_quantities,
    scene_key,
    _failure_record,
)


def test_locked_design_is_exact():
    specs = build_trial_specs()
    n5b_count = len(DC_LEVELS_M) * N_PAIRS * 2
    n5c_count = N_PAIRS * 2
    assert len(specs) == n5b_count + n5c_count == 768
    assert [spec.trial_id for spec in specs] == list(range(len(specs)))

    n5b = [spec for spec in specs if spec.campaign == "n5b"]
    n5c = [spec for spec in specs if spec.campaign == "n5c"]
    assert len(n5b) == n5b_count and len(n5c) == n5c_count
    assert {spec.dc_level_m for spec in n5c} == {0.0}
    assert {spec.dc_level_m for spec in n5b} == set(DC_LEVELS_M)

    for campaign_specs, campaign in ((n5b, "n5b"), (n5c, "n5c")):
        for pair_index in range(N_PAIRS):
            pair = [s for s in campaign_specs
                    if s.pair_index == pair_index and s.dc_level_m == 0.0]
            sides = {spec.probe_side for spec in pair}
            assert sides == {"plus", "minus"}
            plus = next(s for s in pair if s.probe_side == "plus")
            minus = next(s for s in pair if s.probe_side == "minus")
            expected_pair_bearing = pair_index * n5.GOLDEN_ANGLE_RAD
            assert plus.pair_bearing_rad == minus.pair_bearing_rad
            assert plus.probe_axis_rad == pytest.approx(expected_pair_bearing)
            assert minus.probe_axis_rad == pytest.approx(
                minus_probe_bearing_rad(expected_pair_bearing, campaign)
            )


def test_minus_bearing_construction_opposite_and_half_facet():
    bearing = 1.234
    n5b_minus = minus_probe_bearing_rad(bearing, "n5b")
    n5c_minus = minus_probe_bearing_rad(bearing, "n5c")
    assert n5b_minus == pytest.approx(bearing + math.pi)
    assert n5c_minus == pytest.approx(bearing + math.pi + math.pi / 64.0)
    u = np.array([math.cos(bearing), math.sin(bearing)])
    u_minus = np.array([math.cos(n5b_minus), math.sin(n5b_minus)])
    np.testing.assert_allclose(u_minus, -u, atol=1e-15)
    with pytest.raises(ValueError):
        minus_probe_bearing_rad(bearing, "n5x")


def test_only_clearance_and_n_segments_change_one_scene_per_level():
    specs = build_trial_specs()
    assert_preflight(specs)
    canonical = VALIDATED_LOOSE_V1
    assert canonical.task.n_segments == 16
    keys = set()
    for dc in DC_LEVELS_M:
        level_specs = [s for s in specs if s.dc_level_m == dc]
        episodes = [make_episode(level_specs[0]), make_episode(level_specs[-1])]
        assert len({scene_key(e) for e in episodes}) == 1
        keys.add(scene_key(episodes[0]))
        for episode in episodes:
            assert episode.task == replace(
                canonical.task,
                n_segments=64,
                radial_clearance=canonical.task.radial_clearance + dc,
            )
            assert episode.solver == canonical.solver
            assert episode.gains == canonical.gains
            assert episode.insertion == canonical.insertion
            assert episode.spiral_spec is None
            assert episode.initial_offset_r == 2.0 * canonical.task.radial_clearance
    assert len(keys) == len(DC_LEVELS_M)


def test_probe_estimator_recovers_known_hole_center():
    clearance = n5.nominal_clearance_m()
    bearing = 2.5
    hole = np.array([30e-6, -20e-6])
    tip = hole + clearance * np.array([math.cos(bearing), math.sin(bearing)])
    np.testing.assert_allclose(
        estimate_hole_xy(tip, bearing, clearance), hole, rtol=0.0,
        atol=8.0 * np.finfo(float).eps,
    )


def test_error_decomposition_uses_commanded_frame_and_reconstructs_norm():
    bearing = 0.9
    radial = 7e-6
    tangential = -3e-6
    u = np.array([math.cos(bearing), math.sin(bearing)])
    u_perp = np.array([-math.sin(bearing), math.cos(bearing)])
    error = radial * u + tangential * u_perp
    got_radial, got_tangential = decompose_error(error, bearing)
    assert got_radial == pytest.approx(radial, rel=1e-12)
    assert got_tangential == pytest.approx(tangential, rel=1e-12)
    reconstructed = math.hypot(got_radial, got_tangential)
    assert reconstructed == pytest.approx(float(np.linalg.norm(error)), rel=1e-12)


def test_pair_quantities_cancel_common_radius_and_expose_difference():
    penetration = load_penetration_m()
    bearing = 0.4
    u = np.array([math.cos(bearing), math.sin(bearing)])
    u_perp = np.array([-math.sin(bearing), math.cos(bearing)])
    dc = 5e-6
    # Perfect probes: each side rests at the true radius r = c + dc - pen.
    r = n5.nominal_clearance_m() + dc - penetration
    hole = np.array([12e-6, -8e-6])
    est_plus = hole + r * u - n5.nominal_clearance_m() * u
    est_minus = hole - r * u + n5.nominal_clearance_m() * u
    pair = n5.PairMeasurement(
        campaign="n5b",
        dc_level_m=dc,
        pair_index=0,
        pair_bearing_rad=bearing,
        hole_est_plus_xy_m=est_plus,
        hole_est_minus_xy_m=est_minus,
        error_plus_xy_m=est_plus - hole,
        error_minus_xy_m=est_minus - hole,
    )
    quantities = pair_quantities(pair)
    assert quantities["pair_mean_error_norm_m"] == pytest.approx(0.0, abs=1e-15)
    assert quantities["pair_mean_error_radial_m"] == pytest.approx(0.0, abs=1e-15)
    assert quantities["pair_half_difference_along_u_m"] == pytest.approx(
        dc - penetration, rel=1e-12
    )
    assert quantities["dc_estimate_m"] == pytest.approx(dc, rel=1e-12)


def test_pair_ordering_does_not_change_the_mean_and_flips_the_difference():
    bearing = 2.0
    u = np.array([math.cos(bearing), math.sin(bearing)])
    est_plus = np.array([3e-6, 1e-6])
    est_minus = np.array([-1e-6, 2e-6])
    error_plus = est_plus
    error_minus = est_minus

    def make(first_side, second_side):
        return n5.PairMeasurement(
            campaign="n5b",
            dc_level_m=0.0,
            pair_index=0,
            pair_bearing_rad=bearing,
            hole_est_plus_xy_m=est_plus if first_side == "plus" else est_minus,
            hole_est_minus_xy_m=est_minus if second_side == "minus" else est_plus,
            error_plus_xy_m=error_plus if first_side == "plus" else error_minus,
            error_minus_xy_m=error_minus if second_side == "minus" else error_plus,
        )

    original = pair_quantities(make("plus", "minus"))
    swapped = pair_quantities(make("minus", "plus"))
    assert original["pair_mean_error_norm_m"] == pytest.approx(
        swapped["pair_mean_error_norm_m"], rel=1e-15
    )
    np.testing.assert_allclose(
        [
            original["pair_mean_error_radial_m"],
            original["pair_mean_error_tangential_m"],
        ],
        [
            swapped["pair_mean_error_radial_m"],
            swapped["pair_mean_error_tangential_m"],
        ],
        rtol=0.0,
        atol=1e-18,
    )
    assert original["pair_half_difference_along_u_m"] == pytest.approx(
        -swapped["pair_half_difference_along_u_m"], rel=1e-15
    )


def _synthetic_records() -> list[dict[str, object]]:
    """Perfect radial probes: tip = (c_nom + dc - pen) * u, hole at origin.

    Produces the exact locked design so the full analysis path runs without
    physics."""
    penetration = load_penetration_m()
    clearance = n5.nominal_clearance_m()
    records = []
    for spec in build_trial_specs():
        u = np.array([
            math.cos(spec.probe_axis_rad), math.sin(spec.probe_axis_rad),
        ])
        radius = clearance + spec.dc_level_m - penetration
        tip = radius * u
        hole_est = tip - clearance * u
        error = hole_est
        radial, tangential = decompose_error(error, spec.pair_bearing_rad)
        records.append({
            "trial_id": spec.trial_id,
            "campaign": spec.campaign,
            "dc_level_m": spec.dc_level_m,
            "true_clearance_m": n5.true_clearance_m(spec.dc_level_m),
            "pair_index": spec.pair_index,
            "probe_side": spec.probe_side,
            "probe_axis_rad": spec.probe_axis_rad,
            "pair_bearing_rad": spec.pair_bearing_rad,
            "success": True,
            "exclusion_reason": None,
            "settled_tip_xy_m": tip.tolist(),
            "settled_tip_radius_m": radius,
            "measured_tip_bearing_rad": spec.probe_axis_rad,
            "bearing_error_rad": 0.0,
            "tilt_at_settle_rad": 0.0,
            "hole_est_xy_m": hole_est.tolist(),
            "true_hole_xy_m": [0.0, 0.0],
            "error_xy_m": error.tolist(),
            "error_norm_m": float(np.linalg.norm(error)),
            "error_radial_m": radial,
            "error_tangential_m": tangential,
            "time_to_seat_s": 2.0,
            "settled_contact_geoms": [["bore_0", "peg_geom"]],
            "seed": None,
            "schema_version": n5.SCHEMA_VERSION,
        })
        if spec.campaign == "n5c" and spec.probe_side == "minus":
            # The N5c minus error points along the rotated bearing, so its
            # pair-frame tangential component is the projection
            # (dc - pen) * sin(pi + pi/n) = -(dc - pen) * sin(pi/n).
            expected_tangential = -(spec.dc_level_m - penetration) * math.sin(
                math.pi / n5.scene_n_segments()
            )
            assert tangential == pytest.approx(expected_tangential, rel=1e-9)
        else:
            assert tangential == pytest.approx(0.0, abs=1e-18)
    return records


def test_analysis_recovers_exact_dc_from_synthetic_perfect_probes():
    penetration = load_penetration_m()
    summary = n5.analyze(_synthetic_records())

    checks = summary["pre_registered_checks"]
    assert checks["difference_slope_measured"] == pytest.approx(2.0, rel=1e-9)
    assert checks["difference_intercept_measured_m"] == pytest.approx(
        -2.0 * penetration, rel=1e-9
    )
    assert checks["pair_mean_radial_dc0_mean_m"] == pytest.approx(0.0, abs=1e-15)
    assert checks["antipodal_mean_error_dc0_max_m"] == pytest.approx(0.0, abs=1e-15)

    for key, level in summary["by_level"].items():
        dc = level["dc_level_m"]
        assert level["n_probes"] == 2 * N_PAIRS
        assert level["n_pairs"] == N_PAIRS
        np.testing.assert_allclose(
            level["dc_estimate_m"]["mean"], dc, rtol=1e-9, atol=1e-15
        )
        assert level["dc_estimate_error_m"]["max"] == pytest.approx(0.0, abs=1e-15)
    assert summary["incomplete_pairs"] == []
    assert summary["n_success"] == summary["n_trials"] == 768


def test_incomplete_pairs_are_reported_not_dropped():
    records = _synthetic_records()
    dropped = next(
        r for r in records if r["probe_side"] == "minus" and r["pair_index"] == 3
    )
    records.remove(dropped)
    successful = [r for r in records if r["success"]]
    pairs, incomplete = assemble_pairs(successful)
    assert len(incomplete) == 1
    assert incomplete[0]["pair_index"] == 3
    assert incomplete[0]["reason"] == "missing minus side"
    assert len(pairs) == 5 * N_PAIRS + N_PAIRS - 1


def test_failure_record_carries_complete_schema():
    spec = build_trial_specs()[0]
    record = _failure_record(spec, "not seated before max_seconds")
    required = {
        "trial_id", "campaign", "dc_level_m", "true_clearance_m", "pair_index",
        "probe_side", "probe_axis_rad", "pair_bearing_rad", "success",
        "exclusion_reason", "settled_tip_xy_m", "settled_tip_radius_m",
        "measured_tip_bearing_rad", "bearing_error_rad", "tilt_at_settle_rad",
        "hole_est_xy_m", "true_hole_xy_m", "error_xy_m", "error_norm_m",
        "error_radial_m", "error_tangential_m", "time_to_seat_s",
        "settled_contact_geoms", "seed", "schema_version",
    }
    assert required <= set(record)
    assert record["success"] is False
    assert record["exclusion_reason"] == "not seated before max_seconds"
    assert record["hole_est_xy_m"] is None
    assert record["seed"] is None


def test_penetration_constant_is_the_committed_n4_value():
    assert load_penetration_m() == pytest.approx(2.9630093418717097e-06, rel=1e-12)
    original = n5.N4_SUMMARY_PATH
    try:
        n5.N4_SUMMARY_PATH = Path("/nonexistent/n4_summary.json")
        n5.load_penetration_m.cache_clear()
        with pytest.raises(OSError):
            load_penetration_m()
    finally:
        n5.N4_SUMMARY_PATH = original
        n5.load_penetration_m.cache_clear()


def test_n5_driver_does_not_import_the_estimator():
    source = Path("scripts/measure_n5_antipodal.py").read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    assert not any(name.startswith("pegsim.filter") for name in imported)


def test_run_probe_trial_requires_worker_initialization():
    spec = build_trial_specs()[0]
    with pytest.raises(AssertionError):
        n5.run_probe_trial(spec)
