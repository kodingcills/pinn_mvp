"""R4: measured coupled closed-loop dynamics at documented operating states.

Answers two open items with one analysis (see CONTEXT.md "Open and
unresolved"):

1. The ~135 Hz rotational-mode estimate has never been measured. Both
   mj_fullM call sites in control.py slice only the diagonal. This driver
   keeps the FULL 6x6 mass matrix, validates it, and reports the coupled
   continuous modes of M dq'' + B_q dq' + K_q dq = 0.
2. Control decimation at 1000/500 Hz trips the tilt guard on 16/16 trials
   while 2000 Hz is 16/16 stable (L3). This driver evaluates the EXACT
   discrete closed-loop map of the implemented semi-implicit + zero-order-hold
   controller at hold_steps 1/2/4 and reports the spectral radius of each.
   The L3 failure is predicted if and only if the sampled coupled loop is
   locally unstable exactly at the decimations that fail in physics.

Static operating states only: no physics is stepped. M(q) for this
slide+hinge chain depends on the hinge angles; contact enters only through
the explicitly added measured axial stiffness variant. Deterministic; no
seeds exist.
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import mujoco

from pegsim.control import ImpedanceGains
from pegsim.dynamics import (
    axial_contact_stiffness_matrix,
    continuous_modes,
    extract_coupled_state_matrices,
    semi_implicit_discrete_map,
)
from pegsim.rollout import (
    VALIDATED_LOOSE_V1,
    EpisodeConfig,
    build_episode_model_data,
)

ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "results" / "r4_coupled_modes_summary.json"

# MEASURED axial contact/constraint stiffness (CONTEXT.md "Measured numbers").
MEASURED_AXIAL_STIFFNESS_N_PER_M = 277e3

DT_S = VALIDATED_LOOSE_V1.solver.timestep
DECIMATION_SWEEP = (1, 2, 4)  # 2000 Hz, 1000 Hz, 500 Hz control rates

# Operating states are static qpos constructions on the canonical scene. The
# peg is a slide+hinge chain anchored at the tip, so qpos[:3] IS the tip
# position and qpos[3:5] are rx/ry hinge angles directly.
OPERATING_STATES: dict[str, dict[str, float]] = {
    "hover_1mm": {"z": 0.001, "rx_deg": 0.0},
    "plate_contact": {"z": 0.0, "rx_deg": 0.0},
    "tilt_2deg": {"z": 0.0, "rx_deg": 2.0},
}


def _model_data():
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
        control_decimation=1,
        control_delay_steps=0,
    )
    return build_episode_model_data(episode)


def _place_state(spec: dict[str, float]) -> None:
    _DATA.qpos[:] = 0.0
    _DATA.qpos[2] = spec["z"]
    _DATA.qpos[3] = math.radians(spec["rx_deg"])
    mujoco.mj_forward(_MODEL, _DATA)


_MODEL = None
_DATA = None


def _spectral_radius(matrix: np.ndarray) -> float:
    return float(np.max(np.abs(np.linalg.eigvals(matrix))))


def _mode_rows(modes) -> list[dict[str, float]]:
    return [
        {
            "natural_frequency_hz": float(mode.natural_frequency_hz),
            "damping_ratio": float(mode.damping_ratio),
            "eigenvalue_real": float(mode.eigenvalue_real),
            "eigenvalue_imag": float(mode.eigenvalue_imag),
        }
        for mode in modes
    ]


def analyze_configuration(
    state_name: str,
    gains: ImpedanceGains,
    axial_stiffness_n_per_m: float | None,
) -> dict[str, object]:
    _place_state(OPERATING_STATES[state_name])
    matrices = extract_coupled_state_matrices(_MODEL, _DATA, gains.stiffness_vector)
    stiffness_matrix = matrices.stiffness_matrix
    if axial_stiffness_n_per_m is not None:
        stiffness_matrix = (
            stiffness_matrix
            + axial_contact_stiffness_matrix(matrices, axial_stiffness_n_per_m)
        )

    modes = continuous_modes(
        matrices.mass_matrix, matrices.damping_matrix, stiffness_matrix
    )
    mass = matrices.mass_matrix
    spectral_radii = {}
    for hold_steps in DECIMATION_SWEEP:
        map_matrix = semi_implicit_discrete_map(
            mass, matrices.damping_matrix, stiffness_matrix, DT_S, hold_steps
        )
        spectral_radii[f"hold_{hold_steps}"] = {
            "control_rate_hz": 1.0 / (hold_steps * DT_S),
            "spectral_radius": _spectral_radius(map_matrix),
        }

    return {
        "state": state_name,
        "gains": {
            "k_xy": gains.k_xy,
            "k_z": gains.k_z,
            "k_rx": gains.k_rx,
            "k_ry": gains.k_ry,
            "k_rz": gains.k_rz,
        },
        "axial_contact_stiffness_n_per_m": axial_stiffness_n_per_m,
        "mass_diagonal": [float(v) for v in np.diag(mass)],
        "mass_coupling_x_ry": float(mass[0, 4]),
        "mass_coupling_y_rx": float(mass[1, 3]),
        "stiffness_condition_number": float(
            np.linalg.cond(stiffness_matrix)
        ),
        "max_continuous_mode_hz": float(
            max(mode.natural_frequency_hz for mode in modes)
        ),
        "continuous_modes": _mode_rows(modes),
        "discrete": spectral_radii,
    }


def main() -> None:
    global _MODEL, _DATA
    cfg = VALIDATED_LOOSE_V1
    _MODEL, _DATA = _model_data()

    search_gains = replace(cfg.gains, k_z=cfg.insertion.k_z_search)
    configurations: list[dict[str, object]] = []
    for state_name in OPERATING_STATES:
        for label, gains in (
            ("search", search_gains),
            ("seating", cfg.gains),
        ):
            configurations.append(
                analyze_configuration(state_name, gains, axial_stiffness_n_per_m=None)
            )
            if label == "search":
                configurations[-1]["gains_phase"] = "search (k_z=500, pre-commit)"
            else:
                configurations[-1]["gains_phase"] = "seating (k_z=2000)"

    # Measured-axial-stiffness variant at the contact state, search phase
    # (where the L3 tilt-guard failure occurs, before commit).
    configurations.append(
        analyze_configuration(
            "plate_contact",
            search_gains,
            axial_stiffness_n_per_m=MEASURED_AXIAL_STIFFNESS_N_PER_M,
        )
    )
    configurations[-1]["gains_phase"] = "search + measured axial contact stiffness"

    # k_xy=2000 tip-over case at 2000 Hz (CONTEXT.md: tilt 0.11 -> 10.29 deg
    # over ~370 steps, called physical rather than numerical).
    tipover_gains = replace(cfg.gains, k_xy=2000.0)
    configurations.append(
        analyze_configuration("plate_contact", tipover_gains, axial_stiffness_n_per_m=None)
    )
    configurations[-1]["gains_phase"] = "k_xy=2000 tip-over case"

    l3_measured = {
        "2000Hz": "16/16 stable",
        "1000Hz": "0/16 (tilt guard at first plate contact)",
        "500Hz": "0/16 (tilt guard at first plate contact)",
    }
    summary = {
        "config": {
            "name": cfg.name,
            "solver_timestep_s": DT_S,
            "decimation_sweep": list(DECIMATION_SWEEP),
            "measured_axial_stiffness_n_per_m": MEASURED_AXIAL_STIFFNESS_N_PER_M,
            "operating_states": OPERATING_STATES,
            "l3_measured_stability": l3_measured,
        },
        "configurations": configurations,
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )

    print(f"config={cfg.name} dt={DT_S}s states={list(OPERATING_STATES)}")
    header = (
        f"{'state':<14} {'phase':<44} {'max mode Hz':>11} "
        f"{'|l|@2000':>10} {'|l|@1000':>10} {'|l|@500':>10}"
    )
    print(header)
    for entry in configurations:
        discrete = entry["discrete"]
        radii = [
            discrete[f"hold_{hold}"]["spectral_radius"] for hold in DECIMATION_SWEEP
        ]
        phase = entry["gains_phase"]
        print(
            f"{entry['state']:<14} {phase:<44} "
            f"{entry['max_continuous_mode_hz']:>11.3f} "
            f"{radii[0]:>10.4f} {radii[1]:>10.4f} {radii[2]:>10.4f}"
        )
    print(f"summary={SUMMARY_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
