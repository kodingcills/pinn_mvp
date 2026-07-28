"""Disposable: closes Stage A items 2-5. One consistent dataset (full
per-step traces on the blind-hole canonical config, gains verified stable
at this dt) feeds all four items instead of separate one-off checks.

Gains note: default ImpedanceGains (k_rx=k_ry=1.0, dt=0.001) is NOT verified
stable across this offset range -- it hit tilt-instability asserts at 3.25/
3.75/4.0mm offset in an earlier check. Using K_rx=K_ry=5.0 (spec value) at
dt=0.00025 (verified stable, zero instabilities across the 160-trial
R_passive sweep) for every number in this script, stated explicitly so nothing
here is silently mixing a numerically-unstable config into the data.
"""
from __future__ import annotations

import numpy as np

from pegsim.control import ImpedanceGains
from pegsim.rollout import EpisodeConfig, run_episode
from pegsim.scene import SolverSpec, TaskSpec

TASK = TaskSpec(
    peg_radius=0.005,
    radial_clearance=0.0005,
    chamfer_width=0.001,
    plate_thickness=0.01,
    peg_length=0.05,
    peg_mass=0.05,
    n_segments=16,
    friction=0.4,
    # blind_hole=True (default)
)
SOLVER = SolverSpec(timestep=0.00025)
GAINS = ImpedanceGains(k_rx=5.0, k_ry=5.0, k_rz=0.05)

OFFSETS_MM = [1.0, 2.0, 3.0, 3.5, 3.75, 4.0, 4.5, 5.0]

print(f"plate_thickness = {TASK.plate_thickness} m")
print(f"clearance = {TASK.radial_clearance} m, friction (mu) = {TASK.friction}")

logs = {}
for offset_mm in OFFSETS_MM:
    cfg = EpisodeConfig(
        TASK, SOLVER, GAINS, z_start=0.005, z_end=-0.045, z_rate=0.01,
        initial_offset_r=offset_mm / 1000.0, initial_offset_theta=0.0,
        max_seconds=8.0, spiral_spec=None,
    )
    logs[offset_mm] = run_episode(cfg)
print(f"target_depth = {cfg.target_depth} m (plate_thickness - 0.001)")

print("\n=== Item 2/3: AND criterion, final vs max-tilt-moment readings ===")
header = f"{'offset_mm':>9s} {'seated':>7s} {'tip_final':>10s} {'mouth_final':>11s} {'AND_final':>9s} {'tilt_final':>10s} {'max_tilt':>9s} {'tip@maxtilt':>11s} {'mouth@maxtilt':>13s} {'z@maxtilt_mm':>12s} {'z_final_mm':>10s}"
print(header)
for offset_mm, log in logs.items():
    tip_f, mouth_f = log["tip_lateral"][-1], log["mouth_lateral"][-1]
    tilt_f = log["tilt_deg"][-1]
    and_final = (tip_f <= TASK.radial_clearance) and (mouth_f <= TASK.radial_clearance)
    imax = int(np.argmax(log["tilt_deg"]))
    print(
        f"{offset_mm:9.2f} {str(log['seated']):>7s} {tip_f*1000:10.4f} {mouth_f*1000:11.4f} "
        f"{str(and_final):>9s} {tilt_f:10.4f} {log['max_tilt_deg']:9.4f} "
        f"{log['tip_lateral'][imax]*1000:11.4f} {log['mouth_lateral'][imax]*1000:13.4f} "
        f"{log['tip_pos'][imax,2]*1000:12.4f} {log['tip_pos'][-1,2]*1000:10.4f}"
    )

print("\n=== Item 4: terminal force, ringing ===")
print(f"{'offset_mm':>9s} {'peak_term_Fz':>13s} {'term_mean':>10s} {'term_std':>9s} {'n_crossings_of_mean':>20s}")
for offset_mm, log in logs.items():
    fz = log["contact_force"][:, 2]
    tip_z = log["tip_pos"][:, 2]
    floor_z = -TASK.plate_thickness
    near_floor = tip_z <= floor_z + 0.0005
    if near_floor.sum() < 50:
        print(f"{offset_mm:9.2f} {'never bottomed out':>13s}")
        continue
    term_fz = fz[near_floor]
    peak = np.abs(term_fz).max()
    mean = np.abs(term_fz).mean()
    std = term_fz.std()
    # count sign changes of (term_fz - mean) as a crude "threshold crossing" count
    centered = term_fz - term_fz.mean()
    crossings = int(np.sum(np.diff(np.sign(centered)) != 0))
    print(f"{offset_mm:9.2f} {peak:13.4f} {mean:10.4f} {std:9.4f} {crossings:20d}")

print("\n=== Item 5: F_wedge, analytic vs empirical ===")
good = [mm for mm in OFFSETS_MM if logs[mm]["seated"]]
jammed = [mm for mm in OFFSETS_MM if not logs[mm]["seated"]]
print(f"good (seated) offsets: {good}")
print(f"jammed (not seated) offsets: {jammed}")


def lateral_axial_at_final(log):
    f = log["contact_force"][-1]
    return float(np.linalg.norm(f[:2])), abs(float(f[2]))


print(f"{'offset_mm':>9s} {'group':>7s} {'lateral_F_final':>15s} {'axial_F_final':>13s} {'analytic_F_wedge':>16s}")
for offset_mm, log in logs.items():
    lat, ax = lateral_axial_at_final(log)
    f_wedge = ax / (2.0 * TASK.friction) if ax > 0 else float("inf")
    group = "good" if log["seated"] else "jammed"
    print(f"{offset_mm:9.2f} {group:>7s} {lat:15.4f} {ax:13.4f} {f_wedge:16.4f}")
