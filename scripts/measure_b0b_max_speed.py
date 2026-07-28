"""Disposable: B0b -- sweep spiral tangential speed v_t at a fixed offset
just inside R_spiral (search required, offset beyond passive R_passive),
report where capture starts failing."""
from __future__ import annotations

from pegsim.characterize import run_offset_sweep
from pegsim.control import ImpedanceGains
from pegsim.rollout import InsertionProfile
from pegsim.scene import SolverSpec, TaskSpec
from pegsim.search import DELTA_MAX_SPEC_M, SpiralSpec

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
SOLVER = SolverSpec(timestep=0.0005)
GAINS = ImpedanceGains(k_xy=500.0, k_rx=5.0, k_ry=5.0, k_rz=0.05)
INSERTION = InsertionProfile()
PEN_ALLOW = 3.7035e-05
OFFSET = 0.0018  # 1.8mm, within R_spiral, beyond passive R_passive
ANGLES = [0.0, 1.5707963267948966, 3.141592653589793, 4.71238898038469]  # 0/90/180/270 deg

for v_t in (0.01, 0.025, 0.05, 0.10):
    spiral_spec = SpiralSpec(task=TASK, delta_max=DELTA_MAX_SPEC_M, v_t=v_t)
    max_seconds = spiral_spec.r_spiral**2 / (2 * spiral_spec.b * v_t) + 3.0
    results = run_offset_sweep(
        TASK, SOLVER, GAINS, INSERTION,
        offsets_m=[OFFSET], angles_rad=ANGLES,
        z_start=0.005, max_seconds=max_seconds, spiral_spec=spiral_spec, pen_allow=PEN_ALLOW,
    )
    n_seated = sum(r["seated"] for r in results)
    tts = [r["time_to_seat_s"] for r in results if r["seated"]]
    print(f"v_t={v_t*1000:.1f}mm/s: seated {n_seated}/{len(results)}, "
          f"time_to_seat={tts}, max_seconds_budget={max_seconds:.2f}s")
