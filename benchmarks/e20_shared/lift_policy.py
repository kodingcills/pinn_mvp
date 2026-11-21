"""Frozen shared scripted policy for E20: robosuite Lift on UR5e with JOINT_VELOCITY.

Both experiment arms use this policy unchanged; neither arm may tune it. It reads only
observations (joint positions, end-effector and cube positions, gripper state) plus the robot's
own kinematic model (a private MjData over the robot model), never the task grader or sim state.
Output: one robosuite action (6 joint velocities normalised to the controller input range,
plus the gripper command).
"""
from __future__ import annotations

import mujoco
import numpy as np
from scipy.spatial.transform import Rotation

GAIN = 2.0              # Cartesian P gain, 1/s
MAX_EEF_SPEED = 0.15    # m/s
OUTPUT_LIMIT = 0.5      # rad/s, controller output for input 1.0
DAMPING = 0.05          # damped least squares
REACH_TOL = 0.01        # m
GRIP_STEPS = 15         # control steps to close the gripper
LIFT_HEIGHT = 0.15      # m above the grasp height
ROT_GAIN = 2.0          # orientation P gain, 1/s
MAX_ROT_SPEED = 0.6     # rad/s
YAW_OFFSET_DEG = 45.0   # the eef frame is rotated 45 deg from the finger axis; measured: 45 -> 5/5, 0 -> 0/5


class SharedLiftPolicy:
    def __init__(self, model: mujoco.MjModel, eef_site: int, arm_qpos: list[int], arm_dofs: list[int]) -> None:
        self._model, self._data = model, mujoco.MjData(model)
        self._site, self._qpos, self._dofs = eef_site, list(arm_qpos), list(arm_dofs)
        self.phase, self._grip_count, self._grasp_z = "approach", 0, None

    def _joint_velocities(self, q, eef, target, eef_quat, cube_quat) -> np.ndarray:
        self._data.qpos[self._qpos] = q
        mujoco.mj_kinematics(self._model, self._data)
        mujoco.mj_comPos(self._model, self._data)
        jacp, jacr = np.zeros((3, self._model.nv)), np.zeros((3, self._model.nv))
        mujoco.mj_jacSite(self._model, self._data, jacp, jacr, self._site)
        J = np.vstack([jacp[:, self._dofs], jacr[:, self._dofs]])
        v = GAIN * (target - eef)
        if np.linalg.norm(v) > MAX_EEF_SPEED:
            v *= MAX_EEF_SPEED / np.linalg.norm(v)
        w = ROT_GAIN * self._rotation_error(eef_quat, cube_quat)
        if np.linalg.norm(w) > MAX_ROT_SPEED:
            w *= MAX_ROT_SPEED / np.linalg.norm(w)
        twist = np.concatenate([v, w])
        return J.T @ np.linalg.solve(J @ J.T + DAMPING**2 * np.eye(6), twist)

    @staticmethod
    def _rotation_error(eef_quat, cube_quat) -> np.ndarray:
        """World-frame rotation vector from the current gripper orientation to: pointing straight
        down, yawed to the cube's faces (cube symmetry is 90 deg, so take the nearest)."""
        current = Rotation.from_quat(eef_quat)
        cur_yaw = current.as_euler("xyz")[2]
        cube_yaw = Rotation.from_quat(cube_quat).as_euler("xyz")[2] + np.radians(YAW_OFFSET_DEG)
        dyaw = (cube_yaw - cur_yaw + np.pi / 4) % (np.pi / 2) - np.pi / 4
        target = Rotation.from_euler("xyz", [np.pi, 0.0, cur_yaw + dyaw])
        return (target * current.inv()).as_rotvec()

    def act(self, obs: dict) -> np.ndarray:
        q, eef, cube = obs["robot0_joint_pos"], obs["robot0_eef_pos"], obs["cube_pos"]
        quats = (obs["robot0_eef_quat"], obs["cube_quat"])
        grip = -1.0
        if self.phase == "approach":
            target = cube + np.array([0.0, 0.0, 0.08])
            if np.linalg.norm(target - eef) < REACH_TOL:
                self.phase = "descend"
        if self.phase == "descend":
            target = cube.copy()
            if np.linalg.norm(target - eef) < REACH_TOL:
                self.phase, self._grasp_z = "grip", float(eef[2])
        if self.phase == "grip":
            target, grip = eef.copy(), 1.0
            self._grip_count += 1
            if self._grip_count >= GRIP_STEPS:
                self.phase = "lift"
        if self.phase == "lift":
            target, grip = np.array([eef[0], eef[1], self._grasp_z + LIFT_HEIGHT]), 1.0
        dq = self._joint_velocities(q, eef, target, *quats)
        return np.concatenate([np.clip(dq / OUTPUT_LIMIT, -1.0, 1.0), [grip]])
