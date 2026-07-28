"""Milestone 3 GATE: contact model audit (re-audit per user feedback).

Sweeps three solver settings (A default / B tuned / C very stiff) at a
moderate lateral load, then two follow-ups the first pass missed:

  1. High-force variant: A and B under ~15-20N of load against the bore
     wall. Literally "ramping z_des" to build force does NOT work in a
     through-hole: verified directly -- continuing to command z_des deeper
     at the 0.5*capture_radius lateral offset tops out under 1N regardless
     of depth (peak ~0.4N for A, ~0.9N for B, both AT the chamfer, decaying
     to ~0 through the bore), because nothing axially obstructs travel
     except friction once past the chamfer. To get a real, controlled
     15-20N load against the actual wall, this variant instead commands a
     large lateral overshoot (x_des = hole_radius + F/k_xy) while the peg
     sits at mid-bore depth, so the bore wall itself supplies the reaction
     force -- radial, not literally axial, but this is what "the wall" means
     in a peg-in-hole and it is the only way this rig produces a sustained
     15-20N load. Flagging the substitution, not deciding it silently.
  2. Timeconst-clamp test: MuJoCo floors solref timeconst to 2*dt unless the
     REFSAFE flag is disabled (verified against the actual mujoco source,
     mujoco_warp/_src/constraint.py and mjx/_src/constraint.py -- both do
     `timeconst = max(timeconst, 2*timestep)`). At dt=1ms, B's timeconst
     (0.002) and C's (0.0005, floored to 0.002) are IDENTICAL -- confirmed
     directly by reading data.efc_KBIP, not just inferred from behavior: both
     give constraint stiffness K=277008, and C only changes (to K=4432133,
     exactly the predicted 16x = (0.002/0.0005)^2) once dt drops to 0.00025
     and the floor no longer binds.

Runs on the canonical 5mm/50g peg (TASK), not a bigger substitute -- the
Milestone 2 finding that this peg is unstable under lateral motion was a
per-DOF-gains problem (see pegsim/control.py's ImpedanceGains), fixed there,
not worked around here.

A pure zero-lateral-offset drop (scripts/step1_drop.py) never touches
anything -- the peg has 0.5mm clearance all around and falls straight
through the hole center. This script offsets the peg by 0.5*capture_radius
before dropping it -- inside the chamfer's catch range -- so the chamfer
must actually resist a known, sustained geometric overlap.

STOP after this script's report. Do not start Milestone 4 without go-ahead.
"""
from __future__ import annotations

import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mujoco
import numpy as np

from pegsim.control import (
    ImpedanceGains,
    assert_gains_stable,
    compute_and_apply_qfrc,
    min_contact_dist,
    peg_contact_force_world,
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
GAINS = ImpedanceGains()

Z_START = 0.005
Z_END = -0.045
RAMP_RATE = 0.01  # m/s
LATERAL_OFFSET = 0.5 * TASK.capture_radius  # inside the chamfer catch range
# The peg loses ALL wall overlap once its whole shaft has passed below the
# bore -- past this depth, force must drop to ~0 regardless of solver
# stiffness, that is geometry exiting contact, not the wall "giving way".
Z_FLOOR = -(TASK.plate_thickness + TASK.peg_length) + 0.002

SETTINGS = {
    "A_default": SolverSpec(solref=(0.02, 1.0), solimp=(0.9, 0.95, 0.001)),
    "B_tuned": SolverSpec(solref=(0.002, 1.0), solimp=(0.9, 0.95, 0.0001)),
    "C_very_stiff": SolverSpec(solref=(0.0005, 1.0), solimp=(0.9, 0.95, 0.00005)),
}


def high_freq_noise_std(signal: np.ndarray, dt: float, cutoff_hz: float = 50.0) -> float:
    """Std of the component of `signal` at or above cutoff_hz, via a brick-wall
    FFT high-pass (numpy only -- no scipy in this project)."""
    n = len(signal)
    freqs = np.fft.rfftfreq(n, d=dt)
    spectrum = np.fft.rfft(signal)
    spectrum[freqs < cutoff_hz] = 0.0
    filtered = np.fft.irfft(spectrum, n=n)
    return float(np.std(filtered))


def _new_model_data(solver: SolverSpec):
    model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, solver))
    data = mujoco.MjData(model)
    data.qpos[0] = LATERAL_OFFSET
    mujoco.mj_forward(model, data)
    assert_gains_stable(model, data, GAINS, solver.timestep)
    return model, data


