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
    run, _ = _run_mediated(frozenset({CommandMode.CARTESIAN_IMPEDANCE}))
    assert run.disposition == "COMPLETE"


def test_machine_offering_no_command_mode_is_refused_before_motion():
    run, machine = _run_mediated(frozenset())
    assert run.disposition == "REFUSED"
    assert run.refusal_codes == ("unsupported_control_mode",)
    assert machine.open_session_calls == []


def test_string_alias_for_a_mode_is_not_scored():
    # A str is not a CommandMode. Today this fails loudly in the runtime's
    # feasibility evidence, after negotiate() has accepted it (see the xfail below).
    with pytest.raises(AttributeError):
        _run_mediated(frozenset({"cartesian_impedance"}))


@pytest.mark.xfail(strict=True, reason=(
    "M3.3 finding: ExecutionIntent and behaviors declare no required command mode, and "
    "haplous/capabilities.py negotiate() only refuses an empty mode set. A machine offering "
    "only FORCE_CONTROLLED_DESCENT runs the cartesian-impedance insertion behavior to COMPLETE, "
    "and a str alias passes negotiate(). Closing it edits capability negotiation (behavior-freeze)."))
@pytest.mark.parametrize("modes", [
    frozenset({CommandMode.FORCE_CONTROLLED_DESCENT}),
    frozenset({"cartesian_impedance"}),
], ids=["descent_only", "str_alias"])
def test_negotiation_refuses_a_mode_the_behavior_cannot_use(modes):
    from haplous.capabilities import negotiate

    realization = mujoco_backend.build_realization(SCENARIO)
    caps = dataclasses.replace(realization.machine.capabilities(), command_modes=modes)
    intent = systems.build_intent(SCENARIO, realization.condition_factory(SCENARIO, realization.bundle))
    assert negotiate(intent, caps).outcome == "REFUSED"
