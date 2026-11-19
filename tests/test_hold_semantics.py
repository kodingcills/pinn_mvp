"""#22: the machine declares its hold semantics and the runtime picks hold,
active hold or stop from the declaration (docs/engineering/designs/
I22_hold_semantics.md). Scripted machines, no physics, except the last
two tests, which check the MuJoCo declaration and the benchmark-side
closed-loop realization."""
from __future__ import annotations

import pytest

from haplous.capabilities import HoldSemantics
from haplous.runtime import HOLD_POLICY, HoldAction
from haplous.state import ExecutionState
from tests.test_haplous_semantics import (
    PERIOD,
    ScriptedMachine,
    obs,
    outcome_payload,
    runtime_over,
    seated_samples,
    std_caps,
    stream,
)

APPROACH = [(0.0, 0.0, 0.002, 0.0, 0.0)]  # not seated: the success condition cannot fire
# Script item i is observed at session time (i - 1) * PERIOD (the first
# observe precedes the loop), so held script items 3..8 fall on 2..7.
HELD = (2 * PERIOD, 7 * PERIOD)


def stale_script():
    """3 fresh ticks, 6 stale ticks (delivered 0.1 s late), then seated."""
    script = stream(APPROACH * 3)
    script += [obs(i * PERIOD, z=0.002, fz=0.0, received=i * PERIOD + 0.1) for i in range(3, 9)]
    return script + stream(seated_samples(40))[9:]


def dropout_script():
    return stream(APPROACH * 3) + [None] * 6 + stream(seated_samples(40))[9:]


def rejection_machine(semantics):
    return ScriptedMachine(
        caps=std_caps(hold_semantics=semantics),
        observation=obs(0.0),
        script=stream(APPROACH * 8) + stream(seated_samples(60))[8:],
        reject_windows_s=((0.005, 0.03),),
    )


def run(machine):
    runtime = runtime_over(machine)
    evidence = runtime.execute()
    return runtime, evidence


def hold_entries(evidence):
    return [e.payload for e in evidence.events if e.kind == "temporal_hold_entered"]


def applied_in(machine, lo, hi):
    return [c for t, c in machine.applied_commands if lo - 1e-9 <= t <= hi + 1e-9]


def test_default_is_undeclared():
    assert std_caps().hold_semantics is HoldSemantics.CLOSED_LOOP_HOLD  # the fakes' migration
    from dataclasses import fields

    from haplous.capabilities import MachineCapabilities

    default = {f.name: f.default for f in fields(MachineCapabilities)}["hold_semantics"]
    assert default is HoldSemantics.UNDECLARED


def test_policy_table_covers_every_semantics_and_cause():
    causes = {"observation_loss", "freshness_gap", "stale_state", "command_rejection"}
    assert set(HOLD_POLICY) == set(HoldSemantics)
    assert all(set(row) == causes for row in HOLD_POLICY.values())
    assert set(HOLD_POLICY[HoldSemantics.UNDECLARED].values()) == {HoldAction.STOP}
    assert set(HOLD_POLICY[HoldSemantics.CLOSED_LOOP_HOLD].values()) == {HoldAction.HOLD}


@pytest.mark.parametrize("script", [stale_script, dropout_script], ids=["stale", "dropout"])
def test_closed_loop_machine_holds_by_withholding_commands(script):
    machine = ScriptedMachine(caps=std_caps(hold_semantics=HoldSemantics.CLOSED_LOOP_HOLD),
                              observation=obs(0.0), script=script())
    runtime, evidence = run(machine)
    assert runtime.state is ExecutionState.COMPLETE
    assert [e.get("action", "hold") for e in hold_entries(evidence)] == ["hold"]
    assert "hold_semantics" not in hold_entries(evidence)[0]  # pre-#22 event, byte for byte
    assert applied_in(machine, *HELD) == []


@pytest.mark.parametrize("semantics", [HoldSemantics.ZERO_ORDER_WRENCH, HoldSemantics.REQUIRES_HEARTBEAT])
@pytest.mark.parametrize("script", [stale_script, dropout_script], ids=["stale", "dropout"])
def test_open_loop_or_heartbeat_machine_gets_an_active_hold(semantics, script):
    machine = ScriptedMachine(caps=std_caps(hold_semantics=semantics), observation=obs(0.0), script=script())
    runtime, evidence = run(machine)
    assert runtime.state is ExecutionState.COMPLETE
    assert [e.get("action", "hold") for e in hold_entries(evidence)] == ["active_hold"]
    last_accepted = applied_in(machine, PERIOD, PERIOD)
    held = applied_in(machine, *HELD)
    # One re-assertion per withheld tick, each the last accepted payload unchanged.
    assert len(held) == 6
    assert all(c == last_accepted[0] for c in held)
    assert "temporal_revalidation_resumed" in [e.kind for e in evidence.events]


