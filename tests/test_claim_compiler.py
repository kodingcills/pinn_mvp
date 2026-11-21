"""Claim-to-experiment compiler (issue #20): plans, UNSUPPORTED reasons, the
sealed-population guard, and the three negative checks (swapped pairing, truth
leak into a system, modified scoring artifact in M0)."""
from __future__ import annotations

import dataclasses
import inspect
import json
import shutil
from pathlib import Path

import pytest
from test_negative_static_leak import TARGETS
from test_negative_truth_leak import _run, _scenario, leaks

from benchmarks.heb.systems import system_by_name
from evaluation.run_registry import Registry
from hes import claim_compiler as cc
from scripts import claims_run

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "evaluation" / "claim_specs"
CLAIMS = ("temporal_hold", "od1_admission")


def spec(name: str, **changes) -> cc.ClaimSpec:
    return dataclasses.replace(claims_run.load_spec(str(SPECS / f"{name}.json")), **changes)


def codes(result) -> set[str]:
    assert isinstance(result, cc.Unsupported), result
    return {r.code for r in result.reasons}


@pytest.fixture(scope="module")
def plans() -> dict[str, cc.Plan]:
    return {name: cc.compile_claim(spec(name)) for name in CLAIMS}


def sealed_rows(plan: cc.Plan) -> list[dict]:
    problems, rows = claims_run.sealed_inputs(Registry(claims_run.REGISTRY), plan)
    assert problems == [], problems
    return rows


# ---------------------------------------------------------------- compilation


def test_both_claims_compile_to_paired_dev_plans_within_budget(plans):
    for name, plan in plans.items():
        assert isinstance(plan, cc.Plan), name
        assert plan.tier == "DEV" and plan.population.split("@")[0] not in cc.SEALED
        assert set(plan.arms) == {"haplous_mediated", "g1_incumbent", "bare_behavior"}
        assert len({u.unit_id for u in plan.units}) == len(plan.units)
        assert plan.cpu_estimate_s <= plan.claim.budget_cpu_min * 60
    strata = {u.stratum for u in plans["temporal_hold"].units}
    assert strata == {"below_budget", "above_budget"}
    od1 = plans["od1_admission"].units
    assert {u.profile for u in od1 if u.lacks_required} == {"mujoco_fcd_only", "mujoco_ci_only"}
    assert {u.profile for u in od1 if not u.lacks_required} == {"mujoco_heb"}


def test_compilation_is_deterministic(plans):
    assert cc.compile_claim(spec("temporal_hold")).sha256 == plans["temporal_hold"].sha256


def test_joint_position_claim_is_unsupported():
    result = cc.compile_claim(claims_run.load_spec(str(SPECS / "joint_position_unsupported.json")))
    # joint_position is a real mode since #18, but no HEB machine profile offers it.
    assert "capability_absent_from_every_profile" in codes(result)
    assert "unknown_capability" not in codes(result)


@pytest.mark.parametrize("changes, code", [
    ({"budget_cpu_min": 0.5}, "budget_exceeded"),
    ({"operators": ("no_such_operator",)}, "operator_unknown"),
    ({"comparators": ("haplous_mediated", "oracle")}, "comparator_unknown"),
    ({"comparators": ("g1_incumbent", "bare_behavior")}, "subject_arm_missing"),
    ({"machine_profiles": ("mujoco_heb", "ur5e")}, "profile_unknown"),
    ({"requirements": {"hold_budget_s": 0.8}}, "hold_budget_mismatch"),
    ({"property_id": "makes_coffee"}, "property_unknown"),
])
def test_untestable_claims_name_their_reason(changes, code):
    assert code in codes(cc.compile_claim(spec("temporal_hold", **changes)))


def test_admission_needs_contrasting_profiles():
    assert "profiles_do_not_contrast" in codes(cc.compile_claim(spec("od1_admission", machine_profiles=("mujoco_heb",))))


def test_operator_adds_paired_variant_units():
    plan = cc.compile_claim(spec("temporal_hold", n_scenarios=2, operators=("irrelevant_perturbation",)))
    assert len(plan.units) == 4
    assert len({u.scenario_sha256 for u in plan.units}) == 4


# ---------------------------------------------------------------- sealed guard


@pytest.mark.parametrize("population", ["m4_control@v1", "m4_stress@v1", "qualification@v1"])
def test_plans_never_target_sealed_populations(population):
    assert "population_sealed" in codes(cc.compile_claim(spec("temporal_hold", population=population)))


def test_population_sharing_scenarios_with_a_sealed_one_is_refused():
    # dev@v1 is the first 40 scenarios of m4_control@v1.
    assert "population_overlaps_sealed" in codes(cc.compile_claim(spec("od1_admission", population="dev@v1")))


@pytest.mark.parametrize("tier", ["CONFIRMATORY", "TIER3"])
def test_only_dev_tier_compiles(tier):
    assert "tier_not_dev" in codes(cc.compile_claim(spec("temporal_hold", tier=tier)))


def test_executor_refuses_a_hand_edited_plan_targeting_a_sealed_population(plans):
    forged = dataclasses.replace(plans["temporal_hold"], population="m4_stress@v1")
    with pytest.raises(ValueError, match="not the compiler's output"):
        cc.execute_plan(forged)


# ---------------------------------------------------------------- estimands


