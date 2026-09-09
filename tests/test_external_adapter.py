"""External adapter boundary: ExternalTask protocol conformance, the
adapter's Machine surface, and a runtime smoke run over a fake external
task. No external framework imports may appear in haplous/."""
import ast
from pathlib import Path

from haplous import (
    ExecutionIntent,
    ExecutionState,
    HaplousRuntime,
    Requirement,
    RequirementKind,
    SuccessCondition,
)
from haplous.adapters.external import ExternalMachineAdapter
from haplous.behavior import ExecutionBehavior
from haplous.capabilities import (
    CommandMode,
    MachineCapabilities,
    SensingChannel,
)
from haplous.machine import (
    CommandOutcome,
    MachineObservation,
    ServoCommand,
    Stamp,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeExternalTask:
    """Deterministic one-dimensional external task: the tracked pose moves
    to the commanded target in 1 mm steps; commands larger than 3.5 mm
    from the current pose are rejected. Force ramps 0.5 N/tick, capped at
    12 N, always below the intent's 15 N limit. The session reports
    horizon exhaustion after 120 ticks.

    ground_truth()/task_result() are grader-owned state the runtime must
    never consume (they are not on the Machine protocol)."""

    task_id = "fake_external_slide"

    def __init__(self, period_s: float = 0.001) -> None:
        self._period = period_s
        self._tick = 0
        self._pose = 0.005
        self._force = 0.0
        self._fault: str | None = None

    def reset(self, *, seed: int) -> None:
        self._tick = 0
        self._pose = 0.005
        self._force = 0.0
        self._fault = None

    def capabilities(self) -> MachineCapabilities:
        return MachineCapabilities(
            command_modes=frozenset({CommandMode.CARTESIAN_IMPEDANCE}),
            sensing=frozenset(
                {SensingChannel.POSE, SensingChannel.FORCE_Z, SensingChannel.TILT}
            ),
            max_sensed_force_n=25.0,
            max_sensed_tilt_deg=10.0,
            position_resolution_m=1e-6,
            state_period_s=self._period,
            state_max_age_s=0.05,
            max_initial_offset_m=0.01,
            execution_horizon_s=5.0,
            expected_duration_s=1.0,
            supports_stop=True,
        )

    def observe(self) -> MachineObservation:
        t = self._tick * self._period
        return MachineObservation(
            stamp=Stamp(source_s=t, received_s=t),
            pose_m=(0.0, 0.0, self._pose),
            force_z_n=self._force,
            tilt_deg=0.0,
        )

    def apply_command(self, command: ServoCommand) -> CommandOutcome:
        target_z = command.target_pose_m[2]
        if abs(target_z - self._pose) > 0.0035:
            return CommandOutcome(accepted=False, reason="step_too_large")
        self._pose = target_z
        self._tick += 1
        self._force = min(12.0, 0.5 * self._tick)
        return CommandOutcome(accepted=True)

    def step(self) -> None:
        if self._tick >= 120:
            self._fault = "horizon_exhausted"

    def stop(self) -> None:
        pass

    def session_ended(self) -> bool:
        return self._fault is not None

    def session_fault(self) -> str | None:
        return self._fault

    def ground_truth(self) -> dict[str, float]:
        return {"true_offset_m": 0.0}

    def task_result(self) -> dict[str, bool]:
        return {"success": self._pose <= 0.0025}


def _fake_intent() -> ExecutionIntent:
    return ExecutionIntent(
        behavior="external_slide",
        goal_description="descend 3 mm on the fake external task",
        goal_position_m=(0.0, 0.0, 0.002),
        start_position_m=(0.0, 0.0, 0.005),
        requirements=(
            Requirement(
                kind=RequirementKind.MAX_PROCESS_FORCE_N, hard=True, limit=15.0
            ),
            Requirement(
                kind=RequirementKind.STATE_MAX_AGE_S, hard=True, limit=0.05
            ),
            Requirement(kind=RequirementKind.DEADLINE_S, hard=False, limit=3.0),
        ),
        success_condition=SuccessCondition(
            name="descended",
            evaluate=lambda obs: obs[-1].pose_m[2] <= 0.0025,
            window_s=None,
        ),
    )


class _HoldBehavior(ExecutionBehavior):
    def begin(self, intent, caps, observation) -> None:
        self._target = intent.goal_position_m

    def step(self, observation) -> ServoCommand:
        return ServoCommand(target_pose_m=self._target, k_z_n_per_m=500.0)


def test_external_task_protocol_conformance():
    task = FakeExternalTask()
    assert task.task_id == "fake_external_slide"
    task.reset(seed=3)
    obs = task.observe()
    assert obs.pose_m[2] == 0.005
    outcome = task.apply_command(ServoCommand((0.0, 0.0, 0.004), 500.0))
    assert outcome.accepted
    rejection = task.apply_command(ServoCommand((0.0, 0.0, 0.009), 500.0))
    assert not rejection.accepted and rejection.reason == "step_too_large"


def test_adapter_satisfies_machine_surface():
    adapter = ExternalMachineAdapter(FakeExternalTask())
    session = adapter.open_session(
        start_position_m=(0.0, 0.0, 0.005), horizon_s=5.0
    )
    assert session.observe().pose_m[2] == 0.005
    assert session.apply(ServoCommand((0.0, 0.0, 0.004), 500.0)).accepted
    session.step()
    assert not session.ended
    adapter.stop()


def test_runtime_runs_over_external_adapter():
    task = FakeExternalTask()
    task.reset(seed=3)
    machine = ExternalMachineAdapter(task)
    runtime = HaplousRuntime(machine, behaviors={"external_slide": _HoldBehavior})
    feasibility = runtime.submit(_fake_intent())
    assert feasibility.outcome == "SUPPORTED"
    evidence = runtime.execute()
    assert runtime.state == ExecutionState.COMPLETE
    assert any(e.kind == "execution_authorized" for e in evidence.events)


def test_no_external_framework_imports_in_haplous_package():
    banned = {"robosuite", "isaacsim", "isaaclab", "pycontact", "contactbench"}
    for path in sorted((ROOT / "haplous").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                assert root not in banned, f"{path} imports {module}"
