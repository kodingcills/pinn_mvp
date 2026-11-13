"""Negative test (M3.3): an unsupported capability claim must not be scored as supported.

Planted defects run through the real HEB MuJoCo path: a machine whose
capabilities() makes a command-mode claim it cannot back. Track B's
mode-substitution rule (no silent downgrade, no unverified alias) is already
property-tested in tests/test_track_b.py::test_no_silent_downgrade.
"""
from __future__ import annotations

import dataclasses

import pytest

from benchmarks.heb import mujoco_backend, systems
from benchmarks.heb.insertion import insertion_scenarios
from haplous.adapters.mujoco_adapter import MuJoCoMachine
from haplous.capabilities import CommandMode

SCENARIO = {s.id: s for s in insertion_scenarios()}["insert_dropout_large"]


def _run_mediated(command_modes):
    class ClaimingMachine(MuJoCoMachine):
        def capabilities(self):
            return dataclasses.replace(super().capabilities(), command_modes=command_modes)

    realization = mujoco_backend.build_realization(SCENARIO, machine_factory=ClaimingMachine)
    return systems.HaplousSystem().run(realization, SCENARIO), realization.machine


def test_baseline_machine_completes():
    # The standard MuJoCoMachine's own claim: both modes the behavior uses.
    run, _ = _run_mediated(
        frozenset({CommandMode.CARTESIAN_IMPEDANCE, CommandMode.FORCE_CONTROLLED_DESCENT})
    )
    assert run.disposition == "COMPLETE"


def test_machine_offering_no_command_mode_is_refused_before_motion():
    run, machine = _run_mediated(frozenset())
    assert run.disposition == "REFUSED"
    assert run.refusal_codes == ("unsupported_control_mode",)
    assert machine.open_session_calls == []


@pytest.mark.parametrize("modes", [
    frozenset({CommandMode.FORCE_CONTROLLED_DESCENT}),
    frozenset({CommandMode.CARTESIAN_IMPEDANCE}),
    frozenset({"cartesian_impedance"}),
], ids=["descent_only", "impedance_only", "str_alias"])
def test_negotiation_refuses_a_mode_the_behavior_cannot_use(modes):
    # OD-1: the insertion behaviour requires both command modes; a str is
    # never an alias for a CommandMode. Refused at submit, not crashed later.
    run, machine = _run_mediated(modes)
    assert run.disposition == "REFUSED"
    assert run.detail["refusal_stage"] == "submit"
    assert set(run.refusal_codes) == {"unsupported_command_mode"}
    assert machine.open_session_calls == []


def test_descent_only_refusal_is_diagnosable():
    from haplous.capabilities import negotiate

    realization = mujoco_backend.build_realization(SCENARIO)
    caps = dataclasses.replace(
        realization.machine.capabilities(),
        command_modes=frozenset({CommandMode.FORCE_CONTROLLED_DESCENT}),
    )
    intent = systems.build_intent(SCENARIO, realization.condition_factory(SCENARIO, realization.bundle))
    result = negotiate(intent, caps, realization.behavior_factory())
    assert result.outcome == "REFUSED"
    (reason,) = result.reasons
    assert reason.code == "unsupported_command_mode"
    assert reason.data == {
        "required": "cartesian_impedance",
        "available": ["force_controlled_descent"],
        "substitute_considered": None,
        "execution_started": False,
    }
