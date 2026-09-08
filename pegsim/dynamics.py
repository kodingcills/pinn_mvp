"""Coupled closed-loop dynamics analysis for the peg impedance controller.

R04 diagnostic module. ``control.py`` treats each DOF as decoupled: both
``mj_fullM`` call sites slice only the diagonal, and ``assert_gains_stable``
documents that this is necessary but not sufficient because the mass matrix
couples x-translation with ry-rotation (and y with rx). This module keeps the
FULL matrices and constructs the local linear model of the implemented loop:

    M dq'' + B_q dq' + K_q dq = 0

where M(q) is the complete generalized mass matrix at an operating state and

    K_q = Jp^T diag(k_trans) Jp + Jr^T diag(k_rot) Jr
    B_q = Jp^T diag(d_trans) Jp + Jr^T diag(d_rot) Jr

are the joint-space stiffness/damping exactly as the controller renders them
(tau = Jp^T f_trans + Jr^T f_rot, with D_i = 2*sqrt(K_i * M_ii) from
``derived_damping``). Gravity and Coriolis are absent from the linearization
because the controller adds ``qfrc_bias`` compensation, which cancels them
exactly at the operating state.

The discrete analysis mirrors the ACTUAL implemented integration, not an
idealization: ``qfrc_applied`` is a plain per-step external force computed in
Python from the state at the control tick and held until the next tick (zero-
order hold), while MuJoCo's ``implicitfast`` integrator updates velocity with
the held force and then advances position with the NEW velocity (semi-implicit
Euler). For a control period T = hold_steps * dt with the force frozen at its
tick-k value tau_k = -K_q q_k - B_q v_k, the exact one-control-period map is

    v_{k+d} = v_k - T  A (K_q q_k + B_q v_k)          A = M^-1
    q_{k+d} = q_k + T  v_k - dt^2 (d(d+1)/2) A (K_q q_k + B_q v_k)

which reduces to the familiar semi-implicit Euler step at hold_steps = 1.
Eigenvalues of that map outside the unit circle indicate a locally unstable
sampled closed loop.

Contact is NOT part of these matrices. Contacts enter through the constraint
solver, not qfrc_applied; the driver applies the MEASURED axial contact
stiffness explicitly where a contact-loaded operating state is analyzed.
"""
from __future__ import annotations

from dataclasses import dataclass

import mujoco
import numpy as np
from numpy.typing import NDArray

from pegsim.control import (
    PEG_TIP_SITE,
    derived_damping,
    peg_dof_ids,
)

_SYMMETRY_RTOL = 1e-10


@dataclass(frozen=True)
class CoupledStateMatrices:
    """Joint-space (6x6) linearized closed-loop matrices at one state."""

    mass_matrix: NDArray[np.float64]
    damping_matrix: NDArray[np.float64]
    stiffness_matrix: NDArray[np.float64]
    dof_ids: NDArray[np.int64]
    tip_jacobian_linear: NDArray[np.float64]
    tip_jacobian_angular: NDArray[np.float64]


