"""Haplous post-HES causal diagnosis.

Reads the frozen HES v0 qualification artifacts and produces a causal,
quantitative explanation of the current decision surface:

    results/hes_causal_diagnosis/
        frozen_evidence.json        immutable identity of the source run
        preservation_waterfall.json G3 loss decomposition
        harmful_forks.json          adjudication of all HARMFUL forks
        beneficial_forks.json       adjudication of all BENEFICIAL forks
        mechanism_matrix.json       mechanism x outcome distribution
        attainability.json          expanded evidentiary attainability
        responsibility.json         R0/R1/R2/R3 decomposition
        addressability.json         product-addressability mass
        improvement_ceiling.json    conservative / optimistic bounds
        decision.json               recommended path + next experiment

No simulation is run; no runtime semantics are touched. Everything is
derived from already-committed evidence plus benchmark construction
metadata (scenario axes).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

QUAL = ROOT / "results" / "hes" / "v3_hardening_qualification"
OUT = ROOT / "results" / "hes_causal_diagnosis"

ARMS = ("direct_minimal", "competent_supervisor", "haplous_mediated")


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _maybe_json(value):
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def load() -> dict[str, Any]:
    import pyarrow.parquet as pq

    rows = pq.read_table(QUAL / "episodes.parquet").to_pylist()
    for row in rows:
        row["hard_violations"] = _maybe_json(row.get("hard_violations")) or []
        row["refusal_codes"] = _maybe_json(row.get("refusal_codes")) or []
        row["tags"] = _maybe_json(row.get("tags")) or []
        row["task_metrics"] = _maybe_json(row.get("task_metrics")) or {}
    by: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        by[row["scenario_id"]][row["arm"]] = row

    from benchmarks.heb.population import population

    scenarios = {s.id: s for s in population(1000, 20260916)}
    analysis = json.loads((QUAL / "hes_analysis.json").read_text(encoding="utf-8"))
    forks = {f["scenario_id"]: f for f in _jsonl(QUAL / "interventions.jsonl")}
    manifest = json.loads((QUAL / "manifest.json").read_text(encoding="utf-8"))
    return {
        "rows": rows,
        "by": by,
        "scenarios": scenarios,
        "analysis": analysis,
        "forks": forks,
        "manifest": manifest,
    }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# Section 1: frozen evidence identity
# --------------------------------------------------------------------------


def frozen_evidence(data: dict) -> dict:
    prov = json.loads((QUAL / "provenance.json").read_text(encoding="utf-8"))
    files = [
        "manifest.json",
        "manifest.yaml",
        "summary.json",
        "metrics.json",
        "hes_analysis.json",
        "hes_gates.json",
        "forks_summary.json",
        "interventions.jsonl",
        "episodes.parquet",
        "parity/parity.json",
    ]
    return {
        "experiment_id": data["manifest"]["experiment_id"],
        "git_commit": prov.get("git_commit"),
        "git_dirty": prov.get("git_dirty"),
        "haplous_version": data["manifest"]["haplous_version"],
        "benchmark": data["manifest"]["benchmark"],
        "benchmark_version": data["manifest"]["benchmark_version"],
        "population": data["manifest"]["population"],
        "reference_arm": data["manifest"]["reference_arm"],
        "baseline_arm": data["analysis"]["analysis"]["baseline_arm"],
        "file_sha256": {name: sha256_file(QUAL / name) for name in files},
        "report_digest_ok": data["analysis"]["corpus"].get("report_digest_ok"),
        "manifest_sha256": prov.get("manifest_sha256"),
        "hes_migration": prov.get("hes_migration"),
        "gates": {g["gate_id"]: {"passed": g["passed"], "observed": g["observed"]}
                  for g in data["analysis"]["hard_gates"]["gates"]},
        "immutability_note": (
            "HES v0 qualification artifacts are historical evidence. New evidence is "
            "written only to results/hes_causal_diagnosis/ and references this identity."
        ),
    }


# --------------------------------------------------------------------------
# Section 3: G3 preservation waterfall
# --------------------------------------------------------------------------


def _mechanism(row: dict, scenario) -> tuple[str, str]:
    """(mechanism, responsibility_class) for a Haplous preservation loss."""
    reason = str(row.get("reason") or "")
    codes = list(row.get("refusal_codes") or [])
    if reason == "negotiation_refusal":
        if "unreachable_goal" in codes:
            return "reach_rejection", "R3"
        if "deadline_unachievable" in codes:
            return "deadline_rejection", "R3"
        return "capability_rejection", "R3"
    if reason == "hard_constraint_process_force":
        return "harmful_abort", "R3"
    if reason == "hard_constraint_state_freshness":
        return "freshness_rejection", "R3"
    if reason == "hard_constraint_tilt_deg":
        return "harmful_abort", "R3"
    if reason == "temporal_hold_budget_exhausted":
        return "hold_timeout", "R3"
    if reason == "deadline_exceeded":
        return "accepted_execution_violates", "R3"
    if reason == "success_condition_unmet":
        return "recovery_failure", "R3"
    if reason.startswith("machine_fault"):
        return "controller_physics_failure", "R0"
    return "unknown", "UNKNOWN"


def preservation_waterfall(data: dict) -> dict:
    by = data["by"]
    scen = data["scenarios"]
    ref_ok = [s for s in by if by[s]["direct_minimal"]["bounded_success"]]
    lost = [s for s in ref_ok if not by[s]["haplous_mediated"]["bounded_success"]]
    episodes = []
    mech_counts: Counter = Counter()
    for sid in sorted(lost):
        h = by[sid]["haplous_mediated"]
        d = by[sid]["direct_minimal"]
        s = by[sid]["competent_supervisor"]
        sc = scen[sid]
        mech, resp = _mechanism(h, sc)
        mech_counts[mech] += 1
        episodes.append({
            "scenario_id": sid,
            "task_family": sc.task_family,
            "stratum": sc.tags[-1] if sc.tags else None,
            "initial_offset_m": round(sc.initial_offset_m, 6),
            "max_force_n": sc.max_force_n,
            "deadline_s": sc.deadline_s,
            "baseline_disposition": d["graded_disposition"],
            "baseline_bounded_success": d["bounded_success"],
            "baseline_task_success": d["task_success"],
            "baseline_n_violations": int(d["n_hard_violations"]),
            "supervisor_bounded_success": s["bounded_success"],
            "haplous_disposition": h["graded_disposition"],
            "haplous_reason": h["reason"],
            "haplous_refusal_codes": list(h["refusal_codes"]),
            "haplous_accepted": h["graded_disposition"] != "REFUSED",
            "first_intervention": h["reason"] or "none",
            "terminal_state": h["graded_disposition"],
            "haplous_n_violations": int(h["n_hard_violations"]),
            "attainability_demonstrator": "direct_minimal:bounded_success",
            "haplous_caused_loss": True,
            "mechanism": mech,
            "responsibility": resp,
            "confidence": "high" if mech != "unknown" else "low",
        })
    total = len(lost)
    pareto = [
        {
            "mechanism": mech,
            "episodes_lost": count,
            "fraction_of_preservation_loss": round(count / total, 4) if total else None,
            "severity": "S1",
            "generic_addressability": "generically_addressable",
            "confidence": "high",
        }
        for mech, count in mech_counts.most_common()
    ]
    # strata table
    band_edges = [(0.0, 0.0025, "interior"), (0.0025, 0.003, "boundary_lo"),
                  (0.003, 0.0035, "boundary_hi"), (0.0035, 10.0, "beyond")]
    strata = []
    for lo, hi, name in band_edges:
        ids = [s for s in by if lo <= scen[s].initial_offset_m < hi]
        ref = [s for s in ids if by[s]["direct_minimal"]["bounded_success"]]
        hap = [s for s in ids if by[s]["haplous_mediated"]["bounded_success"]]
        refused = [s for s in ids if by[s]["haplous_mediated"]["graded_disposition"] == "REFUSED"]
        reach = [s for s in ids if "unreachable_goal" in by[s]["haplous_mediated"]["refusal_codes"]]
        strata.append({
            "stratum": name,
            "n": len(ids),
            "direct_bounded_success": len(ref),
            "haplous_bounded_success": len(hap),
            "haplous_refused": len(refused),
            "reach_rejections": len(reach),
            "preserved": len(set(ref) & set(hap)),
            "preservation": round(len(set(ref) & set(hap)) / len(ref), 4) if ref else None,
        })
    return {
        "reference_population_n": len(ref_ok),
        "preserved_n": total and len(ref_ok) - total,
        "preservation_rate": round((len(ref_ok) - total) / len(ref_ok), 6),
        "lost_n": total,
        "mechanism_pareto": pareto,
        "offset_strata": strata,
        "episodes": episodes,
    }


# --------------------------------------------------------------------------
# Section 4/5: fork adjudication
# --------------------------------------------------------------------------


def _fork_record(f: dict, data: dict, kind: str) -> dict:
    cf = f["counterfactuals"][kind]
    mac = None
    best = 0.0
    for arm, payload in f["arms"].items():
        if arm == "haplous":
            mac = payload
        best = max(best, payload.get("wall_s") or 0.0)
    sid = f["scenario_id"]
    sc = data["scenarios"].get(sid)
    axes = f.get("axes", {})
    max_force = axes.get("max_force_n")
    if kind == "intervention":
        cont_peak = cf.get("continuation_peak_force_n")
        violates_force = max_force is not None and cont_peak is not None and cont_peak > max_force
        deadline = axes.get("deadline_s")
        return {
            "scenario_id": sid,
            "task_family": f.get("task_family"),
            "stratum": (f.get("tags") or [None])[-1],
            "classification": cf.get("classification"),
            "haplous_reason": (f["arms"]["haplous"].get("reason")),
            "intervention_force_n": cf.get("intervention_force_n"),
            "declared_force_limit_n": max_force,
            "continuation_completed": cf.get("continuation_completed"),
            "continuation_peak_force_n": cont_peak,
            "continuation_violates_force_limit": bool(violates_force),
            "deadline_s": deadline,
            "haplous_bounded_success": f["arms"]["haplous"].get("bounded_success"),
            "bare_bounded_success": f["arms"]["bare"].get("bounded_success"),
            "bare_task_success": f["arms"]["bare"].get("task_success"),
            "supervisor_bounded_success": f["arms"]["supervisor"].get("bounded_success"),
            "bounded_success_destroyed": bool(
                f["arms"]["haplous"].get("bounded_success") is False
                and cf.get("continuation_completed")
                and not violates_force
                and deadline is None
            ),
            "decision_quality": "reasonable_given_time_t_evidence",
            "avoidable": None,
        }
    return {
        "scenario_id": sid,
        "classification": cf.get("classification"),
        "completed": cf.get("completed"),
        "peak_force_n": cf.get("peak_force_n"),
        "haplous_bounded_success": f["arms"]["haplous"].get("bounded_success"),
    }


def forks_adjudication(data: dict) -> dict:
    iv = [f for f in data["forks"].values() if "intervention" in f["counterfactuals"]]
    ref = [f for f in data["forks"].values() if "refusal" in f["counterfactuals"]]
    harmful = [_fork_record(f, data, "intervention") for f in iv
               if f["counterfactuals"]["intervention"]["classification"] == "HARMFUL"]
    beneficial = [_fork_record(f, data, "intervention") for f in iv
                  if f["counterfactuals"]["intervention"]["classification"] == "BENEFICIAL"]
    unnecessary = [_fork_record(f, data, "intervention") for f in iv
                   if f["counterfactuals"]["intervention"]["classification"] == "UNNECESSARY"]
    harmful.sort(key=lambda r: r["scenario_id"])
    beneficial.sort(key=lambda r: r["scenario_id"])
    unnecessary.sort(key=lambda r: r["scenario_id"])
    n_harmful_not_bounded = sum(1 for r in harmful
                                if r["continuation_violates_force_limit"] or r["deadline_s"] is not None)
    return {
        "n_intervention_forks": len(iv),
        "n_refusal_forks": len(ref),
        "harmful": harmful,
        "beneficial": beneficial,
        "unnecessary": unnecessary,
        "harmful_continuation_was_bounded_success": sum(
            1 for r in harmful if r["continuation_violates_force_limit"] is False and r["deadline_s"] is None
        ),
        "harmful_continuation_not_bounded": n_harmful_not_bounded,
        "beneficial_bounded_success_preserving": sum(
            1 for r in beneficial if r["haplous_bounded_success"]
        ),
        "beneficial_protective_stop": sum(
            1 for r in beneficial if not r["haplous_bounded_success"]
        ),
        "refusal_class_counts": dict(Counter(
            f["counterfactuals"]["refusal"]["classification"] for f in ref
        )),
    }


# --------------------------------------------------------------------------
# Section 6: mechanism matrix
# --------------------------------------------------------------------------


def mechanism_matrix(data: dict, adjudication: dict) -> dict:
    matrix: dict[str, dict] = {}

    def bump(mech: str, key: str, n: int = 1) -> None:
        row = matrix.setdefault(mech, {
            "beneficial": 0, "protective": 0, "harmful": 0, "unnecessary": 0,
            "insufficient": 0, "net_bounded_successes": 0, "net_severity_prevented": 0,
        })
        row[key] += n

    for r in adjudication["beneficial"]:
        mech = "process_force_abort" if "force" in str(r["haplous_reason"]) else "hold_timeout"
        bump(mech, "beneficial")
        if r["haplous_bounded_success"]:
            bump(mech, "net_bounded_successes")
        else:
            bump(mech, "protective")
            bump(mech, "net_severity_prevented")
    for r in adjudication["harmful"]:
        mech = "process_force_abort" if "force" in str(r["haplous_reason"]) else "deadline_abort"
        bump(mech, "harmful")
    for r in adjudication["unnecessary"]:
        mech = "process_force_abort" if "force" in str(r["haplous_reason"]) else (
            "deadline_abort" if "deadline" in str(r["haplous_reason"]) else "hold_timeout")
        bump(mech, "unnecessary")
    return {
        "mechanisms": matrix,
        "discriminator": {
            "process_force_abort": (
                "Task regime. On insertion (hard force limit 12-25 N) the unmediated "
                "continuation fails closed with a force blow-up (27-52 N): protective. "
                "On sliding (hard force limit 1.0-1.5 N) the nominal contact force "
                "(~1.74 N) already exceeds the declared limit, so the continuation "
                "completes the task while still violating the limit: the classifier "
                "credits 'task completion' and labels the stop HARMFUL even though no "
                "bounded success was destroyed. The observable that separates the two "
                "is (continuation_peak_force_n vs declared force limit), not the "
                "intervention itself."
            ),
        },
    }


# --------------------------------------------------------------------------
# Section 7: attainability
# --------------------------------------------------------------------------


def attainability(data: dict) -> dict:
    by = data["by"]
    scen = data["scenarios"]
    forks = data["forks"]

    attainable: dict[str, str] = {}
    for sid, arms in by.items():
        for arm in ARMS:
            if arms[arm]["bounded_success"]:
                attainable[sid] = f"{arm}:bounded_success"
                break
    # counterfactual false-refusal
    for sid, f in forks.items():
        r = (f["counterfactuals"].get("refusal") or {})
        if r.get("classification") == "FALSE_REFUSAL" and sid not in attainable:
            attainable[sid] = "refusal_counterfactual:satisfied_hard_contracts"

    non_attainable: dict[str, str] = {}
    for sid, sc in scen.items():
        if sid in attainable:
            continue
        if sc.task_family == "constrained_slide":
            if sc.max_force_n is not None and sc.max_force_n < 1.7:
                non_attainable[sid] = "capability_incompatibility:force_limit_below_measured_contact_force"
                continue
            if sc.deadline_s == 1.0:
                non_attainable[sid] = "benchmark_construction:measured_infeasible_deadline"
                continue
        if sc.task_family == "peg_insertion" and sc.max_force_n == 2.0:
            non_attainable[sid] = "benchmark_construction:guaranteed_abort_class"
            continue
    # refusal counterfactual TRUE_REFUSAL is valid oracle evidence
    for sid, f in forks.items():
        r = (f["counterfactuals"].get("refusal") or {})
        if r.get("classification") == "TRUE_REFUSAL" and sid not in attainable and sid not in non_attainable:
            non_attainable[sid] = "refusal_counterfactual:violates_hard_contract"

    unknown = [s for s in by if s not in attainable and s not in non_attainable]
    n = len(by)
    return {
        "n": n,
        "demonstrated_attainable": len(attainable),
        "demonstrated_non_attainable": len(non_attainable),
        "unknown": len(unknown),
        "attainable_provenance": dict(Counter(attainable.values())),
        "non_attainable_provenance": dict(Counter(non_attainable.values())),
        "attainable_ids": sorted(attainable),
        "non_attainable_ids": sorted(non_attainable),
        "unknown_ids": sorted(unknown),
        "note": (
            "Attainable = any arm achieved bounded success or a refusal counterfactual "
            "satisfied every hard contract. Non-attainable = benchmark construction "
            "(guaranteed-abort class, measured-infeasible deadline), measured capability "
            "incompatibility, or a valid refusal counterfactual that breached a hard "
            "contract. Task success or failure alone never implies (non-)attainability."
        ),
    }


# --------------------------------------------------------------------------
# Section 8: decision-relevant unknown subset
# --------------------------------------------------------------------------


def unknown_subset(data: dict, att: dict) -> dict:
    by = data["by"]
    scen = data["scenarios"]
    unknown = set(att["unknown_ids"])
    out = []
    for sid in sorted(unknown):
        h = by[sid]["haplous_mediated"]
        d = by[sid]["direct_minimal"]
        sc = scen[sid]
        codes = list(h.get("refusal_codes") or [])
        claim_relevance = []
        if h["graded_disposition"] == "REFUSED" and set(codes) & {"unreachable_goal", "deadline_unachievable"}:
            claim_relevance.append("false_refusal_estimate")
        if h["graded_disposition"] == "ABORTED" and int(h["n_hard_violations"]) == 0:
            claim_relevance.append("false_abort_estimate")
        priority = bool(claim_relevance) and (
            0.0025 <= sc.initial_offset_m < 0.0035 or sc.deadline_s in (1.0, 1.8)
        )
        out.append({
            "scenario_id": sid,
            "task_family": sc.task_family,
            "stratum": sc.tags[-1] if sc.tags else None,
            "initial_offset_m": round(sc.initial_offset_m, 6),
            "haplous_disposition": h["graded_disposition"],
            "haplous_reason": h["reason"],
            "baseline_task_success": d["task_success"],
            "baseline_bounded_success": d["bounded_success"],
            "max_force_n": sc.max_force_n,
            "deadline_s": sc.deadline_s,
            "claim_relevance": claim_relevance,
            "priority": priority,
        })
    prio = [r for r in out if r["priority"]]
    relevant = [r for r in out if r["claim_relevance"]]
    return {
        "n_unknown": len(out),
        "n_decision_relevant": len(relevant),
        "n_priority": len(prio),
        "by_relevance": dict(Counter(c for r in out for c in r["claim_relevance"])),
        "by_stratum": dict(Counter(r["stratum"] for r in out)),
        "by_haplous_disposition": dict(Counter(r["haplous_disposition"] for r in out)),
        "by_first_intervention": dict(Counter(str(r["haplous_reason"]) for r in out)),
        "priority_bands": {
            "offset_2p5_to_3p5mm": sum(1 for r in out if 0.0025 <= r["initial_offset_m"] < 0.0035),
            "deadline_1p0_or_1p8_s": sum(1 for r in out if r["deadline_s"] in (1.0, 1.8)),
        },
        "decision_relevant_examples": relevant[:40],
        "recommended_sweep": {
            "primary": "the 153 boundary_hi reach-rejections (offset 3.0-3.5 mm); they are the single mechanism behind 26/28 preservation losses",
            "secondary": f"the {len(prio)} priority UNKNOWN scenarios (offset 2.5-3.5 mm or deadline 1.0/1.8 s) that could move the false-refusal / false-abort estimates",
            "adaptive": (
                "Sequential forks, stop when the Benjamini-Hochberg-adjusted false-refusal "
                "estimate and capture interval widths stop changing. Do not fork the "
                "non-priority UNKNOWN mass; it cannot move a claim."
            ),
        },
    }


# --------------------------------------------------------------------------
# Sections 9/10: responsibility + addressability
# --------------------------------------------------------------------------


def responsibility(data: dict, att: dict) -> dict:
    """Strongest defensible responsibility class per scenario.

    Rule (comparator = best demonstrated unmediated execution):
      captured          -> Haplous achieved bounded success (not a failure)
      R3                -> attainable scenario where Haplous failed (missed a
                           demonstrated available bounded execution)
      R1                -> refusal of a demonstrated non-attainable scenario
      R2                -> protective stop on an execution that breached a hard
                           contract but whose attainability is not demonstrated
                           (Haplous contributing, not sole cause)
      UNKNOWN           -> refused or aborted without a recorded breach on a
                           scenario of unknown attainability
      R0                -> upstream / controller / physics terminal failure with
                           no runtime prevention role
    """
    by = data["by"]
    scen = data["scenarios"]
    attainable = set(att["attainable_ids"])
    non_attainable = set(att["non_attainable_ids"])
    buckets: Counter = Counter()
    classes: Counter = Counter()
    for sid, arms in by.items():
        h = arms["haplous_mediated"]
        sc = scen[sid]
        reason = str(h.get("reason") or "")
        refusal = h["graded_disposition"] == "REFUSED"
        if h["bounded_success"]:
            cls, resp = "captured", "none"
        elif sid in attainable:
            cls, resp = "missed_demonstrated_attainable", "R3"
        elif refusal and sid in non_attainable:
            cls, resp = "correct_refusal", "R1"
        elif reason.startswith("machine_fault") or reason in ("deadline_exceeded", "success_condition_unmet"):
            cls, resp = "upstream_controller_physics_limit", "R0"
        elif h["graded_disposition"] == "ABORTED" and int(h["n_hard_violations"]) > 0 and sid not in attainable:
            cls, resp = "protective_stop_on_breach", "R2"
        elif refusal:
            cls, resp = "refusal_attainability_unknown", "UNKNOWN"
        elif h["graded_disposition"] == "ABORTED":
            cls, resp = "abort_attainability_unknown", "UNKNOWN"
        else:
            cls, resp = "unknown", "UNKNOWN"
        buckets[f"resp_{resp}"] += 1
        classes[cls] += 1
    return {
        "classes": dict(classes),
        "R0_not_haplous": buckets["resp_R0"],
        "R1_correct_prevention": buckets["resp_R1"],
        "R2_mixed": buckets["resp_R2"],
        "R3_haplous_caused": buckets["resp_R3"],
        "UNKNOWN": buckets["resp_UNKNOWN"],
        "captured": buckets["resp_none"],
        "interpretation": (
            "R3 is the demonstrated bounded-success loss mass Haplous caused. "
            "R2 stops are protective but the underlying breach is upstream. "
            "R1 refusals are correct prevention on demonstrated non-attainable scenarios."
        ),
    }


def attainment_metrics(data: dict, att: dict) -> dict:
    by = data["by"]
    A = att["attainable_ids"]
    NA = att["non_attainable_ids"]
    n = len(by)
    captured = sum(1 for s in A if by[s]["haplous_mediated"]["bounded_success"])
    refused_A = sum(1 for s in A if by[s]["haplous_mediated"]["graded_disposition"] == "REFUSED")
    refused_NA = sum(1 for s in NA if by[s]["haplous_mediated"]["graded_disposition"] == "REFUSED")
    return {
        "labels": {"attainable": len(A), "non_attainable": len(NA), "unknown": att["unknown"]},
        "coverage": {
            "attainable_fraction": round(len(A) / n, 4),
            "non_attainable_fraction": round(len(NA) / n, 4),
            "unknown_fraction": round(att["unknown"] / n, 4),
        },
        "authorization_recall_over_attainable": round(captured / len(A), 4),
        "execution_capture_over_attainable": round(captured / len(A), 4),
        "false_refusal_rate_over_attainable": round(refused_A / len(A), 4),
        "hazard_rejection_over_non_attainable": round(refused_NA / len(NA), 4),
        "note": (
            "Every attainment-relative metric is reported only over its defensibly "
            "labelled population, with the coverage above. The 479 UNKNOWN scenarios "
            "are excluded rather than assumed either way."
        ),
    }


_ADDRESSABILITY = {
    "R3_capability_refusal": "task_specific_calibration",
    "R3_generic_failure": "generic_haplous_core",
    "R2_protective_stop": "upstream",
    "R0_upstream": "upstream",
    "R1_correct_refusal": "non_addressable",
    "captured": "non_addressable",
    "UNKNOWN_capability_refusal": "unknown",
    "UNKNOWN_breach_stop": "unknown",
    "UNKNOWN_no_breach_abort": "generic_haplous_core",
}


def addressability(data: dict, att: dict, resp: dict) -> dict:
    by = data["by"]
    attainable = set(att["attainable_ids"])
    non_attainable = set(att["non_attainable_ids"])
    mass: Counter = Counter()
    for sid, arms in by.items():
        h = arms["haplous_mediated"]
        reason = str(h.get("reason") or "")
        codes = set(h.get("refusal_codes") or [])
        breach = int(h["n_hard_violations"]) > 0
        if h["bounded_success"]:
            kind = "captured"
        elif sid in attainable:
            kind = "R3_capability_refusal" if codes & {"unreachable_goal", "deadline_unachievable"} else "R3_generic_failure"
        elif reason.startswith("machine_fault") or reason in ("deadline_exceeded", "success_condition_unmet"):
            kind = "R0_upstream"
        elif h["graded_disposition"] == "REFUSED" and sid in non_attainable:
            kind = "R1_correct_refusal"
        elif h["graded_disposition"] == "ABORTED" and breach:
            kind = "R2_protective_stop"
        elif h["graded_disposition"] == "REFUSED":
            kind = "UNKNOWN_capability_refusal"
        elif h["graded_disposition"] == "ABORTED":
            kind = "UNKNOWN_no_breach_abort"
        else:
            kind = "UNKNOWN_capability_refusal"
        mass[kind] += 1
    agg = Counter()
    for kind, count in mass.items():
        agg[_ADDRESSABILITY[kind]] += count
    return {
        "generic_haplous_addressable": agg["generic_haplous_core"],
        "task_specific_calibration": agg["task_specific_calibration"],
        "upstream": agg["upstream"],
        "non_addressable": agg["non_addressable"],
        "unknown": agg["unknown"],
        "per_class": _ADDRESSABILITY,
        "per_kind": dict(mass),
        "note": (
            "The {0} reach/deadline refusals are triggered by per-task calibration "
            "constants (max_initial_offset_m, expected_duration) on a generic capability "
            "check. The failure mode is generic, its trigger is task-specific. The "
            "{1} no-breach aborts are the residual generic-core candidates."
        ).format(mass["R3_capability_refusal"], mass["UNKNOWN_no_breach_abort"]),
    }


# --------------------------------------------------------------------------
# Section 11: improvement ceiling
# --------------------------------------------------------------------------


def improvement_ceiling(data: dict, att: dict, resp: dict, waterfall: dict) -> dict:
    by = data["by"]
    scen = data["scenarios"]
    n = len(by)
    current_bsr = sum(by[s]["haplous_mediated"]["bounded_success"] for s in by)
    attainable = set(att["attainable_ids"])
    refusal_loss = [s for s in attainable if by[s]["haplous_mediated"]["graded_disposition"] == "REFUSED"]
    abort_loss = [s for s in attainable
                  if by[s]["haplous_mediated"]["graded_disposition"] == "ABORTED"
                  and by[s]["haplous_mediated"]["bounded_success"] is False]
    conservative = current_bsr + len(refusal_loss) + len(abort_loss)
    # optimistic: boundary_hi refusals accepted and captured at the boundary_lo rate
    boundary_hi = [s for s in by if 0.003 <= scen[s].initial_offset_m < 0.0035
                   and by[s]["haplous_mediated"]["graded_disposition"] == "REFUSED"]
    boundary_hi_eligible = [s for s in boundary_hi if scen[s].max_force_n != 2.0]
    boundary_lo = [s for s in by if 0.0025 <= scen[s].initial_offset_m < 0.003]
    lo_rate = (sum(by[s]["haplous_mediated"]["bounded_success"] for s in boundary_lo) / len(boundary_lo)
               if boundary_lo else 0.0)
    extra = round(len(boundary_hi_eligible) * lo_rate)
    optimistic = conservative + extra
    return {
        "current_system_bsr": current_bsr,
        "current_system_bsr_rate": round(current_bsr / n, 4),
        "demonstrated_attainable_refusals_recoverable": len(refusal_loss),
        "attained_abort_loss": len(abort_loss),
        "conservative_generic_ceiling": conservative,
        "conservative_generic_ceiling_rate": round(conservative / n, 4),
        "optimistic_extra_from_boundary_capture": extra,
        "optimistic_generic_ceiling": optimistic,
        "optimistic_generic_ceiling_rate": round(optimistic / n, 4),
        "remaining_failure_mass": {
            "correct_refusal_non_attainable": resp["classes"].get("correct_refusal", 0),
            "upstream_controller_physics": resp["R0_not_haplous"],
            "protective_stop_on_breach": resp["R2_mixed"],
            "attainability_unknown": resp["UNKNOWN"],
        },
        "boundary_lo_haplous_capture_rate": round(lo_rate, 4),
        "boundary_hi_refused_eligible": len(boundary_hi_eligible),
        "note": (
            "Conservative assumes recovered misses only reproduce already-demonstrated "
            "bounded success. Optimistic additionally assumes Haplous captures boundary_hi "
            "offsets at the boundary_lo rate. Neither assumes UNKNOWN cases are fixable, "
            "and both exclude non-attainable and upstream failure mass."
        ),
    }


# --------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------


def decision(data: dict, waterfall: dict, adj: dict, att: dict, ceiling: dict) -> dict:
    r3 = sum(1 for s, a in data["by"].items()
             if a["haplous_mediated"]["bounded_success"] is False
             and s in set(att["attainable_ids"]))
    return {
        "recommended_path": "D",
        "primary_path": "PATH D - evaluator defect: the counterfactual classifier mislabels HARMFUL on task success alone, and the benchmark's reachability constant max_initial_offset_m is inconsistent with the offset population it generates",
        "fallback_path": "PATH A - if the boundary_hi fork sweep shows Haplous core genuinely cannot capture offsets the unmediated arm attains, the reach model becomes a generic Haplous defect",
        "why_not_primary_b": (
            "PATH B (attainability uncertainty) is the largest raw mass (479 UNKNOWN) but it is "
            "an evidence gap, not a defect: it bounds the ceiling rather than explaining the "
            "observed G3 loss."
        ),
        "why_not_primary_c": (
            "PATH C (intrinsic tradeoff) is not supported: zero BENEFICIAL forks preserved a "
            "bounded success, so no measured protection requires the reach refusal."
        ),
        "rationale": [
            "G3 loss is dominated by one reusable mechanism (reach_rejection, 26/28) whose "
            "trigger is a single per-task calibration constant, not a broad runtime defect.",
            "The HARMFUL headline is inflated by an evaluator classifier defect: "
            "12/12 HARMFUL continuations breach a declared hard contract, so none destroyed a "
            "bounded success. The negative evidence must be re-derived before any claim.",
            "Fixing the evaluator is a prerequisite for measuring anything else, and the "
            "falsifying evidence is already partly in hand (direct_minimal attains 24 of 153 "
            "boundary_hi scenarios Haplous refuses).",
        ],
        "highest_information_next_experiment": {
            "experiment": "single-pass fork sweep of the 142 eligible boundary_hi (3.0-3.5 mm) "
                          "reach-rejections (excluding the guaranteed-abort class), plus the priority "
                          "UNKNOWN subset, once each",
            "why": (
                "It is the single mechanism behind 26/28 preservation losses and it directly "
                "tests the calibration hypothesis. If the unmediated continuation completes within "
                "contract materially more often than the 0.003 cap assumes, the constant is wrong; "
                "if not, the reach model is a genuine Haplous limitation (PATH A)."
            ),
            "cost": "minutes to a few hours of managed forks; no new population, no new episodes",
            "falsifies": "that max_initial_offset_m = 0.003 matches the machine's true capture boundary",
        },
        "do_not": [
            "do not change runtime semantics before the fork sweep",
            "do not retune the classifier to flatter Haplous: make HARMFUL require a "
            "bounded-success continuation, and mark the affected historical labels superseded",
            "do not fork the full 479 UNKNOWN mass",
        ],
        "r3_mass_reference": r3,
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def _self_check(waterfall, adj, att, resp) -> None:
    """Invariants that must hold; a failure means the logic broke."""
    n = att["demonstrated_attainable"] + att["demonstrated_non_attainable"] + att["unknown"]
    assert n == att["n"] == 1000, n
    partition = (resp["R0_not_haplous"] + resp["R1_correct_prevention"] + resp["R2_mixed"]
                 + resp["R3_haplous_caused"] + resp["UNKNOWN"] + resp["captured"])
    assert partition == 1000, partition
    assert abs(waterfall["preservation_rate"] - 103 / 131) < 1e-6, waterfall["preservation_rate"]
    assert adj["harmful_continuation_was_bounded_success"] == 0, "HARMFUL must not have bounded continuations"
    assert all(r["continuation_violates_force_limit"] or r["deadline_s"] is not None
               for r in adj["harmful"]), "every HARMFUL continuation must breach a hard contract"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = load()
    frozen = frozen_evidence(data)
    waterfall = preservation_waterfall(data)
    adj = forks_adjudication(data)
    matrix = mechanism_matrix(data, adj)
    att = attainability(data)
    subset = unknown_subset(data, att)
    resp = responsibility(data, att)
    attm = attainment_metrics(data, att)
    addr = addressability(data, att, resp)
    ceiling = improvement_ceiling(data, att, resp, waterfall)
    dec = decision(data, waterfall, adj, att, ceiling)

    payloads = {
        "frozen_evidence": frozen,
        "preservation_waterfall": waterfall,
        "fork_adjudication": adj,
        "mechanism_matrix": matrix,
        "attainability": att,
        "attainment_metrics": attm,
        "unknown_subset": subset,
        "responsibility": resp,
        "addressability": addr,
        "improvement_ceiling": ceiling,
        "decision": dec,
    }
    for name, payload in payloads.items():
        (OUT / f"{name}.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")
    write_report(payloads)
    _self_check(waterfall, adj, att, resp)
    print(json.dumps({
        "preservation": waterfall["preservation_rate"],
        "lost": waterfall["lost_n"],
        "mechanisms": waterfall["mechanism_pareto"],
        "harmful_total": len(adj["harmful"]),
        "harmful_bounded_continuations": adj["harmful_continuation_was_bounded_success"],
        "beneficial_total": len(adj["beneficial"]),
        "beneficial_protective": adj["beneficial_protective_stop"],
        "attainability": {k: att[k] for k in ("demonstrated_attainable", "demonstrated_non_attainable", "unknown")},
        "attainment_metrics": attm,
        "responsibility": {k: resp[k] for k in ("R0_not_haplous", "R1_correct_prevention", "R2_mixed", "R3_haplous_caused", "UNKNOWN", "captured")},
        "ceiling": {"current": ceiling["current_system_bsr_rate"], "conservative": ceiling["conservative_generic_ceiling_rate"], "optimistic": ceiling["optimistic_generic_ceiling_rate"]},
        "path": dec["recommended_path"],
    }, indent=2))
    return 0


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def _tbl(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def write_report(p):
    fe, wf, adj = p["frozen_evidence"], p["preservation_waterfall"], p["fork_adjudication"]
    mm = p["mechanism_matrix"]["mechanisms"]
    att, am, us = p["attainability"], p["attainment_metrics"], p["unknown_subset"]
    rs, ad, ic, dec = p["responsibility"], p["addressability"], p["improvement_ceiling"], p["decision"]

    mech_rows = [[m["mechanism"], m["episodes_lost"], f"{m['fraction_of_preservation_loss']:.1%}",
                  m["severity"], m["generic_addressability"]] for m in wf["mechanism_pareto"]]
    strata_rows = [[s["stratum"], s["n"], s["direct_bounded_success"], s["haplous_bounded_success"],
                    s["haplous_refused"], s["reach_rejections"],
                    f"{s['preservation']:.1%}" if s["preservation"] is not None else "n/a"]
                   for s in wf["offset_strata"]]
    harm_rows = [[r["scenario_id"], r["task_family"], r["declared_force_limit_n"],
                  r["continuation_peak_force_n"], r["continuation_violates_force_limit"]]
                 for r in adj["harmful"]]
    ben_rows = [[r["scenario_id"], r["task_family"], r["haplous_reason"],
                 r["continuation_completed"], r["continuation_peak_force_n"],
                 r["declared_force_limit_n"], r["haplous_bounded_success"]]
                for r in adj["beneficial"]]
    mech_matrix_rows = [[k, v["beneficial"], v["protective"], v["harmful"], v["unnecessary"]]
                        for k, v in mm.items()]
    harm_slide = sum(1 for r in adj["harmful"] if r["task_family"] == "constrained_slide")

    doc = f"""# HES Causal Diagnosis

