"""J4 control check: BEFORE believing a jump from ~85% to 100% capture,
verify the success criterion still says NO when it should.

Three probes:
  1. offsets beyond R_spiral -- the search physically cannot reach the hole,
     so these MUST fail. If they pass, ground_truth_seated is vacuous.
  2. seat quality on passing trials -- final depth, tip and mouth lateral,
     settled and peak contact force. A "seat" that is not at the floor and
     inside clearance is not a seat.
  3. force continuity across the stiffness step (J2's assert).
"""
from __future__ import annotations

import math

import numpy as np

from pegsim.rollout import (
    TARGET_DEPTH_MARGIN_M,
    VALIDATED_LOOSE_V1,
    EpisodeConfig,
    run_episode,
)
from pegsim.search import DELTA_MAX_SPEC_M

CFG = VALIDATED_LOOSE_V1
SPIRAL = CFG.spiral

print(f"config={CFG.name}  R_spiral={SPIRAL.r_spiral * 1000:.4f}mm  "
      f"delta_max={DELTA_MAX_SPEC_M * 1000:.2f}mm  max_seconds={CFG.max_seconds}s")
print(f"target_depth={CFG.task.plate_thickness - TARGET_DEPTH_MARGIN_M:.6f}m  "
      f"clearance+pen_allow={(CFG.task.radial_clearance + CFG.pen_allow) * 1000:.4f}mm\n")


def run(offset_mm, angle_deg):
    episode = EpisodeConfig(
        CFG.task, CFG.solver, CFG.gains, CFG.insertion, z_start=CFG.z_start,
        initial_offset_r=offset_mm / 1000.0, initial_offset_theta=math.radians(angle_deg),
        max_seconds=CFG.max_seconds, spiral_spec=SPIRAL,
        control_decimation=CFG.control_decimation,
        control_delay_steps=CFG.control_delay_steps,
    )
    return run_episode(episode, pen_allow=CFG.pen_allow)


# The search is centered on the NOMINAL (offset) position, so its reach to
# the true hole is offset - R_spiral. Capture is therefore still legitimate
# out to R_spiral + R_cap_dyn = 4.35mm, well past delta_max. Anything beyond
# that has no physical path to the hole and MUST fail.
reach_limit_mm = (SPIRAL.r_spiral + CFG.r_cap_dyn) * 1000
print(f"1. UNREACHABLE OFFSETS (> R_spiral + R_cap_dyn = {reach_limit_mm:.2f}mm) "
      "-- these must NOT seat")
print(f"{'offset_mm':>10s} {'angle':>7s} {'seated':>7s} {'exhausted':>10s} "
      f"{'final_z_mm':>11s} {'final_lat_mm':>13s}")
for offset_mm in (3.0, 4.0, 4.5, 5.0):
    for angle_deg in (0.0, 90.0):
        try:
            log = run(offset_mm, angle_deg)
        except AssertionError as exc:
            print(f"{offset_mm:10.2f} {angle_deg:7.1f} {'TILT':>7s} "
                  f"  {str(exc).split(' -- ')[0][:40]}")
            continue
        print(f"{offset_mm:10.2f} {angle_deg:7.1f} {str(log['seated']):>7s} "
              f"{str(log['spiral_exhausted']):>10s} "
              f"{log['tip_pos'][-1, 2] * 1000:11.4f} "
              f"{log['tip_lateral'][-1] * 1000:13.4f}")

print("\n2. SEAT QUALITY on trials the sweep counts as successes")
print(f"{'offset_mm':>10s} {'angle':>7s} {'final_z_mm':>11s} {'tip_lat_mm':>11s} "
      f"{'mouth_lat_mm':>13s} {'settled_Fz_N':>13s} {'peak_Fz_N':>10s} {'tts_s':>7s}")
for offset_mm, angle_deg in ((1.5, 90.0), (1.75, 0.0), (2.0, 315.0), (2.25, 292.5)):
    log = run(offset_mm, angle_deg)
    settled = abs(log["contact_force"][-200:, 2].mean())
    peak = float(np.abs(log["contact_force"][:, 2]).max())
    print(f"{offset_mm:10.2f} {angle_deg:7.1f} {log['tip_pos'][-1, 2] * 1000:11.4f} "
          f"{log['tip_lateral'][-1] * 1000:11.4f} "
          f"{log['mouth_lateral'][-1] * 1000:13.4f} {settled:13.4f} {peak:10.4f} "
          f"{log['time_to_seat_s']:7.3f}")

print("\n3. FORCE CONTINUITY across the k_z_search -> k_z_seating step")
print(f"{'offset_mm':>10s} {'angle':>7s} {'commit_step':>12s} {'F_before_N':>11s} "
      f"{'F_after_N':>10s} {'max_step_N':>11s}")
for offset_mm, angle_deg in ((1.5, 90.0), (1.75, 0.0), (2.0, 315.0), (2.25, 292.5)):
    log = run(offset_mm, angle_deg)
    committed = log["committed"]
    i = int(np.argmax(committed))
    f = log["f_cmd_z"]
    print(f"{offset_mm:10.2f} {angle_deg:7.1f} {i:12d} {f[i - 1]:11.4f} "
          f"{f[i]:10.4f} {log['max_f_cmd_z_step']:11.4f}")
