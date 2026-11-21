"""E20 arm G: the frozen Lift policy supervised by g1lib through a joint-velocity driver adapter.

The adapter targets the vendor driver interface (capabilities, acquire_control, control_owner,
read_state, send, poll_acks, stop, step, now), not this task: holding on a velocity interface
means commanding zero joint velocity, a disconnect is a machine fault, and the exclusive control
token is the authority.
"""
from __future__ import annotations

from benchmarks.e20_shared import assert_patched_robosuite
from benchmarks.e20_shared.acceptance import lifted
from benchmarks.g1lib import Ack, Sample, Sent, Supervisor, Task

HOLD_BUDGET_S = 0.5          # state or ack loss tolerated under a zero-velocity hold before stopping
VALIDITY_PERIODS = 4         # command validity horizon, in control periods


class JointVelocityDriverAdapter:
    """g1lib adapter for an asynchronous joint-velocity driver with an exclusive control token.
    Commands are ``(joint_velocities_rad_s, gripper)``."""

    def __init__(self, driver, max_steps: int) -> None:
        self.driver, self.max_steps = driver, max_steps
        self.caps = driver.capabilities()
        self._token = None
        self._steps = self._seq = 0
        self._link: str | None = None
        self._gripper: float | None = None

    def now(self) -> float:
        return self.driver.now()

    def _read(self) -> Sample | None:
        try:
            state = self.driver.read_state()
        except ConnectionError:
            self._link = "disconnected"
            return None
        return Sample(state["t_s"], self.driver.now() - state["t_s"], state)

    peek = sample = _read

    def open(self) -> None:
        self._token = self.driver.acquire_control()

    def health(self) -> str | None:
        return self._link

    def has_authority(self) -> bool:
        return self.driver.control_owner() == self._token

    def command_modes(self):
        return self.driver.capabilities()["command_modes"]

    def send(self, command, valid_until_s: float) -> Sent:
        joint_vel, gripper = command
        self._seq += 1
        self._gripper = gripper
        reply = self.driver.send(self._seq, self._token, list(joint_vel), gripper, valid_until_s)
        if reply.get("reason") == "disconnected":
            self._link = "disconnected"
        return Sent("rejected" if reply["status"] == "rejected" else "pending", self._seq)

    def poll_acks(self) -> list[Ack]:
        return [Ack(a["seq"], a["status"]) for a in self.driver.poll_acks()]

    def hold_command(self):
        if self._gripper is None:
            return None  # nothing commanded yet: the arm is at rest
        return [0.0] * self.caps["joints"], self._gripper

    def stop(self) -> None:
        self.driver.stop()

    def step(self) -> None:
        self.driver.step()
        self._steps += 1

    def ended(self) -> bool:
        return self._steps >= self.max_steps

    def fault(self) -> str | None:
        return self._link


class PolicyBehavior:
    """The frozen policy (normalised joint velocities + gripper) as a g1lib Behavior."""

    required_modes = frozenset({"joint_velocity"})

    def __init__(self, policy, max_joint_speed: float, joints: int) -> None:
        self.policy, self.speed, self.joints = policy, max_joint_speed, joints

    def begin(self, state) -> None:
        pass

    def step(self, state):
        action = self.policy.act(state)
        return [a * self.speed for a in action[: self.joints]], float(action[self.joints])


def run(driver, policy, max_steps: int) -> dict:
    assert_patched_robosuite()
    adapter = JointVelocityDriverAdapter(driver, max_steps)
    caps = adapter.caps
    period = caps["control_period_s"]
    behavior = PolicyBehavior(policy, caps["max_joint_speed_rad_s"], caps["joints"])
    task = Task(
        required_modes=behavior.required_modes,
        offered_modes=caps["command_modes"],
        max_state_age_s=period,
        max_untrusted_ticks=int(round(HOLD_BUDGET_S / period)),
        complete=lambda history: lifted(history[-1]),
        command_validity_s=VALIDITY_PERIODS * period,
    )
    out = Supervisor().run(adapter, behavior, task)
    return {"outcome": out.disposition, "reason": out.reason, "events": out.events}
