from __future__ import annotations

import math
from enum import Enum, auto
from typing import assert_never

from haplous import (
    CommandMode,
    ExecutionIntent,
    MachineCapabilities,
    MachineObservation,
    ServoCommand,
)


class _Phase(Enum):
    ABOVE_NUT = auto()
    AT_NUT = auto()
    GRASP = auto()
    LIFT = auto()
    ABOVE_PEG = auto()
    THREAD = auto()
    RELEASE = auto()
    RETREAT = auto()


class NutAssemblyPolicy:
    # step() emits a per-phase pose target at fixed stiffness and no force
    # reference; RobosuiteNutAssemblyTask maps it to an OSC position delta.
    # Impedance only, which that task offers.
    required_command_modes = frozenset({CommandMode.CARTESIAN_IMPEDANCE})
    authorized_substitutes: frozenset = frozenset()
    non_substitutable_modes: frozenset = frozenset()

    def __init__(self) -> None:
        self._phase = _Phase.ABOVE_NUT
        self._phase_steps = 0
        self._gripper = -1.0
        self._nut = (0.0, 0.0, 0.0)
        self._peg = (0.0, 0.0, 0.0)

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
        self._phase = _Phase.ABOVE_NUT
        self._phase_steps = 0
        self._gripper = -1.0
        self._nut = self._point(observation, "nut")
        self._peg = self._point(observation, "peg")

    def step(self, observation: MachineObservation) -> ServoCommand:
        if self._phase in {_Phase.ABOVE_NUT, _Phase.AT_NUT}:
            self._nut = self._point(observation, "nut")
        self._phase_steps += 1
        target = self._phase_target()
        if self._should_advance(observation.pose_m, target):
            self._advance()
            target = self._phase_target()
        return ServoCommand(target_pose_m=target, k_z_n_per_m=150.0)

    def _phase_target(self) -> tuple[float, float, float]:
        nx, ny, nz = self._nut
        px, py, pz = self._peg
        match self._phase:
            case _Phase.ABOVE_NUT:
                return (nx, ny, nz + 0.12)
            case _Phase.AT_NUT | _Phase.GRASP:
                return (nx, ny, nz + 0.012)
            case _Phase.LIFT:
                return (nx, ny, nz + 0.16)
            case _Phase.ABOVE_PEG:
                return (px, py, pz + 0.18)
            case _Phase.THREAD | _Phase.RELEASE:
                return (px, py, pz + 0.035)
            case _Phase.RETREAT:
                return (px, py, pz + 0.18)
            case unreachable:
                assert_never(unreachable)

    def _advance(self) -> None:
        match self._phase:
            case _Phase.ABOVE_NUT:
                self._phase = _Phase.AT_NUT
            case _Phase.AT_NUT:
                self._phase = _Phase.GRASP
                self._gripper = 1.0
            case _Phase.GRASP:
                self._phase = _Phase.LIFT
            case _Phase.LIFT:
                self._phase = _Phase.ABOVE_PEG
            case _Phase.ABOVE_PEG:
                self._phase = _Phase.THREAD
            case _Phase.THREAD:
                self._phase = _Phase.RELEASE
                self._gripper = -1.0
            case _Phase.RELEASE:
                self._phase = _Phase.RETREAT
            case _Phase.RETREAT:
                return
            case unreachable:
                assert_never(unreachable)
        self._phase_steps = 0

    def _dwell_complete(self) -> bool:
        match self._phase:
            case _Phase.GRASP:
                return self._phase_steps >= 12
            case _Phase.RELEASE:
                return self._phase_steps >= 10
            case _Phase.THREAD:
                return self._phase_steps >= 20
            case _Phase.RETREAT | _Phase.ABOVE_NUT | _Phase.AT_NUT | _Phase.LIFT | _Phase.ABOVE_PEG:
                return False
            case unreachable:
                assert_never(unreachable)

    def _should_advance(
        self,
        current: tuple[float, float, float],
        target: tuple[float, float, float],
    ) -> bool:
        match self._phase:
            case _Phase.GRASP | _Phase.THREAD | _Phase.RELEASE:
                return self._dwell_complete()
            case _Phase.RETREAT:
                return False
            case _Phase.ABOVE_NUT | _Phase.AT_NUT | _Phase.LIFT | _Phase.ABOVE_PEG:
                return self._reached(current, target)
            case unreachable:
                assert_never(unreachable)

    @staticmethod
    def _reached(
        current: tuple[float, float, float],
        target: tuple[float, float, float],
    ) -> bool:
        return math.dist(current, target) < 0.012

    @staticmethod
    def _point(observation: MachineObservation, prefix: str) -> tuple[float, float, float]:
        values = tuple(observation.extra_value(f"{prefix}_{axis}") for axis in "xyz")
        assert all(value is not None for value in values)
        return (float(values[0]), float(values[1]), float(values[2]))