def _row(disposition="COMPLETE", n_commands=10, violations=()):
    return {"reported_disposition": disposition, "task_metrics": {"n_applied_commands": n_commands},
            "hard_violations": list(violations)}


def test_admission_estimand():
    lacks, has = (cc.Unit("u", "s", "p", None, "nominal", "d", flag) for flag in (True, False))
    assert cc._admission_correct(_row("REFUSED", 0), lacks)
    assert not cc._admission_correct(_row("COMPLETE", 10), lacks)  # executed without the mode
    assert not cc._admission_correct(_row("ABORTED", 3), lacks)  # stopped, but after acting
    assert cc._admission_correct(_row("COMPLETE", 10), has)
    assert not cc._admission_correct(_row("REFUSED", 0), has)  # false refusal


def test_temporal_hold_estimand_reads_the_heb_grade():
    unit = cc.Unit("u", "s", "p", None, "below_budget", "d")
    assert cc._stale_actuated(_row(violations=[{"code": "state_freshness", "kind": "stale_span_actuated"}]), unit)
    assert not cc._stale_actuated(_row(violations=[{"code": "process_force"}]), unit)


# ---------------------------------------------------------------- negative checks


@pytest.mark.parametrize("name", CLAIMS)
def test_committed_runs_validate(name):
    assert claims_run.validate(str(SPECS / f"{name}.json")) == []


def test_swapped_pairing_is_caught(plans):
    plan = plans["temporal_hold"]
    rows = sealed_rows(plan)
    assert cc.pairing_problems(plan, rows) == []
    a, b = [i for i, r in enumerate(rows) if r["arm"] == "g1_incumbent"][:2]
    swapped = [dict(r) for r in rows]
    swapped[a]["scenario_id"], swapped[b]["scenario_id"] = rows[b]["scenario_id"], rows[a]["scenario_id"]
    # Still exactly one row per (unit, arm): a count-only alignment check passes it.
    assert len({(r["scenario_id"], r["arm"]) for r in swapped}) == len(rows)
    problems = cc.pairing_problems(plan, swapped)
    assert len([p for p in problems if "ran a different scenario" in p]) == 2, problems
    with pytest.raises(ValueError, match="ran a different scenario"):
        cc.score(plan, swapped, resamples=10)


def test_compiled_comparators_are_all_under_the_static_truth_leak_guard(plans):
    guarded = {(path, cls) for path, classes in TARGETS for cls in (classes or (None,))}
    for arm in {a for p in plans.values() for a in p.arms}:
        system = type(system_by_name(arm))
        path = Path(inspect.getsourcefile(system)).resolve().relative_to(ROOT).as_posix()
        assert (path, system.__name__) in guarded or (path, None) in guarded, arm


@pytest.mark.parametrize("system", ["haplous_mediated", "g1_incumbent"])
def test_profiled_machine_leaks_no_truth_into_a_system(system):
    scenario = _scenario("insert_dropout_large")
    crossed = _run(scenario, system, lambda m, s: cc.ProfiledMachine(m, {"max_hold_unobserved_s": 0.5}))
    assert [hit for hit in leaks(crossed, scenario) if hit[0] != "id"] == []


def test_detector_catches_a_profile_that_leaks_truth():
    scenario = _scenario("insert_dropout_large")
    leaky = lambda m, s: cc.ProfiledMachine(m, {"execution_horizon_s": s.fault_at_s})  # noqa: E731
    found = leaks(_run(scenario, "haplous_mediated", leaky), scenario)
    assert any(f == "fault_at_s" and "capabilities()" in path for f, path in found), found


@pytest.fixture()
def registry_copy(tmp_path):
    shutil.copytree(claims_run.REGISTRY, tmp_path / "m0")
    shutil.copytree(claims_run.RESULTS / "temporal_hold", tmp_path / "results" / "temporal_hold")
    return tmp_path


def _validate(tmp: Path) -> list[str]:
    return claims_run.validate(str(SPECS / "temporal_hold.json"), tmp / "m0", tmp / "results")


def test_modified_scoring_artifact_in_m0_is_caught(plans, registry_copy):
    assert _validate(registry_copy) == []
    rows = registry_copy / "m0" / "runs" / claims_run.run_id(plans["temporal_hold"]) / "artifacts" / "rows.jsonl"
    lines = rows.read_text().splitlines()
    first = json.loads(lines[0])
    first["bounded_success"] = not first["bounded_success"]
    rows.write_text("\n".join([json.dumps(first, sort_keys=True), *lines[1:]]) + "\n")
    assert "artifact hash mismatch: rows.jsonl" in _validate(registry_copy)


def test_modified_committed_summary_is_caught(registry_copy):
    path = registry_copy / "results" / "temporal_hold" / "summary.json"
    summary = json.loads(path.read_text())
    summary["by_arm"]["haplous_mediated"]["stale_span_actuated"]["value"] = 0.5
    path.write_text(json.dumps(summary))
    assert _validate(registry_copy) == ["committed summary differs from the re-score of the sealed rows"]


def test_scoring_source_drift_refuses_to_score(plans):
    plan = plans["temporal_hold"]
    drifted = dataclasses.replace(plan, scoring_sources={**plan.scoring_sources, "hes/metrics.py": "0" * 64})
    with pytest.raises(ValueError, match="scoring source hes/metrics.py changed"):
        cc.score(drifted, sealed_rows(plan), resamples=10)
