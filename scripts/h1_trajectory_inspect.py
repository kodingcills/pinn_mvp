"""H1 branch c: look at the actual trajectory before theorizing further.
Three representative failing trials from the H1 table: the z_at_min=5mm tie
artifact (1.5mm/67.5deg), the long-dwell near-miss (2.25mm/292.5deg,
dwell=7.86s), and a clean coverage-gap case (2.0mm/292.5deg)."""
from __future__ import annotations

import math

import numpy as np

from pegsim.rollout import VALIDATED_LOOSE_V1, EpisodeConfig, run_episode
cfg = VALIDATED_LOOSE_V1
spiral_spec = cfg.spiral
max_seconds = cfg.max_seconds

CASES = [
    (1.50, 67.5, "z_at_min=5mm tie artifact"),
    (2.25, 292.5, "long-dwell near-miss"),
    (2.00, 292.5, "clean coverage gap"),
]

for offset_mm, angle_deg, label in CASES:
    episode_cfg = EpisodeConfig(
        cfg.task, cfg.solver, cfg.gains, cfg.insertion, z_start=cfg.z_start,
        initial_offset_r=offset_mm / 1000.0, initial_offset_theta=math.radians(angle_deg),
        max_seconds=max_seconds, spiral_spec=spiral_spec,
        control_decimation=cfg.control_decimation,
        control_delay_steps=cfg.control_delay_steps,
    )
    log = run_episode(episode_cfg, pen_allow=cfg.pen_allow)
    dist = log.tip_lateral
    tip_z = log.tip_pos[:, 2]
    setpoint_xy = log.setpoint_xy
    tip_xy = log.tip_pos[:, :2]
    time = log.time
    n = len(dist)

    print(f"=== {label}: offset={offset_mm}mm theta={angle_deg}deg "
          f"(seated={log.success}, spiral_exhausted={log.spiral_exhausted}, "
          f"n_steps={n}) ===")
    idx = np.linspace(0, n - 1, 25).astype(int)
    print(f"{'t_s':>7s} {'dist_mm':>8s} {'tip_z_mm':>9s} {'|err|_mm':>9s}")
    for i in idx:
        err = np.linalg.norm(setpoint_xy[i] - tip_xy[i])
        print(f"{time[i]:7.3f} {dist[i]*1000:8.4f} {tip_z[i]*1000:9.4f} {err*1000:9.4f}")
    print(f"global min dist={dist.min()*1000:.4f}mm at t={time[np.argmin(dist)]:.4f}s "
          f"(first occurrence)")
    print(f"final dist={dist[-1]*1000:.4f}mm, final tip_z={tip_z[-1]*1000:.4f}mm\n")
