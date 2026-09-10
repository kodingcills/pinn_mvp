"""robosuite 1.5.2 Lift external-task adapter (successful-execution track).

The benchmark's OSC_POSE controller owns its fixed ``kp=150`` gain; the
ManiSkill precedent is followed for the controller-owned actuation channels.
Success is the benchmark's own ``_check_success`` (cube lifted above a
threshold height by the gripper). This adapter exists to repair the R00
evidence hole: the prior campaign's NutAssembly scripted policy never
acquired the nut (0/15 everywhere), so the external MuJoCo track had no
successful-execution comparison. Lift is robosuite's canonical easy task.
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


TASK_ID: Final = "robosuite/1.5.2/Lift"
CONTROL_FREQUENCY_HZ: Final = 20
HORIZON_S: Final = 15.0
OSC_KP: Final = 150.0


def _vertical_tilt_deg(quat_xyzw: np.ndarray) -> float:
    x, y, z, w = (float(value) for value in quat_xyzw)
    axis_z_dot_world_z = 1.0 - 2.0 * (x * x + y * y)
    alignment = min(1.0, max(-1.0, abs(axis_z_dot_world_z)))
    return math.degrees(math.acos(alignment))


class RobosuiteLiftTask:
    task_id = TASK_ID

    def __init__(self, gripper_action: Callable[[], float]) -> None:
        self._gripper_action = gripper_action
        self._env = None
        self._observation = None
        self._pending_action = np.zeros(7, dtype=float)
        self._stopped = False
        self._steps = 0
        self._contact_steps = 0
        self._peak_force_n = 0.0
        self._peak_tilt_deg = 0.0
        self._completion_time_s: float | None = None
        self._first_success_step: int | None = None
        self._controller_config = None
        self._state_period_s = 1.0 / CONTROL_FREQUENCY_HZ
        self._n_substeps = 0

    @property
    def controller_config(self):
        return self._controller_config

    def reset(self, *, seed: int) -> None:
        import robosuite as suite

        if self._env is not None:
            self._env.close()
        config = suite.load_composite_controller_config(robot="Panda")
        self._controller_config = config
        self._env = suite.make(
            env_name="Lift",
            robots="Panda",
            controller_configs=config,
            has_renderer=False,
            has_offscreen_renderer=False,
            use_camera_obs=False,
            control_freq=CONTROL_FREQUENCY_HZ,
            horizon=round(HORIZON_S * CONTROL_FREQUENCY_HZ),
            ignore_done=True,
            seed=seed,
        )
        self._observation = self._env.reset()
        model_timestep_s = float(self._env.sim.model.opt.timestep)
        self._n_substeps = round(float(self._env.control_timestep) / model_timestep_s)
        self._state_period_s = model_timestep_s * self._n_substeps
        self._pending_action.fill(0.0)
        self._pending_action[6] = -1.0
        self._stopped = False
        self._steps = 0
        self._contact_steps = 0
        self._peak_force_n = 0.0
        self._peak_tilt_deg = 0.0
        self._completion_time_s = None
        self._first_success_step = None

    def capabilities(self) -> MachineCapabilities:
        return MachineCapabilities(
            command_modes=frozenset({CommandMode.CARTESIAN_IMPEDANCE}),
            sensing=frozenset(
                {SensingChannel.POSE, SensingChannel.FORCE_Z, SensingChannel.TILT}
            ),
            max_sensed_force_n=1000.0,
            max_sensed_tilt_deg=180.0,
            position_resolution_m=1e-6,
            state_period_s=self._state_period_s,
            state_max_age_s=self._state_period_s,
            max_initial_offset_m=1.0,
            execution_horizon_s=HORIZON_S,
            expected_duration_s=8.0,
            supports_stop=True,
        )

    def observe(self) -> MachineObservation | None:
        if self._env is None or self._observation is None:
            return None
        obs = self._observation
        pose = obs["robot0_eef_pos"]
        quat = obs["robot0_eef_quat"]
        force = np.asarray(self._env.robots[0].ee_force["right"], dtype=float)
        cube = obs["cube_pos"]
        now = float(self._env.sim.data.time)
        success = bool(self._env._check_success())
        return MachineObservation(
            stamp=Stamp(source_s=now, received_s=now),
            pose_m=(float(pose[0]), float(pose[1]), float(pose[2])),
            force_z_n=float(force[2]),
            tilt_deg=_vertical_tilt_deg(quat),
            extra=(
                ("task_success", 1.0 if success else 0.0),
                ("force_mag_n", float(np.linalg.norm(force))),
                ("cube_x", float(cube[0])),
                ("cube_y", float(cube[1])),
                ("cube_z", float(cube[2])),
            ),
        )

    def apply_command(self, command: ServoCommand) -> CommandOutcome:
        if self._stopped or self._observation is None:
            return CommandOutcome(accepted=False, reason="session_inactive")
        current = np.asarray(self._observation["robot0_eef_pos"], dtype=float)
        target = np.asarray(command.target_pose_m, dtype=float)
        output_max = np.array([0.05, 0.05, 0.05], dtype=float)
        self._pending_action[:3] = np.clip((target - current) / output_max, -1.0, 1.0)
        self._pending_action[3:6] = 0.0
        self._pending_action[6] = float(np.clip(self._gripper_action(), -1.0, 1.0))
        return CommandOutcome(accepted=True)

    def step(self) -> None:
        if self._env is None or self._stopped or self.session_ended():
            return
        self._observation, _, _, _ = self._env.step(self._pending_action)
        self._steps += 1
        obs = self._observation
        pose = obs["robot0_eef_pos"]
        quat = obs["robot0_eef_quat"]
        tilt = _vertical_tilt_deg(np.asarray(quat, dtype=float))
        if tilt > self._peak_tilt_deg:
            self._peak_tilt_deg = tilt
        force = np.asarray(self._env.robots[0].ee_force["right"], dtype=float)
        magnitude = float(np.linalg.norm(force))
        if magnitude > self._peak_force_n:
            self._peak_force_n = magnitude
        if self._env.sim.data.ncon > 0:
            self._contact_steps += 1
        success = bool(self._env._check_success())
        if self._first_success_step is None and success:
            self._first_success_step = self._steps
        if self._completion_time_s is None and success:
            self._completion_time_s = float(self._env.sim.data.time)

    def stop(self) -> None:
        self._stopped = True
        self._pending_action.fill(0.0)

    def close(self) -> None:
        if self._env is not None:
            self._env.close()
            self._env = None

    def session_ended(self) -> bool:
        if self._env is None:
            return True
        if self._stopped:
            return True
        if self._first_success_step is not None:
            return self._steps >= self._first_success_step + round(0.5 * CONTROL_FREQUENCY_HZ)
        return self._steps >= round(HORIZON_S * CONTROL_FREQUENCY_HZ)

    def session_fault(self) -> str | None:
        return None

    def ground_truth(self) -> Mapping[str, float | str | bool]:
        if self._env is None:
            return {"status": "not_reset", "task_id": self.task_id}
        cube = self._observation["cube_pos"]
        return {
            "status": "ok",
            "task_id": self.task_id,
            "osc_kp": OSC_KP,
            "commanded_k_z_mapped": False,
            "control_period_s": self._state_period_s,
            "n_substeps": float(self._n_substeps),
            "cube_x_m": float(cube[0]),
            "cube_y_m": float(cube[1]),
            "cube_z_m": float(cube[2]),
            "success_predicate": "robosuite Lift _check_success",
        }

    def task_result(self) -> Mapping[str, float | str | bool | None]:
        success = bool(self._env is not None and self._env._check_success())
        return {
            "success": success,
            "completion_time_s": self._completion_time_s,
            "peak_force_n": self._peak_force_n,
            "peak_tilt_deg": self._peak_tilt_deg,
            "n_contact_steps": self._contact_steps,
            "n_steps": self._steps,
        }