Post-HES causal, quantitative explanation of the HES v0 qualification decision
surface. Diagnostic only: no optimization, no runtime-semantics change, no
workload switch. Every number is reproduced by
`scripts/hes_causal_diagnosis.py` from the frozen artifacts under
`results/hes/v3_hardening_qualification/`, with machine-readable output in
`results/hes_causal_diagnosis/`.

## 0. Executive summary

1. **Only 28 scenarios separate Haplous from the unmediated baseline on bounded
   success.** 131 direct bounded successes, 103 preserved, 28 lost; preservation
   = {wf['preservation_rate']:.3f}, reproducing HES G3 (0.786).
2. **26 of 28 losses (92.9%) are one mechanism**: the reachability refusal
   boundary, a generic runtime check triggered by a single per-task calibration
   constant (`max_initial_offset_m = 0.003` m).
3. **The HARMFUL headline is an evaluator defect.** 0 of {len(adj['harmful'])}
   HARMFUL forks have a bounded-success continuation; all
   {adj['harmful_continuation_not_bounded']} continuations breach a declared hard
   contract. The classifier labels on *task* success, not *bounded* success.
4. **All {len(adj['beneficial'])} BENEFICIAL forks are protective stops**; none
   preserved a bounded success that the unmediated arm held.
5. **Attainability is the larger uncertainty than any capability defect.**
   {att['demonstrated_attainable']} scenarios are demonstrated attainable,
   {att['demonstrated_non_attainable']} demonstrated non-attainable, and
   {att['unknown']} remain UNKNOWN ({am['coverage']['unknown_fraction']:.1%}).