def extract_coupled_state_matrices(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    stiffness_vector: NDArray[np.float64],
) -> CoupledStateMatrices:
    """Extract M(q), B_q, K_q at the CURRENT data state.

    Precondition: ``mj_forward`` (or ``mj_step1``) has already been called for
    the state in ``data``, so site positions and the mass matrix are current.
    ``stiffness_vector`` uses the controller's DOF order (x, y, z, rx, ry, rz)
    -- ``ImpedanceGains.stiffness_vector`` supplies exactly that.
    """
    dof_ids = peg_dof_ids(model)
    full_m = np.zeros((model.nv, model.nv))
    mujoco.mj_fullM(model, data, full_m)
    mass = np.array(full_m[np.ix_(dof_ids, dof_ids)], dtype=float, copy=True)

    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, PEG_TIP_SITE)
    jacp = np.zeros((3, model.nv))
    jacr = np.zeros((3, model.nv))
    mujoco.mj_jacSite(model, data, jacp, jacr, site_id)
    jacp_peg = np.array(jacp[:, dof_ids], dtype=float, copy=True)
    jacr_peg = np.array(jacr[:, dof_ids], dtype=float, copy=True)

    stiffness = np.asarray(stiffness_vector, dtype=float)
    if stiffness.shape != (6,):
        raise ValueError(
            f"stiffness_vector must have shape (6,), got {stiffness.shape}"
        )
    damping = derived_damping(model, data, dof_ids, stiffness)

    k_trans = np.diag(stiffness[:3])
    k_rot = np.diag(stiffness[3:])
    d_trans = np.diag(damping[:3])
    d_rot = np.diag(damping[3:])
    stiffness_matrix = jacp_peg.T @ k_trans @ jacp_peg + jacr_peg.T @ k_rot @ jacr_peg
    damping_matrix = jacp_peg.T @ d_trans @ jacp_peg + jacr_peg.T @ d_rot @ jacr_peg

    _assert_symmetric_positive_definite(mass, "mass matrix M(q)")
    _assert_symmetric_semidefinite(stiffness_matrix, "stiffness matrix K_q")
    _assert_symmetric_semidefinite(damping_matrix, "damping matrix B_q")

    return CoupledStateMatrices(
        mass_matrix=mass,
        damping_matrix=damping_matrix,
        stiffness_matrix=stiffness_matrix,
        dof_ids=np.array(dof_ids, dtype=np.int64, copy=True),
        tip_jacobian_linear=jacp_peg,
        tip_jacobian_angular=jacr_peg,
    )


def _assert_symmetric_positive_definite(matrix: NDArray[np.float64], name: str) -> None:
    if not np.allclose(matrix, matrix.T, rtol=_SYMMETRY_RTOL, atol=0.0):
        raise ValueError(f"{name} is not symmetric to {_SYMMETRY_RTOL} relative tolerance")
    eigenvalues = np.linalg.eigvalsh(matrix)
    if np.min(eigenvalues) <= 0.0:
        raise ValueError(
            f"{name} is not positive-definite (min eigenvalue {eigenvalues.min():.6g})"
        )


def _assert_symmetric_semidefinite(matrix: NDArray[np.float64], name: str) -> None:
    if not np.allclose(matrix, matrix.T, rtol=_SYMMETRY_RTOL, atol=0.0):
        raise ValueError(f"{name} is not symmetric to {_SYMMETRY_RTOL} relative tolerance")
    eigenvalues = np.linalg.eigvalsh(matrix)
    if np.min(eigenvalues) < 0.0:
        raise ValueError(
            f"{name} is not positive-semidefinite (min eigenvalue {eigenvalues.min():.6g})"
        )


@dataclass(frozen=True)
class ContinuousMode:
    """One mode of M d2q + B_q dq + K_q q = 0.

    Complex-conjugate eigenvalue pairs are represented once (the member with
    positive imaginary part). Real eigenvalues (overdamped modes) have zero
    imaginary part. ``natural_frequency_hz`` is |s| / (2*pi) and
    ``damping_ratio`` is -Re(s) / |s| (>1 for real modes).
    """

    eigenvalue_real: float
    eigenvalue_imag: float

    @property
    def natural_frequency_hz(self) -> float:
        return float(np.hypot(self.eigenvalue_real, self.eigenvalue_imag)) / (2.0 * np.pi)

    @property
    def damping_ratio(self) -> float:
        magnitude = float(np.hypot(self.eigenvalue_real, self.eigenvalue_imag))
        return -self.eigenvalue_real / magnitude if magnitude > 0.0 else 0.0


