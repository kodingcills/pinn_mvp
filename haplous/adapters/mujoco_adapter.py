"""MuJoCo machine adapter: implements the haplous Machine protocol by
wrapping the validated pegsim research substrate primitives. This is the
architectural inversion: Haplous runtime logic sits above the machine
boundary; the validated physics and servo level stay behind it.

The machine is a live servo: open_session() resets physics to the commanded
start and returns a session that the runtime steps one supervision period
at a time. The session applies the behavior's accepted servo commands
(target pose + phase axial stiffness) through the pegsim impedance servo,
holds the last accepted command when the runtime withholds authorization,
and models machine-interface faults (stale delivery, dropout, command
rejection, machine fault) from benchmark-owned schedules in its config.
Mechanism parameters (gains, spiral geometry, decimation) are owned here
and are never visible to the runtime as intent."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Mapping, Optional

import mujoco
import numpy as np

from pegsim.control import (
    assert_gains_stable,
    compute_and_apply_qfrc,
    peg_contact_force_world,
    peg_dof_ids,
)
from pegsim.rollout import (
    EpisodeConfig,
    ValidatedConfig,
    build_episode_model_data,
)

from haplous.capabilities import CommandMode, MachineCapabilities, SensingChannel
from haplous.machine import (
    CommandOutcome,
    ControlParameters,
    MachineObservation,
    ServoCommand,
    Stamp,
)


@dataclass(frozen=True)
class MuJoCoMachineConfig:
    observation_decimation: int = 1
    state_max_age_s: float = 0.02
    receive_delay_s: float = 0.0
    dropout_windows_s: tuple[tuple[float, float], ...] = ()
    # (start_s, end_s, extra_delay_s): observations sampled in the window
    # are delivered late by the extra delay, on top of receive_delay_s.
    stale_windows_s: tuple[tuple[float, float, float], ...] = ()
    # Commands handed to the machine in the window are rejected.
    command_rejection_windows_s: tuple[tuple[float, float], ...] = ()
    # Modelled machine fault time: actuation dies at this sim time.
    fault_at_s: float | None = None
    # Benchmark-owned physical disturbances: (t_s, dv_per_peg_dof). At the
    # first step at or after t_s the peg's joint velocities jump by dv —
    # a modelled impulsive disturbance (a bump/jolt), not a control input.
    disturbances: tuple[tuple[float, tuple[float, float, float, float, float, float]], ...] = ()
    expected_duration_s: float = 4.0
    # Measured-success reach override for the refusal boundary. None
    # reports the bundle's geometric search envelope; a value here
    # declares the largest initial offset measured to succeed (the
    # capture boundary), which is what "can this request succeed" means.
    max_initial_offset_m: float | None = None
    max_sensed_force_n: float = 100.0
    max_sensed_tilt_deg: float = 45.0
    position_resolution_m: float = 1e-6


class MuJoCoMachine:
    """Machine implementation over one pegsim validated bundle."""

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
        self._session: Optional[_MuJoCoSession] = None
        self.open_session_calls: list[tuple[tuple[float, float, float], float]] = []
        self.stop_calls = 0

    @property
    def last_session_observations(self) -> tuple[MachineObservation, ...]:
        if self._session is not None:
            return self._session.observations
        return ()

    @property
    def last_session_applied_command_times(self) -> tuple[float, ...]:
        if self._session is not None:
            return tuple(self._session.applied_command_times)
        return ()

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
            spiral_spec=b.spiral if b.use_spiral else None,
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
            max_initial_offset_m=(
                c.max_initial_offset_m
                if c.max_initial_offset_m is not None
                else b.max_initial_offset_m
            ),
            execution_horizon_s=b.max_seconds,
            expected_duration_s=c.expected_duration_s,
            supports_stop=True,
        )

    def open_session(
        self,
        *,
        start_position_m: tuple[float, float, float],
        horizon_s: float,
    ) -> "_MuJoCoSession":
        if self._session is not None and not self._session.ended:
            raise RuntimeError("machine already has an active session")
        self.open_session_calls.append((start_position_m, horizon_s))
        session = _MuJoCoSession(
            self, start_position_m=start_position_m, horizon_s=horizon_s
        )
        self._session = session
        return session

    def observe(self) -> Optional[MachineObservation]:
        if self._session is not None and not self._session.ended:
            return self._session.observe()
        # Outside a live session, report the machine's actual resting state
        # (freshly forwarded, zero contact, no tilt) so the runtime can
        # authorize execution against a real observation.
        pose = tuple(float(v) for v in self._data.site_xpos[self._tip_site_id])
        return MachineObservation(
            stamp=Stamp(
                source_s=0.0, received_s=self._config.receive_delay_s
            ),
            pose_m=pose,
            force_z_n=0.0,
            tilt_deg=0.0,
        )

    def stop(self) -> None:
        self.stop_calls += 1
        if self._session is not None:
            self._session.stop()

    def health(self) -> Mapping[str, float | str | bool]:
        return {"status": "ok", "bundle": self._bundle.name}


_TIME_EPS = 1e-9


class _MuJoCoSession:
    """One live insertion session: resets physics, seeds the commanded
    start, and steps the validated servo one supervision period per call."""

    def __init__(
        self,
        machine: MuJoCoMachine,
        *,
        start_position_m: tuple[float, float, float],
        horizon_s: float,
    ) -> None:
        self._machine = machine
        self._config = machine._config
        self._model = machine._model
        self._data = machine._data
        self._tip_site_id = machine._tip_site_id
        self._horizon_s = min(horizon_s, machine._bundle.max_seconds)
        self._period_s = (
            machine._bundle.solver.timestep * self._config.observation_decimation
        )
        self._gains = machine._bundle.gains
        self._dt = machine._bundle.solver.timestep
        self._delay_steps = machine._bundle.control_delay_steps

        b = machine._bundle
        mujoco.mj_resetData(self._model, self._data)
        self._data.qpos[0] = start_position_m[0]
        self._data.qpos[1] = start_position_m[1]
        self._data.qpos[2] = b.z_start
        mujoco.mj_forward(self._model, self._data)
        assert_gains_stable(self._model, self._data, self._gains, self._dt)
        assert_gains_stable(
            self._model,
            self._data,
            replace(self._gains, k_z=b.insertion.k_z_search),
            self._dt,
        )

        self._pending: Optional[ServoCommand] = None
        self._held = np.zeros(self._model.nv)
        self._delayed: list[np.ndarray] = []
        self._stopped = False
        self._ended = False
        self._fault: Optional[str] = None
        self._observations: list[MachineObservation] = []
        self._applied_disturbances: set[float] = set()
        self.applied_command_times: list[float] = []

    # -- session protocol ---------------------------------------------------

    def observe(self) -> Optional[MachineObservation]:
        t = float(self._data.time)
        if any(lo - _TIME_EPS <= t <= hi + _TIME_EPS for lo, hi in self._config.dropout_windows_s):
            return None
        mujoco.mj_forward(self._model, self._data)
        tip = self._data.site_xpos[self._tip_site_id]
        axis_dir = self._data.site_xmat[self._tip_site_id].reshape(3, 3)[:, 2]
        s = -tip[2] / axis_dir[2]
        mouth = tip[:2] + axis_dir[:2] * s
        force = peg_contact_force_world(self._model, self._data)
        extra_delay = 0.0
        for lo, hi, extra in self._config.stale_windows_s:
            if lo - _TIME_EPS <= t <= hi + _TIME_EPS:
                extra_delay = max(extra_delay, extra)
        obs = MachineObservation(
            stamp=Stamp(
                source_s=t,
                received_s=t + self._config.receive_delay_s + extra_delay,
            ),
            pose_m=(float(tip[0]), float(tip[1]), float(tip[2])),
            force_z_n=float(force[2]),
            tilt_deg=math.degrees(
                math.acos(min(1.0, max(-1.0, float(axis_dir[2]))))
            ),
            extra=(
                ("mouth_x", float(mouth[0])),
                ("mouth_y", float(mouth[1])),
                ("in_contact", 1.0 if self._data.ncon > 0 else 0.0),
            ),
        )
        self._observations.append(obs)
        return obs

    def apply(self, command: ServoCommand) -> CommandOutcome:
        if self._stopped or self._fault is not None or self._ended:
            return CommandOutcome(accepted=False, reason="session_inactive")
        t = float(self._data.time)
        if any(
            lo - _TIME_EPS <= t <= hi + _TIME_EPS
            for lo, hi in self._config.command_rejection_windows_s
        ):
            return CommandOutcome(accepted=False, reason="command_rejected")
        self._pending = command
        return CommandOutcome(accepted=True)

    def step(self) -> None:
        if self._ended:
            return
        if (
            self._config.fault_at_s is not None
            and self._fault is None
            and float(self._data.time) >= self._config.fault_at_s
        ):
            self._fault = "machine_fault"
            self._data.qfrc_applied[:] = 0.0
            self._ended = True
            return
        for t_s, dv in self._config.disturbances:
            if t_s not in self._applied_disturbances and float(self._data.time) >= t_s:
                self._applied_disturbances.add(t_s)
                self._data.qvel[peg_dof_ids(self._model)] += np.array(dv)
        decim = self._config.observation_decimation
        for sub in range(decim):
            if sub == 0 and self._pending is not None:
                command = self._pending
                self._pending = None
                gains = replace(self._gains, k_z=command.k_z_n_per_m)
                try:
                    compute_and_apply_qfrc(
                        self._model,
                        self._data,
                        gains,
                        np.array(command.target_pose_m),
                    )
                except AssertionError:
                    self._fault = "tilt_guard"
                    self._data.qfrc_applied[:] = 0.0
                    self._ended = True
                    return
                # Zero-order hold with modelled command delay: the freshly
                # computed wrench enters a queue and is applied only once
                # its delay slot elapses (exact run_episode sequence).
                self._delayed.append(self._data.qfrc_applied.copy())
                if len(self._delayed) > self._delay_steps:
                    self._held = self._delayed.pop(0)
                self.applied_command_times.append(float(self._data.time))
            if self._stopped or self._fault is not None:
                self._data.qfrc_applied[:] = 0.0
            else:
                self._data.qfrc_applied[:] = self._held
            mujoco.mj_step(self._model, self._data)
        if float(self._data.time) >= self._horizon_s:
            self._ended = True

    def stop(self) -> None:
        self._stopped = True
        self._pending = None
        self._ended = True
        self._data.qfrc_applied[:] = 0.0

    def snapshot_state(self) -> dict:
        """Full resumable state: MuJoCo integration state plus the session's
        own command-hold buffers, so a counterfactual continuation from
        this exact tick is deterministic."""
        spec = mujoco.mjtState.mjSTATE_INTEGRATION
        vec = np.empty(mujoco.mj_stateSize(self._model, spec), dtype=np.float64)
        mujoco.mj_getState(self._model, self._data, vec, spec)
        return {
            "state_vec": vec.copy(),
            "pending": self._pending,
            "held": self._held.copy(),
            "delayed": [d.copy() for d in self._delayed],
            "applied_disturbances": set(self._applied_disturbances),
            "stopped": self._stopped,
            "fault": self._fault,
            "ended": self._ended,
            "n_observations": len(self._observations),
            "time_s": float(self._data.time),
        }

    def restore_state(self, snap: dict) -> None:
        mujoco.mj_setState(
            self._model,
            self._data,
            snap["state_vec"],
            mujoco.mjtState.mjSTATE_INTEGRATION,
        )
        self._pending = snap["pending"]
        self._held = snap["held"].copy()
        self._delayed = [d.copy() for d in snap["delayed"]]
        self._applied_disturbances = set(snap["applied_disturbances"])
        self._stopped = snap["stopped"]
        self._fault = snap["fault"]
        self._ended = snap["ended"]
        del self._observations[snap["n_observations"]:]
        mujoco.mj_forward(self._model, self._data)

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def fault(self) -> Optional[str]:
        return self._fault

    @property
    def observations(self) -> tuple[MachineObservation, ...]:
        return tuple(self._observations)