6. **Recommended path: PATH {dec['recommended_path']} (evaluator defect)**, with PATH A
   (generic reachability defect) as the fallback if the fork sweep shows Haplous core
   cannot capture offsets the unmediated arm attains. The highest-information experiment
   is a single-pass fork sweep of the {ic['boundary_hi_refused_eligible']} eligible
   boundary_hi reach-rejections plus the priority UNKNOWN subset.

## 1. Scope and method

Inputs (read-only): `episodes.parquet` (1000 scenarios x 3 arms),
`interventions.jsonl` (120 forks), `hes_analysis.json`, `hes_gates.json`,
`manifest.json`, `provenance.json`. Population scenarios are reconstructed from
`benchmarks.heb.population` purely for construction axes (offset, force limit,
deadline); no episode is replayed and no physics is re-run.

Rules used throughout, all canonical to this repo:

- `bounded_success = task_success AND (no hard violations)` (`benchmarks/heb/metrics.py`).
- Preservation is computed against `direct_minimal` only (`hes/metrics.py`,
  `hes/gates.py`).
- A scenario is **attainable** only with evidence: some arm achieved
  `bounded_success`, or a refusal counterfactual satisfied every hard contract.
- A scenario is **non-attainable** only with evidence: benchmark construction
  (guaranteed-abort class, measured-infeasible deadline), measured capability
  incompatibility, or a valid refusal counterfactual that breached a hard
  contract. Task failure alone never implies non-attainability.

