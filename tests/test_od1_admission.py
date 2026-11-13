"""OD-1 contract-driven command-mode admission (locked design
docs/eval_program/designs/OD1_command_mode_admission.md, section 6)."""
from __future__ import annotations

import itertools

import pytest

from evaluation.track_b import _resolve_mode
from haplous.behaviors.insertion import InsertionExecutionBehavior
from haplous.behaviors.sliding import SlideExecutionBehavior
from haplous.capabilities import CommandMode, negotiate
from haplous.runtime import HaplousRuntime
from haplous.state import ExecutionState
from tests.test_haplous_semantics import (
    HoldBehavior,
    ScriptedMachine,
    make_intent,
    obs,
    seated_samples,
    std_caps,
    stream,
)

CI = CommandMode.CARTESIAN_IMPEDANCE
FCD = CommandMode.FORCE_CONTROLLED_DESCENT


def contracted(required=frozenset({CI}), authorized=frozenset(), non_substitutable=frozenset()):
    return type("ContractedHold", (HoldBehavior,), {
        "required_command_modes": required,
        "authorized_substitutes": authorized,
        "non_substitutable_modes": non_substitutable,
    })


def submit(behavior, caps, script=()):
    machine = ScriptedMachine(caps=caps, observation=obs(0.0), script=script)
    runtime = HaplousRuntime(machine, behaviors={"motion": behavior})
    return runtime, machine, runtime.submit(make_intent())


def feasibility_event(runtime):
    return next(e for e in runtime.evidence.events if e.kind == "feasibility").payload


def test_existing_behaviors_declare_both_modes():
    for behavior in (InsertionExecutionBehavior, SlideExecutionBehavior):
        assert behavior.required_command_modes == frozenset({CI, FCD})
        assert behavior.authorized_substitutes == frozenset()


class UndeclaredHold:
    """HoldBehavior's commands without any command contract."""

    __init__ = HoldBehavior.__init__
    begin = HoldBehavior.begin
    step = HoldBehavior.step


def test_undeclared_contract_is_refused():
    runtime, machine, result = submit(UndeclaredHold, std_caps())
    assert result.outcome == "REFUSED"
    assert [r.code for r in result.reasons] == ["command_contract_undeclared"]
    assert runtime.state is ExecutionState.REFUSED
    assert machine.open_session_calls == []


def test_exact_mode_is_supported_without_disclosure():
    runtime, _, result = submit(contracted(), std_caps())
    assert result.outcome == "SUPPORTED"
    assert result.disclosures == ()
    assert "disclosures" not in feasibility_event(runtime)


def test_verified_authorized_adapter_is_admitted_with_disclosure():
    caps = std_caps(command_modes=frozenset({FCD}), verified_adapters=frozenset({(CI, FCD)}))
    runtime, machine, result = submit(
        contracted(authorized=frozenset({(CI, FCD)})), caps, stream(seated_samples())
    )
    assert result.outcome == "SUPPORTED"
    assert [(d.code, d.data) for d in result.disclosures] == [
        ("command_mode_substituted", {"required": "cartesian_impedance", "selected": "force_controlled_descent"})
    ]
    assert feasibility_event(runtime)["disclosures"][0]["code"] == "command_mode_substituted"
    runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    assert len(machine.open_session_calls) == 1


@pytest.mark.parametrize("authorized,verified,missing", [
    (frozenset({(CI, FCD)}), frozenset(), ["machine_verification"]),
    (frozenset(), frozenset({(CI, FCD)}), ["task_authorization"]),
], ids=["authorized_only", "verified_only"])
def test_one_sided_substitute_is_refused(authorized, verified, missing):
    caps = std_caps(command_modes=frozenset({FCD}), verified_adapters=verified)
    runtime, machine, result = submit(contracted(authorized=authorized), caps)
    assert result.outcome == "REFUSED"
    (reason,) = result.reasons
    assert reason.code == "unsupported_command_mode"
    assert reason.data == {
        "required": "cartesian_impedance",
        "available": ["force_controlled_descent"],
        "substitute_considered": {
            "pair": ["cartesian_impedance", "force_controlled_descent"],
            "missing": missing,
        },
        "execution_started": False,
    }
    assert feasibility_event(runtime)["reasons"][0]["data"] == reason.data
    assert machine.open_session_calls == []


