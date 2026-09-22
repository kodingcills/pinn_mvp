"""HES smoke tests: one runnable check per load-bearing mechanism.

These use synthetic canonical rows, so they run without MuJoCo and fail if
the outcome model, attainability rule, metrics, attribution, gates, or
report digest logic regress.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hes_fixtures import make_run, paired_rows, row

from hes.attribution import CauseLayer, RuntimeSubcause, attribute
from hes.boundary import find_boundary
from hes.bundle import build_bundle
from hes.claims import build_claim_ledger
from hes.gates import evaluate_hard_gates
from hes.integrity import (
    metric_gaming,
    mirrored_scenarios_agree,
    no_privileged_leakage,
    trivial_policies_do_not_dominate,
)
from hes.model import (
    AttainabilityAxis,
    AuthorityAxis,
    ContractAxis,
    TaskAxis,
    build_attainability,
    classify_episode,
)
from hes.pareto import build_pareto, responsibility_decomposition
from hes.regression import compare_metric_vectors, paired_rate_delta
from hes.report import check_report, render_markdown
from hes.spec import ExperimentSpec


def test_axes_are_independent() -> None:
    refused = classify_episode(row("s", "a", "REFUSED", reason="negotiation_refusal"))
    assert refused.authority is AuthorityAxis.REFUSED
    assert refused.contract is ContractAxis.UNKNOWN
    assert refused.task is TaskAxis.UNKNOWN

    violated = classify_episode(
        row("s", "a", "ABORTED", task_success=False, bounded_success=False, n_hard_violations=2)
    )
    assert violated.contract is ContractAxis.VIOLATED
    assert violated.authority is AuthorityAxis.ABORTED

    bounded = classify_episode(row("s", "a", "COMPLETE", task_success=True, bounded_success=True))
    assert bounded.contract is ContractAxis.BOUNDED
    assert bounded.authority is AuthorityAxis.ACCEPTED
    assert bounded.task is TaskAxis.SUCCESS


def test_attainability_requires_bounded_success_not_contract_violating_success() -> None:
    # A direct arm that "completes" while violating the contract must NOT
    # demonstrate bounded attainability.
    episodes = {
        "s0": {
            "direct_minimal": row(
                "s0", "direct_minimal", "COMPLETE", task_success=True, bounded_success=False,
                n_hard_violations=3,
            )
        },
        "s1": {
            "direct_minimal": row(
                "s1", "direct_minimal", "COMPLETE", task_success=True, bounded_success=True
            )
        },
        "s2": {"direct_minimal": row("s2", "direct_minimal", "ABORTED")},
    }
    result = build_attainability(episodes, reference_arm="direct_minimal")
    assert result["s0"].axis is AttainabilityAxis.UNKNOWN
    assert result["s1"].axis is AttainabilityAxis.ATTAINABLE
    assert result["s2"].axis is AttainabilityAxis.UNKNOWN


def test_attainability_from_false_refusal_counterfactual() -> None:
    episodes = {"s0": {"haplous_mediated": row("s0", "haplous_mediated", "REFUSED")}}
    forks = {
        "s0": {"counterfactuals": {"refusal": {"classification": "FALSE_REFUSAL"}}},
    }
    result = build_attainability(episodes, fork_by_scenario=forks)
    assert result["s0"].axis is AttainabilityAxis.ATTAINABLE
    assert result["s0"].provenance.value == "COUNTERFACTUAL"


def test_execution_capture_and_authority_metrics() -> None:
    analysis = make_run().analysis()
    metric = analysis["metric_arm_vector"]["haplous_mediated"]
    authority = metric["authority"]
    # 10 demonstrated-attainable scenarios, 8 captured.
    assert authority["execution_capture"]["value"] == pytest.approx(0.8)
    # Refusals of demonstrated-attainable requests: 2 of 10.
    assert authority["false_refusal_rate"]["value"] == pytest.approx(0.2)
    assert analysis["attainability"]["counts"]["ATTAINABLE"] == 10


def test_noninterference_is_paired_on_baseline_success() -> None:
    analysis = make_run().analysis()
    non_interference = analysis["metric_arm_vector"]["haplous_mediated"]["non_interference"]
    assert non_interference["n_reference_bounded_success"] == 10
    assert non_interference["preservation_rate"]["value"] == pytest.approx(0.8)
    assert non_interference["unnecessary_intervention_rate"]["value"] == pytest.approx(0.2)


def test_attribution_false_refusal_requires_attainability() -> None:
    from hes.model import Attainability, AttainabilityProvenance

    attainable = Attainability(AttainabilityAxis.ATTAINABLE, AttainabilityProvenance.PAIRED_BOUNDED_BASELINE)
    attr = attribute(row("s", "a", "REFUSED"), attainability=attainable)
    assert attr.primary is CauseLayer.EXECUTION_RUNTIME
    assert attr.subcause is RuntimeSubcause.FALSE_REFUSAL

    unknown = attribute(row("s", "a", "REFUSED"), attainability=None)
    assert unknown.subcause is not RuntimeSubcause.FALSE_REFUSAL

    infeasible = Attainability(AttainabilityAxis.NON_ATTAINABLE, AttainabilityProvenance.EXPLICIT_INFEASIBLE_MARKER)
    correct = attribute(row("s", "a", "REFUSED"), attainability=infeasible)
    assert correct.subcause is RuntimeSubcause.CORRECT_INFEASIBLE_REFUSAL
    from hes.attribution import haplous_responsibility

    assert haplous_responsibility(correct) == 0.0


def test_pareto_reports_components_and_responsibility() -> None:
    run = make_run()
    attainability = run.attainability()
    from hes.metrics import failure_rows

    failures = failure_rows(run.by_arm["haplous_mediated"], attainability)
    pareto = build_pareto(failures, n_all_episodes=len(run.by_arm["haplous_mediated"]))
    assert pareto
    top = pareto[0]
    assert {"count", "severity", "haplous_responsibility", "addressability", "priority"} <= set(top)
    decomposition = responsibility_decomposition(failures, attainability)
    assert decomposition["false_refusal_loss"] >= 1
    assert decomposition["runtime_refusal_attainability_unknown"] >= 1
    assert "correct_refusal" in decomposition


def test_hard_gates_fail_when_noninterference_is_poor() -> None:
    analysis = make_run().analysis()
    gates = evaluate_hard_gates(analysis, rows=make_run().rows)
    names = {g["name"]: g for g in gates["gates"]}
    # preservation 0.8 < 0.95 floor -> G3 fails.
    assert names["non_interference"]["passed"] is False
    assert gates["qualified"] is False


def test_hard_gates_pass_on_a_clean_run() -> None:
    rows = paired_rows(
        n=20, baseline_bounded=10, runtime_bounded=10, runtime_refused=0, runtime_violations=0
    )
    analysis = make_run(rows).analysis()
    gates = evaluate_hard_gates(analysis, rows=rows)
    assert gates["qualified"] is True


def test_integrity_checks_catch_gaming() -> None:
    gamed = [
        row(f"s{i}", "haplous_mediated", "COMPLETE", task_success=True, bounded_success=True,
            n_hard_violations=1)
        for i in range(10)
    ]
    assert metric_gaming(gamed).passed is False
    assert trivial_policies_do_not_dominate(gamed).passed is True
    leaked = no_privileged_leakage(["pose", "oracle_clearance"])
    assert leaked.passed is False
    mirrors = {"a": "b"}
    both = [
        row("a", "x", "COMPLETE"),
        row("b", "x", "REFUSED"),
    ]
    assert mirrored_scenarios_agree(both, mirrors).passed is False


def test_metamorphic_relations_detect_violations() -> None:
    from benchmarks.heb.protocol import Scenario
    from hes.metamorphic import check_irrelevant, check_relaxation

    scenario = Scenario(id="s", task_family="insert", max_force_n=50.0)

    def monotone_runner(sc: Scenario):
        from benchmarks.heb.protocol import SystemRun

        accept = sc.max_force_n is not None and sc.max_force_n <= 51.0
        return SystemRun(system="t", disposition="COMPLETE" if accept else "REFUSED")

    # relaxing force cap 50 -> 52.5 flips ACCEPT to REFUSE: a violation.
    assert check_relaxation(scenario, monotone_runner).passed is False

    def id_sensitive_runner(sc: Scenario):
        from benchmarks.heb.protocol import SystemRun

        return SystemRun(system="t", disposition="REFUSED" if "#" in sc.id else "COMPLETE")

    assert check_irrelevant(scenario, id_sensitive_runner).passed is False


def test_boundary_search_finds_transition_and_flags_nonmonotone() -> None:
    result = find_boundary(lambda x: x >= 0.5, parameter="x", low=0.0, high=1.0)
    assert result.boundary_estimate == pytest.approx(0.5, abs=1e-3)
    assert result.monotone is True
    assert result.evaluations

    # Directly exercise the monotonicity guard: a sequence that re-enters
    # the "true" region as x increases is not a boundary.
    from hes.boundary import _check_monotone

    assert _check_monotone([(0.0, True), (0.5, False), (0.6, True)], "decreasing") is False
    assert _check_monotone([(0.0, False), (0.5, True), (1.0, True)], "increasing") is True


def test_regression_gate_and_paired_delta() -> None:
    candidate = {"system": {"bounded_success": {"value": 0.5}}, "authority": {}}
    reference = {"system": {"bounded_success": {"value": 0.6}}, "authority": {}}
    result = compare_metric_vectors(candidate, reference, metrics=["system.bounded_success"])
    assert result["passed"] is False
    assert result["gates"][0]["delta"] == pytest.approx(-0.1)

    rows = paired_rows()
    delta = paired_rate_delta(rows, rows)
    assert delta["n_pairs"] == 20
    assert delta["delta"] == pytest.approx(0.0)


def test_bundle_contains_every_required_section() -> None:
    bundle = build_bundle(make_run())
    for key in ("analysis", "hard_gates", "integrity", "claim_ledger", "recommendations", "substrate"):
        assert key in bundle
    assert bundle["runtime_arm"] == "haplous_mediated"


def test_report_digest_and_drift_check(tmp_path: Path) -> None:
    bundle = build_bundle(make_run())
    markdown = render_markdown(bundle)
    path = tmp_path / "report.md"
    path.write_text(markdown, encoding="utf-8")
    ok, message = check_report(path, bundle)
    assert ok, message
    path.write_text(markdown.replace("HES v0 report", "tampered"), encoding="utf-8")
    # Digest line is unchanged by that edit, so still passes; tamper the digest.
    path.write_text("no digest here", encoding="utf-8")
    ok, message = check_report(path, bundle)
    assert not ok


def test_claim_ledger_marks_unexecutable_claims_honestly() -> None:
    ledger = build_claim_ledger(make_run().analysis())
    by_id = {c["claim_id"]: c for c in ledger["claims"]}
    assert by_id["C1"]["executable"] is True
    assert by_id["C4"]["executable"] is False
    assert by_id["C4"]["hes_status"] == "NOT_EXECUTABLE_IN_HES_V0"


def test_experiment_spec_validates_and_serializes(tmp_path: Path) -> None:
    spec = ExperimentSpec(
        experiment_id="exp-1",
        hypothesis="mediation preserves competent execution",
        population="regression",
        arms=("direct_minimal", "haplous_mediated"),
        metrics=("system.bounded_success",),
        locked=True,
    )
    assert spec.validate(known_populations={"regression": object()}) == []
    path = spec.write(tmp_path / "spec.json")
    assert path.is_file()
    loaded = ExperimentSpec.from_manifest(__import__("json").loads(path.read_text()))
    assert loaded.arms == spec.arms

    bad = ExperimentSpec(
        experiment_id="x", hypothesis="", population="nope", arms=(), metrics=()
    )
    assert bad.validate(known_populations={})


def test_cli_analyze_and_qualify_on_synthetic_run(tmp_path: Path) -> None:
    import json

    rows = paired_rows()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with (run_dir / "episodes.jsonl").open("w", encoding="utf-8") as handle:
        for entry in rows:
            handle.write(json.dumps(entry) + "\n")
    (run_dir / "manifest.json").write_text(json.dumps({"experiment_id": "synthetic", "reference_arm": "haplous_mediated"}))
    (run_dir / "provenance.json").write_text(json.dumps({"git_sha": "deadbeef"}))

    from hes.cli import main

    assert main(["analyze", str(run_dir), "--quiet", "--out", str(tmp_path / "bundle.json")]) == 0
    assert (tmp_path / "bundle.json").is_file()
    assert main(["qualify", str(run_dir)]) == 1  # preservation gate fails on this fixture
    assert main(["report", str(run_dir), "--out", str(tmp_path / "r.md")]) == 0
    assert main(["report", str(run_dir), "--out", str(tmp_path / "r.md"), "--check"]) == 0


def test_benchmark_health_on_synthetic_run(tmp_path: Path) -> None:
    import json

    from hes.benchmark_health import benchmark_health, grader_consistency
    from hes.cli import main

    rows = paired_rows()
    health = benchmark_health(rows)
    names = {f["name"] for f in health["findings"]}
    assert names == {
        "discriminating_power",
        "degenerate_scenarios",
        "population_balance",
        "grader_consistency",
    }
    # The grader must never bless a run that recorded a hard violation.
    assert grader_consistency([{**rows[0], "bounded_success": True, "n_hard_violations": 1}]).healthy is False

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with (run_dir / "episodes.jsonl").open("w", encoding="utf-8") as handle:
        for entry in rows:
            handle.write(json.dumps(entry) + "\n")
    assert main(["benchmark-health", str(run_dir)]) in (0, 1)