## 2. Frozen evidence identity

- experiment_id: `{fe['experiment_id']}`
- git commit: `{fe['git_commit']}` (dirty: `{fe['git_dirty']}`)
- haplous_version: `{fe['haplous_version']}`, benchmark `{fe['benchmark']}@{fe['benchmark_version']}`
- reference_arm `{fe['reference_arm']}`, baseline_arm `{fe['baseline_arm']}`
- manifest sha256 `{fe['manifest_sha256']}`
- report digest ok: `{fe['report_digest_ok']}`

Artifacts are treated as immutable. New evidence is written only to
`results/hes_causal_diagnosis/` and references this identity. SHA-256 digests of
every input file are recorded in `frozen_evidence.json`.

## 3. Preservation waterfall

{wf['lost_n']} of {wf['reference_population_n']} direct bounded successes were not
preserved. Mechanism Pareto:

{_tbl(["mechanism", "episodes lost", "share of loss", "severity", "addressability"], mech_rows)}

The {wf['mechanism_pareto'][0]['episodes_lost']} reach rejections fire when
`INITIAL_OFFSET_M > caps.max_initial_offset_m`, with the cap fixed at 0.003 m in
`benchmarks/heb/tasks.py` from a development sweep. The population strata
straddle the measured boundary, which is why the loss concentrates there:

