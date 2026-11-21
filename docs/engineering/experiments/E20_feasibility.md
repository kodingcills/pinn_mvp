# E20 feasibility gate: UR5e Lift with JOINT_VELOCITY (before pre-registration)

Shared check only; no experiment arm was developed. Coordinator, 2026-11-21. robosuite 1.5.2
(latest on PyPI, pinned), mujoco 3.9.0, robosuite venv per `docs/eval_program/REPRODUCE.md` (M5.2).

## 1. Upstream defect and vendor patch

As released, robosuite 1.5.2's `JointVelocityController` cannot be constructed for any robot
(UR5e and Panda both fail): `__init__` assigns the config flag to `self.torque_compensation`, a
read-only base-class property that returns gravity-compensation torques, and `run_controller`
then tests that array for truth. The operator reports two open upstream fixes (PR #794, #795);
the coordinator did not inspect them (GitHub access in this session is limited to this repo).

The fix is two lines and leaves the control law untouched: the flag is stored as
`self.use_torque_compensation` and `run_controller` tests the flag.

- Patch: `patches/robosuite-1.5.2-joint-velocity.patch`.
- Apply: `<venv>/bin/python scripts/apply_robosuite_patch.py`. It refuses any robosuite other than
  1.5.2 and any `joint_vel.py` that is neither the pristine file
  (sha256 `4e8e5c15…19fc1`) nor the patched one (`53801168…f8f4da`), and is idempotent.
- Guard: `benchmarks.e20_shared.assert_patched_robosuite()` raises unless robosuite is 1.5.2
  with the patched file. Both arms must call it first, so they run the identical dependency.

## 2. Controller feasibility (`scripts/feasibility/e20_ur5e_joint_velocity.py` → `results/e20/feasibility.json`)

| Check | Result |
|---|---|
| Construction (UR5e Lift, JOINT_VELOCITY, 20 Hz, action dim 7) | yes |
| Tracking, every arm joint, +0.3 / −0.3 / 0 rad/s, commanded vs observed traces recorded | signs correct on all 18 cases; steady mean error 0.0004–0.0011 rad/s; zero command stays at 0 |
| Saturation (action 5.0, input range ±1) | commanded velocity clipped to 0.5 rad/s (observed max 0.503); torques within actuator limits |
| Gravity compensation enabled vs disabled (2 s zero-velocity hold) | max joint drift 0.0000 vs 0.0056 rad: the restored flag takes effect |
| Finite simulation state (qpos, qvel, qacc) | finite in every case |
| Independent task grader (`env._check_success()`) | reachable; false at reset |

A re-run reproduces the result file identically.

## 3. Shared scripted policy (`benchmarks/e20_shared/lift_policy.py`, frozen)

Joint-velocity resolved-rate control (damped least squares on the robot's own kinematic model)
with position and orientation targets: approach above the cube, descend, close, lift. It reads
only observations and the robot model, never the grader or simulator state.

Development (coordinator only, before freezing): position-only control lifted the cube in 2 of 5
seeds; orientation control with the measured 45 deg offset between the end-effector frame and the
finger axis gave 5/5 (0 deg offset: 0/5).

Nominal validation (`scripts/feasibility/e20_shared_policy_nominal.py` →
`results/e20/shared_policy_nominal.json`), seeds 0–9: **9/10 successes**, 3.95–4.3 s sim time,
finite state throughout. Seed 8 fails identically for any arm: orientation is correct but the
grasp slips after lifting the cube about 2 mm. The policy is frozen as is; seed 8 is excluded
from nominal acceptance seeds rather than tuned around.

**Gate: PASS.** No further vendor change was needed; the control surface is unchanged.
