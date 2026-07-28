import math

import mujoco
import numpy as np
import pytest

from pegsim.control import (
    MAX_TILT_RAD,
    ImpedanceGains,
    _orientation_error,
    assert_gains_stable,
    compute_and_apply_qfrc,
    derived_damping,
    peg_contact_force_world,
    peg_dof_ids,
    stability_cap,
)
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf

# The canonical peg. Every downstream milestone uses this -- no substitute
# fixture for stability convenience (see ImpedanceGains' docstring for why
# the default gains were changed instead of the peg).
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
GRAVITY = 9.81


def _fresh_model_data(task: TaskSpec = TASK):
    xml = build_mjcf(task, SOLVER)
    model = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(model)
    return model, data


class TestPegDofIds:
    def test_returns_six_ordered_dofs(self):
        model, _ = _fresh_model_data()
        ids = peg_dof_ids(model)
        assert list(ids) == [0, 1, 2, 3, 4, 5]


class TestDerivedDamping:
    def test_rz_damping_much_smaller_than_rx_ry(self):
        model, data = _fresh_model_data()
        mujoco.mj_forward(model, data)
        dof_ids = peg_dof_ids(model)
        gains = ImpedanceGains()
        damping = derived_damping(model, data, dof_ids, gains.stiffness_vector)
        d_rx, d_ry, d_rz = damping[3], damping[4], damping[5]
        # rz spins about the cylinder's long axis: far smaller inertia than
        # rx/ry, which carry a parallel-axis term from the tip-mounted hinge.
        assert d_rz < d_rx / 3.0
        assert d_rz < d_ry / 3.0

    def test_damping_positive(self):
        model, data = _fresh_model_data()
        mujoco.mj_forward(model, data)
        dof_ids = peg_dof_ids(model)
        gains = ImpedanceGains()
        damping = derived_damping(model, data, dof_ids, gains.stiffness_vector)
        assert np.all(damping > 0)


class TestStabilityCap:
    """K_i <= (2/dt)^2 * M_ii: necessary (catches gross violations like the
    original shared K_rot=5 on rz) but NOT sufficient (x/ry and y/rx are
    coupled through mj_fullM's off-diagonal terms -- see ImpedanceGains'
    docstring). Both properties get their own test."""

    def test_default_gains_pass_the_per_dof_cap(self):
        model, data = _fresh_model_data()
        mujoco.mj_forward(model, data)
        gains = ImpedanceGains()
        assert_gains_stable(model, data, gains, SOLVER.timestep)  # must not raise

    def test_old_shared_k_rot_five_on_rz_would_have_violated_the_cap(self):
        model, data = _fresh_model_data()
        mujoco.mj_forward(model, data)
        dof_ids = peg_dof_ids(model)
        cap = stability_cap(model, data, dof_ids, SOLVER.timestep)
        rz_cap = cap[5]
        assert rz_cap < 5.0, f"expected rz's cap (~2.5) to be under the old shared K_rot=5, got {rz_cap}"

    def test_cap_scales_as_inverse_dt_squared(self):
        model, data = _fresh_model_data()
        mujoco.mj_forward(model, data)
        dof_ids = peg_dof_ids(model)
        cap_1ms = stability_cap(model, data, dof_ids, 0.001)
        cap_2ms = stability_cap(model, data, dof_ids, 0.002)
        np.testing.assert_allclose(cap_1ms, 4.0 * cap_2ms)

    def test_raises_when_a_dof_exceeds_its_cap(self):
        model, data = _fresh_model_data()
        mujoco.mj_forward(model, data)
        # k_rz=5 matches the OLD shared K_rot value, which exceeds rz's cap
        # (~2.5) on this peg at dt=1ms -- exactly the regression this guards.
        gains = ImpedanceGains(k_rz=5.0)
        with pytest.raises(AssertionError, match="peg_rz"):
            assert_gains_stable(model, data, gains, SOLVER.timestep)


class TestStaticRestContactForce:
    """Milestone 2 required test: a peg resting statically on the plate must
    show a summed world-frame contact force of +z with magnitude ~= m*g.
    This is the ground-truth check for the contact-frame rotation and
    geom1/geom2 sign convention -- everything downstream (Milestone 3's
    go/no-go verdict) depends on this being right."""

    def test_peg_resting_on_table_shows_plus_z_weight(self):
        model, data = _fresh_model_data()
        r_mid = (TASK.hole_radius + TASK.chamfer_width + TASK.plate_outer_radius) / 2.0
        data.qpos[0] = r_mid  # x: land on the flat table ring, away from the hole
        data.qpos[1] = 0.0
        data.qpos[2] = 0.001  # start 1mm above the table surface and let it settle
        mujoco.mj_forward(model, data)

        for _ in range(4000):  # 4s at dt=1ms: ample time for contact to settle
            data.qfrc_applied[:] = 0.0  # no controller -- pure gravity + contact
            mujoco.mj_step(model, data)

        speed = np.linalg.norm(data.qvel[:3])
        assert speed < 1e-3, f"peg has not settled, speed={speed}"

        force = peg_contact_force_world(model, data)
        assert force[2] == pytest.approx(TASK.peg_mass * GRAVITY, rel=0.05)
        assert abs(force[0]) < 0.05 * TASK.peg_mass * GRAVITY
        assert abs(force[1]) < 0.05 * TASK.peg_mass * GRAVITY


