"""J4: re-run the capture sweep after the J1/J2 gate unification.

Offsets 1.0-2.25mm, 16 angles each, full grid reported. Per trial: seated,
time-to-seat, and the PAUSE DUTY CYCLE (fraction of the search window in
which the spiral was held instead of advancing) -- the quantity H1 measured
at 82.8% on its slow trial and the reason the old gate failed.

The cardinal-vs-diagonal split is deliberately NOT reported as a finding:
n=4 clears a 95% bar on any 4/4, and at an underlying rate of ~81% that
happens ~43% of the time by chance. The full 16-angle grid is the result.

Also emits the time-to-seat distribution J5 needs to size max_seconds from
measurement instead of arc-length arithmetic.
"""
from __future__ import annotations

import math

import numpy as np

from pegsim.rollout import VALIDATED_LOOSE_V1, EpisodeConfig, run_episode

CFG = VALIDATED_LOOSE_V1
SPIRAL = CFG.spiral
OFFSETS_MM = [1.0, 1.25, 1.5, 1.75, 2.0, 2.25]
ANGLES_DEG = [i * 22.5 for i in range(16)]

arc_length = (SPIRAL.r_spiral ** 2 - SPIRAL.a ** 2) / (2.0 * SPIRAL.b)
t_coverage = arc_length / SPIRAL.v_t

print(f"config={CFG.name}")
print(f"  task: peg_r={CFG.task.peg_radius * 1000}mm clearance="
      f"{CFG.task.radial_clearance * 1000}mm chamfer={CFG.task.chamfer_width * 1000}mm "
      f"plate={CFG.task.plate_thickness * 1000}mm mu={CFG.task.friction}")
print(f"  solver: dt={CFG.solver.timestep} cone={CFG.solver.cone} "
      f"integrator={CFG.solver.integrator} solref={CFG.solver.solref} "
      f"solimp={CFG.solver.solimp}")
print(f"  gains: k_xy={CFG.gains.k_xy} k_z_seating={CFG.gains.k_z} "
      f"k_rx=k_ry={CFG.gains.k_rx} k_rz={CFG.gains.k_rz}")
print(f"  insertion: k_z_search={CFG.insertion.k_z_search} "
      f"f_search={CFG.insertion.f_search} f_target={CFG.insertion.f_target} "
      f"f_yield={CFG.insertion.f_yield} v_descent={CFG.insertion.v_descent}")
print(f"  spiral: v_t={SPIRAL.v_t * 1000}mm/s a=b={SPIRAL.b * 1e6:.2f}um "
      f"pitch={2 * math.pi * SPIRAL.b * 1000:.4f}mm R_spiral={SPIRAL.r_spiral * 1000:.4f}mm")
print(f"  arc_length={arc_length * 1000:.2f}mm T_coverage={t_coverage:.2f}s "
      f"max_seconds={CFG.max_seconds}s")
print(f"  pen_allow={CFG.pen_allow * 1e6:.2f}um r_cap_dyn={CFG.r_cap_dyn * 1000}mm\n")

rows = {}
for offset_mm in OFFSETS_MM:
    for angle_deg in ANGLES_DEG:
        episode = EpisodeConfig(
            CFG.task, CFG.solver, CFG.gains, CFG.insertion, z_start=CFG.z_start,
            initial_offset_r=offset_mm / 1000.0,
            initial_offset_theta=math.radians(angle_deg),
            max_seconds=CFG.max_seconds, spiral_spec=SPIRAL,
            control_decimation=CFG.control_decimation,
            control_delay_steps=CFG.control_delay_steps,
        )
        try:
            log = run_episode(episode, pen_allow=CFG.pen_allow)
            rows[(offset_mm, angle_deg)] = {
                "seated": bool(log["seated"]),
                "tts": float(log["time_to_seat_s"]),
                "paused": float(log["paused_fraction"]),
                "exhausted": log["spiral_exhausted"],
                "max_tilt": float(log["max_tilt_deg"]),
                "peak_fz": float(np.abs(log["contact_force"][:, 2]).max()),
                "max_f_step": float(log["max_f_cmd_z_step"]),
                "unstable": False,
            }
        except AssertionError:
            rows[(offset_mm, angle_deg)] = {
                "seated": False, "tts": math.inf, "paused": math.nan,
                "exhausted": None, "max_tilt": math.nan, "peak_fz": math.nan,
                "max_f_step": math.nan, "unstable": True,
            }

print("Full grid: 1=seated, 0=not, X=tilt-unstable")
print("offset_mm\\deg " + " ".join(f"{d:5.1f}" for d in ANGLES_DEG))
for offset_mm in OFFSETS_MM:
    cells = []
    for deg in ANGLES_DEG:
        r = rows[(offset_mm, deg)]
        cells.append("  X  " if r["unstable"] else ("  1  " if r["seated"] else "  0  "))
    print(f"{offset_mm:13.2f} " + " ".join(cells))

print(f"\n{'offset_mm':>10s} {'capture':>9s} {'tts_mean':>9s} {'tts_p50':>8s} "
      f"{'tts_max':>8s} {'pause_mean':>11s} {'pause_max':>10s} {'peak_Fz':>9s}")
all_tts = []
for offset_mm in OFFSETS_MM:
    rs = [rows[(offset_mm, d)] for d in ANGLES_DEG]
    seated = [r for r in rs if r["seated"]]
    tts = [r["tts"] for r in seated]
    all_tts += tts
    paused = [r["paused"] for r in rs if not math.isnan(r["paused"])]
    peaks = [r["peak_fz"] for r in rs if not math.isnan(r["peak_fz"])]
    print(f"{offset_mm:10.2f} {len(seated):6d}/16 "
          f"{np.mean(tts) if tts else math.nan:9.3f} "
          f"{np.median(tts) if tts else math.nan:8.3f} "
          f"{max(tts) if tts else math.nan:8.3f} "
          f"{np.mean(paused):11.4f} {max(paused):10.4f} {max(peaks):9.3f}")

n_seated = sum(r["seated"] for r in rows.values())
print(f"\noverall capture: {n_seated}/{len(rows)} = {100 * n_seated / len(rows):.1f}%")
print(f"max commanded-Fz single-step jump across all trials: "
      f"{max(r['max_f_step'] for r in rows.values() if not math.isnan(r['max_f_step'])):.4f}N")
print(f"max tilt across all trials: "
      f"{max(r['max_tilt'] for r in rows.values() if not math.isnan(r['max_tilt'])):.3f}deg")

print("\nPer-trial detail (offset, angle, seated, tts_s, pause_frac, exhausted)")
for offset_mm in OFFSETS_MM:
    for deg in ANGLES_DEG:
        r = rows[(offset_mm, deg)]
        print(f"  {offset_mm:5.2f} {deg:6.1f} {str(r['seated']):>5s} "
              f"{r['tts']:8.3f} {r['paused']:8.4f} {str(r['exhausted']):>5s}")

print("\nJ5: time-to-seat distribution on SUCCESSFUL captures")
if all_tts:
    arr = np.array(all_tts)
    p99 = float(np.percentile(arr, 99))
    print(f"  n={arr.size} min={arr.min():.3f}s p50={np.median(arr):.3f}s "
          f"p90={np.percentile(arr, 90):.3f}s p99={p99:.3f}s max={arr.max():.3f}s")
    print(f"  arc-length estimate T_coverage      = {t_coverage:.3f}s")
    print(f"  measured p99                        = {p99:.3f}s")
    print(f"  measured p99 x 2.0 (proposed T_max) = {2.0 * p99:.3f}s")
    print(f"  current max_seconds                 = {CFG.max_seconds:.3f}s")
