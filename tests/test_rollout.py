import math
import copy
from dataclasses import asdict, fields

import numpy as np
import pytest

from pegsim.characterize import measure_settling_depths
from pegsim.control import ImpedanceGains, assert_gains_stable
from pegsim.rollout import (
    COMMIT_RELEASE_FRACTION,
    DEPTH_COMMIT,
    VALIDATED_LOOSE_V1,
    EpisodeConfig,
    InsertionProfile,
    TrialResult,
    assert_commit_gate_reachable,
    assert_pen_allow_meaningful,
    axis_xy_at_z,
    build_episode_model_data,
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
GAINS = ImpedanceGains(k_xy=500.0, k_z=2000.0, k_rx=1.0, k_ry=1.0)
INSERTION = InsertionProfile(k_z_search=500.0, f_search=4.0)


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

    def test_commit_gate_bracketed_by_measured_settling(self):
        """J2: DEPTH_COMMIT's lower bound is a MEASUREMENT, so this test
        takes it rather than asserting against a literal.

        Offsets must sit ABOVE R_passive (1.5mm) or the peg funnels straight
        in and there is no resting state to measure -- below R_passive the
        measurement asserts, which is the right failure. Settling deepens as
        the rim overhangs more of the chamfer, so the near-hole end of this
        range is the binding one."""
        cfg = VALIDATED_LOOSE_V1
        depths = measure_settling_depths(
            cfg.task, cfg.solver, cfg.gains, cfg.insertion,
            offsets_m=[0.00175, 0.002, 0.003, 0.005], z_start=cfg.z_start,
        )
        assert_commit_gate_reachable(
            cfg.insertion, cfg.gains, max(depths.values()), cfg.depth_commit,
        )

    def test_search_force_budget(self):
        cfg = VALIDATED_LOOSE_V1
        assert_search_force_budget(
            cfg.task, cfg.gains.k_xy, cfg.insertion.f_search, cfg.spiral, cfg.r_cap_dyn,
        )


class TestInsertionProfileConstraints:
    def test_rejects_f_target_at_or_above_f_yield(self):
        with pytest.raises(AssertionError, match="f_target"):
            InsertionProfile(k_z_search=500.0, f_search=4.0, f_target=25.0, f_yield=25.0)

    def test_rejects_nonpositive_v_descent(self):
        with pytest.raises(AssertionError, match="v_descent"):
            InsertionProfile(k_z_search=500.0, f_search=4.0, v_descent=0.0)


class TestEpisodeConfigConstraints:
    def test_rejects_nonpositive_z_start(self):
        with pytest.raises(AssertionError, match="z_start"):
            EpisodeConfig(TASK, SOLVER, GAINS, INSERTION, z_start=-0.01,
                          initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=1.0)

    def test_default_target_depth_is_derived_from_plate_thickness(self):
        cfg = EpisodeConfig(TASK, SOLVER, GAINS, INSERTION, z_start=0.005,
                             initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=1.0)
        assert cfg.target_depth == pytest.approx(TASK.plate_thickness - 2.0 * 70e-6)

    def test_rejects_commit_gate_beyond_search_descent_allowance(self):
        insertion = InsertionProfile(k_z_search=2000.0, f_search=1.0)
        with pytest.raises(AssertionError, match="physically unreachable"):
            EpisodeConfig(
                TASK,
                SOLVER,
                GAINS,
                insertion,
                z_start=0.005,
                initial_offset_r=0.002,
                initial_offset_theta=0.0,
                max_seconds=1.0,
                depth_commit=0.0005,
            )

    def test_pen_allow_must_stay_small_relative_to_clearance(self):
        with pytest.raises(AssertionError, match="criterion goes vacuous"):
            assert_pen_allow_meaningful(TASK, 0.2 * TASK.radial_clearance)

    @pytest.mark.parametrize(
        ("field", "value"),
        (("control_decimation", 0), ("control_decimation", 1.5),
         ("control_delay_steps", -1), ("control_delay_steps", True)),
    )
    def test_rejects_invalid_control_timing(self, field, value):
        kwargs = {field: value}
        with pytest.raises(AssertionError, match=field):
            EpisodeConfig(
                TASK,
                SOLVER,
                GAINS,
                INSERTION,
                z_start=0.005,
                initial_offset_r=0.0,
                initial_offset_theta=0.0,
                max_seconds=1.0,
                **kwargs,
            )


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
    @staticmethod
    def _assert_results_bit_identical(first, second):
        first_values = asdict(first)
        second_values = asdict(second)
        assert first_values.keys() == second_values.keys()
        for key in first_values:
            a, b = first_values[key], second_values[key]
            if isinstance(a, np.ndarray):
                np.testing.assert_array_equal(a, b, err_msg=key)
            elif isinstance(a, float) and math.isnan(a):
                assert math.isnan(b), key
            else:
                assert a == b, key

    def test_consecutive_identical_episodes_are_bit_identical_and_do_not_mutate_config(self):
        cfg = VALIDATED_LOOSE_V1
        episode = EpisodeConfig(
            cfg.task,
            cfg.solver,
            cfg.gains,
            cfg.insertion,
            z_start=cfg.z_start,
            initial_offset_r=0.002,
            initial_offset_theta=math.radians(22.5),
            max_seconds=cfg.max_seconds,
            spiral_spec=cfg.spiral,
        )
        before = copy.deepcopy(episode)
        model, data = build_episode_model_data(episode)

        first = run_episode(
            episode, pen_allow=cfg.pen_allow, model=model, data=data,
        )
        assert episode == before
        second = run_episode(
            episode, pen_allow=cfg.pen_allow, model=model, data=data,
        )

        assert episode == before
        self._assert_results_bit_identical(first, second)

    def test_max_seconds_terminates_episode_without_spiral(self):
        max_seconds = 0.01
        cfg = EpisodeConfig(
            TASK,
            SOLVER,
            GAINS,
            INSERTION,
            z_start=0.005,
            initial_offset_r=0.003,
            initial_offset_theta=0.0,
            max_seconds=max_seconds,
            spiral_spec=None,
        )

        log = run_episode(cfg)

        assert len(log.time) == int(max_seconds / SOLVER.timestep)
        assert log.time[-1] == pytest.approx(max_seconds, abs=1e-15)
        assert not log.success
        assert math.isinf(log.time_to_seat_s)
        assert log.settled_tip_xy is None
        assert log.settled_setpoint_xy is None
        assert log.settled_descent is None
        assert log.tilt_at_settle_rad is None
        assert log.true_hole_xy is None
        assert log.nominal_xy is None

    def test_decimated_controller_holds_qfrc_between_ticks(self):
        decimation = 4
        cfg = EpisodeConfig(
            TASK,
            SOLVER,
            GAINS,
            INSERTION,
            z_start=0.005,
            initial_offset_r=0.0,
            initial_offset_theta=0.0,
            max_seconds=0.012,
            control_decimation=decimation,
        )

        log = run_episode(cfg)

        np.testing.assert_array_equal(
            np.flatnonzero(log.control_tick), np.array([0, 4, 8]),
        )
        for start in range(0, len(log.time), decimation):
            block = log.qfrc_applied[start:start + decimation]
            np.testing.assert_array_equal(block, np.broadcast_to(block[0], block.shape))

    def test_one_cycle_delay_applies_previous_control_tick_command(self):
        common = dict(
            task=TASK,
            solver=SOLVER,
            gains=GAINS,
            insertion=INSERTION,
            z_start=0.005,
            initial_offset_r=0.0,
            initial_offset_theta=0.0,
            max_seconds=0.012,
            control_decimation=4,
        )
        immediate = run_episode(EpisodeConfig(**common, control_delay_steps=0))
        delayed = run_episode(EpisodeConfig(**common, control_delay_steps=1))

        np.testing.assert_array_equal(delayed.qfrc_applied[:4], 0.0)
        np.testing.assert_array_equal(
            delayed.qfrc_applied[4:8], immediate.qfrc_applied[:4],
        )

    def test_zero_offset_no_spiral_seats(self):
        cfg = EpisodeConfig(
            TASK, SOLVER, GAINS, INSERTION, z_start=0.005,
            initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=6.0,
        )
        log = run_episode(cfg)
        assert log.success
        assert log.time_to_seat_s < 6.0

    def test_zero_offset_with_spiral_still_seats(self):
        """The search overlay must not destabilize or prevent the trivial
        (already-aligned) case from seating."""
        spiral_spec = SpiralSpec(task=TASK, delta_max=DELTA_MAX_SPEC_M, v_t=0.005)
        cfg = EpisodeConfig(
            TASK, SOLVER, GAINS, INSERTION, z_start=0.005,
            initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=10.0,
            spiral_spec=spiral_spec,
        )
        log = run_episode(cfg)
        assert log.success

    def test_capture_localization_error_within_clearance(self):
        """Phase 2 pre-flight P1: capture_mouth_lateral is logged at the
        exact step search_done fires, so it must satisfy the same
        <=radial_clearance condition that triggered search_done in the
        first place -- otherwise the two are measuring different moments."""
        validated = VALIDATED_LOOSE_V1
        cfg = EpisodeConfig(
            validated.task,
            validated.solver,
            validated.gains,
            validated.insertion,
            z_start=validated.z_start,
            initial_offset_r=validated.task.capture_radius,
            initial_offset_theta=0.0,
            max_seconds=validated.max_seconds,
            spiral_spec=validated.spiral,
            control_decimation=validated.control_decimation,
            control_delay_steps=validated.control_delay_steps,
        )
        log = run_episode(cfg, pen_allow=validated.pen_allow)
        assert log.capture_step is not None
        assert log.capture_mouth_lateral <= validated.task.radial_clearance
        assert log.capture_tip_lateral is not None

        assert log.success
        required_consecutive = max(
            1, int(round(cfg.seat_persist_s / validated.solver.timestep)),
        )
        onset_index = int(np.flatnonzero(log.time == log.time_to_seat_s)[0])
        settle_index = onset_index + required_consecutive - 1
        np.testing.assert_array_equal(log.settled_tip_xy, log.tip_pos[settle_index, :2])
        np.testing.assert_array_equal(
            log.settled_setpoint_xy, log.setpoint_xy[settle_index],
        )
        assert not np.shares_memory(log.settled_tip_xy, log.tip_pos)
        assert not np.shares_memory(log.settled_setpoint_xy, log.setpoint_xy)
        assert log.settled_descent == pytest.approx(
            log.z_flat[settle_index] - log.tip_pos[settle_index, 2], abs=1e-15,
        )
        assert log.tilt_at_settle_rad == pytest.approx(
            math.radians(log.tilt_deg[settle_index]), abs=1e-15,
        )
        np.testing.assert_array_equal(log.true_hole_xy, np.zeros(2))
        np.testing.assert_allclose(
            log.nominal_xy, [validated.task.capture_radius, 0.0], atol=1e-15,
        )

    def test_settled_fields_are_optional_and_result_is_not_frozen(self):
        settled_fields = {
            "settled_tip_xy",
            "settled_setpoint_xy",
            "settled_descent",
            "tilt_at_settle_rad",
            "true_hole_xy",
            "nominal_xy",
        }
        defaults = {field.name: field.default for field in fields(TrialResult)}
        assert all(defaults[name] is None for name in settled_fields)
        assert not TrialResult.__dataclass_params__.frozen

    def test_settled_force_stays_near_f_target(self):
        """A1 fix: settled axial force should track f_target (default 15N),
        not the old stale-ramp-driven 70N."""
        cfg = EpisodeConfig(
            TASK, SOLVER, GAINS, INSERTION, z_start=0.005,
            initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=6.0,
        )
        log = run_episode(cfg)
        settled_fz = abs(log.contact_force[-200:, 2].mean())
        assert settled_fz == pytest.approx(INSERTION.f_target, rel=0.1)

    def test_commanded_axial_force_is_continuous_across_the_stiffness_step(self):
        """J2: the search->seating handoff changes k_z by 4x while the
        position error is real and nonzero. Parameterizing the seating phase
        by FORCE (z_des = z_tip - F_cmd/k_z, re-referenced at the edge) makes
        the commanded force identical either side of the switch. Stepping k_z
        with the setpoint held would jump it 0.75N -> 3.0N -- in sim an
        impulse through a spring, on hardware a velocity discontinuity that
        trips a protective stop."""
        cfg = VALIDATED_LOOSE_V1
        episode = EpisodeConfig(
            cfg.task, cfg.solver, cfg.gains, cfg.insertion, z_start=0.005,
            initial_offset_r=0.0012, initial_offset_theta=0.0,
            max_seconds=cfg.max_seconds, spiral_spec=cfg.spiral,
        )
        log = run_episode(episode, pen_allow=cfg.pen_allow)
        assert log.committed.any(), "episode never committed -- nothing to test"
        assert log.max_f_cmd_z_step < 0.5, (
            f"commanded axial force jumped {log.max_f_cmd_z_step:.4f}N in a "
            "single physics step"
        )

    def test_commit_gate_has_hysteresis(self):
        """J1: contact chatter around a bare threshold would stutter the
        spiral -- and the axial stiffness -- on and off at physics rate.
        Every release must happen at the lower threshold, never at
        DEPTH_COMMIT itself."""
        cfg = VALIDATED_LOOSE_V1
        episode = EpisodeConfig(
            cfg.task, cfg.solver, cfg.gains, cfg.insertion, z_start=0.005,
            initial_offset_r=0.0018, initial_offset_theta=math.radians(45.0),
            max_seconds=cfg.max_seconds, spiral_spec=cfg.spiral,
        )
        log = run_episode(episode, pen_allow=cfg.pen_allow)
        committed = log.committed
        depth = log.z_flat - log.tip_pos[:, 2]
        releases = np.flatnonzero(committed[:-1] & ~committed[1:])
        for i in releases:
            assert depth[i + 1] <= COMMIT_RELEASE_FRACTION * cfg.depth_commit * 1.05, (
                f"released at depth {depth[i + 1] * 1000:.4f}mm, above the "
                f"hysteresis threshold "
                f"{COMMIT_RELEASE_FRACTION * cfg.depth_commit * 1000:.4f}mm"
            )

    def test_peak_force_stays_under_f_yield(self):
        cfg = EpisodeConfig(
            TASK, SOLVER, GAINS, INSERTION, z_start=0.005,
            initial_offset_r=0.0, initial_offset_theta=0.0, max_seconds=6.0,
        )
        log = run_episode(cfg)
        peak_fz = float(np.abs(log.contact_force[:, 2]).max())
        assert peak_fz < INSERTION.f_yield
