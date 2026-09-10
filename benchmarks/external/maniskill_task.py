"""ManiSkill 3.0.1 PegInsertionSide-v1 external-task adapter (SAPIEN/PhysX CPU).

The benchmark's ``pd_ee_pose`` controller owns rotation tracking and the
gripper. Haplous ``ServoCommand`` carries the Cartesian position target; the
recorded stream's rotation delta and gripper components ride as task-owned
actuation channels (same pattern as the robosuite adapter's controller-owned
``kp`` and gripper). Contact force is a PhysX impulse-derived proxy: the net
impulse on robot bodies in the last sim substep divided by the sim timestep.
Across-engine force magnitudes are not comparable; see the campaign report.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Final

import numpy as np

from haplous import (
    CommandMode,
    CommandOutcome,
    MachineCapabilities,
    MachineObservation,
    SensingChannel,
    ServoCommand,
    Stamp,
)


TASK_ID: Final = "maniskill/3.0.1/PegInsertionSide"
CONTROL_FREQUENCY_HZ: Final = 20
SIM_FREQUENCY_HZ: Final = 100
HORIZON_S: Final = 25.0
HORIZON_MARGIN_STEPS: Final = 50
POS_DELTA_LIMIT: Final = 2.0


def _tool_tilt_deg(quat_wxyz: np.ndarray) -> float:
    _w, x, y, _z = (float(v) for v in quat_wxyz)
    tz = 1.0 - 2.0 * (x * x + y * y)
    alignment = min(1.0, max(-1.0, abs(tz)))
    return math.degrees(math.acos(alignment))


class ManiSkillPegInsertionTask:
    task_id = TASK_ID

    def __init__(
        self,
        gripper_action: Callable[[], float],
        rotation_action: Callable[[], np.ndarray],
        stream_length: int,
    ) -> None:
        self._gripper_action = gripper_action
        self._rotation_action = rotation_action
        self._stream_length = stream_length
        self._env = None
        self._observation = None
        self._pending_action = np.zeros(7, dtype=float)
        self._stopped = False
        self._steps = 0
        self._contact_steps = 0
        self._peak_force_n = 0.0
        self._completion_time_s: float | None = None
        self._sim_dt = 1.0 / SIM_FREQUENCY_HZ

    def reset(self, *, seed: int) -> None:
        import gymnasium as gym
        import mani_skill.envs  # noqa: F401

        if self._env is not None:
            self._env.close()
        self._env = gym.make(
            "PegInsertionSide-v1",
            obs_mode="state",
            render_mode=None,
            sim_backend="cpu",
            control_mode="pd_ee_pose",
        )
        self._observation, _ = self._env.reset(seed=seed)
        self._stopped = False
        self._steps = 0
        self._contact_steps = 0
        self._peak_force_n = 0.0
        self._peak_tilt_deg = 0.0
        self._completion_time_s = None
        self._last_info: dict = {}
        self._first_success_step: int | None = None
        self._pending_action.fill(0.0)

    def _robot_contact_force_n(self) -> tuple[float, float, float]:
        assert self._env is not None
        u = self._env.unwrapped
        robot_body_ids = {id(body) for link in u.agent.robot.links for body in link._bodies}
        total = np.zeros(3, dtype=float)
        for contact in u.scene.px.get_contacts():
            if not any(id(b) in robot_body_ids for b in contact.bodies):
                continue
            for point in contact.points:
                total += np.asarray(point.impulse, dtype=float)
        return tuple(total / self._sim_dt)  # type: ignore[return-value]

    def capabilities(self) -> MachineCapabilities:
        return MachineCapabilities(
            command_modes=frozenset({CommandMode.CARTESIAN_IMPEDANCE}),
            sensing=frozenset(
                {SensingChannel.POSE, SensingChannel.FORCE_Z, SensingChannel.TILT}
            ),
            max_sensed_force_n=1000.0,
            max_sensed_tilt_deg=180.0,
            position_resolution_m=1e-5,
            state_period_s=1.0 / CONTROL_FREQUENCY_HZ,
            state_max_age_s=0.1,
            max_initial_offset_m=1.0,
            execution_horizon_s=HORIZON_S,
            expected_duration_s=float(self._stream_length) / CONTROL_FREQUENCY_HZ,
            supports_stop=True,
        )

    def observe(self) -> MachineObservation | None:
        if self._env is None or self._observation is None:
            return None
        u = self._env.unwrapped
        pose = u.agent.controller.controllers["arm"].ee_link.pose
        quat_wxyz = np.asarray(pose.q, dtype=float).reshape(-1)
        pos = np.asarray(pose.p, dtype=float).reshape(-1)
        fx, fy, fz = self._robot_contact_force_n()
        now = self._steps / CONTROL_FREQUENCY_HZ
        success = bool(self._env_info_success())
        peg = np.asarray(u.peg.pose.p, dtype=float).reshape(-1)
        box = np.asarray(u.box.pose.p, dtype=float).reshape(-1)
        return MachineObservation(
            stamp=Stamp(source_s=now, received_s=now),
            pose_m=(float(pos[0]), float(pos[1]), float(pos[2])),
            force_z_n=float(fz),
            tilt_deg=_tool_tilt_deg(quat_wxyz),
            extra=(
                ("task_success", 1.0 if success else 0.0),
                ("force_x_n", float(fx)),
                ("force_y_n", float(fy)),
                ("force_mag_n", float(math.sqrt(fx * fx + fy * fy + fz * fz))),
                ("peg_x", float(peg[0])),
                ("peg_y", float(peg[1])),
                ("peg_z", float(peg[2])),
                ("hole_x", float(box[0])),
                ("hole_y", float(box[1])),
                ("hole_z", float(box[2])),
            ),
        )

    def _env_info_success(self) -> bool:
        # success is evaluated by the env itself each step; the ManiSkill
        # predicate is peg-head-in-hole overlap, benchmark-owned
        return getattr(self, "_last_info", {}).get("success", False)

    def apply_command(self, command: ServoCommand) -> CommandOutcome:
        if self._stopped or self._env is None:
            return CommandOutcome(accepted=False, reason="session_inactive")
        u = self._env.unwrapped
        current = np.asarray(
            u.agent.controller.controllers["arm"].ee_link.pose.p, dtype=float
        ).reshape(-1)
        target = np.asarray(command.target_pose_m, dtype=float)
        self._pending_action[:3] = np.clip(target - current, -POS_DELTA_LIMIT, POS_DELTA_LIMIT)
        self._pending_action[3:6] = np.asarray(self._rotation_action(), dtype=float)
        self._pending_action[6] = float(np.clip(self._gripper_action(), -1.0, 1.0))
        return CommandOutcome(accepted=True)

    def step(self) -> None:
        if self._env is None or self._stopped:
            return
        self._observation, _, terminated, _truncated, info = self._env.step(
            self._pending_action
        )
        self._last_info = info
        self._steps += 1
        quat_wxyz = np.asarray(
            self._env.unwrapped.agent.controller.controllers["arm"].ee_link.pose.q,
            dtype=float,
        ).reshape(-1)
        tilt = _tool_tilt_deg(quat_wxyz)
        if tilt > self._peak_tilt_deg:
            self._peak_tilt_deg = tilt
        force = np.asarray(self._robot_contact_force_n(), dtype=float)
        magnitude = float(np.linalg.norm(force))
        if magnitude > self._peak_force_n:
            self._peak_force_n = magnitude
        if magnitude > 1e-6:
            self._contact_steps += 1
        if self._completion_time_s is None and bool(info.get("success", False)):
            self._completion_time_s = self._steps / CONTROL_FREQUENCY_HZ
        if self._first_success_step is None and bool(info.get("success", False)):
            self._first_success_step = self._steps

    def _success_persisted(self) -> bool:
        return self._first_success_step is not None and (
            self._steps >= self._first_success_step + round(0.1 * CONTROL_FREQUENCY_HZ)
        )

    def stop(self) -> None:
        self._stopped = True

    def close(self) -> None:
        if self._env is not None:
            self._env.close()
            self._env = None

    def session_ended(self) -> bool:
        if self._env is None:
            return True
        return (
            self._stopped
            or self._success_persisted()
            or self._steps >= self._stream_length + round(HORIZON_MARGIN_STEPS)
        )

    def session_fault(self) -> str | None:
        return None

    def ground_truth(self) -> Mapping[str, float | str | bool]:
        if self._env is None:
            return {"status": "not_reset", "task_id": self.task_id}
        u = self._env.unwrapped
        peg = np.asarray(u.peg.pose.p, dtype=float).reshape(-1)
        box = np.asarray(u.box.pose.p, dtype=float).reshape(-1)
        return {
            "status": "ok",
            "task_id": self.task_id,
            "control_mode": "pd_ee_pose",
            "sim_backend": "physx_cpu",
            "commanded_k_z_mapped": False,
            "control_period_s": 1.0 / CONTROL_FREQUENCY_HZ,
            "force_proxy": "physx_last_substep_net_impulse_on_robot_links",
            "peg_x_m": float(peg[0]),
            "peg_y_m": float(peg[1]),
            "peg_z_m": float(peg[2]),
            "hole_x_m": float(box[0]),
            "hole_y_m": float(box[1]),
            "hole_z_m": float(box[2]),
        }

    def task_result(self) -> Mapping[str, float | str | bool | None]:
        success = bool(getattr(self, "_last_info", {}).get("success", False))
        return {
            "success": success,
            "completion_time_s": self._completion_time_s,
            "peak_force_n": self._peak_force_n,
            "n_contact_steps": self._contact_steps,
            "n_steps": self._steps,
        }