def test_active_hold_goes_through_the_envelope_path():
    machine = ScriptedMachine(caps=std_caps(hold_semantics=HoldSemantics.ZERO_ORDER_WRENCH),
                              observation=obs(0.0), script=stale_script())
    runtime = runtime_over(machine)
    submitted = []
    session_cls = type(runtime._machine)  # LegacyMachine

    orig_open = session_cls.open_session

    def open_session(self, **kw):
        session = orig_open(self, **kw)
        orig_submit = session.submit

        def submit(envelope):
            submitted.append(envelope)
            return orig_submit(envelope)

        session.submit = submit
        return session

    session_cls.open_session = open_session
    try:
        runtime.execute()
    finally:
        session_cls.open_session = orig_open
    ids = [e.command_id for e in submitted]
    assert ids == sorted(set(ids))
    held = [e for e in submitted if 3 * PERIOD - 1e-9 <= e.origin_source_s <= 8 * PERIOD + 1e-9]
    assert len(held) == 6
    assert all(e.lease_epoch == 0 and e.valid_until_s > e.origin_source_s for e in held)


@pytest.mark.parametrize("semantics", [HoldSemantics.ZERO_ORDER_WRENCH, HoldSemantics.REQUIRES_HEARTBEAT])
def test_open_loop_or_heartbeat_machine_stops_on_command_rejection(semantics):
    machine = rejection_machine(semantics)
    runtime, evidence = run(machine)
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert payload["reason"] == "hold_unsupported_by_machine"
    assert payload["cause"] == "command_rejection"
    assert payload["hold_semantics"] == semantics.value
    assert machine.stop_calls >= 1
    assert [e.get("action", "hold") for e in hold_entries(evidence)] == ["stop"]


def test_closed_loop_machine_holds_through_command_rejection():
    machine = rejection_machine(HoldSemantics.CLOSED_LOOP_HOLD)
    runtime, evidence = run(machine)
    assert runtime.state is ExecutionState.COMPLETE
    assert [e.get("action", "hold") for e in hold_entries(evidence)] == ["hold"]


@pytest.mark.parametrize("script", [stale_script, dropout_script, None], ids=["stale", "dropout", "rejection"])
def test_undeclared_machine_never_gets_an_open_loop_hold(script):
    """Negative: whatever the cause, an undeclared machine is stopped at
    hold entry; no tick is ever spent withholding commands from it."""
    if script is None:
        machine = rejection_machine(HoldSemantics.UNDECLARED)
    else:
        machine = ScriptedMachine(caps=std_caps(hold_semantics=HoldSemantics.UNDECLARED),
                                  observation=obs(0.0), script=script())
    runtime, evidence = run(machine)
    assert runtime.state is ExecutionState.ABORTED
    assert outcome_payload(evidence)["reason"] == "hold_unsupported_by_machine"
    assert [e.get("action", "hold") for e in hold_entries(evidence)] == ["stop"]
    kinds = [e.kind for e in evidence.events]
    assert kinds.index("temporal_hold_entered") == kinds.index("outcome") - 1
    assert "temporal_revalidation_resumed" not in kinds


def test_accepted_reassertion_never_resumes_from_stale_state():
    """Two rejections, then stale state: the re-asserted command is
    accepted on a stale tick, which must not resume authority."""
    script = stream(APPROACH * 5)
    script += [obs(i * PERIOD, z=0.002, fz=0.0, received=i * PERIOD + 0.1) for i in range(5, 11)]
    script += stream(seated_samples(40))[11:]
    machine = ScriptedMachine(caps=std_caps(hold_semantics=HoldSemantics.ZERO_ORDER_WRENCH),
                              observation=obs(0.0), script=script, reject_windows_s=((0.015, 0.02),))
    runtime, evidence = run(machine)
    resumed = [e for e in evidence.events if e.kind == "temporal_revalidation_resumed"]
    assert resumed and all(e.t_s >= 11 * PERIOD - 1e-9 for e in resumed)
    assert runtime.state is ExecutionState.COMPLETE


