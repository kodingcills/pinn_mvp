"""H4: find and explain the tts_max=11.83s anomaly at 1.75mm (Gate 1). Full
arc length (R_spiral^2-a^2)/(2b) at v_t predicts a full-traversal time;
11.83s exceeds that. Re-runs all 16 angles at 1.75mm, reports time_to_seat
per angle, and for the slowest one measures pause duty directly from the
production loop's logged searching and spiral_advanced states. This keeps the
historical diagnostic valid after the split depth gates were removed."""
from __future__ import annotations

import math

from pegsim.rollout import VALIDATED_LOOSE_V1, EpisodeConfig, run_episode

cfg = VALIDATED_LOOSE_V1
spiral_spec = cfg.spiral
max_seconds = cfg.max_seconds

arc_length = (spiral_spec.r_spiral**2 - spiral_spec.a**2) / (2.0 * spiral_spec.b)
t_coverage = arc_length / spiral_spec.v_t
print(f"v_t={spiral_spec.v_t*1000:.3f}mm/s, a={spiral_spec.a*1000:.4f}mm, "
      f"r_spiral={spiral_spec.r_spiral*1000:.4f}mm")
print(f"arc_length={arc_length*1000:.3f}mm, T_coverage (continuous, no pauses)={t_coverage:.3f}s")
print(f"max_seconds budget used={max_seconds:.3f}s\n")

OFFSET_MM = 1.75
angles = [i * 22.5 for i in range(16)]
results = []
for deg in angles:
    episode_cfg = EpisodeConfig(
        cfg.task, cfg.solver, cfg.gains, cfg.insertion, z_start=cfg.z_start,
        initial_offset_r=OFFSET_MM / 1000.0, initial_offset_theta=math.radians(deg),
        max_seconds=max_seconds, spiral_spec=spiral_spec,
        control_decimation=cfg.control_decimation,
        control_delay_steps=cfg.control_delay_steps,
    )
    log = run_episode(episode_cfg, pen_allow=cfg.pen_allow)
    results.append((deg, log))
    print(f"theta={deg:6.1f}deg seated={log.success} time_to_seat_s={log.time_to_seat_s:.4f} "
          f"spiral_exhausted={log.spiral_exhausted}")

seated_results = [(deg, log) for deg, log in results if log.success]
slowest_deg, slowest_log = max(seated_results, key=lambda x: x[1].time_to_seat_s)
print(f"\nSlowest seated trial: theta={slowest_deg}deg, "
      f"time_to_seat_s={slowest_log.time_to_seat_s:.4f}, "
      f"spiral_exhausted={slowest_log.spiral_exhausted}")

seated_step = int(round(slowest_log.time_to_seat_s / cfg.solver.timestep))
capture_step = slowest_log.capture_step
up_to = capture_step if capture_step is not None else seated_step
searching = slowest_log.searching[:up_to]
advanced = slowest_log.spiral_advanced[:up_to]
n_advancing = int((searching & advanced).sum())
n_paused = int((searching & ~advanced).sum())
print(f"Up to capture_step={capture_step} (search phase only): "
      f"advancing_steps={n_advancing}, paused_steps={n_paused}, "
      f"paused_fraction={n_paused/(n_advancing+n_paused)*100:.1f}%")
print(f"Naive continuous-advance prediction for this many advancing steps: "
      f"{n_advancing*cfg.solver.timestep:.3f}s of spiral time would need to reach "
      f"{arc_length*1000:.2f}mm arc length")
