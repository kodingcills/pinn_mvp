"""Phase 2 pre-flight P1: capture-localization error. Decides whether Phase
2 is an ESTIMATION problem (capture position directly encodes hole location
with a tight, consistent residual -> just correct the estimate) or a
LEARNING problem (residual is noisy/regime-dependent -> needs a learned
policy).

Independent of search tuning: measures the peg's own FK position error
(sim-only ground truth, Rule Zero -- grades the search, never drives it) at
the exact instant search_done first fires, across an area-uniform sample of
offsets covering the full delta_max=2mm uncertainty disk (constraint 9:
r = delta_max*sqrt(u), never r ~ U(0, delta_max)), using
VALIDATED_LOOSE_V1's search config throughout.
"""
from __future__ import annotations

import math

import numpy as np

from pegsim.rollout import VALIDATED_LOOSE_V1, EpisodeConfig, run_episode
from pegsim.search import DELTA_MAX_SPEC_M

N_TRIALS = 64
RNG_SEED = 0
cfg = VALIDATED_LOOSE_V1
spiral_spec = cfg.spiral

rng = np.random.default_rng(RNG_SEED)
u = rng.uniform(0.0, 1.0, N_TRIALS)
r_samples = DELTA_MAX_SPEC_M * np.sqrt(u)
theta_samples = rng.uniform(0.0, 2.0 * math.pi, N_TRIALS)

capture_mouth_lateral = []
capture_tip_lateral = []
capture_time_s = []
n_no_capture = 0

print(f"{'r_mm':>7s} {'theta_deg':>10s} {'captured':>9s} {'cap_mouth_mm':>13s} "
      f"{'cap_tip_mm':>11s} {'cap_time_s':>11s} {'seated':>7s}")

for r, theta in zip(r_samples, theta_samples):
    episode_cfg = EpisodeConfig(
        cfg.task, cfg.solver, cfg.gains, cfg.insertion, z_start=cfg.z_start,
        initial_offset_r=float(r), initial_offset_theta=float(theta),
        max_seconds=cfg.max_seconds, spiral_spec=spiral_spec,
        control_decimation=cfg.control_decimation,
        control_delay_steps=cfg.control_delay_steps,
    )
    try:
        log = run_episode(episode_cfg, pen_allow=cfg.pen_allow)
    except AssertionError as e:
        print(f"{r*1000:7.3f} {math.degrees(theta):10.2f} {'ERROR':>9s} -- {e}")
        n_no_capture += 1
        continue

    captured = log["capture_step"] is not None
    if captured:
        capture_mouth_lateral.append(log["capture_mouth_lateral"])
        capture_tip_lateral.append(log["capture_tip_lateral"])
        capture_time_s.append(log["time"][log["capture_step"]])
    else:
        n_no_capture += 1

    print(f"{r*1000:7.3f} {math.degrees(theta):10.2f} {str(captured):>9s} "
          f"{(log['capture_mouth_lateral']*1000 if captured else float('nan')):13.4f} "
          f"{(log['capture_tip_lateral']*1000 if captured else float('nan')):11.4f} "
          f"{(log['time'][log['capture_step']] if captured else float('nan')):11.4f} "
          f"{str(log['seated']):>7s}")

capture_mouth_lateral = np.array(capture_mouth_lateral) * 1000  # mm
capture_tip_lateral = np.array(capture_tip_lateral) * 1000

print(f"\n{N_TRIALS} trials, delta_max={DELTA_MAX_SPEC_M*1000:.1f}mm, "
      f"config=VALIDATED_LOOSE_V1 ({cfg.validated_by})")
print(f"captured: {N_TRIALS - n_no_capture}/{N_TRIALS}")
if len(capture_mouth_lateral) > 0:
    print(f"capture_mouth_lateral (mm): mean={capture_mouth_lateral.mean():.4f} "
          f"std={capture_mouth_lateral.std():.4f} min={capture_mouth_lateral.min():.4f} "
          f"max={capture_mouth_lateral.max():.4f} "
          f"p50={np.percentile(capture_mouth_lateral, 50):.4f} "
          f"p90={np.percentile(capture_mouth_lateral, 90):.4f}")
    print(f"capture_tip_lateral (mm):   mean={capture_tip_lateral.mean():.4f} "
          f"std={capture_tip_lateral.std():.4f} min={capture_tip_lateral.min():.4f} "
          f"max={capture_tip_lateral.max():.4f}")
    print(f"time to capture (s): mean={np.mean(capture_time_s):.3f} "
          f"max={np.max(capture_time_s):.3f}")