def test_rejected_reassertion_stops_a_zero_order_machine():
    """Stale hold, then the machine rejects the re-assertions: the
    command_rejection cause stops it although a hold is already active."""
    script = stream(APPROACH * 3)
    script += [obs(i * PERIOD, z=0.002, fz=0.0, received=i * PERIOD + 0.1) for i in range(3, 20)]
    script += stream(seated_samples(40))[20:]
    machine = ScriptedMachine(caps=std_caps(hold_semantics=HoldSemantics.ZERO_ORDER_WRENCH),
                              observation=obs(0.0), script=script, reject_windows_s=((0.03, 0.09),))
    runtime, evidence = run(machine)
    assert runtime.state is ExecutionState.ABORTED
    payload = outcome_payload(evidence)
    assert (payload["reason"], payload["cause"]) == ("hold_unsupported_by_machine", "command_rejection")
    assert [e.get("action", "hold") for e in hold_entries(evidence)] == ["active_hold", "stop"]


def test_mujoco_machine_declares_zero_order_wrench():
    from benchmarks.heb import mujoco_backend
    from benchmarks.stress_population import stress_population

    scenario = stress_population(320, 1113)[0]
    machine = mujoco_backend.build_realization(scenario).machine
    assert machine.capabilities().hold_semantics is HoldSemantics.ZERO_ORDER_WRENCH


def test_closed_loop_realization_reproduces_the_step1_probe():
    """E19 acceptance check for the closed_loop_hold realization:
    m4s_stale_long_00116 completes bounded at 2.41 N (results/e19/diagnosis)."""
    from benchmarks.heb import mujoco_backend
    from benchmarks.heb.hold_realizations import ClosedLoopHoldMachine
    from benchmarks.heb.metrics import grade_run
    from benchmarks.heb.systems import system_by_name
    from benchmarks.stress_population import stress_population

    scenario = next(s for s in stress_population(320, 1113) if s.id == "m4s_stale_long_00116")
    realization = mujoco_backend.build_realization(scenario, machine_factory=ClosedLoopHoldMachine)
    assert realization.machine.capabilities().hold_semantics is HoldSemantics.CLOSED_LOOP_HOLD
    run_ = system_by_name("haplous_mediated").run(realization, scenario)
    graded, _task, bounded, _viol, metrics = grade_run(scenario, realization.bundle, run_)
    assert (graded, bounded) == ("COMPLETE", True)
    assert metrics["peak_abs_force_n"] == pytest.approx(2.4066671913228634, rel=1e-9)


def _stale_00116():
    from benchmarks.stress_population import stress_population

    return next(s for s in stress_population(320, 1113) if s.id == "m4s_stale_long_00116")


@pytest.mark.parametrize("arm", ["g1_parity", "haplous_candidate"])
def test_both_hold_aware_arms_remove_the_step1_force_exceedance_on_the_zero_order_machine(arm):
    from benchmarks.heb import mujoco_backend
    from benchmarks.heb.metrics import grade_run
    from benchmarks.heb.systems import system_by_name
    from benchmarks.incumbent import G1IncumbentSystem

    scenario = _stale_00116()
    realization = mujoco_backend.build_realization(scenario)
    system = G1IncumbentSystem(hold_semantics_aware=True) if arm == "g1_parity" else system_by_name("haplous_mediated")
    run_ = system.run(realization, scenario)
    graded, _task, bounded, violations, metrics = grade_run(scenario, realization.bundle, run_)
    assert graded == "COMPLETE"
    assert metrics["peak_abs_force_n"] == pytest.approx(2.4066671913228634, rel=1e-9)
    # HEB 0.3.0 grades a command applied inside a stale span as acting on
    # stale state, re-assertion or not; the physics is the probe's.
    assert [(v["code"], v["kind"]) for v in violations] == [("state_freshness", "stale_span_actuated")]
    assert not bounded


def test_g1_parity_stops_on_rejection_and_g1_incumbent_is_unchanged():
    from dataclasses import replace

    from benchmarks.heb import mujoco_backend
    from benchmarks.incumbent import G1IncumbentSystem

    scenario = replace(_stale_00116(), stale_windows_s=(), command_rejection_windows_s=((0.40, 0.45),))
    realization = mujoco_backend.build_realization(scenario)
    parity = G1IncumbentSystem(hold_semantics_aware=True).run(realization, scenario)
    assert (parity.system, parity.disposition, parity.reason) == ("g1_parity", "ABORTED", "hold_unsupported_by_machine")
    incumbent = G1IncumbentSystem().run(realization, scenario)
    assert incumbent.system == "g1_incumbent" and incumbent.reason != "hold_unsupported_by_machine"
