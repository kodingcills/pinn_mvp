"""Stub-machine contract: admission, refusal and bounded abort through the
public runtime API with no simulator. Runs on a bare `pip install haplous`
(stdlib only). Prints one JSON document; exits non-zero if the contract
does not hold."""
from __future__ import annotations

import json
import sys

from haplous import (
    CommandMode,
    CommandOutcome,
    ExecutionIntent,
    HaplousRuntime,
    MachineCapabilities,
    MachineObservation,
    Requirement,
    RequirementKind,
    SensingChannel,
    ServoCommand,
    Stamp,
    SuccessCondition,
)

PERIOD_S = 0.005
ALL_SENSING = frozenset({SensingChannel.POSE, SensingChannel.FORCE_Z, SensingChannel.TILT})


class StubSession:
    """Reports a constant pose and axial force on a clock it advances per step."""

    def __init__(self, force_z_n: float, horizon_s: float) -> None:
        self.force_z_n, self.horizon_s, self.t_s, self.ended, self.fault = force_z_n, horizon_s, 0.0, False, None

    def observe(self) -> MachineObservation:
        return MachineObservation(Stamp(self.t_s, self.t_s), (0.0, 0.0, -0.010), self.force_z_n, 0.0)

    def apply(self, command: ServoCommand) -> CommandOutcome:
        return CommandOutcome(accepted=not self.ended, reason="session_ended" if self.ended else None)

    def step(self) -> None:
        self.t_s += PERIOD_S
        self.ended = self.ended or self.t_s >= self.horizon_s

    def stop(self) -> None:
        self.ended = True


class StubMachine:
    def __init__(self, force_z_n: float, sensing: frozenset = ALL_SENSING) -> None:
        self.force_z_n, self.stop_calls = force_z_n, 0
        self.caps = MachineCapabilities(
            command_modes=frozenset({CommandMode.CARTESIAN_IMPEDANCE}), sensing=sensing,
            max_sensed_force_n=100.0, max_sensed_tilt_deg=45.0, position_resolution_m=1e-6,
            state_period_s=PERIOD_S, state_max_age_s=0.02, max_initial_offset_m=0.004,
            execution_horizon_s=2.0, expected_duration_s=1.0, supports_stop=True,
        )

    def capabilities(self) -> MachineCapabilities:
        return self.caps

    def observe(self) -> MachineObservation:
        return MachineObservation(Stamp(0.0, 0.0), (0.0, 0.0, -0.010), self.force_z_n, 0.0)

    def open_session(self, *, start_position_m, horizon_s) -> StubSession:
        return StubSession(self.force_z_n, horizon_s)

    def stop(self) -> None:
        self.stop_calls += 1

    def health(self) -> dict:
        return {"status": "ok"}


class HoldBehavior:
    required_command_modes = frozenset({CommandMode.CARTESIAN_IMPEDANCE})
    authorized_substitutes: frozenset = frozenset()
    non_substitutable_modes: frozenset = frozenset()

    def begin(self, intent, caps, observation) -> None:
        pass

    def step(self, observation: MachineObservation) -> ServoCommand:
        return ServoCommand(target_pose_m=observation.pose_m, k_z_n_per_m=1000.0)


def run_leg(machine: StubMachine) -> dict:
    intent = ExecutionIntent(
        behavior="hold", goal_description="hold seated under 25 N",
        goal_position_m=(0.0, 0.0, 0.0), start_position_m=(0.0, 0.0, 0.0),
        requirements=(
            Requirement(RequirementKind.MAX_PROCESS_FORCE_N, hard=True, limit=25.0),
            Requirement(RequirementKind.DEADLINE_S, hard=True, limit=2.0),
        ),
        success_condition=SuccessCondition("seated", lambda obs: bool(obs) and obs[-1].pose_m[2] <= -0.009),
        recovery_policy=None,
    )
    runtime = HaplousRuntime(machine, behaviors={"hold": HoldBehavior})
    feasibility = runtime.submit(intent)
    if feasibility.outcome != "REFUSED":
        runtime.execute()
    evidence = runtime.evidence
    outcomes = [e.payload for e in evidence.events if e.kind == "outcome"]
    return {
        "feasibility": feasibility.outcome,
        "reasons": [r.code for r in feasibility.reasons],
        "final_state": runtime.state.value,
        "outcome": outcomes[-1] if outcomes else None,
        "violations": [v["code"] for v in evidence.constraint_violations()],
        "machine_stop_calls": machine.stop_calls,
        "evidence_event_kinds": sorted({e["kind"] for e in evidence.to_json_dict()["events"]}),
    }


def main() -> int:
    report = {
        "admitted": run_leg(StubMachine(force_z_n=15.0)),
        "refused": run_leg(StubMachine(force_z_n=15.0, sensing=frozenset({SensingChannel.POSE}))),
        "bounded_abort": run_leg(StubMachine(force_z_n=30.0)),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    ok = (
        report["admitted"]["feasibility"] == "SUPPORTED" and report["admitted"]["final_state"] == "complete"
        and report["refused"]["final_state"] == "refused" and "missing_force_measurement" in report["refused"]["reasons"]
        and report["bounded_abort"]["final_state"] == "aborted"
        and report["bounded_abort"]["violations"] == ["process_force"]
        and report["bounded_abort"]["machine_stop_calls"] == 1
    )
    print("stub contract:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
