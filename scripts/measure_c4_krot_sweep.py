"""Disposable: C4 -- test the prediction that softening K_rot INCREASES
R_passive at the loose config (search OFF), because tip-and-lever is
moment-limited and a soft K_rot lets the peg tilt further under the same
(F_yield-capped) F_search."""
from __future__ import annotations

from pegsim.control import ImpedanceGains
from pegsim.rollout import EpisodeConfig, InsertionProfile, run_episode
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
)
SOLVER = SolverSpec(timestep=0.0005)
INSERTION = InsertionProfile()
PEN_ALLOW = 3.7035e-05

# Coarse scan per K_rot to bracket the boundary, then refine.
coarse_offsets_mm = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0]

for k_rot in (5.0, 1.0, 0.3, 0.1):
    gains = ImpedanceGains(k_rx=k_rot, k_ry=k_rot, k_rz=0.05)
    boundary = None
    last_ok = None
    for offset_mm in coarse_offsets_mm:
        cfg = EpisodeConfig(TASK, SOLVER, gains, INSERTION, z_start=0.005,
                             initial_offset_r=offset_mm / 1000.0, initial_offset_theta=0.0,
                             max_seconds=8.0, spiral_spec=None)
        try:
            log = run_episode(cfg, pen_allow=PEN_ALLOW)
            seated = log["seated"]
            tilt = log["max_tilt_deg"]
        except AssertionError:
            seated = False
            tilt = float("nan")
        print(f"K_rot={k_rot} offset={offset_mm}mm seated={seated} max_tilt={tilt:.3f}")
        if seated:
            last_ok = offset_mm
        elif last_ok is not None and boundary is None:
            boundary = (last_ok, offset_mm)
            break
    print(f"--> K_rot={k_rot}: R_passive bracket = {boundary}\n")