def run_offset_drop(solver: SolverSpec) -> dict[str, np.ndarray]:
    """x_des is held at LATERAL_OFFSET for the whole episode, not just the
    initial qpos -- commanding x_des=0 while starting the peg offset would
    just have the K_xy=500N/m spring yank it back to center in free air
    before it ever reaches z=0."""
    model, data = _new_model_data(solver)
    dt = solver.timestep
    n_steps = int(abs(Z_END - Z_START) / (RAMP_RATE * dt)) + 1
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")

    tip_pos = np.zeros((n_steps, 3))
    contact_force = np.zeros((n_steps, 3))
    min_dist = np.full(n_steps, np.inf)

    t0 = time.perf_counter()
    for i in range(n_steps):
        z_des = max(Z_START - i * RAMP_RATE * dt, Z_END)
        mujoco.mj_forward(model, data)
        compute_and_apply_qfrc(model, data, GAINS, np.array([LATERAL_OFFSET, 0.0, z_des]))
        mujoco.mj_step(model, data)

        tip_pos[i] = data.site_xpos[site_id]
        contact_force[i] = peg_contact_force_world(model, data)
        min_dist[i] = min_contact_dist(data)
    wall_clock_s = time.perf_counter() - t0

    return {"tip_pos": tip_pos, "contact_force": contact_force, "min_dist": min_dist,
            "wall_clock_s": wall_clock_s, "dt": dt}


MID_BORE_DEPTH = -0.005  # comfortably inside [-chamfer_width, -plate_thickness], away from either edge


def press_bore_wall(solver: SolverSpec, target_force_n: float = 17.5) -> dict[str, object]:
    """Descend centered to mid-bore depth, then ramp a large lateral
    overshoot (x_des = hole_radius + target_force_n/k_xy) so the bore wall
    itself must supply target_force_n of reaction to hold the peg back --
    a controlled, reproducible way to load the wall to a known force,
    independent of solver stiffness (K_xy is what sets the commanded force;
    the wall's compliance is exactly what's being measured)."""
    model, data = _new_model_data_centered(solver)
    dt = solver.timestep

    descend_steps = int(abs(MID_BORE_DEPTH - Z_START) / (RAMP_RATE * dt)) + 1
    for i in range(descend_steps):
        z_des = max(Z_START - i * RAMP_RATE * dt, MID_BORE_DEPTH)
        mujoco.mj_forward(model, data)
        compute_and_apply_qfrc(model, data, GAINS, np.array([0.0, 0.0, z_des]))
        mujoco.mj_step(model, data)

    x_target = TASK.hole_radius + target_force_n / GAINS.k_xy
    press_steps = 3000
    ramp_steps = 2000
    t0 = time.perf_counter()
    for i in range(press_steps):
        alpha = min(1.0, (i + 1) / ramp_steps)
        mujoco.mj_forward(model, data)
        compute_and_apply_qfrc(model, data, GAINS, np.array([alpha * x_target, 0.0, MID_BORE_DEPTH]))
        mujoco.mj_step(model, data)
    wall_clock_s = time.perf_counter() - t0

    force = peg_contact_force_world(model, data)
    dist = min_contact_dist(data)
    return {
        "radial_force_n": float(np.linalg.norm(force[:2])),
        "axial_force_n": float(force[2]),
        "max_interpenetration_mm": -dist * 1000.0 if np.isfinite(dist) and dist < 0 else 0.0,
        "wall_clock_s": wall_clock_s,
    }


def _new_model_data_centered(solver: SolverSpec):
    model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, solver))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    assert_gains_stable(model, data, GAINS, solver.timestep)
    return model, data


def summarize(name: str, result: dict[str, np.ndarray]) -> dict[str, object]:
    finite_dist = result["min_dist"][np.isfinite(result["min_dist"])]
    max_interpenetration_mm = -finite_dist.min() * 1000.0 if len(finite_dist) else 0.0
    peak_axial_force_n = float(np.max(np.abs(result["contact_force"][:, 2])))
    noise_std_n = high_freq_noise_std(result["contact_force"][:, 2], result["dt"])

    final_pos = result["tip_pos"][-1]
    seated = bool(np.isfinite(final_pos).all() and abs(final_pos[2] - Z_END) < 1e-3)

    return {
        "name": name, "seated": seated,
        "max_interpenetration_mm": max_interpenetration_mm,
        "peak_axial_force_n": peak_axial_force_n,
        "noise_std_n": noise_std_n,
        "wall_clock_s": result["wall_clock_s"],
    }


def verdict(summary: dict[str, object]) -> str:
    clearance_mm = TASK.radial_clearance * 1000.0
    if not summary["seated"]:
        return "FAIL -- did not seat"
    if summary["max_interpenetration_mm"] > clearance_mm:
        return f"FAIL -- interpenetration {summary['max_interpenetration_mm']:.4f}mm exceeds clearance {clearance_mm:.4f}mm"
    if summary["max_interpenetration_mm"] > 0.5 * clearance_mm:
        return f"MARGINAL -- interpenetration {summary['max_interpenetration_mm']:.4f}mm is a large fraction of clearance {clearance_mm:.4f}mm"
    return "PASS"


