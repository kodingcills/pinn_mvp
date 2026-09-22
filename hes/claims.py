"""Claim ledger: claim -> test -> metric -> evidence -> uncertainty.

Claims are read from the existing ``evaluation/claims.yaml`` registry (HES
does not fork the registry). For each claim HES reports whether a
machine-checkable test exists and, when one does, derives a status from the
HES metric vector. Claims whose support condition genuinely cannot be
separated from the artifact (recovery, latency, kernel portability) are
reported as not executable from the current evidence rather than assigned a
convenient status.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from evaluation.registry import load_claims

_REGISTRY = Path(__file__).resolve().parent.parent / "evaluation" / "claims.yaml"


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


_BASELINE_ALIASES = {
    "direct_upstream_stream": "direct_minimal",
    "bare_behavior": "direct_minimal",
    "bare_behavior_no_recovery": "direct_minimal",
    "frozen_policy_direct": "direct_minimal",
    "direct_loop": "direct_minimal",
}


def _resolve_arm(analysis: Mapping[str, Any], name: str) -> str | None:
    arms = set(analysis.get("arms") or ())
    if name in arms:
        return name
    alias = _BASELINE_ALIASES.get(name)
    if alias in arms:
        return alias
    return None


def _c1(analysis: Mapping[str, Any], arm: str, baselines: list[str]) -> tuple[str, list[str]]:
    notes: list[str] = []
    statuses: list[str] = []
    seen: set[str] = set()
    for name in baselines:
        baseline = _resolve_arm(analysis, name)
        if baseline is None or baseline in seen:
            notes.append(f"baseline {name} has no paired arm in this run")
            continue
        seen.add(baseline)
        preservation = _m(analysis, arm, "non_interference.preservation_rate")
        if preservation is None:
            notes.append(f"no paired scenarios with {baseline}")
            continue
        statuses.append("SUPPORTED_WITHIN_TESTED_SCOPE" if preservation >= 0.99 else "WEAKENED")
        notes.append(f"preservation vs {baseline} = {preservation:.3f}")
    if not statuses:
        return "UNKNOWN", notes
    return ("SUPPORTED_WITHIN_TESTED_SCOPE" if all(s.startswith("SUPPORTED") for s in statuses) else "WEAKENED"), notes


def _c2(analysis: Mapping[str, Any], arm: str, baselines: list[str]) -> tuple[str, list[str]]:
    notes: list[str] = []
    haplous_viol = _m(analysis, arm, "authority.violation_given_acceptance")
    haplous_cov = _m(analysis, arm, "system.coverage")
    if haplous_viol is None or haplous_cov is None:
        return "UNKNOWN", ["violation/coverage unavailable"]
    decisive = False
    for baseline in baselines:
        base_viol = _m(analysis, baseline, "authority.violation_given_acceptance")
        base_cov = _m(analysis, baseline, "system.coverage")
        if base_viol is None or base_cov is None:
            continue
        notes.append(
            f"vs {baseline}: violation {haplous_viol:.3f} vs {base_viol:.3f}; "
            f"coverage {haplous_cov:.3f} vs {base_cov:.3f}"
        )
        if haplous_cov >= 0.9 * base_cov and haplous_viol <= base_viol:
            decisive = True
        if haplous_viol > base_viol and haplous_cov <= base_cov:
            return "REFUTED_WITHIN_TESTED_SCOPE", notes + ["more violations at no more coverage"]
    if decisive:
        return "SUPPORTED_WITH_CAVEATS", notes
    return "WEAKENED", notes


def _c3(analysis: Mapping[str, Any], arm: str, baselines: list[str]) -> tuple[str, list[str]]:
    notes: list[str] = []
    for baseline in baselines:
        dominated = True
        for path in ("system.bounded_success", "system.coverage", "authority.violation_given_acceptance"):
            mine = _m(analysis, arm, path)
            theirs = _m(analysis, baseline, path)
            if mine is None or theirs is None:
                dominated = False
                break
        if dominated:
            bs_m = _m(analysis, arm, "system.bounded_success") or 0.0
            bs_b = _m(analysis, baseline, "system.bounded_success") or 0.0
            cov_m = _m(analysis, arm, "system.coverage") or 0.0
            cov_b = _m(analysis, baseline, "system.coverage") or 0.0
            viol_m = _m(analysis, arm, "authority.violation_given_acceptance") or 0.0
            viol_b = _m(analysis, baseline, "authority.violation_given_acceptance") or 0.0
            if bs_m >= bs_b and cov_m >= cov_b and viol_m <= viol_b:
                notes.append(f"{arm} weakly dominates {baseline} on (BSR, coverage, violation)")
                continue
        notes.append(f"{arm} does not dominate {baseline} on (BSR, coverage, violation)")
    return ("SUPPORTED_WITH_CAVEATS" if any("dominates" in n and "not" not in n for n in notes) else "WEAKENED"), notes


def _c11(analysis: Mapping[str, Any], arm: str, baselines: list[str]) -> tuple[str, list[str]]:
    supervisor = next((b for b in baselines if "supervisor" in b), None)
    if supervisor is None:
        supervisor = "competent_supervisor" if "competent_supervisor" in (analysis.get("arms") or ()) else None
    if supervisor is None:
        return "UNKNOWN", ["no competent supervisor arm present"]
    mine = _m(analysis, arm, "non_interference.preservation_rate")
    theirs = _m(analysis, supervisor, "non_interference.preservation_rate")
    notes = [
        f"vs {supervisor}: preservation {mine} vs {theirs}",
        f"BSR {_m(analysis, arm, 'system.bounded_success')} vs {_m(analysis, supervisor, 'system.bounded_success')}",
        f"violation {_m(analysis, arm, 'authority.violation_given_acceptance')} vs {_m(analysis, supervisor, 'authority.violation_given_acceptance')}",
    ]
    return "WEAKENED", notes


_EXECUTABLE: dict[str, Callable[[Mapping[str, Any], str, list[str]], tuple[str, list[str]]]] = {
    "C1": _c1,
    "C2": _c2,
    "C3": _c3,
    "C11": _c11,
}

_NOT_EXECUTABLE = {
    "C4": "recovery channels are absent from canonical episode rows",
    "C5": "requires a controller-candidate tournament, not an execution campaign",
    "C6": "requires an external learned-policy platform run",
    "C7": "requires boundary sweeps; run `hes boundary` and feed the envelope",
    "C8": "requires cross-adapter code-diff evidence, not an execution campaign",
    "C9": "requires loop-latency rows, not canonical episode rows",
    "C10": "requires the reliability fuzz/soak harness, not an execution campaign",
    "C12": "requires cross-engine pairing, not a single-engine campaign",
}


def build_claim_ledger(
    analysis: Mapping[str, Any],
    *,
    arm: str | None = None,
    registry_path: str | Path = _REGISTRY,
) -> dict[str, Any]:
    registry = load_claims(registry_path)
    arm = arm or analysis.get("baseline_arm") or "haplous_mediated"
    # Prefer the Haplous arm explicitly when present.
    if "haplous_mediated" in (analysis.get("arms") or ()):
        arm = "haplous_mediated"
    entries: list[dict[str, Any]] = []
    for claim in registry:
        baselines = list(claim.baselines)
        if claim.claim_id in _EXECUTABLE:
            status, notes = _EXECUTABLE[claim.claim_id](analysis, arm, baselines)
            entries.append(
                {
                    "claim_id": claim.claim_id,
                    "name": claim.name,
                    "statement": claim.statement,
                    "registry_status": claim.epistemic_status,
                    "hes_status": status,
                    "executable": True,
                    "arm": arm,
                    "metrics": list(claim.primary_metrics),
                    "evidence": list(claim.current_evidence),
                    "uncertainty": "derived from a single internal campaign; see caveats",
                    "counterexamples": [],
                    "next_experiment": "replicate on a held-out population",
                    "notes": notes,
                }
            )
        else:
            entries.append(
                {
                    "claim_id": claim.claim_id,
                    "name": claim.name,
                    "statement": claim.statement,
                    "registry_status": claim.epistemic_status,
                    "hes_status": "NOT_EXECUTABLE_IN_HES_V0",
                    "executable": False,
                    "arm": arm,
                    "metrics": list(claim.primary_metrics),
                    "evidence": list(claim.current_evidence),
                    "uncertainty": "not separated from the artifact by this evidence",
                    "counterexamples": [],
                    "next_experiment": _NOT_EXECUTABLE.get(claim.claim_id, "define an executable test"),
                    "notes": [_NOT_EXECUTABLE.get(claim.claim_id, "no executable test registered")],
                }
            )
    return {"arm": arm, "claims": entries}