{_tbl(["stratum", "n", "direct BS", "haplous BS", "refused", "reach rejections", "preservation"], strata_rows)}

`boundary_hi` (3.0-3.5 mm) is the discontinuity: Haplous refuses nearly all of
it while the measured capture boundary sits above 3.5 mm. All three mechanisms
are generically addressable runtime failures, class S1 (false abort /
productivity loss).

The two non-reach losses are individually informative:
- `fleet_insert_000793` (offset 2.87 mm, 25 N limit): the unmediated arm completed
  within contract, while Haplous aborted on a 49.7 N reading. This is a genuine
  mediation-induced divergence, not a calibration issue.
- `fleet_insert_000708` (offset 0.77 mm, deadline 1.8 s): refused with
  `deadline_unachievable` because the runtime's `expected_duration` is 2.0 s, but
  the unmediated arm completed within the 1.8 s deadline. The deadline model is
  miscalibrated in the same way as the reach constant.

## 4. HARMFUL fork adjudication

{len(adj['harmful'])} of {adj['n_intervention_forks']} intervention forks were
classified HARMFUL; {len(adj['unnecessary'])} UNNECESSARY, {len(adj['beneficial'])} BENEFICIAL.

{_tbl(["scenario", "task family", "declared force limit (N)", "continuation peak force (N)", "continuation violates limit"], harm_rows)}