class TestOrientationErrorFrame:
    """Locks in the world-frame convention: mju_subQuat alone gives the
    error in the current body's local frame, which would silently mismatch
    omega/jacr_peg (both world-frame) as soon as the peg is tilted."""

    def test_error_is_expressed_in_world_frame_not_body_frame(self):
        def quat_about(axis, deg):
            q = np.zeros(4)
            mujoco.mju_axisAngle2Quat(q, np.array(axis, dtype=float), math.radians(deg))
            return q

        quat_cur = quat_about([0.0, 0.0, 1.0], 90.0)  # peg currently yawed 90deg
        world_delta = quat_about([1.0, 0.0, 0.0], 20.0)  # desired: 20deg further about WORLD x
        quat_des = np.zeros(4)
        mujoco.mju_mulQuat(quat_des, world_delta, quat_cur)  # left-multiply = world-frame delta

        rotvec = _orientation_error(quat_cur, quat_des)
        np.testing.assert_allclose(rotvec, [math.radians(20.0), 0.0, 0.0], atol=1e-9)


class TestImpedanceClosedLoop:
    """All on TASK, the canonical 5mm/50g peg -- per-DOF gains (K_rx=K_ry=1,
    K_rz=0.05) replace the old shared K_rot=5 specifically so this peg is
    stable under lateral motion without a substitute fixture. See
    ImpedanceGains' docstring for the empirical sweep that set K_rx=K_ry=1."""

    def test_holds_small_lateral_setpoint_in_free_space(self):
        """Sanity check for sign errors in the J^T F_cart mapping: with no
        contact, the peg must converge to a small commanded setpoint, not
        diverge."""
        # A step-jump setpoint is not how this controller is ever driven (real
        # usage ramps z_des at ~10mm/s, see step1_drop.py); a large instant
        # position error creates a huge transient spring force which, via the
        # peg's CoM-offset inertial coupling (tip != CoM), genuinely induces
        # tilt -- that is real rigid-body physics, not a controller bug. Ramp
        # the setpoint here too, matching realistic usage.
        model, data = _fresh_model_data()
        mujoco.mj_forward(model, data)
        gains = ImpedanceGains()
        assert_gains_stable(model, data, gains, SOLVER.timestep)
        target = np.array([0.0018, -0.0018, 0.01])  # near 1.25*capture_radius laterally
        ramp_steps = 1000

        for i in range(3000):
            alpha = min(1.0, (i + 1) / ramp_steps)
            mujoco.mj_forward(model, data)
            compute_and_apply_qfrc(model, data, gains, alpha * target)
            mujoco.mj_step(model, data)

        site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
        np.testing.assert_allclose(data.site_xpos[site_id], target, atol=5e-4)
        assert np.linalg.norm(data.qvel[:3]) < 1e-2

    def test_zero_lateral_offset_z_ramp_is_stable(self):
        """Milestone 2's actual usage (step1_drop.py): x_des/y_des held at
        zero, only z_des ramps."""
        model, data = _fresh_model_data()
        mujoco.mj_forward(model, data)
        gains = ImpedanceGains()

        z_start, z_end, rate = 0.005, -0.045, 0.01  # meters, m/s (per spec)
        dt = SOLVER.timestep
        ramp_steps = int(abs(z_end - z_start) / (rate * dt)) + 1
        settle_steps = 500  # let the critically-damped tracking lag relax once the ramp ends

        for i in range(ramp_steps + settle_steps):
            z_des = max(z_start - i * rate * dt, z_end)
            mujoco.mj_forward(model, data)
            compute_and_apply_qfrc(model, data, gains, np.array([0.0, 0.0, z_des]))
            mujoco.mj_step(model, data)

        site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
        quat_cur = np.zeros(4)
        mujoco.mju_mat2Quat(quat_cur, data.site_xmat[site_id])
        tilt = 2.0 * math.acos(min(1.0, abs(quat_cur[0])))
        assert tilt < math.radians(0.1)
        np.testing.assert_allclose(data.site_xpos[site_id][2], z_end, atol=1e-4)

    def test_tilt_assert_fires_beyond_ten_degrees(self):
        model, data = _fresh_model_data()
        dof_ids = peg_dof_ids(model)
        rx_dof = dof_ids[3]
        data.qpos[rx_dof] = math.radians(15.0)  # beyond the 10 deg bound
        mujoco.mj_forward(model, data)
        gains = ImpedanceGains()
        with pytest.raises(AssertionError, match="tilt"):
            compute_and_apply_qfrc(model, data, gains, np.zeros(3))

    def test_max_tilt_constant_is_ten_degrees(self):
        assert MAX_TILT_RAD == pytest.approx(math.radians(10.0))
