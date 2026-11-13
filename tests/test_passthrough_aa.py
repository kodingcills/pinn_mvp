"""A/A: the pass-through path must reproduce the direct arm exactly."""
import dataclasses

import pytest

from benchmarks.passthrough import (
    PassThroughMachine,
    PassThroughSession,
    PassThroughSystem,
    aa_compare,
    run_trajectory,
)
from scripts.passthrough_aa import scenarios

SCENARIOS = scenarios()
CAP_S = 3.0  # full-horizon A/A is in scripts/passthrough_aa.py


def test_both_families_present():
    assert any(s.path_end_m is None for s in SCENARIOS)
    assert any(s.path_end_m is not None for s in SCENARIOS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_passthrough_identical_to_direct(scenario):
    row = aa_compare(scenario, horizon_s=CAP_S)
    assert row["identical"], row["differing_fields"]
    assert row["n_observations"] > 0 and row["n_commands"] > 0


class _NudgedSession(PassThroughSession):
    """Planted defect: perturbs one command by 1e-9 m."""

    def apply(self, command):
        if self.calls["apply"] == 5:
            x, y, z = command.target_pose_m
            command = dataclasses.replace(command, target_pose_m=(x, y, z + 1e-9))
        return super().apply(command)


class _NudgedSystem(PassThroughSystem):
    def __init__(self):
        super().__init__(
            machine_cls=lambda m: PassThroughMachine(m, session_cls=_NudgedSession)
        )


def test_planted_one_command_perturbation_is_detected():
    s = next(s for s in SCENARIOS if s.id == "insert_nominal")
    row = aa_compare(s, passthrough_cls=_NudgedSystem, horizon_s=CAP_S)
    assert not row["identical"]


def test_passthrough_forwards_every_command_and_counts():
    s = next(s for s in SCENARIOS if s.id == "insert_command_rejection_transient")
    system = PassThroughSystem()
    traj = run_trajectory(s, system, CAP_S)
    (session,) = system.machine.sessions
    assert session.calls["apply"] == len(traj.commands) == len(session.forwarded)
    assert [c for c, _ in session.forwarded] == list(traj.commands)
    assert any(not o.accepted for _, o in session.forwarded)
