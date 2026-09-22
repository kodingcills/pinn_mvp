"""Integration check against the committed v3 qualification package.

This pins the HES analysis of the real 1000-scenario / 3000-episode package
so a change in HES that silently reinterprets historical evidence fails.
It also pins the honesty properties: attainability is UNKNOWN where not
demonstrated, no scenario is claimed non-attainable, and hazard rejection
is reported NOT AVAILABLE.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hes.bundle import build_bundle
from hes.run import load_run

CAMPAIGN = Path(__file__).resolve().parent.parent / "results" / "v3_hardening_qualification"
pytestmark = pytest.mark.skipif(
    not (CAMPAIGN / "episodes.parquet").is_file(),
    reason="v3 qualification package not present",
)


@pytest.fixture(scope="module")
def bundle():
    return build_bundle(load_run(CAMPAIGN))


def test_campaign_loads_with_expected_shape(bundle) -> None:
    analysis = bundle["analysis"]
    assert analysis["n_scenarios"] == 1000
    assert analysis["n_episodes"] == 3000
    assert analysis["baseline_arm"] == "direct_minimal"
    assert set(analysis["arms"]) == {
        "direct_minimal",
        "competent_supervisor",
        "haplous_mediated",
    }


def test_haplous_headline_numbers_match_committed_summary(bundle) -> None:
    metric = bundle["analysis"]["metric_arm_vector"]["haplous_mediated"]
    assert metric["system"]["bounded_success"]["value"] == pytest.approx(0.182)
    assert metric["system"]["coverage"]["value"] == pytest.approx(0.573)


def test_attainability_is_honest(bundle) -> None:
    attain = bundle["analysis"]["attainability"]
    # 215 demonstrated-attainable; the remainder is UNKNOWN, not NON_ATTAINABLE.
    assert attain["counts"]["ATTAINABLE"] == 215
    assert "NON_ATTAINABLE" not in attain["counts"]
    assert attain["counts"]["UNKNOWN"] == 785


def test_execution_capture_is_attainment_relative(bundle) -> None:
    metric = bundle["analysis"]["metric_arm_vector"]["haplous_mediated"]
    assert metric["authority"]["execution_capture"]["value"] == pytest.approx(182 / 215, abs=1e-6)


def test_hazard_rejection_reports_not_available(bundle) -> None:
    metric = bundle["analysis"]["metric_arm_vector"]["haplous_mediated"]
    hazard = metric["authority"]["hazard_rejection"]
    assert hazard["value"] is None
    assert hazard["reason"] == "no_scenario_demonstrated_non_attainable"


def test_intervention_analysis_is_not_inverted(bundle) -> None:
    intervention = bundle["analysis"]["intervention"]
    assert intervention["counts"]["BENEFICIAL"] == 5
    assert intervention["counts"]["HARMFUL"] == 12
    assert intervention["counts"]["UNNECESSARY"] == 6
    # Harmful (destroyed success) exceeds beneficial (prevented breach) here.
    assert intervention["harmful_intervention_rate"]["value"] > intervention["intervention_precision"]["value"]


def test_bundle_reports_not_available_channels(bundle) -> None:
    not_available = bundle["analysis"]["not_available"]
    for channel in (
        "integrated_constraint_excursion",
        "collision_metrics",
        "decision_latency",
        "evidence_completeness",
    ):
        assert channel in not_available


def test_hard_gates_run_on_real_campaign(bundle) -> None:
    gates = bundle["hard_gates"]
    assert {"qualified", "gates"} <= set(gates)
    assert len(gates["gates"]) == 5


def test_runtime_arm_attribution_has_runtime_subcauses(bundle) -> None:
    attribution = bundle["analysis"]["metric_arm_vector"]["haplous_mediated"]["attribution"]
    assert attribution["by_runtime_subcause"]
    assert attribution["n_failures"] == 818