"""Frozen official-demonstration replay policy for PegInsertionSide.

The upstream system is the official ManiSkill motion-planning demonstration
stream for episode ``episode_id`` (converted to ``pd_ee_pose`` by the
official ``mani_skill.trajectory.replay_trajectory`` tool, commit-frozen
H5). It is an OPEN-LOOP frozen stream (campaign Mode A): the same actions
are issued in both arms; Haplous mediation gates them downstream.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np

from haplous import ExecutionIntent, MachineCapabilities, MachineObservation, ServoCommand


class DemoStreamPolicy:
    def __init__(self, trajectory_path: Path, episode_id: int) -> None:
        with h5py.File(trajectory_path, "r") as handle:
            group = handle[f"traj_{episode_id}"]
            self._actions = np.asarray(group["actions"][:], dtype=float)
        self._index = 0
        self._rotation = np.zeros(3, dtype=float)
        self._gripper = -1.0

    @property
    def rotation_action(self) -> np.ndarray:
        return self._rotation

    @property
    def gripper_action(self) -> float:
        return self._gripper

    def begin(
        self,
        intent: ExecutionIntent,
        caps: MachineCapabilities,
        observation: MachineObservation,
    ) -> None:
        del intent, caps, observation
        self._index = 0
        self._rotation = self._actions[0, 3:6].copy()
        self._gripper = float(self._actions[0, 6])

    def step(self, observation: MachineObservation) -> ServoCommand:
        # target = current EE pose + recorded position delta: the adapter
        # re-derives (target - current) so the frozen action stream is
        # reproduced exactly while Haplous only ever sees an absolute pose
        # command.
        if self._index >= len(self._actions):
            return ServoCommand(target_pose_m=observation.pose_m, k_z_n_per_m=0.0)
        action = self._actions[self._index]
        self._rotation = action[3:6].copy()
        self._gripper = float(action[6])
        self._index += 1
        target = (
            observation.pose_m[0] + float(action[0]),
            observation.pose_m[1] + float(action[1]),
            observation.pose_m[2] + float(action[2]),
        )
        return ServoCommand(target_pose_m=target, k_z_n_per_m=0.0)
