"""E20 arm H: the frozen lift policy executed under the existing Haplous runtime.

The E20 driver is presented to `HaplousRuntime` as a leased, asynchronous Machine
(`DriverMachine` / `DriverSession`) on the joint-velocity command surface; the frozen policy is
the ExecutionBehavior. Admission, the ack ledger, the lease, temporal hold, completion and
evidence are all the runtime's. This module only translates between the driver's vocabulary
and Haplous types, and turns the runtime's evidence into the acceptance record.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from benchmarks.e20_shared.acceptance import lifted
from haplous import (
    Ack,
    AckStatus,
    ActionEnvelope,
    ClockDomain,
    CommandMode,
    ExecutionIntent,
    HaplousRuntime,
    JointVelocityTarget,
    MachineCapabilities,
    MachineObservation,
    Requirement,
    RequirementKind,
    SensingChannel,
    Stamp,
    StopResponse,
    SuccessCondition,
)

BEHAVIOR = "e20_lift"
# Integration choices, in control periods: state older than 1.5 periods is stale (also the
# command validity horizon), and the machine may hold its last command for 2 periods untrusted.
STALE_PERIODS = 1.5
HOLD_PERIODS = 2

_ACKS = {"queued": AckStatus.ACCEPTED_FOR_DISPATCH, "accepted": AckStatus.CONTROLLER_ACCEPTED,
         "rejected": AckStatus.REJECTED, "unknown": AckStatus.UNKNOWN}


def _ack(raw: dict) -> Ack:
    return Ack(int(raw["seq"]), _ACKS.get(raw["status"], AckStatus.UNKNOWN), raw.get("reason"))


def to_state(obs: MachineObservation) -> dict:
    """Rebuild the driver's state dict from an observation's flattened channels."""
    groups: dict[str, list[float]] = defaultdict(list)
    for key, value in obs.extra:
        groups[key.rsplit(".", 1)[0]].append(value)
    return {k: np.array(v) for k, v in groups.items()} | {"t_s": obs.stamp.source_s}


class DriverSession:
    """MachineSession + LeasedSession over the driver. Time advances only through `wait()`
    (the TickSource), one driver.step() per control period, up to `max_steps`."""

    def __init__(self, driver, max_steps: int) -> None:
        self._d, self._max_steps, self._steps = driver, max_steps, 0
        self._token: int | None = None
        self.fault: str | None = None

    # -- MachineSession -------------------------------------------------------------------------
    def observe(self) -> MachineObservation | None:
        try:
            state = self._d.read_state()
        except ConnectionError:
            self.fault = "disconnected"
            return None
        extra = tuple((f"{k}.{i}", float(x)) for k, v in state.items() if k != "t_s"
                      for i, x in enumerate(np.ravel(v)))
        return MachineObservation(
            stamp=Stamp(float(state["t_s"]), self._d.now(), ClockDomain.CONTROLLER),
            pose_m=tuple(float(x) for x in state["robot0_eef_pos"]), force_z_n=0.0, tilt_deg=0.0,
            extra=extra)

    def submit(self, envelope: ActionEnvelope) -> Ack:
        p = envelope.payload
        if not isinstance(p, JointVelocityTarget):
            return Ack(envelope.command_id, AckStatus.REJECTED, "payload_kind")
        return _ack(self._d.send(envelope.command_id, envelope.lease_epoch, list(p.velocities_rad_s),
                                 p.gripper, envelope.valid_until_s))

    def poll_acks(self) -> list[Ack]:
        return [_ack(a) for a in self._d.poll_acks()]

    def stop(self) -> None:
        self._d.stop()

    @property
    def ended(self) -> bool:
        return self._steps >= self._max_steps

    # -- LeasedSession --------------------------------------------------------------------------
    def acquire_lease(self) -> int:
        self._token = self._d.acquire_control()
        return self._token

    @property
    def lease_epoch(self) -> int | None:
        return self._token if self._d.control_owner() == self._token else None

    # -- TickSource -----------------------------------------------------------------------------
    def wait(self) -> None:
        self._d.step()
        self._steps += 1


class DriverMachine:
    """Machine over the driver: capabilities are read live from the driver every time."""

    def __init__(self, driver, session: DriverSession) -> None:
        self._d, self._session = driver, session

    def capabilities(self) -> MachineCapabilities:
        c = self._d.capabilities()
        period = float(c["control_period_s"])
        known = {m.value for m in CommandMode}
        return MachineCapabilities(
            command_modes=frozenset(CommandMode(m) for m in c["command_modes"] if m in known),
            sensing=frozenset({SensingChannel.POSE}), max_sensed_force_n=0.0, max_sensed_tilt_deg=0.0,
            position_resolution_m=0.0, state_period_s=period, state_max_age_s=STALE_PERIODS * period,
            max_initial_offset_m=float("inf"), execution_horizon_s=float("inf"), expected_duration_s=0.0,
            supports_stop=True, max_hold_unobserved_s=HOLD_PERIODS * period,
            leased_authority=bool(c.get("exclusive_control")), stop_response=StopResponse.HOLD_POSITION)

    def observe(self) -> MachineObservation | None:
        return self._session.observe()

    def open_session(self, *, start_position_m, horizon_s) -> DriverSession:
        return self._session

    def stop(self) -> None:
        self._d.stop()

    def health(self) -> dict:
        return {"status": "ok"}


class PolicyBehavior:
    """The frozen policy as an ExecutionBehavior: one policy.act() per supervision tick."""

    required_command_modes = frozenset({CommandMode.JOINT_VELOCITY})
    authorized_substitutes: frozenset = frozenset()
    non_substitutable_modes = frozenset({CommandMode.JOINT_VELOCITY})

    def __init__(self, policy, max_joint_speed: float) -> None:
        self._policy, self._speed = policy, max_joint_speed

    def begin(self, intent, caps, observation) -> None:
        pass  # the policy carries its own phase state; the runtime grants no recovery here

    def step(self, observation: MachineObservation) -> JointVelocityTarget:
        action = self._policy.act(to_state(observation))  # normalised controller input + gripper
        return JointVelocityTarget(tuple(float(a) * self._speed for a in action[:6]), float(action[6]))


def run(driver, policy, max_steps: int) -> dict:
    session = DriverSession(driver, max_steps)
    machine = DriverMachine(driver, session)
    speed = float(driver.capabilities()["max_joint_speed_rad_s"])
    runtime = HaplousRuntime(machine, {BEHAVIOR: lambda: PolicyBehavior(policy, speed)}, tick_source=session)
    caps = machine.capabilities()
    start = machine.observe()
    pose = start.pose_m if start is not None else (0.0, 0.0, 0.0)
    intent = ExecutionIntent(
        behavior=BEHAVIOR, goal_description="lift the cube above the table", goal_position_m=pose,
        start_position_m=pose,
        requirements=(Requirement(RequirementKind.STATE_MAX_AGE_S, hard=True, limit=caps.state_max_age_s),),
        success_condition=SuccessCondition("cube_lifted", lambda w: lifted(to_state(w[-1])),
                                           window_s=caps.state_period_s))
    if runtime.submit(intent).outcome != "REFUSED":
        runtime.execute()
    return to_record(runtime)


def to_record(runtime: HaplousRuntime) -> dict:
    events = [{"t_s": e.t_s, "kind": e.kind, "payload": e.payload} for e in runtime.evidence.events]
    outcome = runtime.state.value.upper()
    reason = None
    if outcome != "COMPLETE":
        for e in reversed(events):
            p = e["payload"]
            if p.get("reason") or p.get("reasons"):
                reason = p.get("reason") or ",".join(r["code"] for r in p["reasons"])
                break
        reason = reason or outcome.lower()
    return {"outcome": outcome, "reason": reason, "events": events}
