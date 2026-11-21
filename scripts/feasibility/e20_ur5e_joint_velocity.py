"""E20 shared feasibility gate: UR5e Lift with robosuite's JOINT_VELOCITY controller.

No experimental arm is involved. Run with the robosuite venv after
`scripts/apply_robosuite_patch.py`:
    MUJOCO_GL=osmesa <venv>/bin/python -I scripts/feasibility/e20_ur5e_joint_velocity.py OUT.json
Checks: construction, +/-/0 velocity tracking per arm joint (commanded vs observed traces),
gravity compensation on and off, actuator saturation, finite state, grader access.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

import numpy as np
import robosuite as suite
from robosuite.controllers import load_composite_controller_config, load_part_controller_config

FREQ = 20
STEPS = 12  # 0.6 s per segment


def make_env(gravity_comp: bool = True):
    cfg = load_composite_controller_config(robot="UR5e")
    arm = load_part_controller_config(default_controller="JOINT_VELOCITY")
    arm["use_torque_compensation"] = gravity_comp
    cfg["body_parts"]["right"] = {**arm, "gripper": cfg["body_parts"]["right"]["gripper"]}
    env = suite.make("Lift", robots="UR5e", controller_configs=cfg, has_renderer=False,
                     has_offscreen_renderer=False, use_camera_obs=False, seed=0, control_freq=FREQ)
    env.reset()
    return env


def arm_ctrl(env):
    return env.robots[0].part_controllers["right"]


def finite(env) -> bool:
    d = env.sim.data
    return bool(np.isfinite(d.qpos).all() and np.isfinite(d.qvel).all() and np.isfinite(d.qacc).all())


def segment(env, joint: int, action_value: float):
    """Command one arm joint for STEPS control steps; return commanded and observed velocity traces."""
    ctrl = arm_ctrl(env)
    act = np.zeros(env.action_dim)
    act[joint] = action_value
    commanded, observed = [], []
    for _ in range(STEPS):
        obs, *_ = env.step(act)
        commanded.append(float(ctrl.goal_vel[joint]))
        observed.append(float(obs["robot0_joint_vel"][joint]))
    return commanded, observed


def main(out: Path) -> int:
    env = make_env()
    ctrl = arm_ctrl(env)
    n_arm = ctrl.joint_dim
    report = {
        "robosuite": importlib.metadata.version("robosuite"),
        "joint_vel_py_sha256": hashlib.sha256(Path(sys.modules[type(ctrl).__module__].__file__).read_bytes()).hexdigest(),
        "controller": type(ctrl).__name__, "control_freq_hz": FREQ, "arm_joints": n_arm,
        "action_dim": int(env.action_dim),
        "output_limit_rad_s": float(np.max(np.abs(ctrl.output_max))),
        "constructed": True,
    }

    # Tracking: +, -, 0 per joint. Action 0.6 maps to 0.3 rad/s (input +-1 -> output +-0.5).
    tracking = []
    for j in range(n_arm):
        for label, a in (("positive", 0.6), ("negative", -0.6), ("zero", 0.0)):
            env.reset()
            cmd, obs_v = segment(env, j, a)
            tail = np.array(obs_v[STEPS // 2:])
            target = np.array(cmd[STEPS // 2:])
            tracking.append({
                "joint": j, "case": label, "commanded_rad_s": [round(x, 4) for x in cmd],
                "observed_rad_s": [round(x, 4) for x in obs_v],
                "steady_mean_abs_err_rad_s": round(float(np.mean(np.abs(tail - target))), 4),
                "sign_ok": bool(np.sign(np.mean(tail)) == np.sign(a)) if a else bool(np.max(np.abs(tail)) < 0.05),
                "finite": finite(env),
            })
    report["tracking"] = tracking

    # Saturation: an action beyond the input range must be clipped to the output limit.
    env.reset()
    cmd, obs_v = segment(env, 0, 5.0)
    report["saturation"] = {
        "action": 5.0, "commanded_rad_s": [round(x, 4) for x in cmd],
        "observed_rad_s": [round(x, 4) for x in obs_v],
        "commanded_max_abs": round(float(np.max(np.abs(cmd))), 4),
        "observed_max_abs": round(float(np.max(np.abs(obs_v))), 4),
        # robosuite rebuilds the controller on reset, so read the live one
        "torques_within_limits": bool(np.all(np.abs(arm_ctrl(env).torques) <= np.abs(arm_ctrl(env).actuator_max) + 1e-9)),
        "finite": finite(env),
    }

    # Gravity compensation on vs off: hold zero velocity for 2 s, measure joint drift.
    def drift(comp: bool):
        e = make_env(comp)
        q0 = e.sim.data.qpos[e.robots[0]._ref_joint_pos_indexes].copy()
        for _ in range(2 * FREQ):
            e.step(np.zeros(e.action_dim))
        q1 = e.sim.data.qpos[e.robots[0]._ref_joint_pos_indexes]
        return {"max_abs_joint_drift_rad": round(float(np.max(np.abs(q1 - q0))), 4), "finite": finite(e)}
    report["gravity_compensation"] = {"enabled": drift(True), "disabled": drift(False)}

    # Grader: the task's own success check is reachable and false at rest.
    env.reset()
    report["grader"] = {"check_success_at_reset": bool(env._check_success())}

    t = report["tracking"]
    report["gate"] = {
        "tracking_signs_ok": all(r["sign_ok"] for r in t),
        "all_finite": all(r["finite"] for r in t) and report["saturation"]["finite"]
        and all(v["finite"] for v in report["gravity_compensation"].values()),
        "saturation_clipped": report["saturation"]["commanded_max_abs"] <= report["output_limit_rad_s"] + 1e-9,
        "grader_reachable": report["grader"]["check_success_at_reset"] is False,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("robosuite", "joint_vel_py_sha256", "gate")}, indent=1))
    print(json.dumps({"tracking_err": [(r["joint"], r["case"], r["steady_mean_abs_err_rad_s"]) for r in t],
                      "saturation": {k: report["saturation"][k] for k in ("commanded_max_abs", "observed_max_abs", "torques_within_limits")},
                      "gravity": report["gravity_compensation"]}))
    return 0 if all(report["gate"].values()) else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
