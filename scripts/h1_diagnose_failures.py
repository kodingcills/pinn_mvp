"""H1: diagnose the 15% capture failures at offset>=1.5mm found in Gate 1.
Re-runs the exact 10 failing (offset, angle) trials from that grid through
production run_episode (full fidelity, no re-implemented loop), then
post-processes the returned tip_pos/time arrays -- everything needed is
already logged, no new instrumentation loop required.
"""
from __future__ import annotations

import math

import numpy as np

from pegsim.rollout import VALIDATED_LOOSE_V1, EpisodeConfig, run_episode
cfg = VALIDATED_LOOSE_V1
spiral_spec = cfg.spiral
max_seconds = cfg.max_seconds
R_CAP_DYN = cfg.r_cap_dyn

# Exact failing (offset_mm, angle_deg) pairs from the Gate 1 grid.
FAILING_TRIALS = [
    (1.50, 67.5), (1.50, 90.0), (1.50, 112.5),
    (1.75, 0.0), (1.75, 337.5),
    (2.00, 292.5), (2.00, 315.0), (2.00, 337.5),
    (2.25, 292.5), (2.25, 315.0),
]

print(f"pitch={2*math.pi*spiral_spec.b*1000:.4f}mm, R_cap_dyn={R_CAP_DYN*1000:.2f}mm, "
      f"v_t={spiral_spec.v_t*1000:.2f}mm/s")
print()

for offset_mm, angle_deg in FAILING_TRIALS:
    episode_cfg = EpisodeConfig(
        cfg.task, cfg.solver, cfg.gains, cfg.insertion, z_start=cfg.z_start,
        initial_offset_r=offset_mm / 1000.0, initial_offset_theta=math.radians(angle_deg),
        max_seconds=max_seconds, spiral_spec=spiral_spec,
        control_decimation=cfg.control_decimation,
        control_delay_steps=cfg.control_delay_steps,
    )
    log = run_episode(episode_cfg, pen_allow=cfg.pen_allow)
    dt = cfg.solver.timestep

    dist = log.tip_lateral  # already norm(tip_xy) vs world origin, full time series
    tip_z = log.tip_pos[:, 2]
    tip_xy = log.tip_pos[:, :2]

    i_min = int(np.argmin(dist))
    min_dist = dist[i_min]
    z_at_min = tip_z[i_min]

    # Tangential speed at closest approach: central difference of tip xy.
    lo, hi = max(0, i_min - 1), min(len(dist) - 1, i_min + 1)
    v_xy = (tip_xy[hi] - tip_xy[lo]) / ((hi - lo) * dt)
    tangential_speed = float(np.linalg.norm(v_xy))

    # Dwell window: contiguous steps around i_min where dist <= R_cap_dyn.
    within = dist <= R_CAP_DYN
    if within[i_min]:
        lo_i = i_min
        while lo_i > 0 and within[lo_i - 1]:
            lo_i -= 1
        hi_i = i_min
        while hi_i < len(within) - 1 and within[hi_i + 1]:
            hi_i += 1
        dwell_s = (hi_i - lo_i + 1) * dt
        z_entry = tip_z[lo_i]
        z_min_during = tip_z[lo_i:hi_i + 1].min()
        z_drop = z_entry - z_min_during
    else:
        dwell_s = 0.0
        z_drop = 0.0

    branch = "a: crossed, no capture" if min_dist < R_CAP_DYN else "b: coverage gap"
    print(f"offset={offset_mm}mm theta={angle_deg}deg: min_dist={min_dist*1000:.4f}mm "
          f"z_at_min={z_at_min*1000:.4f}mm dwell={dwell_s*1000:.2f}ms z_drop={z_drop*1000:.4f}mm "
          f"tangential_speed={tangential_speed*1000:.2f}mm/s "
          f"spiral_exhausted={log.spiral_exhausted} branch=[{branch}]")
