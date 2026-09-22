"""Rule-based next-experiment recommendation.

Given the current metric vector, attribution, and claim ledger, propose the
single experiment that most reduces decision-relevant uncertainty per unit
of cost. This is deterministic rules, not a learned policy, and every
recommendation names the uncertainty it would remove and why it dominates
the alternatives.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Recommendation:
    experiment: str
    removes_uncertainty: str
    rationale: str
    estimated_cost: str
    priority: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment": self.experiment,
            "removes_uncertainty": self.removes_uncertainty,
            "rationale": self.rationale,
            "estimated_cost": self.estimated_cost,
            "priority": self.priority,
        }


def _m(analysis: Mapping[str, Any], arm: str, path: str) -> float | None:
    node: Any = (analysis.get("metric_arm_vector") or {}).get(arm)
    if node is None:
        return None
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    if isinstance(node, Mapping):
        value = node.get("value")
        return float(value) if isinstance(value, (int, float)) else None
    return float(node) if isinstance(node, (int, float)) else None


def recommend_next(
    analysis: Mapping[str, Any],
    *,
    claim_ledger: Mapping[str, Any] | None = None,
    runtime_arm: str = "haplous_mediated",
    top_k: int = 3,
) -> list[dict[str, Any]]:
    recs: list[Recommendation] = []
    arms = list(analysis.get("arms") or ())
    baseline = analysis.get("baseline_arm")

    attainment = (analysis.get("attainability") or {}).get("counts") or {}
    n_unknown = int(attainment.get("UNKNOWN", 0))
    n_total = sum(int(v) for v in attainment.values()) or 1
    if n_unknown / n_total > 0.25:
        recs.append(
            Recommendation(
                "counterfactual_sweep_on_unknown_attainability",
                "attainability of the large UNKNOWN scenario mass",
                f"{n_unknown}/{n_total} scenarios have UNKNOWN attainability; "
                "every attainment-relative metric is bounded by this",
                "counterfactual forks on a stratified sample",
                0.5 + n_unknown / n_total,
            )
        )

    capture = _m(analysis, runtime_arm, "authority.execution_capture")
    coverage = _m(analysis, runtime_arm, "system.coverage")
    if capture is not None and capture < 0.9:
        recs.append(
            Recommendation(
                "capture_improvement_on_attainable_scenarios",
                "why demonstrated-attainable execution is not captured",
                f"execution capture is {capture:.3f}; the gap is a runtime regression target",
                "paired replay of the missed capturable scenarios",
                0.4 + (1.0 - capture),
            )
        )
    if coverage is not None and coverage < 0.8:
        recs.append(
            Recommendation(
                "refusal_cause_decomposition",
                "which refusals are correct vs productivity losses",
                f"coverage is {coverage:.3f}; need per-refusal counterfactual classification",
                "fork each refused scenario once",
                0.4 + (1.0 - coverage) * 0.5,
            )
        )

    intervention = analysis.get("intervention") or {}
    harmful = intervention.get("harmful_intervention_rate") or {}
    harmful_value = harmful.get("value") if isinstance(harmful, Mapping) else None
    if isinstance(harmful_value, (int, float)) and harmful_value > 0.2:
        recs.append(
            Recommendation(
                "harmful_intervention_root_cause",
                "which mediation episodes destroy working execution",
                f"harmful intervention rate {harmful_value:.3f} is high",
                "targeted replay of harmful forks with attribution",
                0.4 + float(harmful_value),
            )
        )

    if claim_ledger:
        non_exec = [
            c for c in claim_ledger.get("claims", ()) if not c.get("executable")
        ]
        if non_exec:
            recs.append(
                Recommendation(
                    "make_unexecutable_claims_executable",
                    "which registered claims are actually testable by HES",
                    f"{len(non_exec)} of {len(claim_ledger.get('claims', []))} claims have no executable test",
                    "1-2 days per claim to define an executable test",
                    0.3,
                )
            )

    supervisor = next((a for a in arms if "supervisor" in a), None)
    if supervisor:
        mine = _m(analysis, runtime_arm, "system.bounded_success")
        theirs = _m(analysis, supervisor, "system.bounded_success")
        if mine is not None and theirs is not None and mine <= theirs:
            recs.append(
                Recommendation(
                    "incumbent_differentiation_head_to_head",
                    "whether the runtime differentiates from a competent supervisor",
                    f"BSR {mine:.3f} vs supervisor {theirs:.3f}; no differentiation shown",
                    "preregistered head-to-head on a fresh population",
                    0.6,
                )
            )

    recs.sort(key=lambda r: -r.priority)
    if not recs:
        recs.append(
            Recommendation(
                "replicate_current_campaign",
                "reproducibility of the current result",
                "no dominant weakness detected; the next risk is reproducibility",
                "rerun the campaign with a fresh seed",
                0.1,
            )
        )
    return [r.as_dict() for r in recs[:top_k]]