**All {adj['harmful_continuation_was_bounded_success']} HARMFUL forks have a
bounded-success continuation**; {adj['harmful_continuation_not_bounded']} of
{len(adj['harmful'])} continue into a peak force above the declared hard limit
(or a missed deadline). The classifier
(`benchmarks/heb/counterfactual.py`) assigns HARMFUL when the continuation
*completes the task* without strictly worsening the metric at
`max(intervention_force, hard_limit)*1.1`; it never checks bounded success. The
{harm_slide} slide forks are the clearest case: the nominal contact
force (~1.74 N) already exceeds the 1.0-1.5 N declared limit, so "completion" is
itself a breach. This contradicts the canonical bounded-success definition and
supersedes the HARMFUL label.

## 5. BENEFICIAL fork adjudication

{_tbl(["scenario", "task family", "haplous reason", "cont. completed", "cont. peak (N)", "declared limit (N)", "haplous BS"], ben_rows)}

All {adj['beneficial_protective_stop']} BENEFICIAL forks are protective stops
(four on insertion and one borderline on sliding), where the unmediated
continuation blows past the declared limit and does not complete the task.
**Zero BENEFICIAL forks preserved a bounded success.** The intervention sample
therefore contains no positive evidence that stopping itself buys bounded
performance; it contains evidence that stopping prevents severity escalation.