def measure_efc_stiffness(solver: SolverSpec, sim_seconds: float = 2.0) -> float | None:
    """Read MuJoCo's own internal constraint stiffness (efc_KBIP column 0)
    directly, rather than only inferring the solref clamp from force/
    penetration behavior. Returns the median K sampled across all steps with
    an active normal contact, or None if contact never engaged."""
    model, data = _new_model_data(solver)
    dt = solver.timestep
    n = int(sim_seconds / dt)
    k_samples = []
    for i in range(n):
        z_des = max(Z_START - i * RAMP_RATE * dt, Z_END)
        mujoco.mj_forward(model, data)
        compute_and_apply_qfrc(model, data, GAINS, np.array([LATERAL_OFFSET, 0.0, z_des]))
        mujoco.mj_step(model, data)
        if data.ncon:
            mujoco.mj_forward(model, data)
            k_col = data.efc_KBIP[: data.ncon, 0]
            nonzero = k_col[k_col > 0]
            if len(nonzero):
                k_samples.append(nonzero[0])
    return float(np.median(k_samples)) if k_samples else None


def main() -> None:
    print("=== Baseline sweep (0.5*capture_radius lateral load) ===")
    results = {}
    summaries = []
    for name, solver in SETTINGS.items():
        result = run_offset_drop(solver)
        results[name] = result
        summaries.append(summarize(name, result))

    header = f"{'setting':14s} {'seated':7s} {'max_penet(mm)':14s} {'peak_Fz(N)':11s} {'noise_std(N)':13s} {'wall_clock(s)':13s} {'verdict'}"
    print(header)
    print("-" * len(header))
    for s in summaries:
        print(
            f"{s['name']:14s} {str(s['seated']):7s} {s['max_interpenetration_mm']:14.5f} "
            f"{s['peak_axial_force_n']:11.4f} {s['noise_std_n']:13.6f} {s['wall_clock_s']:13.4f} {verdict(s)}"
        )

    fig, axes = plt.subplots(len(SETTINGS), 1, figsize=(8, 9), sharex=True)
    for ax, (name, result) in zip(axes, results.items()):
        t = np.arange(len(result["min_dist"])) * result["dt"]
        ax.plot(t, result["contact_force"][:, 2])
        ax.set_ylabel(f"{name}\nF_z (N)")
    axes[-1].set_xlabel("time (s)")
    fig.suptitle(f"Axial contact force, offset drop at 0.5*capture_radius ({LATERAL_OFFSET*1000:.3f}mm)")
    fig.tight_layout()
    fig.savefig("data/contact_model_audit_force_profiles.png", dpi=150)
    print("force-profile plot saved to data/contact_model_audit_force_profiles.png")

    print("\n=== High-force variant: ~17.5N pressed against the bore wall (A, B only) ===")
    print("(ramping z_des alone tops out under 1N in a through-hole -- see module docstring; this presses")
    print(" the peg's side against the bore wall instead, which is the substitution being flagged)")
    hf_header = f"{'setting':14s} {'radial_F(N)':12s} {'axial_F(N)':11s} {'max_penet(mm)':14s} {'verdict'}"
    print(hf_header)
    print("-" * len(hf_header))
    clearance_mm = TASK.radial_clearance * 1000.0
    for name in ("A_default", "B_tuned"):
        hf = press_bore_wall(SETTINGS[name])
        hf_verdict = "FAIL -- interpenetration exceeds clearance, peg effectively inside the wall" if hf["max_interpenetration_mm"] > clearance_mm else "PASS"
        print(
            f"{name:14s} {hf['radial_force_n']:12.4f} {hf['axial_force_n']:11.4f} "
            f"{hf['max_interpenetration_mm']:14.5f} {hf_verdict}"
        )

    print("\n=== Timeconst-clamp test: does dt=0.00025 unclamp C from B? ===")
    k_b_1ms = measure_efc_stiffness(SETTINGS["B_tuned"])
    k_c_1ms = measure_efc_stiffness(SETTINGS["C_very_stiff"])
    solver_c_unclamped = SolverSpec(solref=(0.0005, 1.0), solimp=(0.9, 0.95, 0.00005), timestep=0.00025)
    k_c_quarter_ms = measure_efc_stiffness(solver_c_unclamped)
    print(f"B   @dt=0.001   : median constraint K = {k_b_1ms}")
    print(f"C   @dt=0.001   : median constraint K = {k_c_1ms}")
    print(f"C   @dt=0.00025 : median constraint K = {k_c_quarter_ms}")
    if k_b_1ms is not None and k_c_1ms is not None:
        print(f"B vs C @dt=0.001 identical: {np.isclose(k_b_1ms, k_c_1ms, rtol=1e-6)} (expected True -- both floored to timeconst=2*dt=0.002)")
    if k_c_1ms is not None and k_c_quarter_ms is not None:
        ratio = k_c_quarter_ms / k_c_1ms
        print(f"C unclamped/clamped stiffness ratio: {ratio:.3f} (predicted (0.002/0.0005)^2 = 16.0)")

    print(
        "\nSTOP: Milestone 3 re-audit gate. Report the numbers above and wait "
        "for go-ahead before Milestone 4."
    )


if __name__ == "__main__":
    main()