def continuous_modes(
    mass: NDArray[np.float64],
    damping: NDArray[np.float64],
    stiffness: NDArray[np.float64],
) -> tuple[ContinuousMode, ...]:
    """Modes of the continuous linearization, slowest first.

    Solves the state-space form A = [[0, I], [-M^-1 K, -M^-1 B_q]], which is
    the quadratic eigenproblem (s^2 M + s B_q + K) v = 0 written in first-order
    form. M must be positive-definite.
    """
    n = mass.shape[0]
    if damping.shape != (n, n) or stiffness.shape != (n, n):
        raise ValueError("mass, damping, and stiffness shapes must agree")
    _assert_symmetric_positive_definite(mass, "mass matrix M(q)")
    solved_mk = np.linalg.solve(mass, stiffness)
    solved_mb = np.linalg.solve(mass, damping)
    state_matrix = np.block([
        [np.zeros((n, n)), np.eye(n)],
        [-solved_mk, -solved_mb],
    ])
    eigenvalues = np.linalg.eigvals(state_matrix)

    modes: list[ContinuousMode] = []
    for value in eigenvalues:
        if abs(value.imag) <= 8.0 * np.finfo(float).eps * max(1.0, abs(value)):
            modes.append(ContinuousMode(float(value.real), 0.0))
        elif value.imag > 0.0:
            modes.append(ContinuousMode(float(value.real), float(value.imag)))
    modes.sort(key=lambda mode: mode.natural_frequency_hz)
    return tuple(modes)


def semi_implicit_discrete_map(
    mass: NDArray[np.float64],
    damping: NDArray[np.float64],
    stiffness: NDArray[np.float64],
    dt: float,
    hold_steps: int,
) -> NDArray[np.float64]:
    """Exact one-control-period closed-loop map of the implemented loop.

    The controller samples (q, v) at the tick, computes tau = -K_q q - B_q v,
    and holds it for ``hold_steps`` physics steps of length ``dt``. Each
    physics step is semi-implicit Euler on the held force:

        v_j = v_{j-1} + dt M^-1 tau_k
        q_j = q_{j-1} + dt v_j

    composing to the closed form documented in the module docstring.
    ``hold_steps=1`` is the decimation-1 loop; the L3 1000 Hz / 500 Hz
    conditions are hold_steps=2 / 4 at dt=5e-4.
    """
    if dt <= 0.0:
        raise ValueError("dt must be positive")
    if type(hold_steps) is not int or hold_steps < 1:
        raise ValueError("hold_steps must be a positive integer")
    n = mass.shape[0]
    _assert_symmetric_positive_definite(mass, "mass matrix M(q)")

    control_period = hold_steps * dt
    ramp = dt * dt * hold_steps * (hold_steps + 1) / 2.0
    solved_mk = np.linalg.solve(mass, stiffness)
    solved_mb = np.linalg.solve(mass, damping)
    identity = np.eye(n)
    return np.block([
        [identity - ramp * solved_mk, control_period * identity - ramp * solved_mb],
        [-control_period * solved_mk, identity - control_period * solved_mb],
    ])


def semi_implicit_frozen_force_step(
    mass: NDArray[np.float64],
    force: NDArray[np.float64],
    position: NDArray[np.float64],
    velocity: NDArray[np.float64],
    dt: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """One semi-implicit Euler step under a CONSTANT applied force.

    Reference implementation of the inner physics step used by
    ``semi_implicit_discrete_map``; tests use it to verify the closed-form
    composition against brute-force stepping.
    """
    if dt <= 0.0:
        raise ValueError("dt must be positive")
    acceleration = np.linalg.solve(mass, force)
    new_velocity = velocity + dt * acceleration
    new_position = position + dt * new_velocity
    return new_position, new_velocity


def axial_contact_stiffness_matrix(
    matrices: CoupledStateMatrices,
    axial_stiffness_n_per_m: float,
) -> NDArray[np.float64]:
    """Joint-space stiffness of a vertical contact spring at the peg tip.

    Models the MEASURED axial constraint stiffness (plate contact) as a
    world +z spring acting at the tip site, mapped through the linear
    Jacobian: K_contact = k * Jp^T (e_z e_z^T) Jp. The contact point of a
    rim-contact state sits at the tip height, so the tip-site Jacobian is the
    documented approximation; the lateral wall stiffness of an elliptic-cone
    contact is NOT modeled here.
    """
    if axial_stiffness_n_per_m < 0.0:
        raise ValueError("axial_stiffness_n_per_m must be nonnegative")
    direction = np.zeros((3, 3))
    direction[2, 2] = 1.0
    jacp = matrices.tip_jacobian_linear
    return axial_stiffness_n_per_m * (jacp.T @ direction @ jacp)
