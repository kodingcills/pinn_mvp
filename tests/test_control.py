import math

import mujoco
import numpy as np
import pytest

from pegsim.control import (
    MAX_TILT_RAD,
    ImpedanceGains,
    _orientation_error,
    compute_and_apply_qfrc,
    derived_damping,
    peg_contact_force_world,
    peg_dof_ids,
)
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf

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

# Bigger than TASK specifically so rz's own inertia clears the explicit-force
# stability margin (see TestImpedanceClosedLoop docstring below): with TASK's
# 5mm/50g peg, M_rz = 0.5*m*r^2 = 6.25e-7 kg*m^2, giving a stable-timestep
# bound dt_max = 2*sqrt(M_rz/k_rot) = 0.71ms -- under the 1ms step this
# project fixes, and any nonzero lateral motion excites a genuine numerical
# (not physical) blow-up in rz. STABLE_TASK's 10mm/200g peg gives
# M_rz = 1e-5, dt_max = 2.8ms, comfortably clear.
STABLE_TASK = TaskSpec(
    peg_radius=0.01,
    radial_clearance=0.0005,
    chamfer_width=0.001,
    plate_thickness=0.02,
    peg_length=0.08,
    peg_mass=0.2,
    n_segments=16,
    friction=0.4,
)


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
    """Uses STABLE_TASK, not the module-level TASK: see STABLE_TASK's comment.
    With TASK's small peg, any nonzero lateral (x/y) setpoint seeds a real
    (not sign-error) numerical blow-up in rz, because explicit force
    injection (qfrc_applied, mandated by the spec so control.py is the sole
    source of stiffness/damping) gets none of the implicit integrator's
    stability benefit -- only native passive joint terms do. Flagged to the
    user: this is a genuine finding for Milestone 4 (continuous lateral
    motion during spiral search) with a small peg at K_rot=5, dt=1ms, not
    something to silently work around here."""

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
        model, data = _fresh_model_data(STABLE_TASK)
        mujoco.mj_forward(model, data)
        gains = ImpedanceGains()
        target = np.array([0.001, -0.0015, 0.01])  # free space, well above the plate
        ramp_steps = 1000

        for i in range(3000):
            alpha = min(1.0, (i + 1) / ramp_steps)
            mujoco.mj_forward(model, data)
            compute_and_apply_qfrc(model, data, gains, alpha * target)
            mujoco.mj_step(model, data)

        site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
        np.testing.assert_allclose(data.site_xpos[site_id], target, atol=5e-4)
        assert np.linalg.norm(data.qvel[:3]) < 1e-2

    def test_zero_lateral_offset_z_ramp_is_stable_on_small_peg(self):
        """Milestone 2's actual usage (step1_drop.py): x_des/y_des held at
        zero, only z_des ramps. Uses TASK (the small peg), not STABLE_TASK --
        with zero lateral setpoint there is nothing to seed rz's marginal
        instability, so this must stay clean on the real fixture."""
        model, data = _fresh_model_data(TASK)
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
