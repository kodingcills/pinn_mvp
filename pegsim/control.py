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
    derived per-DOF from the mass matrix (see derived_damping).

    Rotational stiffness is per-DOF, not a shared k_rot: rz's inertia (about
    the cylinder's long axis, no parallel-axis term) is roughly two orders of
    magnitude smaller than rx/ry's (which pick up a parallel-axis term since
    the hinges sit at the tip, not the CoM). k_rz defaults near-zero, not
    exactly zero: a round peg in a round hole doesn't care what angle it
    spins to about its own axis, so rz needs no restoring stiffness for
    insertion, but derived_damping's D=2*sqrt(K*M) needs a nonzero K to
    produce any damping at all -- exactly zero would leave rz undamped and
    free to spin up from any small perturbation.

    k_rx/k_ry=1.0, not the original 5.0: the per-DOF diagonal cap in
    stability_cap (K <= (2/dt)^2*M_ii) says K=5 is fine in isolation for
    rx/ry on the 5mm/50g peg (M_ii=4.2e-5 gives a cap of ~168) -- but that
    check treats each DOF as decoupled, and it isn't. mj_fullM's off-diagonal
    terms (M_x,ry = M_y,rx = 1.25e-3, verified numerically) couple x-translation
    with ry-rotation and y with rx (the same tip-vs-CoM offset that couples
    gravity into rx/ry). Empirically, K_rx=K_ry=5 and 2 both diverge under
    lateral motion despite passing the per-DOF cap; 1.5 and 1.25 are still
    marginal (unstable under a faster/larger lateral ramp); 1.0 held stable
    under both. The per-DOF diagonal cap is necessary but NOT sufficient for
    this coupled 6-DOF chain -- flagged, not silently worked around.
    """

    # k_xy, k_rx, k_ry, and SolverSpec.timestep have NO defaults, deliberately:
    # a validated config is the COMBINATION of these four (plus f_search), not
    # any one in isolation (see PHASE2.md's G4 finding -- k_xy=1000 was only
    # ever validated together with k_rx=k_ry=5.0 and dt=0.0005; changing one in
    # isolation from a "convenient default" silently produces an untested
    # combination and breaks either the search config or a caller tuned
    # against the others). Callers must state all three explicitly, or use
    # pegsim.rollout.VALIDATED_LOOSE_V1.
    k_xy: float
    k_rx: float
    k_ry: float
    k_z: float = 2000.0
    k_rz: float = 0.05

    def __post_init__(self) -> None:
        assert self.k_xy > 0, "k_xy must be positive"
        assert self.k_z > 0, "k_z must be positive"
        assert self.k_rx > 0, "k_rx must be positive"
        assert self.k_ry > 0, "k_ry must be positive"
        assert self.k_rz > 0, "k_rz must be positive (near-zero is fine, exactly zero leaves rz undamped)"

    @property
    def stiffness_vector(self) -> np.ndarray:
        """Order matches PEG_DOF_JOINT_NAMES: (x, y, z, rx, ry, rz)."""
        return np.array([self.k_xy, self.k_xy, self.k_z, self.k_rx, self.k_ry, self.k_rz])


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


def stability_cap(model: mujoco.MjModel, data: mujoco.MjData, dof_ids: np.ndarray, dt: float) -> np.ndarray:
    """Max per-DOF stiffness for which explicit force injection stays stable:
    K_i <= (2/dt)^2 * M_ii.

    qfrc_applied is a plain per-step external force, computed in Python from
    the CURRENT state -- it gets none of MuJoCo's implicit treatment of
    passive (native joint stiffness/damping) terms, regardless of
    integrator="implicitfast". That makes this equivalent to semi-implicit
    Euler integration of a mass-spring system driven by an external force,
    whose stability limit is omega_n*dt < 2 with omega_n = sqrt(K/M);
    rearranged, K <= (2/dt)^2 * M. Verified against the rz blow-up in
    Milestone 2/3: M_rz for the 5mm/50g peg was 6.25e-7, giving a cap of 2.5
    at dt=1ms -- the old shared K_rot=5 exceeded it by 2x, exactly matching
    the observed instability.
    """
    full_m = np.zeros((model.nv, model.nv))
    mujoco.mj_fullM(model, data, full_m)
    m_diag = full_m[dof_ids, dof_ids]
    return (2.0 / dt) ** 2 * m_diag


def assert_gains_stable(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    gains: ImpedanceGains,
    dt: float,
    warn_margin: float = 0.8,
) -> None:
    """One-time stability check at controller construction (call once per
    episode, right after the initial mj_forward, before stepping). Asserts no
    DOF's stiffness exceeds its stability_cap at the model's current
    configuration, and prints a warning for any DOF within warn_margin of its
    cap even if not yet violated.

    NECESSARY, NOT SUFFICIENT: this treats each DOF as decoupled, but x/ry
    and y/rx are coupled through mj_fullM's off-diagonal terms (the same
    tip-vs-CoM offset that couples gravity into rx/ry). K_rx=K_ry=5 and 2
    both pass this per-DOF check comfortably (cap ~168 at rest) yet diverge
    empirically under lateral motion; only empirical sweeping down to
    K_rx=K_ry=1 gave a version that actually held (see ImpedanceGains
    defaults' comment). This function still catches gross violations (it
    caught the original shared K_rot=5 on rz, cap 2.5), it just cannot catch
    coupling-driven instability the diagonal-only formula doesn't model.
    """
    dof_ids = peg_dof_ids(model)
    cap = stability_cap(model, data, dof_ids, dt)
    stiffness = gains.stiffness_vector
    for name, k, c in zip(PEG_DOF_JOINT_NAMES, stiffness, cap):
        assert k <= c, (
            f"K for {name} ({k:.6g}) exceeds the explicit-integration stability "
            f"cap (2/dt)^2*M_ii = {c:.6g} at dt={dt} -- this WILL blow up, not "
            "a maybe (see the rz finding in the Milestone 2/3 report)"
        )
        if k > warn_margin * c:
            print(
                f"WARNING: {name} stiffness {k:.6g} is at {100.0 * k / c:.1f}% of "
                f"its stability cap {c:.6g} (dt={dt}) -- little margin left"
            )


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
