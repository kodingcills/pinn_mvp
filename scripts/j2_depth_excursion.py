"""J2: what depth does the tip ACTUALLY reach while merely sweeping, as
opposed to committing? DEPTH_COMMIT's lower bound is this distribution, and
the H1 brief's "0.06-0.10mm partial-overhang settling band" turned out to be
a DYNAMIC excursion during active spiral drag, not a static rest depth
(static rest measures 4.5um -- see pegsim.characterize.measure_settling_depths).

Reports both, side by side, at both the old and new search stiffness, so the
two numbers can never be confused for each other again.
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from pegsim.characterize import measure_settling_depths
from pegsim.rollout import (
    DEPTH_COMMIT,
    VALIDATED_LOOSE_V1,
    EpisodeConfig,
    run_episode,
)

CFG = VALIDATED_LOOSE_V1
REST_OFFSETS_M = [0.00175, 0.002, 0.0025, 0.003, 0.004, 0.005]
SWEEP_OFFSETS_M = [0.0015, 0.00175, 0.002, 0.00225]
SWEEP_ANGLES_DEG = [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0]

print(f"config={CFG.name}")
print(f"  k_z_search={CFG.insertion.k_z_search} N/m  k_z_seating={CFG.gains.k_z} N/m")
print(f"  f_search={CFG.insertion.f_search} N  v_t={CFG.v_t * 1000} mm/s")
print(f"  descent allowance f_search/k_z_search = "
      f"{CFG.insertion.descent_allowance * 1000:.3f}mm")
print(f"  DEPTH_COMMIT = {DEPTH_COMMIT * 1000:.3f}mm\n")

# The old k_z_search = k_z_seating = 2000 cannot be constructed any more:
# EpisodeConfig.__post_init__ rejects it, because f_search/k_z_search =
# 0.5mm leaves no room for a 0.5mm DEPTH_COMMIT with the required 2x margin.
# That rejection IS the J2 finding -- the window was too narrow to place a
# threshold in safely, which is what made the old 0.05mm gate look like the
# only option.
print("A. STATIC rest depth below the table, spiral OFF (measure_settling_depths)")
print(f"{'k_z_search':>11s} {'offset_mm':>10s} {'rest_depth_um':>14s}")
depths = measure_settling_depths(
    CFG.task, CFG.solver, CFG.gains, CFG.insertion,
    offsets_m=REST_OFFSETS_M, z_start=CFG.z_start,
)
for offset, depth in depths.items():
    print(f"{CFG.insertion.k_z_search:11.0f} {offset * 1000:10.2f} {depth * 1e6:14.2f}")
print(f"{'':11s} {'MAX':>10s} {max(depths.values()) * 1e6:14.2f}")

print("\nB. DYNAMIC depth excursion while SWEEPING, measured only over steps"
      "\n   before the gate first commits (the regime it must not fire in)")
print(f"{'offset_mm':>10s} {'angle_deg':>10s} {'p50_um':>9s} {'p99_um':>9s} "
      f"{'max_um':>9s} {'paused_frac':>12s} {'tts_s':>8s} {'seated':>7s}")
all_p99 = []
all_max = []
for offset_mm in SWEEP_OFFSETS_M:
    for angle_deg in SWEEP_ANGLES_DEG:
        episode = EpisodeConfig(
            CFG.task, CFG.solver, CFG.gains, CFG.insertion, z_start=CFG.z_start,
            initial_offset_r=offset_mm, initial_offset_theta=math.radians(angle_deg),
            max_seconds=CFG.max_seconds, spiral_spec=CFG.spiral,
            control_decimation=CFG.control_decimation,
            control_delay_steps=CFG.control_delay_steps,
        )
        log = run_episode(episode, pen_allow=CFG.pen_allow)
        depth = log["z_flat"] - log["tip_pos"][:, 2]
        committed = log["committed"]
        first_commit = int(np.argmax(committed)) if committed.any() else len(depth)
        window = depth[:first_commit]
        window = window[np.isfinite(log["min_contact_dist"][:first_commit])]
        if window.size == 0:
            continue
        p50, p99, mx = np.percentile(window, 50), np.percentile(window, 99), window.max()
        all_p99.append(p99)
        all_max.append(mx)
        print(f"{offset_mm * 1000:10.2f} {angle_deg:10.1f} {p50 * 1e6:9.2f} "
              f"{p99 * 1e6:9.2f} {mx * 1e6:9.2f} {log['paused_fraction']:12.4f} "
              f"{log['time_to_seat_s']:8.3f} {str(log['seated']):>7s}")

print(f"\nacross all sweep trials: max p99 = {max(all_p99) * 1e6:.2f}um, "
      f"max excursion = {max(all_max) * 1e6:.2f}um")
print(f"DEPTH_COMMIT / max p99 = {DEPTH_COMMIT / max(all_p99):.2f}x")
print(f"DEPTH_COMMIT / max excursion = {DEPTH_COMMIT / max(all_max):.2f}x")
