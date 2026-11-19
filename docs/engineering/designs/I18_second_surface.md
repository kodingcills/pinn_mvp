# I18: second command surface (joint position)

Issue #18 (runtime + evaluation, Gate B). Base `76063e8`. Implements the second payload of
ARCH-001 decision 3 on top of the #16 boundary and the #17 lifecycle.

## Spike and decision

One bounded spike (`scratchpad/i18/spike1.py`, `spike2.py`, not committed) compared the two
candidates the issue names.

| | (a) robosuite Lift, `JOINT_POSITION` | (b) native MuJoCo Panda from `panda_inertial.xml` |
|---|---|---|
| Task and grader | Existing: robosuite Lift, graded by robosuite's own `_check_success` | None: the XML has no geoms, no gripper and no task. The insertion geometry and the HEB grader assume the pegsim free-floating peg |
| Controller | robosuite's `JointPositionController` source, as shipped | MuJoCo position actuators: a gain on an actuator, not a controller with its own semantics |
| New code | A task subclass, a policy and a driver | A scene, a peg-on-flange attachment, a task binding and a grader port |
| Venv | Separate (robosuite 1.5.2 needs mujoco 3.9 and numpy < 2) | Main |

The spike measured three things in option (a). The controller accepts `input_type="absolute"`,
so a joint target is its setpoint, and it tracks a 0.3 rad step to within 1.7 mrad. The
policy's own kinematic model (`panda_inertial.xml`, mount at (-0.56, 0, 0.912), grip site
0.097 m along the hand axis) agrees with robosuite's end-effector position within 0.5 mm. A
joint-space scripted Lift succeeds on seeds 0, 1 and 2, at 3.0 s to 3.5 s of sim time.

**Decision: (a).** It is the only option that reuses an existing task and an independent
grader, and the controller is real controller software rather than a stiffness parameter.

**What "real controller" means here.** It is robosuite 1.5.2's `JointPositionController`
with its packaged defaults: kp=50, damping ratio 1, setpoint error decoupled through the
mass matrix plus gravity compensation, and torques clipped to the Panda actuator ranges. It
runs in simulated physics. It is not a Franka FCI joint impedance loop, and nothing here is
evidence about a physical Panda (HARDWARE_NOT_VALIDATED).

## Types

- `haplous/machine.py` adds `JointPositionTarget(positions_rad: tuple[float, ...])`, one
  absolute setpoint per joint in the controller's joint order. It also adds
  `CommandPayload = CartesianImpedanceTarget | JointPositionTarget`, the type of
  `ActionEnvelope.payload`.
- `haplous/capabilities.py` adds `CommandMode.JOINT_POSITION`. It adds `PAYLOAD_MODES`, which
  maps each payload type to the modes it realizes (Cartesian maps to impedance and
  force-controlled descent, joint maps to joint position). It also adds
  `StopResponse {ACTUATION_OFF, HOLD_POSITION}` and
  `MachineCapabilities.stop_response = ACTUATION_OFF`.
- Interrupt semantics on the joint surface work as follows. A newer accepted target replaces
  the old one at the next control step, with no blending. A temporal hold keeps servoing to
  the last accepted target, which is the existing protocol rule. `stop()` latches the
  measured joint position and keeps servoing to it while the world keeps advancing
  (`HOLD_POSITION`). The MuJoCo Cartesian machine keeps `ACTUATION_OFF`, because its stop
  zeroes the applied wrench.

## Guards (new)

1. **Session payload check.** `LegacySession` rejects with `unsupported_payload` any payload
   whose `PAYLOAD_MODES` do not meet the machine's declared modes. This replaces the
   hard-coded Cartesian `isinstance` check. `LegacySession(inner, command_modes)` now takes the
   machine's modes.
2. **Runtime contract guard.** If a behaviour emits a payload whose modes do not meet its
   own `required_command_modes`, the runtime aborts before dispatch with
   `command_contract_violated` (event plus outcome). Before this change only one payload type
   existed, so the hole could not occur. With two surfaces, a machine offering both would
   otherwise execute an unauthorized substitution silently.

## Evidence

- The `feasibility.capabilities` record gains `stop_response`.
- `motion_started` gains `payload_kinds`, the payload types the behaviour's contract admits.
  Guard 2 holds every dispatched command to them.
- The episode log's command rows carry the payload as written (`positions_rad` or
  `target_pose_m`). `evaluation.timeline` and Studio's `series` plot Cartesian targets only,
  and they now skip joint rows instead of raising.

## Machine and behaviour

`benchmarks/external/robosuite_lift_joint.py` holds three pieces:

- `RobosuiteLiftJointTask` subclasses the Lift task. It switches the arm to the packaged
  `JOINT_POSITION` config with absolute input and declares `{JOINT_POSITION}` and
  `HOLD_POSITION`. Its observations add `q0..q6` and drop `task_success`, which is grader
  truth.
- `JointLiftPolicy` uses the same phases as `LiftPolicy`. On each tick it takes one damped
  least-squares step on its own Panda model, closed on the observed grip position.
- `lift_joint_intent` completes when the observed cube height is above 0.84 m. That is the
  height Lift itself uses, but the grader is still `_check_success`.

The task submits through `ExternalMachineAdapter`, `LegacyMachine` and `LegacySession`.
Acks are synchronous (`CONTROLLER_ACCEPTED` or `REJECTED` at submit) and pass through the #17
`CommandLedger`, which is adequate for a sim controller stepped by `SimTickSource`. The
gripper command uses the same side channel as the Cartesian `LiftPolicy` (the task reads
`policy.gripper_action`), so it is not inside the envelope. That is a known limit.

## Parity and scoring epoch

`scripts/od1_parity.py --before 76063e8 --out results/b4/parity.json` gives 108/108 identical
(after side `79778e2`). Cartesian behaviours emit `CartesianImpedanceTarget`, whose modes
include their contracts, so guard 2 is never reached. MuJoCo machines declare Cartesian
modes, so guard 1 behaves exactly like the old `isinstance` check. No new scoring epoch is
needed.
