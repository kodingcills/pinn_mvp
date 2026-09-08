"""Unit tests for pegsim.dynamics: analytic recovery, exact discretization,
and stability boundaries. Physics-side operating states are covered by the
R4 driver; these tests are pure linear algebra plus one compiled model."""
import math

import mujoco
import numpy as np
import pytest

from pegsim.dynamics import (
    axial_contact_stiffness_matrix,
    continuous_modes,
    extract_coupled_state_matrices,
    semi_implicit_discrete_map,
    semi_implicit_frozen_force_step,
)
from pegsim.rollout import (
    VALIDATED_LOOSE_V1,
    EpisodeConfig,
    build_episode_model_data,
)


def _canonical_model_data():
    """Canonical scene with the peg hovering 1 mm above the plate."""
    cfg = VALIDATED_LOOSE_V1
    episode = EpisodeConfig(
        task=cfg.task,
        solver=cfg.solver,
        gains=cfg.gains,
        insertion=cfg.insertion,
        z_start=cfg.z_start,
        initial_offset_r=0.0,
        initial_offset_theta=0.0,
        max_seconds=cfg.max_seconds,
        spiral_spec=None,
        depth_commit=cfg.depth_commit,
        control_decimation=cfg.control_decimation,
        control_delay_steps=cfg.control_delay_steps,
    )
    model, data = build_episode_model_data(episode)
    data.qpos[:] = 0.0
    data.qpos[2] = 0.001
    mujoco.mj_forward(model, data)
    return model, data


def test_extracted_matrices_are_symmetric_and_mass_positive_definite():
    model, data = _canonical_model_data()
    matrices = extract_coupled_state_matrices(
        model, data, VALIDATED_LOOSE_V1.gains.stiffness_vector
    )
    n = model.nv
    assert matrices.mass_matrix.shape == (n, n) == (6, 6)
    np.testing.assert_allclose(
        matrices.mass_matrix, matrices.mass_matrix.T, rtol=1e-12, atol=0.0
    )
    assert np.min(np.linalg.eigvalsh(matrices.mass_matrix)) > 0.0
    np.testing.assert_allclose(
        matrices.stiffness_matrix, matrices.stiffness_matrix.T, rtol=1e-12, atol=0.0
    )
    np.testing.assert_allclose(
        matrices.damping_matrix, matrices.damping_matrix.T, rtol=1e-12, atol=0.0
    )
    assert np.min(np.linalg.eigvalsh(matrices.stiffness_matrix)) >= 0.0
    assert np.min(np.linalg.eigvalsh(matrices.damping_matrix)) >= 0.0


def test_mass_matrix_has_documented_translation_rotation_coupling():
    model, data = _canonical_model_data()
    matrices = extract_coupled_state_matrices(
        model, data, VALIDATED_LOOSE_V1.gains.stiffness_vector
    )
    # DOF order: x, y, z, rx, ry, rz. The x/ry and y/rx couplings are the
    # documented tip-vs-CoM offset terms (control.py quotes M_x,ry = 1.25e-3).
    coupling_x_ry = abs(matrices.mass_matrix[0, 4])
    coupling_y_rx = abs(matrices.mass_matrix[1, 3])
    assert coupling_x_ry > 0.0
    assert coupling_y_rx > 0.0
    assert 0.5e-3 < coupling_x_ry < 2.5e-3
    assert 0.5e-3 < coupling_y_rx < 2.5e-3
    # The rx/ry diagonal is the documented ~4.2e-5 kg*m^2 hinge inertia
    # (control.py's stability-cap example), not a translational mass.
    assert 3.0e-5 < matrices.mass_matrix[3, 3] < 6.0e-5
    assert 3.0e-5 < matrices.mass_matrix[4, 4] < 6.0e-5


def test_single_dof_undamped_modes_match_analytic_natural_frequency():
    mass = np.array([[2.0]])
    damping = np.array([[0.0]])
    stiffness = np.array([[18.0]])  # omega_n = 3 rad/s = 0.477 Hz
    modes = continuous_modes(mass, damping, stiffness)
    assert len(modes) == 1
    assert modes[0].natural_frequency_hz == pytest.approx(3.0 / (2.0 * math.pi))
    assert modes[0].damping_ratio == pytest.approx(0.0, abs=1e-12)


def test_single_dof_damped_modes_match_analytic_root():
    mass = np.array([[1.0]])
    damping = np.array([[0.4]])
    stiffness = np.array([[25.0]])  # omega_n = 5, zeta = 0.4 / (2*5) = 0.04
    modes = continuous_modes(mass, damping, stiffness)
    assert len(modes) == 1
    expected_real = -0.2
    expected_imag = 5.0 * math.sqrt(1.0 - 0.04**2)
    assert modes[0].eigenvalue_real == pytest.approx(expected_real, rel=1e-9)
    assert modes[0].eigenvalue_imag == pytest.approx(expected_imag, rel=1e-9)
    assert modes[0].damping_ratio == pytest.approx(0.04, rel=1e-9)


def test_two_dof_coupled_stiffness_matches_analytic_modes():
    mass = np.eye(2)
    damping = np.zeros((2, 2))
    stiffness = np.array([[4.0, 1.0], [1.0, 4.0]])  # eigenvalues 3 and 5
    modes = continuous_modes(mass, damping, stiffness)
    frequencies = sorted(mode.natural_frequency_hz for mode in modes)
    expected = sorted([math.sqrt(3.0), math.sqrt(5.0)])
    np.testing.assert_allclose(
        np.array(frequencies) * 2.0 * math.pi, expected, rtol=1e-9
    )


