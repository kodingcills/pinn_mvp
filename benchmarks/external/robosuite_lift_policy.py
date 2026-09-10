"""Scripted pick-and-lift policy for the robosuite Lift successful-execution
baseline (R00). Phase machine identical in style to NutAssemblyPolicy; the
cube pose rides in observation extras. This policy exists to be frozen and
then paired direct vs Haplous - it is not an incumbent-supervisor claim.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import assert_never

from haplous import ExecutionIntent, MachineCapabilities, MachineObservation, ServoCommand


class _Phase(Enum):
    ABOVE_CUBE = auto()
    DESCEND = auto()
    GRASP = auto()
    LIFT = auto()


class LiftPolicy:
    def __init__(self) -> None:
        self._phase = _Phase.ABOVE_CUBE
        self._phase_steps = 0
        self._gripper = -1.0
        self._cube = (0.0, 0.0, 0.0)

    @property
    def gripper_action(self) -> float:
        return self._gripper

    def begin(
        self,
        intent: ExecutionIntent,
        caps: MachineCapabilities,
        observation: MachineObservation,
    ) -> None:
        del intent, caps
        self._phase = _Phase.ABOVE_CUBE
        self._phase_steps = 0
        self._gripper = -1.0
        self._cube = self._point(observation, "cube")

    def step(self, observation: MachineObservation) -> ServoCommand:
        self._cube = self._point(observation, "cube")
        self._phase_steps += 1
        target = self._phase_target()
        if self._should_advance(observation.pose_m):
            self._advance()
            target = self._phase_target()
        return ServoCommand(target_pose_m=target, k_z_n_per_m=150.0)

    def _phase_target(self) -> tuple[float, float, float]:
        cx, cy, cz = self._cube
        match self._phase:
            case _Phase.ABOVE_CUBE:
                return (cx, cy, cz + 0.12)
            case _Phase.DESCEND:
                return (cx, cy, cz + 0.012)
            case _Phase.GRASP:
                return (cx, cy, cz + 0.012)
            case _Phase.LIFT:
                return (cx, cy, cz + 0.22)
            case unreachable:
                assert_never(unreachable)

    def _advance(self) -> None:
        match self._phase:
            case _Phase.ABOVE_CUBE:
                self._phase = _Phase.DESCEND
            case _Phase.DESCEND:
                self._phase = _Phase.GRASP
                self._gripper = 1.0
            case _Phase.GRASP:
                self._phase = _Phase.LIFT
            case _Phase.LIFT:
                return
            case unreachable:
                assert_never(unreachable)
        self._phase_steps = 0

    def _should_advance(self, current: tuple[float, float, float]) -> bool:
        match self._phase:
            case _Phase.GRASP:
                return self._phase_steps >= 12
            case _Phase.LIFT:
                return False
            case _Phase.ABOVE_CUBE | _Phase.DESCEND:
                return self._reached(current, self._phase_target())
            case unreachable:
                assert_never(unreachable)

    @staticmethod
    def _reached(
        current: tuple[float, float, float],
        target: tuple[float, float, float],
    ) -> bool:
        return (
            abs(current[0] - target[0]) < 0.012
            and abs(current[1] - target[1]) < 0.012
            and abs(current[2] - target[2]) < 0.015
        )

    @staticmethod
    def _point(observation: MachineObservation, prefix: str) -> tuple[float, float, float]:
        values = tuple(observation.extra_value(f"{prefix}_{axis}") for axis in "xyz")
        assert all(value is not None for value in values)
        return (float(values[0]), float(values[1]), float(values[2]))