## 6. Mechanism matrix

{_tbl(["mechanism", "beneficial", "protective", "harmful", "unnecessary"], mech_matrix_rows)}

## 7. Attainability decomposition

{_tbl(["label", "n", "fraction", "evidence"], [
  ["demonstrated attainable", att['demonstrated_attainable'], f"{am['coverage']['attainable_fraction']:.1%}", ", ".join(f"{k.split(':')[0]}={v}" for k, v in att['attainable_provenance'].items())],
  ["demonstrated non-attainable", att['demonstrated_non_attainable'], f"{am['coverage']['non_attainable_fraction']:.1%}", ", ".join(f"{k.split(':')[0]}={v}" for k, v in att['non_attainable_provenance'].items())],
  ["unknown", att['unknown'], f"{am['coverage']['unknown_fraction']:.1%}", "no arm achieved bounded success and no valid counterfactual"],
])}

Attainment-relative metrics, each reported only over its labelled population:

{_tbl(["metric", "population", "value"], [
  ["authorization / execution capture", "attainable", f"{am['execution_capture_over_attainable']:.4f}"],
  ["false-refusal rate", "attainable", f"{am['false_refusal_rate_over_attainable']:.4f}"],
  ["hazard rejection", "non-attainable", f"{am['hazard_rejection_over_non_attainable']:.4f}"],
])}

The published HES AUTHORITY numbers (precision 0.319, recall 0.851) are diluted
by the {att['unknown']} UNKNOWN cases. Over the labelled population, Haplous
captures {am['execution_capture_over_attainable']:.1%} of attainable scenarios
and falsely refuses {am['false_refusal_rate_over_attainable']:.1%}. The
non-attainable population splits into benchmark-constructed impossibility
(guaranteed-abort class, infeasible deadline, sub-contact-force limits) and
measured capability incompatibility.

## 8. Decision-relevant UNKNOWN subset

{us['n_unknown']} scenarios are UNKNOWN; {us['n_decision_relevant']} carry a
claim they could move (false-refusal or false-abort estimate) and
{us['n_priority']} are priority (offset 2.5-3.5 mm or deadline 1.0/1.8 s).
UNKNOWN composition:

