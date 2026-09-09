"""R05 locked-experiment gate tests (G1-G8 of R05-v1.yaml).

Synthetic-trace tests are fast; exactly one test runs real physics (the
truth-poison regression G1 plus guidance recentering G4), with MAX_SECONDS
shortened to keep the suite fast. The locked experiment itself always uses
the module constants; monkeypatching here never leaks into other tests.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pytest

import pegsim.r05 as r05
from pegsim.filter import StateFilter
from pegsim.rollout import TrialResult

N = 400  # synthetic trace length (samples)


def _synthetic_trace(
    *,
    depth_m: np.ndarray,
    fz_n: np.ndarray,
    tilt_deg: np.ndarray,
    tip_xy: Optional[np.ndarray] = None,
) -> TrialResult:
    """Build a minimal TrialResult whose observable telemetry matches the
    given profiles. dt = 0.5 ms, so 200 samples = 0.1 s."""
    n = depth_m.size
    time = np.arange(n) * 0.0005
    tip = np.zeros((n, 3)) if tip_xy is None else np.column_stack([
        tip_xy, np.zeros(n, dtype=float),
    ])
    # z_flat - tip_z = depth  =>  tip_z = z_flat - depth, z_flat = 0
    tip[:, 2] = -depth_m
    zeros = np.zeros(n)
    return TrialResult(
        time=time,
        tip_pos=tip,
        setpoint_xy=np.zeros((n, 2)),
        z_des=np.zeros(n),
        min_contact_dist=np.zeros(n),
        contact_force=np.column_stack([
            np.zeros(n), np.zeros(n), fz_n,
        ]),
        tip_lateral=np.linalg.norm(tip[:, :2], axis=1),
        mouth_lateral=np.zeros(n),
        tilt_deg=tilt_deg,
        max_tilt_deg=float(np.max(tilt_deg)),
        f_cmd_z=np.zeros(n),
        committed=np.zeros(n, dtype=bool),
        spiral_theta=np.zeros(n),
        z_flat=np.zeros(n),
        control_tick=np.ones(n, dtype=bool),
        qfrc_applied=np.zeros((n, 6)),
        searching=np.zeros(n, dtype=bool),
        spiral_advanced=np.zeros(n, dtype=bool),
        paused_fraction=0.0,
        max_f_cmd_z_step=0.0,
        max_f_cmd_z_step_global=0.0,
        success=False,
        time_to_seat_s=0.0,
        capture_step=None,
        capture_tip_lateral=None,
        capture_mouth_lateral=None,
        spiral_exhausted=None,
        z_flat_final=None,
    )


# ---------------------------------------------------------------------------
# G8: locked evidence fingerprints
# ---------------------------------------------------------------------------


def test_g8_evidence_fingerprints_pass_on_committed_files() -> None:
    r05.assert_evidence_fingerprints()


def test_g8_fingerprints_fail_closed_on_drift(monkeypatch) -> None:
    monkeypatch.setattr(r05, "LOCKED_PENETRATION_BIAS_M", 9.9e-6)
    try:
        r05.assert_evidence_fingerprints()
    except RuntimeError as exc:
        assert "penetration bias drifted" in str(exc)
    else:
        raise AssertionError("fingerprint check did not fail closed")


# ---------------------------------------------------------------------------
# Scenario generation (locked RNG streams)
# ---------------------------------------------------------------------------


def test_dev_and_holdout_manifests_have_locked_counts() -> None:
    dev = r05.generate_manifest(
        "dev", master_seed=r05.DEV_MASTER_SEED,
        strata=r05.DEV_STRATA, n_per_stratum=r05.DEV_N_PER_STRATUM,
    )
    heldout = r05.generate_manifest(
        "heldout", master_seed=r05.HELDOUT_MASTER_SEED,
        strata=r05.HELDOUT_STRATA, n_per_stratum=r05.HELDOUT_N_PER_STRATUM,
    )
    assert len(dev) == 20
    assert len(heldout) == 80


def test_scenario_generation_is_deterministic_and_canonical() -> None:
    a = r05.generate_scenario(r05.DEV_MASTER_SEED, r05.DEV_STRATA[2], 3, 4)
    b = r05.generate_scenario(r05.DEV_MASTER_SEED, r05.DEV_STRATA[2], 3, 4)
    assert a == b
    j1 = r05.canonical_manifest_json([a])
    j2 = r05.canonical_manifest_json([a])
    assert j1 == j2


def test_scenario_ranges_honor_locked_strata() -> None:
    heldout = r05.generate_manifest(
        "holdout", master_seed=r05.HELDOUT_MASTER_SEED,
        strata=r05.HELDOUT_STRATA, n_per_stratum=r05.HELDOUT_N_PER_STRATUM,
    )
    assert len(heldout) == 80
    for s in heldout:
        lo, hi = r05.HELDOUT_STRATA[s.stratum_id].fixture_radius_m
        assert lo <= s.fixture_radius_m <= hi
        if s.stratum_id == 0:
            assert s.clearance_offset_m == 0.0
            assert s.tcp_translation_m is None
        else:
            assert s.clearance_offset_m in (-1e-5, -5e-6, 5e-6, 1e-5)
        if s.stratum_id == 2:
            assert s.tcp_translation_m is not None
            assert s.tcp_yaw_rad in (0.01, -0.01)
            assert s.encoder_resolution_m == 2e-6
        else:
            assert s.tcp_translation_m is None and s.tcp_yaw_rad is None
        assert 0.0 <= s.offset_bearing_rad < 2 * math.pi
        assert 0.0 <= s.first_probe_bearing_rad < 2 * math.pi
        assert s.arm_order_bit in (0, 1)


# ---------------------------------------------------------------------------
# G2 / G3: probe command construction and task split
# ---------------------------------------------------------------------------


def test_g2_probe_commands_are_c0_plus_minus_radius_u() -> None:
    _, control = r05.build_tasks(0.0)
    c0 = np.array([0.0012, -0.0007])
    theta = 1.234
    u = np.array([math.cos(theta), math.sin(theta)])
    plus_cfg = r05.probe_episode_at(control, c0, theta)
    minus_cfg = r05.probe_episode_at(control, c0, theta + math.pi)
    for cfg, expected_start in (
        (plus_cfg, c0 + r05.PROBE_RADIUS_M * u),
        (minus_cfg, c0 - r05.PROBE_RADIUS_M * u),
    ):
        rebuilt = np.array([
            cfg.initial_offset_r * math.cos(cfg.initial_offset_theta),
            cfg.initial_offset_r * math.sin(cfg.initial_offset_theta),
        ])
        assert np.allclose(rebuilt, expected_start, rtol=0.0, atol=1e-15)
        assert cfg.spiral_spec is None


def test_g3_physical_and_control_tasks_differ_by_dc_only() -> None:
    dc = 7e-6
    physical, control = r05.build_tasks(dc)
    assert physical.radial_clearance == r05.NOMINAL_CLEARANCE_M + dc
    assert control.radial_clearance == r05.NOMINAL_CLEARANCE_M
    for field in (
        "peg_radius", "chamfer_width", "plate_thickness",
        "peg_length", "peg_mass", "n_segments", "friction",
    ):
        assert getattr(physical, field) == getattr(control, field)


# ---------------------------------------------------------------------------
# Operational completion (locked criterion)
# ---------------------------------------------------------------------------


def _depth_profile_with_persistence_at(n: int, start_index: int) -> np.ndarray:
    depth = np.zeros(n)
    depth[start_index:] = r05.TARGET_DEPTH_M + 1e-5
    return depth


def test_operational_complete_requires_full_200_sample_window() -> None:
    n = 600
    # persistence only completes at sample 299 (window [100..299])
    depth = np.zeros(n)
    depth[100:] = r05.TARGET_DEPTH_M + 1e-5
    tr = _synthetic_trace(depth_m=depth, fz_n=np.zeros(n), tilt_deg=np.zeros(n))
    outcome = r05.operational_complete(tr)
    assert outcome.complete
    assert outcome.completion_index == 299
    assert outcome.completion_time_s == 299 * 0.0005


def test_operational_complete_fails_on_prefix_force_violation() -> None:
    n = 400
    depth = np.zeros(n)
    depth[50:] = r05.TARGET_DEPTH_M + 1e-5
    fz = np.zeros(n)
    fz[10] = 30.0  # a prefix force excursion before any window completes
    tr = _synthetic_trace(depth_m=depth, fz_n=fz, tilt_deg=np.zeros(n))
    outcome = r05.operational_complete(tr)
    assert not outcome.complete
    assert outcome.reason == "prefix force/tilt bound violated"


def test_operational_complete_fails_when_depth_never_reached() -> None:
    n = 400
    tr = _synthetic_trace(
        depth_m=np.full(n, 0.5 * r05.TARGET_DEPTH_M),
        fz_n=np.zeros(n),
        tilt_deg=np.zeros(n),
    )
    outcome = r05.operational_complete(tr)
    assert not outcome.complete
    assert outcome.reason == "FK depth persistence never reached"


# ---------------------------------------------------------------------------
# G5: unseatable pair leaves the filter untouched
# ---------------------------------------------------------------------------


def test_g5_unseatable_pair_leaves_filter_unchanged(monkeypatch) -> None:
    n = 300
    unseatable = _synthetic_trace(
        depth_m=np.zeros(n), fz_n=np.zeros(n), tilt_deg=np.zeros(n)
    )
    calls = {"count": 0}

    def fake_run_episode(cfg, pen_allow=0.0, *, model=None, data=None):
        calls["count"] += 1
        return unseatable

    monkeypatch.setattr(r05, "run_episode", fake_run_episode)
    r05.assert_evidence_fingerprints()
    c0 = np.array([0.0006, 0.0002])
    _, control = r05.build_tasks(0.0)
    fresh = r05.make_filter(c0)
    reference = r05.make_filter(c0)
    record, events, times, peaks = r05.attempt_probe_pair(
        fresh, control, c0, 0.5, None, model=None, data=None
    )
    assert record is None
    assert len(events) == 1 and events[0].kind == "unseatable"
    assert times == [r05.MAX_SECONDS, r05.MAX_SECONDS]
    assert peaks == [0.0, 0.0]
    assert calls["count"] == 2
    assert np.array_equal(fresh.state_m, reference.state_m)
    assert np.array_equal(fresh.covariance_m2, reference.covariance_m2)


# ---------------------------------------------------------------------------
# G4: guidance starts exactly at the filter's h estimate (real physics)
# ---------------------------------------------------------------------------


def _feasible_probe_scenario() -> r05.Scenario:
    """Hand-built probe-feasible scenario: c0 at 0.5 mm, first probe bearing
    perpendicular to the fixture error so both probe sides sit at
    sqrt(0.5^2 + 1^2) = 1.118 mm from the true hole (inside the measured
    1.5 mm passive envelope; N2 measured 1.0 mm seating 128/128)."""
    theta_e = 0.0
    return r05.Scenario(
        scenario_id="test-feasible",
        stratum_id=1,
        stratum_name="probe_feasible_clean",
        replicate_id=0,
        seed=0,
        fixture_radius_m=0.0005,
        offset_bearing_rad=theta_e,
        c0_x_m=0.0005,
        c0_y_m=0.0,
        clearance_offset_m=0.0,
        tcp_translation_m=None,
        tcp_yaw_rad=None,
        encoder_resolution_m=None,
        first_probe_bearing_rad=theta_e + math.pi / 2.0,
        arm_order_bit=0,
    )


def test_g4_guided_start_equals_filter_h_estimate(monkeypatch) -> None:
    monkeypatch.setattr(r05, "MAX_SECONDS", 5.0)
    import mujoco
    from pegsim.scene import build_mjcf

    scenario = _feasible_probe_scenario()
    physical, control = r05.build_tasks(scenario.clearance_offset_m)
    model = mujoco.MjModel.from_xml_string(build_mjcf(physical, r05._SOLVER, 0.0))
    data = mujoco.MjData(model)
    candidate = r05.HAPLOUS_CANDIDATES[0]  # H1: one pair, straight drop
    execution, production, diagnostics = r05.run_haplous_arm(
        scenario, candidate, 0.005, model=model, data=data
    )
    h_final = np.array(diagnostics["final_filter_state_m"][:2])
    assert execution.guided
    assert execution.spiral_usage == "guided_drop"
    assert np.allclose(
        execution.production_start_xy, h_final, rtol=0.0, atol=1e-15
    )
    # the production episode's initial condition IS h_hat
    rebuilt = np.array([
        production.setpoint_xy[0, 0],
        production.setpoint_xy[0, 1],
    ])
    # setpoint starts at the commanded xy (the peg start) -> equals h_hat
    assert np.allclose(rebuilt, h_final, rtol=0.0, atol=1e-9)


# ---------------------------------------------------------------------------
# G1: truth-poison regression (real physics)
# ---------------------------------------------------------------------------


def _poison_truth_fields(tr: TrialResult) -> TrialResult:
    tr.success = False
    tr.time_to_seat_s = 0.0
    tr.true_hole_xy = np.array([123.0, -456.0])
    tr.settled_tip_xy = None
    tr.settled_setpoint_xy = None
    tr.settled_descent = None
    tr.tilt_at_settle_rad = None
    tr.settled_contact_geoms = None
    tr.capture_step = None
    tr.capture_tip_lateral = None
    tr.capture_mouth_lateral = None
    return tr


def test_g1_truth_poison_cannot_change_treatment(monkeypatch) -> None:
    monkeypatch.setattr(r05, "MAX_SECONDS", 5.0)
    import mujoco
    from pegsim.scene import build_mjcf

    scenario = _feasible_probe_scenario()
    physical, _ = r05.build_tasks(scenario.clearance_offset_m)

    def run_and_record(poison: bool) -> dict:
        model = mujoco.MjModel.from_xml_string(
            build_mjcf(physical, r05._SOLVER, 0.0)
        )
        data = mujoco.MjData(model)
        if poison:
            real_run = r05.run_episode

            def poisoned(cfg, pen_allow=0.0, *, model=None, data=None):
                tr = real_run(cfg, pen_allow, model=model, data=data)
                return _poison_truth_fields(tr)

            monkeypatch.setattr(r05, "run_episode", poisoned)
        candidate = r05.HAPLOUS_CANDIDATES[0]
        execution, production, diagnostics = r05.run_haplous_arm(
            scenario, candidate, 0.005, model=model, data=data
        )
        return {
            "execution": execution,
            "production_traces": {
                "tip_pos": production.tip_pos,
                "setpoint_xy": production.setpoint_xy,
                "z_flat": production.z_flat,
                "contact_force": production.contact_force,
                "tilt_deg": production.tilt_deg,
            },
            "diagnostics": diagnostics,
        }

    clean = run_and_record(poison=False)
    poisoned = run_and_record(poison=True)

    clean_exec = clean["execution"]
    poison_exec = poisoned["execution"]
    assert clean_exec.spiral_usage == poison_exec.spiral_usage
    assert clean_exec.guided == poison_exec.guided
    assert clean_exec.probe_side_motions == poison_exec.probe_side_motions
    assert [
        (e.kind, e.nis) for e in clean_exec.probe_events
    ] == [(e.kind, e.nis) for e in poison_exec.probe_events]
    assert clean["diagnostics"] == poisoned["diagnostics"]
    for key in clean["production_traces"]:
        assert np.array_equal(
            clean["production_traces"][key],
            poisoned["production_traces"][key],
        ), f"production trace {key} changed under truth poisoning"


# ---------------------------------------------------------------------------
# Grading and analysis
# ---------------------------------------------------------------------------


def test_grade_production_envelope_failure_classification() -> None:
    n = 300
    depth = np.zeros(n)
    depth[50:] = r05.TARGET_DEPTH_M + 1e-5
    fz = np.zeros(n)
    fz[10] = 30.0  # prefix force excursion -> envelope failure
    tr = _synthetic_trace(depth_m=depth, fz_n=fz, tilt_deg=np.zeros(n))
    grade = r05.grade_production(tr, r05.NOMINAL_CLEARANCE_M)
    assert not grade.success
    assert grade.failure_class == "contact_envelope"
    assert grade.peak_abs_fz_n == 30.0


def test_grade_production_success_and_time() -> None:
    n = 300
    depth = np.zeros(n)
    depth[50:] = r05.TARGET_DEPTH_M + 1e-5
    tr = _synthetic_trace(depth_m=depth, fz_n=np.zeros(n), tilt_deg=np.zeros(n))
    grade = r05.grade_production(tr, r05.NOMINAL_CLEARANCE_M)
    assert grade.success
    assert grade.failure_class == "none"
    # depth is satisfied from sample 50; the first fully-ok window is
    # [50..249], so the earliest completing sample is 249
    assert grade.seat_time_s == 249 * 0.0005


def test_exact_mcnemar_matches_locked_examples() -> None:
    assert r05.exact_mcnemar_one_sided(8, 0) == 0.00390625
    assert r05.exact_mcnemar_one_sided(0, 0) == 1.0
    assert r05.exact_mcnemar_one_sided(1, 0) == 0.5


def test_paired_table_counts_and_delta() -> None:
    baseline = [True, True, False, False]
    haplous = [True, False, False, True]
    table = r05.paired_table(baseline, haplous)
    assert table["n10_haplous_only"] == 1
    assert table["n01_baseline_only"] == 1
    assert table["n11_both"] == 1
    assert table["n00_neither"] == 1
    assert table["delta_hat"] == 0.0
