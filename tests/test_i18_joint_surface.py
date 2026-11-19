"""Issue #18: the joint-position command surface through admission, session
payload checks, the runtime's contract guard and Studio's read side. The
robosuite episode itself runs in its own venv (scripts/i18_joint_surface.py);
everything here runs without robosuite."""
from __future__ import annotations

import json

import pytest

from benchmarks.external.robosuite_lift_joint import JointLiftPolicy, RobosuiteLiftJointTask
from benchmarks.external.robosuite_lift_policy import LiftPolicy
from haplous.adapters.external import ExternalMachineAdapter
from haplous.adapters.legacy import CommandOutcome, LegacySession, sim_runtime
from haplous.adapters.mujoco_adapter import MuJoCoMachine
from haplous.capabilities import CommandMode, StopResponse
from haplous.machine import (
    Ack,
    AckStatus,
    ActionEnvelope,
    CartesianImpedanceTarget,
    ClockDomain,
    JointPositionTarget,
)
from haplous.state import ExecutionState
from pegsim.rollout import VALIDATED_LOOSE_V1
from tests.test_haplous_semantics import (
    HoldBehavior,
    ScriptedMachine,
    make_intent,
    obs,
    outcome_payload,
    seated_samples,
    std_caps,
    stream,
)

CI = CommandMode.CARTESIAN_IMPEDANCE
FCD = CommandMode.FORCE_CONTROLLED_DESCENT
JP = CommandMode.JOINT_POSITION
JOINT_TARGET = JointPositionTarget(positions_rad=(0.0,) * 7)
CART_TARGET = CartesianImpedanceTarget(target_pose_m=(0.0, 0.0, -0.01), k_z_n_per_m=500.0)


class CountingJointMachine(ExternalMachineAdapter):
    """The robosuite joint-position machine, unreset: admission reads only
    capabilities(), so no simulator is needed to decide it."""

    def __init__(self) -> None:
        super().__init__(RobosuiteLiftJointTask(lambda: -1.0))
        self.open_session_calls = 0

    def open_session(self, **kwargs):
        self.open_session_calls += 1
        return super().open_session(**kwargs)


def _unsupported(feasibility):
    return [dict(r.data) for r in feasibility.reasons if r.code == "unsupported_command_mode"]


def test_joint_machine_declares_only_the_joint_surface_and_its_stop():
    caps = CountingJointMachine().capabilities()
    assert caps.command_modes == frozenset({JP})
    assert caps.stop_response is StopResponse.HOLD_POSITION
    assert JointLiftPolicy.required_command_modes == frozenset({JP})


def test_cartesian_only_machine_refuses_the_joint_contract_with_no_session():
    machine = MuJoCoMachine(VALIDATED_LOOSE_V1)
    assert JP not in machine.capabilities().command_modes
    runtime = sim_runtime(machine, behaviors={"motion": JointLiftPolicy})
    feasibility = runtime.submit(make_intent())
    assert feasibility.outcome == "REFUSED"
    [refusal] = _unsupported(feasibility)
    assert refusal["required"] == "joint_position"
    assert refusal["available"] == ["cartesian_impedance", "force_controlled_descent"]
    assert refusal["substitute_considered"] is None
    assert machine.open_session_calls == []
    assert runtime.state is ExecutionState.REFUSED


@pytest.mark.parametrize("behavior", [LiftPolicy, HoldBehavior])
def test_joint_only_machine_refuses_cartesian_contracts_with_no_session(behavior):
    machine = CountingJointMachine()
    runtime = sim_runtime(machine, behaviors={"motion": behavior})
    feasibility = runtime.submit(make_intent())
    assert feasibility.outcome == "REFUSED"
    assert {r["required"] for r in _unsupported(feasibility)} == {"cartesian_impedance"}
    assert machine.open_session_calls == 0


def test_joint_only_machine_admits_the_joint_contract():
    runtime = sim_runtime(CountingJointMachine(), behaviors={"motion": JointLiftPolicy})
    feasibility = runtime.submit(make_intent())
    assert not _unsupported(feasibility)
    stop = next(e for e in runtime.evidence.events if e.kind == "feasibility").payload["capabilities"]
    assert stop["stop_response"] == "hold_position"
    assert stop["command_modes"] == ["joint_position"]


class _Applied:
    def __init__(self) -> None:
        self.applied: list = []

    def apply(self, command):
        self.applied.append(command)
        return CommandOutcome(accepted=True)


def _envelope(payload):
    return ActionEnvelope(1, 0, 0.0, ClockDomain.SIM, 0.02, payload, "x")