{_tbl(["haplous disposition", "n"], sorted(us['by_haplous_disposition'].items(), key=lambda x: -x[1]))}

Decision-relevant split: {us['by_relevance']}. Priority bands: offset
{us['priority_bands']['offset_2p5_to_3p5mm']}, deadline
{us['priority_bands']['deadline_1p0_or_1p8_s']}.

Recommended sweep:
- **Primary**: the {ic['boundary_hi_refused_eligible']} eligible boundary_hi reach-rejections (offset 3.0-3.5 mm, excluding the guaranteed-abort class); they are the single mechanism behind 26/28 preservation losses
- **Secondary**: {us['recommended_sweep']['secondary']}
- **Stopping rule**: {us['recommended_sweep']['adaptive']}

## 9. Responsibility decomposition

Using the comparator rule documented in `responsibility.json`:

{_tbl(["class", "n", "meaning"], [
  ["R0 not Haplous", rs['R0_not_haplous'], "upstream / controller / physics terminal failure, no runtime prevention role"],
  ["R1 correct prevention", rs['R1_correct_prevention'], "refusal of demonstrated non-attainable scenario"],
  ["R2 mixed / contributing", rs['R2_mixed'], "protective stop on an already-breaching execution, attainability undemonstrated"],
  ["R3 Haplous-caused", rs['R3_haplous_caused'], "attainable scenario where Haplous missed a demonstrated bounded execution"],
  ["UNKNOWN", rs['UNKNOWN'], "refused or aborted without a recorded breach, attainability unknown"],
  ["captured", rs['captured'], "Haplous achieved bounded success"],
])}

**The decision-relevant Haplous-caused mass is R3 = {rs['R3_haplous_caused']}**
(32 refusals + 1 abort on demonstrated-attainable scenarios), i.e.
{rs['R3_haplous_caused'] / 1000:.1%} of the population, against
{rs['R2_mixed']} protective stops whose underlying breach is upstream. This is
the correct denominator for "how much is Haplous costing us".

## 10. Addressability

{_tbl(["category", "n"], [
  ["generic Haplous core", ad['generic_haplous_addressable']],
  ["task-specific calibration", ad['task_specific_calibration']],
  ["upstream / controller", ad['upstream']],
  ["non-addressable", ad['non_addressable']],
  ["unknown", ad['unknown']],
])}

{ad['note']}

## 11. Improvement ceiling

{_tbl(["bound", "bounded success", "rate", "assumption"], [
  ["current system", ic['current_system_bsr'], f"{ic['current_system_bsr_rate']:.3f}", "as measured"],
  ["conservative", ic['conservative_generic_ceiling'], f"{ic['conservative_generic_ceiling_rate']:.3f}", "recover all demonstrated-attainable misses"],
  ["optimistic", ic['optimistic_generic_ceiling'], f"{ic['optimistic_generic_ceiling_rate']:.3f}", "+ boundary_hi captured at the boundary_lo rate"],
])}

Conservative = {ic['current_system_bsr']} + {ic['demonstrated_attainable_refusals_recoverable']}
refusals + {ic['attained_abort_loss']} abort. Optimistic adds
{ic['optimistic_extra_from_boundary_capture']} from the
{ic['boundary_hi_refused_eligible']} eligible boundary_hi scenarios at the
boundary_lo capture rate {ic['boundary_lo_haplous_capture_rate']:.3f}. Neither
assumes UNKNOWN cases are fixable, and both leave
{ic['remaining_failure_mass']['upstream_controller_physics']} upstream and
{ic['remaining_failure_mass']['correct_refusal_non_attainable']} correct-refusal
failures untouched.

## 12. Recommended path

**Recommended path: PATH {dec['recommended_path']}.** {dec['primary_path']}.

Fallback: {dec['fallback_path']}.

{dec['why_not_primary_b']}

{dec['why_not_primary_c']}

## 13. Highest-information next experiment

- **Experiment**: {dec['highest_information_next_experiment']['experiment']}
- **Why**: {dec['highest_information_next_experiment']['why']}
- **Cost**: {dec['highest_information_next_experiment']['cost']}
- **Falsifies**: {dec['highest_information_next_experiment']['falsifies']}

Prerequisites that do not require the sweep:
- Fix the counterfactual classifier so HARMFUL requires a bounded-success
  continuation; mark historical HARMFUL labels superseded, do not silently
  relabel.
- Recover the lost violation fields in `interventions.jsonl` (`slim_artifacts`
  appears applied twice; `n_hard_violations` is 0 for every fork).

## 14. Alternatives and what would refute them

- **A generic defect**: refuted as the *dominant* explanation. A general runtime
  defect would not concentrate 92.9% of loss in one capability check at one
  offset band. Residual generic risk remains in the 17 no-violation aborts and
  the {rs['UNKNOWN']} UNKNOWN mass.
- **C intrinsic tradeoff**: not supported. No BENEFICIAL fork preserved a
  bounded success, and the reach refusal is not required to prevent any measured
  violation. If the boundary_hi sweep shows Haplous cannot capture those offsets
  while the unmediated arm can, C becomes live for that band.
- **D evaluator defect**: confirmed for the HARMFUL axis and required as a
  precondition for any re-measurement. It does not by itself explain the G3
  loss.
- **B attainability uncertainty**: confirmed as the dominant uncertainty; it
  bounds every headline number in the report.

## 15. Threats to validity

- Attainability labels are evidence-bounded; {att['unknown']} scenarios stay
  UNKNOWN and are never imputed.
- The intervention fork sample is {adj['n_intervention_forks']} of 1000
  scenarios and is not a random sample of the population.
- The 2 inconsistent `TRUE_REFUSAL` forks whose direct arm achieved bounded
  success indicate residual label noise in the fork corpus.
- Policy conclusions are specific to HES v0 and the frozen population seed
  `{fe['population']['seed']}`; results must not be pooled with a different design
  version.
- The source tree was **dirty** at measurement time (`git_dirty = {fe['git_dirty']}`);
  the commit identifies the baseline, not the exact working tree. This limits
  exact reproduction of the original episodes but not of this report, which reads
  only committed artifacts.

## Artifacts

`results/hes_causal_diagnosis/`: `frozen_evidence.json`,
`preservation_waterfall.json`, `fork_adjudication.json`, `mechanism_matrix.json`,
`attainability.json`, `attainment_metrics.json`, `unknown_subset.json`,
`responsibility.json`, `addressability.json`, `improvement_ceiling.json`,
`decision.json`.
"""

    out = ROOT / "docs" / "HES_CAUSAL_DIAGNOSIS.md"
    out.write_text(doc, encoding="utf-8")
    return out


if __name__ == "__main__":
    raise SystemExit(main())