"""Disposable: E3 -- sweep f_search x k_xy (search-phase axial preload and
lateral stiffness), report measured steady-state tracking lag, whether the
peg actually tracks the spiral setpoint (vs staying friction-locked near its
start, per the D1/D3 force-budget finding), and the 16-angle capture
fraction at 1.8mm offset for each combo -- via the REAL rollout.run_episode
path (pegsim.characterize.run_offset_sweep), not a hand-rolled loop, so this
also cross-checks the diagnostic scripts against production code.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

from pegsim.characterize import run_offset_sweep
from pegsim.control import ImpedanceGains, assert_gains_stable, compute_and_apply_qfrc
from pegsim.rollout import DEPTH_ENGAGE_MARGIN, InsertionProfile, _rate_limited_advance, axis_xy_at_z
from pegsim.scene import SolverSpec, TaskSpec, build_mjcf
from pegsim.search import DELTA_MAX_SPEC_M, ArchimedeanSpiral, SpiralSpec, assert_search_force_budget

TASK = TaskSpec(
    peg_radius=0.005, radial_clearance=0.0005, chamfer_width=0.001,
    plate_thickness=0.01, peg_length=0.05, peg_mass=0.05, n_segments=16, friction=0.4,
)
SOLVER = SolverSpec(timestep=0.0005)
PEN_ALLOW = 3.7035e-05
R_CAP_DYN = 0.0016  # measured, scripts/measure_c2a_dynamic_capture_radius.py
V_T = 0.005
NO_CAPTURE_OFFSET = 0.005  # 5mm: outside R_spiral+delta_max, capture cannot trigger


def measure_lag(k_xy: float, f_search: float) -> dict:
    """Run a single trial at an offset the spiral can never reach, so
    engagement/capture never confounds the measurement -- isolates pure
    lateral tracking dynamics during search."""
    gains = ImpedanceGains(k_xy=k_xy, k_rx=5.0, k_ry=5.0, k_rz=0.05)
    insertion = InsertionProfile(f_search=f_search)
    spiral_spec = SpiralSpec(task=TASK, delta_max=DELTA_MAX_SPEC_M, v_t=V_T)

    model = mujoco.MjModel.from_xml_string(build_mjcf(TASK, SOLVER))
    data = mujoco.MjData(model)
    data.qpos[0] = NO_CAPTURE_OFFSET
    data.qpos[2] = 0.005
    mujoco.mj_forward(model, data)
    assert_gains_stable(model, data, gains, SOLVER.timestep)
    nominal_xy = np.array([NO_CAPTURE_OFFSET, 0.0])
    spiral = ArchimedeanSpiral(spiral_spec)

    dt = SOLVER.timestep
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip")
    z_search_target = -insertion.f_search / gains.k_z
    z_des_raw = 0.005
    xy_des = nominal_xy

    n_settle = 4000
    for _ in range(n_settle):
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        z_des_raw = _rate_limited_advance(z_des_raw, z_search_target, insertion.v_descent * dt)
        z_des = max(z_des_raw, tip_now[2] - insertion.f_target / gains.k_z)
        compute_and_apply_qfrc(model, data, gains, np.array([xy_des[0], xy_des[1], z_des]))
        mujoco.mj_step(model, data)

    n_sweep = int(spiral_spec.r_spiral**2 / (2 * spiral_spec.b * V_T) / dt) + 2000
    lag_log = np.zeros(n_sweep)
    dist_from_start_log = np.zeros(n_sweep)
    tip_start = data.site_xpos[site_id][:2].copy()
    for i in range(n_sweep):
        mujoco.mj_forward(model, data)
        tip_now = data.site_xpos[site_id]
        axis_dir_now = data.site_xmat[site_id].reshape(3, 3)[:, 2]
        engaging_slope = tip_now[2] < -DEPTH_ENGAGE_MARGIN
        z_des_raw = _rate_limited_advance(z_des_raw, z_search_target, insertion.v_descent * dt)
        z_des = max(z_des_raw, tip_now[2] - insertion.f_target / gains.k_z)
        if not engaging_slope:
            xy_des = nominal_xy + spiral.step(dt)
        compute_and_apply_qfrc(model, data, gains, np.array([xy_des[0], xy_des[1], z_des]))
        mujoco.mj_step(model, data)
        tip_now2 = data.site_xpos[site_id]
        lag_log[i] = np.linalg.norm(tip_now2[:2] - xy_des)
        dist_from_start_log[i] = np.linalg.norm(tip_now2[:2] - tip_start)

    steady_lag = lag_log[-int(n_sweep * 0.2):].mean()
    max_travel = dist_from_start_log.max()
    return {"steady_lag_mm": steady_lag * 1000, "max_travel_mm": max_travel * 1000}


def main() -> None:
    F_SEARCH_VALUES = [0.5, 1.0, 2.0, 4.0]
    K_XY_VALUES = [500.0, 1000.0, 2000.0]
    angles = [i * 2.0 * math.pi / 16 for i in range(16)]

    print(f"{'f_search':>9s} {'k_xy':>7s} {'pred_lag_mm':>12s} {'meas_lag_mm':>12s} "
          f"{'max_travel_mm':>14s} {'tracks':>7s} {'budget_ok':>10s} {'capture_16':>11s}")

    for k_xy in K_XY_VALUES:
        for f_search in F_SEARCH_VALUES:
            pred_lag = TASK.friction * f_search / k_xy
            measured = measure_lag(k_xy, f_search)
            tracks = measured["max_travel_mm"] > 1.0  # peg genuinely moved, not friction-locked

            gains = ImpedanceGains(k_xy=k_xy, k_rx=5.0, k_ry=5.0, k_rz=0.05)
            insertion = InsertionProfile(f_search=f_search)
            spiral_spec = SpiralSpec(task=TASK, delta_max=DELTA_MAX_SPEC_M, v_t=V_T)
            try:
                assert_search_force_budget(TASK, k_xy, f_search, spiral_spec, R_CAP_DYN)
                budget_ok = True
            except AssertionError:
                budget_ok = False

            max_seconds = spiral_spec.r_spiral**2 / (2 * spiral_spec.b * V_T) + 3.0
            results = run_offset_sweep(
                TASK, SOLVER, gains, insertion,
                offsets_m=[0.0018], angles_rad=angles,
                z_start=0.005, max_seconds=max_seconds, spiral_spec=spiral_spec, pen_allow=PEN_ALLOW,
            )
            n_seated = sum(r["seated"] for r in results)

            print(f"{f_search:9.2f} {k_xy:7.0f} {pred_lag*1000:12.4f} "
                  f"{measured['steady_lag_mm']:12.4f} {measured['max_travel_mm']:14.4f} "
                  f"{str(tracks):>7s} {str(budget_ok):>10s} {f'{n_seated}/16':>11s}")


if __name__ == "__main__":
    main()