def test_non_substitutable_mode_is_never_substituted():
    caps = std_caps(command_modes=frozenset({FCD}), verified_adapters=frozenset({(CI, FCD)}))
    behavior = contracted(authorized=frozenset({(CI, FCD)}), non_substitutable=frozenset({CI}))
    _, machine, result = submit(behavior, caps)
    assert result.outcome == "REFUSED"
    assert result.reasons[0].data["substitute_considered"]["missing"] == ["substitutable"]
    assert machine.open_session_calls == []


def test_empty_mode_set_keeps_its_existing_refusal():
    _, _, result = submit(contracted(), std_caps(command_modes=frozenset()))
    assert [r.code for r in result.reasons] == ["unsupported_control_mode"]


class LosingMachine(ScriptedMachine):
    """Offers the required mode until its session has applied N commands."""

    def __init__(self, lose_after, **kwargs):
        super().__init__(**kwargs)
        self.lose_after = lose_after

    def capabilities(self):
        if len(self.applied_commands) >= self.lose_after:
            return std_caps(command_modes=frozenset({FCD}))
        return super().capabilities()


def test_capability_loss_mid_run_aborts_through_stop():
    machine = LosingMachine(5, observation=obs(0.0), script=stream([(0.0, 0.0, 0.0, 1.0, 0.0)] * 50))
    runtime = HaplousRuntime(machine, behaviors={"motion": contracted()})
    assert runtime.submit(make_intent()).outcome == "SUPPORTED"
    evidence = runtime.execute()
    assert runtime.state is ExecutionState.ABORTED
    assert machine.stop_calls == 1
    assert len(machine.applied_commands) == 5
    lost = [e.payload for e in evidence.events if e.kind == "command_capability_lost"]
    assert lost == [{"required": ["cartesian_impedance"], "available": ["force_controlled_descent"]}]
    outcome = [e.payload for e in evidence.events if e.kind == "outcome"][-1]
    assert outcome["result"] == "ABORTED" and outcome["reason"] == "command_capability_lost"


MODES = (CI, FCD)


def _cases():
    for req, available, authorize, verify, nonsub in itertools.product(
        MODES, itertools.chain.from_iterable(itertools.combinations(MODES, k) for k in range(3)),
        (False, True), (False, True), (False, True),
    ):
        other = FCD if req is CI else CI
        yield req, frozenset(available), authorize, verify, nonsub, other


@pytest.mark.parametrize("case", list(_cases()), ids=lambda c: "-".join(map(str, (
    c[0].value, sorted(m.value for m in c[1]), *c[2:5]))))
def test_track_b_differential(case):
    """haplous owns runtime admission; Track B's _resolve_mode is the oracle.
    Exhaustive over the two-mode space: exact, authorized-only, verified-only,
    both, non-substitutable."""
    req, available, authorize, verify, nonsub, other = case
    track_b_admits = _resolve_mode({
        "required_mode": req.value,
        "available_modes": sorted(m.value for m in available),
        "allowed_mode_equivalents": {req.value: [other.value]} if authorize else {},
        "verified_mode_equivalents": {req.value: [other.value]} if verify else {},
        "non_substitutable_modes": [req.value] if nonsub else [],
    })[1] is None
    behavior = contracted(
        required=frozenset({req}),
        authorized=frozenset({(req, other)}) if authorize else frozenset(),
        non_substitutable=frozenset({req}) if nonsub else frozenset(),
    )()
    caps = std_caps(
        command_modes=available,
        verified_adapters=frozenset({(req, other)}) if verify else frozenset(),
    )
    haplous_admits = negotiate(make_intent(), caps, behavior).outcome != "REFUSED"
    assert haplous_admits == track_b_admits
