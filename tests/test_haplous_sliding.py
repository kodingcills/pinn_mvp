"""Task B (constrained-contact slide) integration: the runtime mediates
real constrained-contact execution through the MuJoCo adapter's stepping
session over the slide substrate. Pins the S04 semantic contract: live
recovery from facet-tap breaches, permission-gated recovery, unrecoverable
disturbance aborts, and soft force-floor semantics."""
from __future__ import annotations

from benchmarks.heb.sliding import sliding_scenarios
from benchmarks.heb.systems import build_intent
from benchmarks.heb import mujoco_backend

from haplous.runtime import HaplousRuntime
from haplous.state import ExecutionState


def _run(scenario_id: str):
    scen = next(s for s in sliding_scenarios() if s.id == scenario_id)
    realization = mujoco_backend.build_realization(scen)
    condition = realization.condition_factory(scen, realization.bundle)
    intent = build_intent(scen, condition)
    runtime = HaplousRuntime(
        realization.machine,
        behaviors={scen.task_family: realization.behavior_factory},
    )
    feasibility = runtime.submit(intent)
    if feasibility.outcome == "REFUSED":
        return runtime
    runtime.execute()
    return runtime


def _outcome(runtime):
    return [
        e for e in runtime.evidence.events if e.kind == "outcome"
    ][-1]


def _payload(runtime):
    return _outcome(runtime).payload


def test_slide_nominal_completes_bounded():
    runtime = _run("slide_nominal")
    assert runtime.state is ExecutionState.COMPLETE
    outcome = _outcome(runtime)
    assert outcome.payload["result"] == "COMPLETE"
    assert outcome.payload["success_condition"] == "path_completed"


def test_force_floor_is_soft_during_approach():
    """The declared floor cannot hold before contact; it is stated soft so
    the runtime degrades and proceeds instead of aborting."""
    scen = next(s for s in sliding_scenarios() if s.id == "slide_nominal")
    realization = mujoco_backend.build_realization(scen)
    condition = realization.condition_factory(scen, realization.bundle)
    intent = build_intent(scen, condition)
    floors = [
        r for r in intent.requirements if r.kind.value == "min_process_force_n"
    ]
    assert floors and floors[0].hard is False


def test_facet_tap_recovery_permission_flips_disposition():
    """Same facet-tap physics under a 1.5N ceiling: with recovery permitted
    the execution completes through multiple authorized recovery episodes;
    with permission removed it aborts at the first tap."""
    runtime = _run("slide_disturbance_recoverable_ceiling")
    assert runtime.state is ExecutionState.COMPLETE
    decisions = [
        e for e in runtime.evidence.events if e.kind == "recovery_decision"
    ]
    assert len(decisions) >= 3, "facet taps did not trigger recoveries"

    runtime2 = _run("slide_disturbance_no_recovery")
    assert runtime2.state is ExecutionState.ABORTED
    assert _outcome(runtime2).payload["reason"] == "hard_constraint_process_force"


def test_severe_disturbance_aborts_live():
    runtime = _run("slide_disturbance_severe")
    assert runtime.state is ExecutionState.ABORTED
    outcome = _outcome(runtime)
    assert outcome.payload["reason"] == "machine_fault_tilt_guard"
    assert outcome.t_s < 2.0


def test_slide_evidence_reconstructs_recovery_lineage():
    runtime = _run("slide_disturbance_recoverable_ceiling")
    kinds = [e.kind for e in runtime.evidence.events]
    assert "recovery_decision" in kinds
    assert "constraint_violation" in kinds
    transitions = [
        e.payload.get("to")
        for e in runtime.evidence.events
        if e.kind == "state_transition"
    ]
    assert "recovering" in transitions
    assert "executing" in transitions
    assert _outcome(runtime).payload["result"] == "COMPLETE"
