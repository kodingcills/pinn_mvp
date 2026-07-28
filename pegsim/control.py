"""Cartesian impedance controller for the peg, mapped through the Jacobian at
the peg_tip site.

F_cart = [F_trans; F_rot] is computed in Cartesian space, then mapped to
generalized (joint) force via tau = J^T @ F_cart using mj_jacSite evaluated
every step. tau is never assigned to qfrc_applied directly -- only J^T tau
respects the (possibly non-identity) mapping from Cartesian force to joint
force, which for the rotational block here is NOT the identity: rx/ry/rz are
chained hinges, so ry's world axis is R_x(rx)@(0,1,0) and rz's is
R_x(rx)@R_y(ry)@(0,0,1).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

PEG_DOF_JOINT_NAMES = ("peg_x", "peg_y", "peg_z", "peg_rx", "peg_ry", "peg_rz")
PEG_TIP_SITE = "peg_tip"
PEG_BODY = "peg"

MAX_TILT_RAD = math.radians(10.0)


@dataclass(frozen=True)
class ImpedanceGains:
    """Cartesian stiffness. Damping is never a free parameter here -- it is
    derived per-DOF from the mass matrix (see derived_damping)."""

    k_xy: float = 500.0
    k_z: float = 2000.0
    k_rot: float = 5.0

    def __post_init__(self) -> None:
        assert self.k_xy > 0, "k_xy must be positive"
        assert self.k_z > 0, "k_z must be positive"
        assert self.k_rot > 0, "k_rot must be positive"

    @property
    def stiffness_vector(self) -> np.ndarray:
        """Order matches PEG_DOF_JOINT_NAMES: (x, y, z, rx, ry, rz)."""
        return np.array([self.k_xy, self.k_xy, self.k_z, self.k_rot, self.k_rot, self.k_rot])


def peg_dof_ids(model: mujoco.MjModel) -> np.ndarray:
    return np.array([
        model.jnt_dofadr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)]
        for name in PEG_DOF_JOINT_NAMES
    ])


def derived_damping(model: mujoco.MjModel, data: mujoco.MjData, dof_ids: np.ndarray,
                     stiffness: np.ndarray) -> np.ndarray:
    """D_i = 2*sqrt(K_i * M_ii), read fresh from the mass matrix diagonal.

    Never hardcoded, never shared between translational and rotational DOFs:
    critical damping for the hinges depends on inertia, not the peg's point
    mass, the hinges sit at the tip rather than the center of mass (a
    parallel-axis term inflates rx/ry), and rz -- about the cylinder's long
    axis -- has roughly 40x smaller inertia than rx/ry. Recomputed every call
    because the rotational entries of M are configuration-dependent for a
    chained hinge, not constant.
    """
    full_m = np.zeros((model.nv, model.nv))
    mujoco.mj_fullM(model, data, full_m)
    m_diag = full_m[dof_ids, dof_ids]
    assert np.all(m_diag > 0), "mass matrix diagonal must be positive -- check armature/joint setup"
    return 2.0 * np.sqrt(stiffness * m_diag)


def _orientation_error(quat_cur: np.ndarray, quat_des: np.ndarray) -> np.ndarray:
    """WORLD-frame rotation vector (axis * angle, radians) taking quat_cur to
    quat_des: exp(rotvec) * quat_cur = quat_des.

    mju_subQuat(res, qa, qb) computes res in qb's LOCAL frame (qb*exp(res)=qa)
    -- verified empirically, not assumed. Using it directly here would give a
    rot_err expressed in the peg's current body frame while omega (from
    jacr_peg @ qvel, via mj_jacSite) and jacr_peg itself are both world-frame,
    silently mismatching F_rot's two terms as soon as the peg is tilted at
    all. Composing the error explicitly (quat_des * conj(quat_cur)) keeps
    everything in world frame.
    """
    quat_cur_inv = np.zeros(4)
    mujoco.mju_negQuat(quat_cur_inv, quat_cur)
    quat_err = np.zeros(4)
    mujoco.mju_mulQuat(quat_err, quat_des, quat_cur_inv)
    rotvec = np.zeros(3)
    mujoco.mju_quat2Vel(rotvec, quat_err, 1.0)
    return rotvec


def compute_and_apply_qfrc(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    gains: ImpedanceGains,
    x_des: np.ndarray,
    quat_des: np.ndarray | None = None,
) -> np.ndarray:
    """One impedance control step. Mutates data.qfrc_applied in place.

    Precondition: mj_forward or mj_step1 has already been called for the
    current state this step, so qfrc_bias, site_xpos/xmat, and qvel are all
    current -- qfrc_bias is only valid gravity/Coriolis compensation for the
    state it was computed at.

    Returns the 6D Cartesian wrench F_cart actually commanded, for logging.
    """
    if quat_des is None:
        quat_des = np.array([1.0, 0.0, 0.0, 0.0])

    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, PEG_TIP_SITE)
    dof_ids = peg_dof_ids(model)

    jacp = np.zeros((3, model.nv))
    jacr = np.zeros((3, model.nv))
    mujoco.mj_jacSite(model, data, jacp, jacr, site_id)
    jacp_peg = jacp[:, dof_ids]
    jacr_peg = jacr[:, dof_ids]

    x_cur = data.site_xpos[site_id].copy()
    qvel_peg = data.qvel[dof_ids]
    v_lin = jacp_peg @ qvel_peg
    omega = jacr_peg @ qvel_peg

    quat_cur = np.zeros(4)
    mujoco.mju_mat2Quat(quat_cur, data.site_xmat[site_id])
    rot_err = _orientation_error(quat_cur, quat_des)

    tilt = np.linalg.norm(rot_err)
    assert tilt < MAX_TILT_RAD, (
        f"peg tilt {math.degrees(tilt):.2f} deg exceeds the 10 deg safety bound -- "
        "the rx/ry/rz Euler chain goes singular near ry=+/-90deg, and this assert "
        "must fire before gimbal lock presents as unexplained instability"
    )

    stiffness = gains.stiffness_vector
    damping = derived_damping(model, data, dof_ids, stiffness)

    f_trans = stiffness[:3] * (x_des - x_cur) - damping[:3] * v_lin
    f_rot = stiffness[3:] * rot_err - damping[3:] * omega
    f_cart = np.concatenate([f_trans, f_rot])

    tau = jacp_peg.T @ f_trans + jacr_peg.T @ f_rot

    data.qfrc_applied[:] = 0.0
    data.qfrc_applied[dof_ids] = tau + data.qfrc_bias[dof_ids]

    return f_cart


def peg_contact_force_world(model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray:
    """Sum active contact forces on the peg body into a single world-frame
    3-vector.

    mj_contactForce returns the wrench in the CONTACT frame, and by MuJoCo
    convention the returned force acts on geom2 (reaction on geom1 is the
    negative) -- verified, not assumed, by the static-rest test in
    test_control.py: a peg resting on the plate must show a summed
    world-frame force of +z with magnitude ~= m*g.
    """
    peg_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, PEG_BODY)
    total = np.zeros(3)
    result = np.zeros(6)
    for i in range(data.ncon):
        con = data.contact[i]
        body1 = model.geom_bodyid[con.geom1]
        body2 = model.geom_bodyid[con.geom2]
        if body1 != peg_body_id and body2 != peg_body_id:
            continue
        mujoco.mj_contactForce(model, data, i, result)
        frame = con.frame.reshape(3, 3)
        force_world = frame.T @ result[:3]
        sign = 1.0 if body2 == peg_body_id else -1.0
        total += sign * force_world
    return total


def min_contact_dist(data: mujoco.MjData) -> float:
    """Minimum signed distance across active contacts; negative = interpenetration.
    Returns +inf if there are no active contacts."""
    if data.ncon == 0:
        return math.inf
    return min(data.contact[i].dist for i in range(data.ncon))
