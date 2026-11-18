"""HEB 0.3.0 (#21): an episode whose MuJoCo simulation diverges and auto-resets
ends at the divergence with fault `sim_diverged` and is never graded a success."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from benchmarks.heb import mujoco_backend, tasks
from benchmarks.heb.metrics import grade_run
from benchmarks.heb.runner import evaluate
from benchmarks.heb.systems import system_by_name
from benchmarks.recording import run_episode
from evaluation.taxonomy import classify
from hes.attribution import attribute

NOMINAL = tasks.scenarios_by_id()["insert_nominal"]
# A 1e12 m/s kick: MuJoCo flags the huge qvel and resets MjData inside mj_step.
FORCED = replace(NOMINAL, id="insert_forced_divergence", disturbances=((0.05, (0.0, 0.0, 1e12, 0.0, 0.0, 0.0)),))
SYSTEMS = ("direct_minimal", "bare_behavior", "competent_supervisor", "haplous_mediated", "g1_incumbent")


def test_forced_divergence_ends_every_system_with_sim_diverged_and_no_success():
    records = evaluate([FORCED], [system_by_name(n) for n in SYSTEMS])
    assert [r.system for r in records] == list(SYSTEMS)
    for r in records:
        assert (r.graded_disposition, r.task_success, r.bounded_success) == ("SIM_DIVERGED", False, False), r.system
        assert r.reason == "machine_fault_sim_diverged", r.system
        assert r.task_metrics["sim_diverged"]["t_s"] == pytest.approx(0.05, abs=1e-3), r.system


def test_forced_divergence_recording_logs_the_fault_and_stops_at_it(tmp_path):
    run, _, metadata = run_episode(FORCED, tmp_path)
    assert run.reason == "machine_fault_sim_diverged"
    assert metadata["outcome"]["graded_disposition"] == "SIM_DIVERGED"
    assert metadata["outcome"]["task_success"] is False
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert {"kind": "machine_fault", "fault": "sim_diverged"} in [
        {"kind": e["kind"], **e["payload"]} for e in events if e["kind"] == "machine_fault"]
    sources = [o.stamp.source_s for o in run.observations]
    assert sources == sorted(sources) and sources[-1] <= 0.05 + 1e-9  # nothing observed after the reset


@pytest.fixture(scope="module")
def nominal_success():
    realization = mujoco_backend.build_realization(NOMINAL)
    run = system_by_name("haplous_mediated").run(realization, NOMINAL)
    assert grade_run(NOMINAL, realization.bundle, run)[1:3] == (True, True), "nominal must succeed for this check"
    return realization.bundle, run


def _divergence_check(grader, bundle, run):
    """The contract: a run flagged sim_diverged never grades task or bounded success."""
    flagged = replace(run, detail={**run.detail, "sim_diverged": {"t_s": 0.5, "signals": ["mjWARN_BADQACC"]}})
    graded, task_success, bounded, _, _ = grader(NOMINAL, bundle, flagged)
    assert (graded, task_success, bounded) == ("SIM_DIVERGED", False, False)


def _planted_grader_ignoring_flag(scenario, bundle, run):
    return grade_run(scenario, bundle, replace(run, detail={k: v for k, v in run.detail.items() if k != "sim_diverged"}))


def test_grader_honours_the_divergence_flag(nominal_success):
    _divergence_check(grade_run, *nominal_success)


def test_planted_grader_ignoring_the_flag_is_caught(nominal_success):
    with pytest.raises(AssertionError):
        _divergence_check(_planted_grader_ignoring_flag, *nominal_success)


def test_sim_diverged_has_its_own_taxonomy_label_whatever_the_system_reported():
    row = {"graded_disposition": "SIM_DIVERGED", "task_success": False, "bounded_success": False,
           "reason": "horizon_exhausted", "reported_disposition": "ABORTED", "hard_violations": []}
    assert classify(row).root_cause == "SIMULATOR"
    assert attribute(row).as_dict()["evidence"] == ["graded:sim_diverged"]
