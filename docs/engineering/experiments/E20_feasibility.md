# E20 feasibility check: UR5e Lift with JOINT_VELOCITY (before pre-registration)

Shared check only; no arm was developed. 2026-11-21, robosuite 1.5.2 (latest on PyPI),
mujoco 3.9.0, venv per `docs/eval_program/REPRODUCE.md` (M5.2).

| Check | Result |
|---|---|
| UR5e Lift instantiates with its default controller (OSC) | yes; `_check_success()` reachable |
| JOINT_VELOCITY controller, UR5e | **fails at construction**: `AttributeError: property 'torque_compensation' of 'JointVelocityController' object has no setter` |
| JOINT_VELOCITY controller, Panda | fails the same way: the defect is robot-independent |

Cause (upstream robosuite 1.5.2, `controllers/parts/generic/joint_vel.py`): `__init__` assigns the
config flag to `self.torque_compensation`, which the base class defines as a read-only property
returning gravity-compensation torques; `run_controller` then tests that array for truth
(`if self.torque_compensation:`), which would also fail. The controller is unusable as released.

Driver: `scripts/feasibility/e20_ur5e_joint_velocity.py` (run with the robosuite venv).
Decision on how to proceed: operator (options in the session record and on #11).
