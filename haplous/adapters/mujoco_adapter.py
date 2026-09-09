"""MuJoCo machine adapter: implements the haplous Machine protocol by
wrapping the validated pegsim research substrate (run_episode). This is the
architectural inversion: Haplous runtime logic sits above the machine
boundary; the validated physics stays behind it, unwritten."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional

import mujoco

from pegsim.rollout import (
    EpisodeConfig,
    ValidatedConfig,
    build_episode_model_data,
    run_episode,
)

from haplous.capabilities import CommandMode, MachineCapabilities, SensingChannel
from haplous.machine import (
    ControlParameters,
    MachineObservation,
    MotionRecord,
    Stamp,
)


@dataclass(frozen=True)
class MuJoCoMachineConfig:
    observation_decimation: int = 10
    state_max_age_s: float = 0.02
    receive_delay_s: float = 0.0
    dropout_windows_s: tuple[tuple[float, float], ...] = ()
    expected_duration_s: float = 4.0
    max_sensed_force_n: float = 100.0
    max_sensed_tilt_deg: float = 45.0
    position_resolution_m: float = 1e-6


class MuJoCoMachine:
    """Machine implementation over one pegsim validated bundle. Mechanism
    parameters (gains, spiral geometry, decimation) are owned here and are
    never visible to the runtime as intent."""

    def __init__(
        self,
        bundle: ValidatedConfig,
        *,
        config: Optional[MuJoCoMachineConfig] = None,
        model=None,
        data=None,
    ) -> None:
        self._bundle = bundle
        self._config = config or MuJoCoMachineConfig()
        if model is None:
            template = self._episode(0.0, 0.0, bundle.max_seconds)
            model, data = build_episode_model_data(template)
        self._model = model
        self._data = data
        self._tip_site_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_SITE, "peg_tip"
        )
        self._last: Optional[MachineObservation] = None
        self.execute_calls: list[tuple[tuple[float, float, float], float]] = []
        self.stop_calls = 0

    @property
    def parameters(self) -> ControlParameters:
        """The mechanism parameters behind this machine, published as a
        distinct type so they can never be confused with intent."""
        return ControlParameters(name=self._bundle.name)

    def _episode(
        self, offset_r: float, offset_theta: float, horizon_s: float
    ) -> EpisodeConfig:
        b = self._bundle
        return EpisodeConfig(
            task=b.task,
            solver=b.solver,
            gains=b.gains,
            insertion=b.insertion,
            z_start=b.z_start,
            initial_offset_r=offset_r,
            initial_offset_theta=offset_theta,
            max_seconds=min(horizon_s, b.max_seconds),
            spiral_spec=b.spiral,
            control_decimation=b.control_decimation,
            control_delay_steps=b.control_delay_steps,
        )

    def capabilities(self) -> MachineCapabilities:
        b = self._bundle
        c = self._config
        return MachineCapabilities(
            command_modes=frozenset(
                {
                    CommandMode.CARTESIAN_IMPEDANCE,
                    CommandMode.FORCE_CONTROLLED_DESCENT,
                }
            ),
            sensing=frozenset(
                {SensingChannel.POSE, SensingChannel.FORCE_Z, SensingChannel.TILT}
            ),
            max_sensed_force_n=c.max_sensed_force_n,
            max_sensed_tilt_deg=c.max_sensed_tilt_deg,
            position_resolution_m=c.position_resolution_m,
            state_period_s=b.solver.timestep * c.observation_decimation,
            state_max_age_s=c.state_max_age_s,
            max_initial_offset_m=b.spiral.r_spiral + b.r_cap_dyn,
            execution_horizon_s=b.max_seconds,
            expected_duration_s=c.expected_duration_s,
            supports_stop=True,
        )

    def execute(
        self,
        *,
        start_position_m: tuple[float, float, float],
        horizon_s: float,
    ) -> MotionRecord:
        self.execute_calls.append((start_position_m, horizon_s))
        x, y = start_position_m[0], start_position_m[1]
        cfg = self._episode(math.hypot(x, y), math.atan2(y, x), horizon_s)
        try:
            tr = run_episode(
                cfg,
                pen_allow=self._bundle.pen_allow,
                model=self._model,
                data=self._data,
            )
        except AssertionError as exc:
            reason = (
                "tilt_guard" if "tilt" in str(exc).lower() else "configuration_error"
            )
            return MotionRecord(observations=(), ended_early=True, end_reason=reason)
        observations: list[MachineObservation] = []
        decim = self._config.observation_decimation
        for i in range(0, len(tr.time), decim):
            t = float(tr.time[i])
            if any(lo <= t <= hi for lo, hi in self._config.dropout_windows_s):
                continue
            observations.append(
                MachineObservation(
                    stamp=Stamp(
                        source_s=t, received_s=t + self._config.receive_delay_s
                    ),
                    pose_m=(
                        float(tr.tip_pos[i, 0]),
                        float(tr.tip_pos[i, 1]),
                        float(tr.tip_pos[i, 2]),
                    ),
                    force_z_n=float(tr.contact_force[i, 2]),
                    tilt_deg=float(tr.tilt_deg[i]),
                )
            )
        self._last = observations[-1] if observations else None
        return MotionRecord(
            observations=tuple(observations), ended_early=False, end_reason=None
        )

    def observe(self) -> Optional[MachineObservation]:
        if self._last is not None:
            return self._last
        # Before any motion, report the machine's actual resting state
        # (freshly forwarded, zero contact, no tilt) so the runtime can
        # authorize execution against a real observation.
        pose = tuple(float(v) for v in self._data.site_xpos[self._tip_site_id])
        return MachineObservation(
            stamp=Stamp(source_s=0.0, received_s=0.0),
            pose_m=pose,
            force_z_n=0.0,
            tilt_deg=0.0,
        )

    def stop(self) -> None:
        self.stop_calls += 1

    def health(self) -> Mapping[str, float | str | bool]:
        return {"status": "ok", "bundle": self._bundle.name}
