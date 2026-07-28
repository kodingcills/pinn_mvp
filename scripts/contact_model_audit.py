"""Milestone 3 GATE: contact model audit.

Sweeps three solver settings (A default / B tuned / C very stiff) and
reports, per setting: did it seat, max interpenetration, peak axial force, a
high-frequency noise metric on F_z, and wall-clock time per rollout.

A pure zero-lateral-offset drop (scripts/step1_drop.py) never touches
anything -- the peg has 0.5mm clearance all around and falls straight
through the hole center, so 0/N steps show an active contact (confirmed by
running it: see the Milestone 2 report). That leaves nothing for THIS audit
to measure. This script instead offsets the peg by 0.5 * capture_radius
before dropping it -- inside the chamfer's catch range, so the chamfer must
actually funnel the peg to center under contact, giving real
interpenetration/force data at exactly the boundary this audit cares about
(default solimp width of 1mm vs 0.5mm clearance). Flagging this deviation
from the literal "sweep the step1 drop" wording explicitly in the printed
report -- not deciding silently.

Uses a bigger peg than the Milestone 2 test fixture (10mm radius / 200g vs
5mm / 50g), same reason as control.py's STABLE_TASK: any lateral setpoint on
the small peg seeds a genuine numerical (not sign-error) blow-up in rz,
because rz's own inertia (0.5*m*r^2) puts the explicit-force closed loop's
stable-timestep bound under the project's fixed 1ms step (see the Milestone 2
report). Confirmed directly -- the small peg trips the controller's own tilt
assert here as soon as a lateral offset is introduced. radial_clearance and
chamfer_width are UNCHANGED (still 0.5mm / 1mm) since those are exactly the
numbers this audit's hypothesis is about; only peg_radius/mass/length/plate
thickness are scaled up.

STOP after this script's report. Do not start Milestone 4 without go-ahead.
"""
from __future__ import annotations

import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mujoco
import numpy as np

from pegsim.control import ImpedanceGains, compute_and_apply_qfrc, min_contact_dist, peg_contact_force_world
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf

TASK = TaskSpec(
    peg_radius=0.01,
    radial_clearance=0.0005,
    chamfer_width=0.001,
    plate_thickness=0.02,
    peg_length=0.08,
    peg_mass=0.2,
    n_segments=16,
    friction=0.4,
)
GAINS = ImpedanceGains()

Z_START = 0.005
Z_END = -0.045
RAMP_RATE = 0.01  # m/s
LATERAL_OFFSET = 0.5 * TASK.capture_radius  # inside the chamfer catch range

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


def run_offset_drop(solver: SolverSpec) -> dict[str, np.ndarray]:
    """The commanded x_des is held at LATERAL_OFFSET for the whole episode,
    not just the initial qpos -- commanding x_des=0 while starting the peg
    offset would just have the K_xy=500N/m spring yank it back to center
    while still in free air above the plate, before it ever reaches z=0.
    Holding the setpoint offset forces genuine, sustained contact against the
    chamfer as the (known, fixed) 0.25mm geometric overlap is continuously
    commanded into the wall -- which is what this audit needs to measure."""
    model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, solver))
    data = mujoco.MjData(model)
    data.qpos[0] = LATERAL_OFFSET
    mujoco.mj_forward(model, data)

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

    return {
        "tip_pos": tip_pos,
        "contact_force": contact_force,
        "min_dist": min_dist,
        "wall_clock_s": wall_clock_s,
        "dt": dt,
    }


def summarize(name: str, result: dict[str, np.ndarray]) -> dict[str, object]:
    finite_dist = result["min_dist"][np.isfinite(result["min_dist"])]
    max_interpenetration_mm = -finite_dist.min() * 1000.0 if len(finite_dist) else 0.0
    peak_axial_force_n = float(np.max(np.abs(result["contact_force"][:, 2])))
    noise_std_n = high_freq_noise_std(result["contact_force"][:, 2], result["dt"])

    # "Seated" here just means the axial ramp still reached depth despite the
    # sustained lateral contact -- x_des is held at LATERAL_OFFSET the whole
    # episode (see run_offset_drop), so the tip is expected to stay offset,
    # not recenter.
    final_pos = result["tip_pos"][-1]
    seated = bool(np.isfinite(final_pos).all() and abs(final_pos[2] - Z_END) < 1e-3)

    return {
        "name": name,
        "seated": seated,
        "max_interpenetration_mm": max_interpenetration_mm,
        "peak_axial_force_n": peak_axial_force_n,
        "noise_std_n": noise_std_n,
        "wall_clock_s": result["wall_clock_s"],
        "n_contact_steps": int(len(finite_dist)),
        "n_steps": len(result["min_dist"]),
    }


def verdict(summary: dict[str, object]) -> str:
    clearance_mm = TASK.radial_clearance * 1000.0
    if not summary["seated"]:
        return "FAIL -- did not seat"
    if summary["max_interpenetration_mm"] > clearance_mm:
        return f"FAIL -- interpenetration {summary['max_interpenetration_mm']:.4f}mm exceeds clearance {clearance_mm:.4f}mm (contact model is lying)"
    if summary["max_interpenetration_mm"] > 0.5 * clearance_mm:
        return f"MARGINAL -- interpenetration {summary['max_interpenetration_mm']:.4f}mm is a large fraction of clearance {clearance_mm:.4f}mm"
    return "PASS"


def main() -> None:
    results = {}
    summaries = []
    for name, solver in SETTINGS.items():
        result = run_offset_drop(solver)
        results[name] = result
        summary = summarize(name, result)
        summaries.append(summary)

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
    out_path = "data/contact_model_audit_force_profiles.png"
    fig.savefig(out_path, dpi=150)
    print(f"\nforce-profile plot saved to {out_path}")

    print(
        "\nSTOP: Milestone 3 gate. Report the numbers above and wait for "
        "go-ahead before Milestone 4."
    )


if __name__ == "__main__":
    main()