def test_discrete_map_at_hold_one_matches_closed_form_semi_implicit_euler():
    mass = np.array([[1.5]])
    damping = np.array([[0.3]])
    stiffness = np.array([[12.0]])
    dt = 0.001
    map_matrix = semi_implicit_discrete_map(mass, damping, stiffness, dt, 1)
    m_inv_k = float(stiffness.item() / mass.item())
    m_inv_b = float(damping.item() / mass.item())
    expected = np.array([
        [1.0 - dt * dt * m_inv_k, dt * (1.0 - dt * m_inv_b)],
        [-dt * m_inv_k, 1.0 - dt * m_inv_b],
    ])
    np.testing.assert_allclose(map_matrix, expected, rtol=1e-14)


def test_discrete_map_composition_matches_brute_force_frozen_force_stepping():
    mass = np.array([
        [2.0, 0.0, 0.3],
        [0.0, 1.0, 0.0],
        [0.3, 0.0, 0.5],
    ])
    damping = np.array([
        [0.4, 0.0, 0.1],
        [0.0, 0.2, 0.0],
        [0.1, 0.0, 0.6],
    ])
    stiffness = np.array([
        [9.0, 0.0, 1.0],
        [0.0, 7.0, 0.0],
        [1.0, 0.0, 11.0],
    ])
    dt = 0.0005
    hold_steps = 4

    position = np.array([0.01, -0.02, 0.005])
    velocity = np.array([0.1, -0.05, 0.02])
    held_force = -(stiffness @ position + damping @ velocity)

    brute_position = position.copy()
    brute_velocity = velocity.copy()
    for _ in range(hold_steps):
        brute_position, brute_velocity = semi_implicit_frozen_force_step(
            mass, held_force, brute_position, brute_velocity, dt
        )

    map_matrix = semi_implicit_discrete_map(
        mass, damping, stiffness, dt, hold_steps
    )
    state = np.concatenate([position, velocity])
    composed = map_matrix @ state
    np.testing.assert_allclose(
        composed[:3], brute_position, rtol=1e-12, atol=1e-15
    )
    np.testing.assert_allclose(
        composed[3:], brute_velocity, rtol=1e-12, atol=1e-15
    )


def test_semi_implicit_stability_boundary_at_omega_dt_two():
    # Undamped spring, semi-implicit Euler: marginal at omega*dt = 2,
    # unstable above, stable below.
    mass = np.array([[1.0]])
    damping = np.array([[0.0]])
    stiffness = np.array([[9.0]])  # omega = 3
    inside = semi_implicit_discrete_map(mass, damping, stiffness, 0.6, 1)
    outside = semi_implicit_discrete_map(mass, damping, stiffness, 0.7, 1)
    assert np.max(np.abs(np.linalg.eigvals(inside))) <= 1.0 + 1e-12
    assert np.max(np.abs(np.linalg.eigvals(outside))) > 1.0


def test_damped_sampled_loop_stabilizes_undamped_boundary():
    # Adding the rendered damping pulls the same spring inside the unit circle
    # at the dt that is marginal undamped.
    mass = np.array([[1.0]])
    damping = np.array([[0.6]])  # B = 2*sqrt(K*M) rendered critical damping
    stiffness = np.array([[9.0]])
    map_matrix = semi_implicit_discrete_map(mass, damping, stiffness, 0.6, 1)
    assert np.max(np.abs(np.linalg.eigvals(map_matrix))) < 1.0


def test_invalid_inputs_raise():
    mass = np.eye(2)
    with pytest.raises(ValueError):
        semi_implicit_discrete_map(mass, np.zeros((2, 2)), np.eye(2), -1.0, 1)
    with pytest.raises(ValueError):
        semi_implicit_discrete_map(mass, np.zeros((2, 2)), np.eye(2), 0.001, 0)
    with pytest.raises(ValueError):
        semi_implicit_discrete_map(mass, np.zeros((2, 2)), np.eye(2), 0.001, 1.5)
    singular = np.array([[1.0, 0.0], [0.0, 0.0]])
    with pytest.raises(ValueError):
        continuous_modes(singular, np.zeros((2, 2)), np.eye(2))
    with pytest.raises(ValueError):
        continuous_modes(np.eye(3), np.zeros((2, 2)), np.eye(3))


def test_axial_contact_stiffness_maps_through_tip_jacobian():
    model, data = _canonical_model_data()
    data.qpos[2] = 0.0  # zero hover height does not change the zero-tilt map
    mujoco.mj_forward(model, data)
    matrices = extract_coupled_state_matrices(
        model, data, VALIDATED_LOOSE_V1.gains.stiffness_vector
    )
    k_axial = 277e3
    contact = axial_contact_stiffness_matrix(matrices, k_axial)
    # At zero tilt the tip z-jacobian row for the z slide is exactly 1 and the
    # slides carry no rotation, so K_contact[z, z] = k_axial exactly.
    assert contact[2, 2] == pytest.approx(k_axial, rel=1e-12)
    # The z slide cannot produce rotation about z, and the hinge axes at zero
    # tilt are horizontal: the contact spring stores no rz energy.
    assert contact[5, 5] == pytest.approx(0.0, abs=1e-9)
    with pytest.raises(ValueError):
        axial_contact_stiffness_matrix(matrices, -1.0)
