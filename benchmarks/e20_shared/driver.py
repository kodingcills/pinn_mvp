"""Frozen E20 machine harness: a neutral, vendor-style driver for the UR5e joint-velocity
controller, with controller/transport faults injected identically for both arms.

The interface deliberately mirrors neither Haplous nor G1. A system integrates by calling:
    capabilities(), acquire_control(), read_state(), send(...), poll_acks(), stop(), step().
The driver applies a command to the plant when the controller accepts it; it does NOT check
validity windows itself (like many real controllers), so supervision is the system's job.
`plant_log` is the independent record of what actually reached the plant; the acceptance suite
grades from it, never from a system's own report. The task grader is not reachable from here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

PERIOD_S = 0.05
MAX_JOINT_SPEED = 0.5  # rad/s at controller input 1.0
STATE_KEYS = ("robot0_joint_pos", "robot0_joint_vel", "robot0_eef_pos", "robot0_eef_quat",
              "cube_pos", "cube_quat", "robot0_gripper_qpos")


@dataclass(frozen=True)
class Fault:
    """kind: none | stale | late_ack | duplicate_ack | disconnect | revoke | no_velocity_mode."""

    kind: str = "none"
    start_s: float = 1.0
    duration_s: float = 0.6
    ack_delay_s: float = 0.30  # late_ack: every ack arrives this late


@dataclass
class _Pending:
    seq: int
    due_s: float
    action: np.ndarray
    status: str


@dataclass
class UR5eVelocityDriver:
    env: object
    fault: Fault = field(default_factory=Fault)

    def __post_init__(self) -> None:
        self._obs = self.env.reset()
        self._last_state = self._state()
        self._token, self._owner, self._connected, self._stopped = 0, None, True, False
        self._pending: list[_Pending] = []
        self._acks: list[dict] = []
        self._applied = np.zeros(7)
        self.plant_log: list[dict] = []   # every command applied to the plant
        self.sends: list[dict] = []       # every send() call as received
        self.stop_calls: list[float] = []

    # -- what the system may call ------------------------------------------------------------
    def capabilities(self) -> dict:
        modes = [] if self.fault.kind == "no_velocity_mode" else ["joint_velocity"]
        return {"command_modes": modes, "joints": 6, "gripper": "binary",
                "max_joint_speed_rad_s": MAX_JOINT_SPEED, "control_period_s": PERIOD_S,
                "ack_model": "asynchronous", "exclusive_control": True,
                "stop_behaviour": "zero_velocity_latched"}

    def acquire_control(self) -> int:
        self._token += 1
        self._owner = self._token
        return self._token

    def control_owner(self) -> int | None:
        return self._owner

    def read_state(self) -> dict:
        """Latest state sample; under a stale fault the same old sample (same t_s) is returned."""
        if self._in_fault("stale"):
            return dict(self._last_state)
        if not self._connected:
            raise ConnectionError("controller disconnected")
        self._last_state = self._state()
        return dict(self._last_state)

    def send(self, seq: int, token: int, joint_vel: list[float], gripper: float, valid_until_s: float) -> dict:
        t = self.now()
        self.sends.append({"t_s": t, "seq": seq, "token": token, "joint_vel": list(map(float, joint_vel)),
                           "gripper": float(gripper), "valid_until_s": valid_until_s})
        if not self._connected:
            return {"seq": seq, "status": "unknown", "reason": "disconnected"}
        if token != self._owner:
            return {"seq": seq, "status": "rejected", "reason": "not_control_owner"}
        if self._stopped:
            return {"seq": seq, "status": "rejected", "reason": "stopped"}
        action = np.concatenate([np.clip(np.asarray(joint_vel, float) / MAX_JOINT_SPEED, -1, 1), [gripper]])
        delay = self.fault.ack_delay_s if self.fault.kind == "late_ack" else 0.0
        self._pending.append(_Pending(seq, t + delay, action, "accepted"))
        return {"seq": seq, "status": "queued"}

    def poll_acks(self) -> list[dict]:
        if not self._connected:
            return []
        out, self._acks = self._acks, []
        return out

    def stop(self) -> None:
        self.stop_calls.append(self.now())
        self._stopped, self._pending = True, []
        self._applied = np.concatenate([np.zeros(6), [self._applied[6]]])

    def step(self) -> None:
        """Advance the world one control period (the harness, not the system, owns time)."""
        t = self.now()
        if self.fault.kind == "disconnect" and t >= self.fault.start_s:
            self._connected = False
        if self.fault.kind == "revoke" and t >= self.fault.start_s and self._owner == self._token:
            self._owner = self._token + 1000  # another client took exclusive control
        for p in [p for p in self._pending if p.due_s <= t + 1e-9]:
            self._pending.remove(p)
            if not self._stopped:
                self._applied = p.action
            ack = {"seq": p.seq, "status": p.status, "t_s": t}
            self._acks.append(ack)
            if self.fault.kind == "duplicate_ack" and self._in_fault("duplicate_ack"):
                self._acks.append(dict(ack))
        self.plant_log.append({"t_s": t, "joint_vel": (self._applied[:6] * MAX_JOINT_SPEED).tolist(),
                               "gripper": float(self._applied[6])})
        self._obs, *_ = self.env.step(self._applied)

    def now(self) -> float:
        return round(float(self.env.sim.data.time), 6)

    # -- internals ----------------------------------------------------------------------------
    def _in_fault(self, kind: str) -> bool:
        t = self.now()
        return self.fault.kind == kind and self.fault.start_s <= t < self.fault.start_s + self.fault.duration_s

    def _state(self) -> dict:
        s = {k: np.asarray(self._obs[k]).copy() for k in STATE_KEYS}
        s["t_s"] = self.now()
        return s
