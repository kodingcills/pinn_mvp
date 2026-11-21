"""robosuite 1.5.2 Lift on the joint-position command surface (issue #18,
docs/engineering/designs/I18_second_surface.md).

The arm runs robosuite's own JointPositionController with its packaged
defaults (kp=50, critically damped, mass-matrix decoupling plus gravity
compensation, torques clipped to the Panda actuator ranges), switched to
absolute input so a JointPositionTarget is the controller's setpoint. The
policy owns its own kinematic model of the Panda (Franka's published
modified-DH parameters, the geometry of benchmarks/realdata/panda_inertial.xml),
not robosuite's. Completion truth stays with robosuite:
task_result() reports `_check_success`, which the runtime never reads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Final

import numpy as np

from benchmarks.external.robosuite_lift_task import RobosuiteLiftTask
from haplous import (
    CommandMode,
    CommandOutcome,
    CommandPayload,
    ExecutionIntent,
    JointPositionTarget,
    MachineCapabilities,
    MachineObservation,
    Requirement,
    RequirementKind,
    StopResponse,
    SuccessCondition,
)

TASK_ID: Final = "robosuite/1.5.2/Lift/joint_position"
N_JOINTS: Final = 7
# Lift's own success height: table top (0.8 m) + 0.04 m. The runtime sees
# the cube height as an observation; the grader is `_check_success`.
CUBE_LIFTED_Z_M: Final = 0.84
# Franka Panda modified DH (a, d, alpha) per joint; the flange is 0.107 m
# along joint 7's axis.
PANDA_MDH: Final = (
    (0.0, 0.333, 0.0),
    (0.0, 0.0, -np.pi / 2),
    (0.0, 0.316, np.pi / 2),
    (0.0825, 0.0, np.pi / 2),
    (-0.0825, 0.384, -np.pi / 2),
    (0.0, 0.0, np.pi / 2),
    (0.088, 0.0, np.pi / 2),
)
# Policy-side calibration, measured once in the I18 spike against robosuite
# 1.5.2: the Panda mount (identity rotation) and the grip site, 0.097 m past
# the hand (flange + 0.107 m). FK agrees with robosuite's eef within 0.5 mm.
BASE_POS_M: Final = np.array([-0.56, 0.0, 0.912])
TOOL_Z_M: Final = 0.107 + 0.097


class RobosuiteLiftJointTask(RobosuiteLiftTask):
    """Lift with the arm under JOINT_POSITION. On stop() the controller
    latches the measured joint position and the world keeps advancing
    (StopResponse.HOLD_POSITION); `trace` keeps one benchmark-side
    (t, q, qdot, eef) sample per control step for physical-response
    measurements and never reaches the runtime."""

    task_id = TASK_ID

    def __init__(
        self,
        gripper_action: Callable[[], float],
        *,
        dropout_windows_s: tuple[tuple[float, float], ...] = (),
    ) -> None:
        super().__init__(gripper_action)
        self._dropout_windows_s = dropout_windows_s
        self.trace: list[dict] = []

    def _make_controller_config(self, config: dict) -> dict:
        from robosuite.controllers import load_part_controller_config

        arm = config["body_parts"]["right"]
        config["body_parts"]["right"] = {
            **load_part_controller_config(default_controller="JOINT_POSITION"),
            "input_type": "absolute",
            "gripper": arm["gripper"],
        }
        return config

    def reset(self, *, seed: int) -> None:
        super().reset(seed=seed)
        self._pending_action = np.r_[self._joint_pos(), -1.0]
        self.trace = []
        self._record()

    def capabilities(self) -> MachineCapabilities:
        return replace(
            super().capabilities(),
            command_modes=frozenset({CommandMode.JOINT_POSITION}),
            stop_response=StopResponse.HOLD_POSITION,
        )

    def observe(self) -> MachineObservation | None:
        obs = super().observe()
        if obs is None or any(lo <= obs.stamp.source_s < hi for lo, hi in self._dropout_windows_s):
            return None
        joints = tuple((f"q{i}", float(v)) for i, v in enumerate(self._joint_pos()))
        # task_success is grader truth; this surface keeps it out of observations.
        return replace(obs, extra=tuple(kv for kv in obs.extra if kv[0] != "task_success") + joints)

    def apply_command(self, command: CommandPayload) -> CommandOutcome:
        if self._stopped or self._observation is None:
            return CommandOutcome(accepted=False, reason="session_inactive")
        if not isinstance(command, JointPositionTarget) or len(command.positions_rad) != N_JOINTS:
            return CommandOutcome(accepted=False, reason="payload_shape")
        self._pending_action[:N_JOINTS] = command.positions_rad
        self._pending_action[N_JOINTS] = float(np.clip(self._gripper_action(), -1.0, 1.0))
        return CommandOutcome(accepted=True)

    def step(self) -> None:
        if self._stopped and self._env is not None:
            # The world keeps advancing after stop; the controller holds.
            self._observation, _, _, _ = self._env.step(self._pending_action)
        else:
            super().step()
        self._record()

    def stop(self) -> None:
        if not self._stopped and self._observation is not None:
            self._pending_action[:N_JOINTS] = self._joint_pos()
        self._stopped = True

    def _joint_pos(self) -> np.ndarray:
        return np.asarray(self._observation["robot0_joint_pos"], dtype=float)

    def _record(self) -> None:
        o = self._observation
        self.trace.append({
            "t_s": float(self._env.sim.data.time),
            "q": [float(v) for v in o["robot0_joint_pos"]],
            "qdot": [float(v) for v in o["robot0_joint_vel"]],
            "eef_m": [float(v) for v in o["robot0_eef_pos"]],
            "target_q": [float(v) for v in self._pending_action[:N_JOINTS]],
        })


class JointLiftPolicy:
    """Scripted pick-and-lift that commands joint positions. Same phases and
    thresholds as LiftPolicy; each tick it maps the phase's Cartesian goal
    to a joint setpoint by one damped least-squares step of its own Panda
    model, closed on the observed gripper position, holding the hand
    orientation it started with."""

    # step() returns JointPositionTarget only.
    required_command_modes = frozenset({CommandMode.JOINT_POSITION})
    authorized_substitutes: frozenset = frozenset()
    non_substitutable_modes: frozenset = frozenset()

    _STEP_M = 0.03
    _DAMPING = 1e-4

    def __init__(self) -> None:
        self._phase = 0
        self._phase_steps = 0
        self._gripper = -1.0
        self._r0 = np.eye(3)

    @property
    def gripper_action(self) -> float:
        return self._gripper

    def begin(self, intent: ExecutionIntent, caps: MachineCapabilities, observation: MachineObservation) -> None:
        del intent, caps
        self._phase, self._phase_steps, self._gripper = 0, 0, -1.0
        self._r0 = _kinematics(_joints(observation))[1]

    def step(self, observation: MachineObservation) -> JointPositionTarget:
        q = _joints(observation)
        x = np.asarray(observation.pose_m)
        cube = np.array([observation.extra_value(f"cube_{a}") for a in "xyz"], dtype=float)
        self._phase_steps += 1
        if self._phase in (0, 1) and np.all(np.abs(x - self._goal(cube)) < [0.012, 0.012, 0.015]):
            self._phase, self._phase_steps = self._phase + 1, 0
            if self._phase == 2:
                self._gripper = 1.0
        elif self._phase == 2 and self._phase_steps >= 12:
            self._phase, self._phase_steps = 3, 0
        _, r, jac = _kinematics(q)
        rot_err = 0.5 * sum(np.cross(r[:, i], self._r0[:, i]) for i in range(3))
        dq = jac.T @ np.linalg.solve(jac @ jac.T + self._DAMPING * np.eye(6),
                                     np.r_[np.clip(self._goal(cube) - x, -self._STEP_M, self._STEP_M), rot_err])
        return JointPositionTarget(positions_rad=tuple(float(v) for v in q + dq))

    def _goal(self, cube: np.ndarray) -> np.ndarray:
        return cube + (0.0, 0.0, (0.12, 0.012, 0.012, 0.22)[self._phase])



def _kinematics(q: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Grip-site position (world), hand rotation and the 6x7 geometric
    Jacobian at q, from the modified-DH chain."""
    t = np.eye(4)
    axes, origins = [], []
    for (a, d, alpha), theta in zip(PANDA_MDH, q, strict=True):
        ca, sa, ct, st = np.cos(alpha), np.sin(alpha), np.cos(theta), np.sin(theta)
        t = t @ np.array([[ct, -st, 0.0, a],
                          [st * ca, ct * ca, -sa, -d * sa],
                          [st * sa, ct * sa, ca, d * ca],
                          [0.0, 0.0, 0.0, 1.0]])
        axes.append(t[:3, 2].copy())
        origins.append(t[:3, 3].copy())
    tip = t[:3, 3] + TOOL_Z_M * t[:3, 2]
    jac = np.vstack([np.column_stack([np.cross(z, tip - o) for z, o in zip(axes, origins, strict=True)]),
                     np.column_stack(axes)])
    return BASE_POS_M + tip, t[:3, :3], jac


def _joints(observation: MachineObservation) -> np.ndarray:
    return np.array([observation.extra_value(f"q{i}") for i in range(N_JOINTS)], dtype=float)


def _cube_lifted(observations) -> bool:
    return bool(observations) and (observations[-1].extra_value("cube_z") or 0.0) > CUBE_LIFTED_Z_M


def lift_joint_intent(observation: MachineObservation, *, deadline_s: float = 12.0) -> ExecutionIntent:
    return ExecutionIntent(
        behavior="robosuite_lift_joint",
        goal_description="grasp the cube and lift it, commanding joint positions",
        goal_position_m=observation.pose_m,
        start_position_m=observation.pose_m,
        requirements=(
            Requirement(RequirementKind.MAX_PROCESS_FORCE_N, True, 25.0),
            Requirement(RequirementKind.MAX_TILT_DEG, True, 30.0),
            Requirement(RequirementKind.STATE_MAX_AGE_S, True, 0.1),
            Requirement(RequirementKind.DEADLINE_S, False, deadline_s),
        ),
        success_condition=SuccessCondition("cube_lifted", _cube_lifted, window_s=0.25),
    )
