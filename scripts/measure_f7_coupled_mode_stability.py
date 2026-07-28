"""Disposable: F7 -- diagnose the k_xy=2000 tilt-instability crash in
measure_e3_search_phase_sweep.py's measure_lag(). Per-DOF stability_cap
passes easily for k_xy=2000 (translational cap is huge relative to k_xy),
so the crash must come from a COUPLED mode: the mass matrix's off-diagonal
x/ry, y/rx terms (already documented in ImpedanceGains) mixing with the
Jacobian-mapped stiffness. Builds the full 6x6 joint-space stiffness K and
mass M at several checkpoints through the trajectory and reports
max(omega*dt) from the generalized eigenvalues of (K, M), compared against
the explicit-integration limit of 2.0 (see control.stability_cap's
docstring: K <= (2/dt)^2*M <=> omega*dt <= 2).
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

from pegsim.control import (
    PEG_DOF_JOINT_NAMES,
    ImpedanceGains,
    compute_and_apply_qfrc,
    derived_damping,
    peg_dof_ids,
)
from pegsim.rollout import DEPTH_ENGAGE_MARGIN, InsertionProfile, _rate_limited_advance
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf
from pegsim.search import ArchimedeanSpiral, DELTA_MAX_SPEC_M, SpiralSpec

TASK = TaskSpec(
    peg_radius=0.005, radial_clearance=0.0005, chamfer_width=0.001,
    plate_thickness=0.01, peg_length=0.05, peg_mass=0.05, n_segments=16, friction=0.4,
)
SOLVER = SolverSpec(timestep=0.0005)
K_XY = 2000.0
F_SEARCH = 0.5
GAINS = ImpedanceGains(k_xy=K_XY, k_rx=5.0, k_ry=5.0, k_rz=0.05)
INSERTION = InsertionProfile(f_search=F_SEARCH)
V_T = 0.005
NO_CAPTURE_OFFSET = 0.005


def joint_space_stiffness(model, data, dof_ids, gains: ImpedanceGains) -> np.ndarray:
    """K_joint = J^T diag(stiffness) J at the current site Jacobian --
    reproduces exactly what compute_and_apply_qfrc applies, so this is the
    real effective stiffness the integrator sees, not an assumed-diagonal
    approximation."""
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
    jacp = np.zeros((3, model.nv))
    jacr = np.zeros((3, model.nv))
    mujoco.mj_jacSite(model, data, jacp, jacr, site_id)
    jacp_peg = jacp[:, dof_ids]
    jacr_peg = jacr[:, dof_ids]
    stiffness = gains.stiffness_vector
    k_trans = np.diag(stiffness[:3])
    k_rot = np.diag(stiffness[3:])
    return jacp_peg.T @ k_trans @ jacp_peg + jacr_peg.T @ k_rot @ jacr_peg


def max_omega_dt(model, data, dof_ids, gains: ImpedanceGains, dt: float) -> tuple[float, np.ndarray]:
    full_m = np.zeros((model.nv, model.nv))
    mujoco.mj_fullM(model, data, full_m)
    m = full_m[np.ix_(dof_ids, dof_ids)]
    k = joint_space_stiffness(model, data, dof_ids, gains)
    eigvals = np.linalg.eigvals(np.linalg.solve(m, k))
    eigvals = np.real(eigvals)
    eigvals = np.clip(eigvals, 0.0, None)
    omega = np.sqrt(eigvals)
    return float(omega.max() * dt), omega


def main() -> None:
    model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, SOLVER))
    data = mujoco.MjData(model)
    data.qpos[0] = NO_CAPTURE_OFFSET
    data.qpos[2] = 0.005
    mujoco.mj_forward(model, data)
    dof_ids = peg_dof_ids(model)
    dt = SOLVER.timestep

    nominal_xy = np.array([NO_CAPTURE_OFFSET, 0.0])
    spiral_spec = SpiralSpec(task=TASK, delta_max=DELTA_MAX_SPEC_M, v_t=V_T)
    spiral = ArchimedeanSpiral(spiral_spec)
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
    z_search_target = -INSERTION.f_search / GAINS.k_z
    z_des_raw = 0.005
    xy_des = nominal_xy.copy()

    print(f"{'step':>7s} {'tilt_deg':>9s} {'max_omega_dt':>13s} {'omega_dt_rx':>12s} "
          f"{'omega_dt_ry':>12s} {'omega_dt_xy':>12s}")

    n_settle = 4000
    for i in range(n_settle):
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        z_des_raw = _rate_limited_advance(z_des_raw, z_search_target, INSERTION.v_descent * dt)
        z_des = max(z_des_raw, tip_now[2] - INSERTION.f_target / GAINS.k_z)
        compute_and_apply_qfrc(model, data, GAINS, np.array([xy_des[0], xy_des[1], z_des]))
        mujoco.mj_step(model, data)

    for i in range(30000):
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        engaging_slope = tip_now[2] < -DEPTH_ENGAGE_MARGIN
        z_des_raw = _rate_limited_advance(z_des_raw, z_search_target, INSERTION.v_descent * dt)
        z_des = max(z_des_raw, tip_now[2] - INSERTION.f_target / GAINS.k_z)
        if not engaging_slope:
            xy_des = nominal_xy + spiral.step(dt)

        if i % 500 == 0:
            m_dt, omega = max_omega_dt(model, data, dof_ids, GAINS, dt)
            quat_cur = np.zeros(4)
            mujoco.mju_mat2Quat(quat_cur, data.site_xmat[site_id])
            tilt_deg = math.degrees(2.0 * math.acos(min(1.0, abs(quat_cur[0]))))
            idx = {name: k for k, name in enumerate(PEG_DOF_JOINT_NAMES)}
            print(f"{i:7d} {tilt_deg:9.4f} {m_dt:13.4f} "
                  f"{omega[idx['peg_rx']] * dt:12.4f} {omega[idx['peg_ry']] * dt:12.4f} "
                  f"{omega[idx['peg_x']] * dt:12.4f}")
            if tilt_deg > 9.0:
                print("tilt approaching the 10deg safety bound -- stopping diagnostic here")
                break

        try:
            compute_and_apply_qfrc(model, data, GAINS, np.array([xy_des[0], xy_des[1], z_des]))
        except AssertionError as e:
            print(f"CRASHED at step {i}: {e}")
            m_dt, omega = max_omega_dt(model, data, dof_ids, GAINS, dt)
            print(f"at crash: max_omega_dt={m_dt:.4f}, per-DOF omega*dt={dict(zip(PEG_DOF_JOINT_NAMES, (omega * dt).tolist()))}")
            break
        mujoco.mj_step(model, data)


if __name__ == "__main__":
    main()