@pytest.mark.parametrize("modes, payload, status, reason", [
    (frozenset({CI, FCD}), JOINT_TARGET, AckStatus.REJECTED, "unsupported_payload"),
    (frozenset({JP}), CART_TARGET, AckStatus.REJECTED, "unsupported_payload"),
    (frozenset({JP}), JOINT_TARGET, AckStatus.CONTROLLER_ACCEPTED, None),
])
def test_session_accepts_only_payloads_of_declared_modes(modes, payload, status, reason):
    inner = _Applied()
    assert LegacySession(inner, modes).submit(_envelope(payload)) == Ack(1, status, reason)
    assert inner.applied == ([payload] if reason is None else [])


class JointDeclaredCartesianEmitter(HoldBehavior):
    """Declares the joint surface, emits Cartesian targets."""

    required_command_modes = frozenset({JP})


def test_command_outside_the_contract_aborts_before_dispatch_on_a_dual_mode_machine():
    # Not yet seated, so the first tick dispatches instead of completing.
    machine = ScriptedMachine(caps=std_caps(command_modes=frozenset({CI, JP})), observation=obs(0.0),
                              script=stream(seated_samples(z=0.0, fz=0.0)))
    runtime = sim_runtime(machine, behaviors={"motion": JointDeclaredCartesianEmitter})
    assert runtime.submit(make_intent()).outcome != "REFUSED"
    evidence = runtime.execute()
    assert outcome_payload(evidence)["reason"] == "command_contract_violated"
    violation = next(e for e in evidence.events if e.kind == "command_contract_violated").payload
    assert violation == {"payload_kind": "CartesianImpedanceTarget", "required": ["joint_position"]}
    assert machine.applied_commands == [], "no command reached the machine"
    assert machine.stop_calls >= 1


class JointHold(HoldBehavior):
    required_command_modes = frozenset({JP})

    def step(self, observation):
        return JOINT_TARGET


@pytest.mark.parametrize("behavior, modes, kinds", [
    (JointHold, frozenset({JP}), ["JointPositionTarget"]),
    # Absent on the legacy surface: sealed recordings keep their bytes.
    (HoldBehavior, frozenset({CI}), None),
])
def test_motion_started_records_a_non_legacy_payload_kind(behavior, modes, kinds):
    machine = ScriptedMachine(caps=std_caps(command_modes=modes), observation=obs(0.0),
                              script=stream(seated_samples(z=0.0, fz=0.0)))
    runtime = sim_runtime(machine, behaviors={"motion": behavior})
    runtime.submit(make_intent())
    started = next(e for e in runtime.execute().events if e.kind == "motion_started").payload
    assert started.get("payload_kinds") == kinds


def test_studio_and_timeline_read_a_log_with_joint_commands(tmp_path):
    from evaluation import timeline
    from studio.data import series

    rows = [
        {"tick": 0, "kind": "observation",
         "observation": {"pose_m": [0.0, 0.0, 1.0], "force_z_n": 0.0, "tilt_deg": 0.0, "extra": {},
                         "stamp": {"source_s": 0.0, "received_s": 0.0}}},
        {"tick": 0, "kind": "command", "command": {"positions_rad": [0.0] * 7},
         "outcome": {"accepted": True, "reason": None}},
    ]
    (tmp_path / timeline.TELEMETRY).write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / timeline.EVENTS).write_text("")
    (tmp_path / timeline.METADATA).write_text(json.dumps({"timestep_s": 0.05, "n_observations": 1, "n_ticks": 1}))
    assert timeline.load(tmp_path)["target_pose_m"] == []
    assert series(tmp_path, 0.05)["command_target_pose_m"] == [None]


def test_policy_kinematics_match_the_repo_panda_model():
    """JointLiftPolicy's DH chain is the geometry of panda_inertial.xml."""
    from pathlib import Path

    import mujoco
    import numpy as np

    from benchmarks.external import robosuite_lift_joint as rj

    model = mujoco.MjModel.from_xml_path(str(Path(rj.__file__).parents[1] / "realdata" / "panda_inertial.xml"))
    data = mujoco.MjData(model)
    hand = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "hand")
    rng = np.random.default_rng(18)
    for _ in range(20):
        q = rng.uniform(model.jnt_range[:, 0], model.jnt_range[:, 1])
        data.qpos[:] = q
        mujoco.mj_kinematics(model, data)
        xmat = data.xmat[hand].reshape(3, 3)
        tip = rj.BASE_POS_M + data.xpos[hand] + xmat @ (0.0, 0.0, 0.097)
        got, _, jac = rj._kinematics(q)
        assert np.allclose(got, tip, atol=1e-9)
        eps = 1e-6
        numeric = np.column_stack([(rj._kinematics(q + eps * e)[0] - got) / eps for e in np.eye(7)])
        assert np.allclose(jac[:3], numeric, atol=1e-5)
