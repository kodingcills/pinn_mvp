"""Feasibility only: UR5e Lift with JOINT_VELOCITY in robosuite 1.5.2. No arm development."""
import json, numpy as np, robosuite as suite
from robosuite.controllers import load_composite_controller_config, load_part_controller_config

cfg = load_composite_controller_config(robot="UR5e")
arm = load_part_controller_config(default_controller="JOINT_VELOCITY")
cfg["body_parts"]["right"].update({k: v for k, v in arm.items()})
env = suite.make("Lift", robots="UR5e", controller_configs=cfg, has_renderer=False,
                 has_offscreen_renderer=False, use_camera_obs=False, seed=0, control_freq=20)
obs = env.reset()
robot = env.robots[0]
ctrl = robot.part_controllers["right"]
out = {"controller": type(ctrl).__name__, "control_freq": env.control_freq,
       "action_dim": int(env.action_dim), "n_arm_joints": len(robot.arm_joint_indexes["right"] if isinstance(robot.arm_joint_indexes, dict) else robot.arm_joint_indexes)}
q0 = obs["robot0_joint_pos"].copy()
cmd = np.zeros(env.action_dim); cmd[0] = 0.5          # command joint 0, 0.5 of the controller's range
vels = []
for _ in range(10):
    obs, r, done, info = env.step(cmd)
    vels.append(float(obs["robot0_joint_vel"][0]))
for _ in range(10):
    obs, r, done, info = env.step(np.zeros(env.action_dim))
    vels.append(float(obs["robot0_joint_vel"][0]))
out["joint0_vel_trace"] = [round(v, 4) for v in vels]
out["joint0_moved_rad"] = round(float(obs["robot0_joint_pos"][0] - q0[0]), 4)
out["grader_check_success"] = bool(env._check_success())
out["sim_time_s"] = round(float(env.sim.data.time), 3)
print(json.dumps(out, indent=1))
