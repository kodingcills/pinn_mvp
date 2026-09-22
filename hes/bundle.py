"""Assemble a full HES analysis bundle from a run directory."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hes.characterize import characterize
from hes.benchmark_health import benchmark_health
from hes.claims import build_claim_ledger
from hes.corpus import corpus_from_failures, load_corpus
from hes.gates import evaluate_hard_gates
from hes.integrity import run_integrity_checks
from hes.recommender import recommend_next
from hes.run import Run, load_run


def runtime_arm_name(run: Run) -> str:
    if "haplous_mediated" in run.arms:
        return "haplous_mediated"
    reference = run.manifest.get("reference_arm")
    if reference in run.arms:
        return str(reference)
    if "haplous" in run.arms:
        return "haplous"
    return run.arms[-1] if run.arms else "unknown"


def build_bundle(
    source: str | Path | Run,
    *,
    runtime_arm: str | None = None,
    corpus_entry_limit: int = 20,
) -> dict[str, Any]:
    run = source if isinstance(source, Run) else load_run(source)
    analysis = run.analysis()
    arm = runtime_arm or runtime_arm_name(run)

    integrity = run_integrity_checks(run.rows)
    gates = evaluate_hard_gates(analysis, runtime_arm=arm, rows=run.rows)
    ledger = build_claim_ledger(analysis, arm=arm)
    recommendations = recommend_next(analysis, claim_ledger=ledger, runtime_arm=arm)

    committed = load_corpus()
    bootstrapped = corpus_from_failures(
        list(run.by_arm.get(arm, [])),
        benchmark_version=run.experiment_id,
        limit=corpus_entry_limit,
    )
    corpus_summary = {
        "n": len(committed.entries) + len(bootstrapped.entries),
        "n_committed": len(committed.entries),
        "n_bootstrapped": len(bootstrapped.entries),
        "n_without_regression_test": len(committed.without_regression_test())
        + len(bootstrapped.without_regression_test()),
    }

    return {
        "runtime_arm": arm,
        "analysis": analysis,
        "hard_gates": gates,
        "integrity": integrity,
        "claim_ledger": ledger,
        "recommendations": recommendations,
        "corpus": corpus_summary,
        "benchmark_health": benchmark_health(run.rows),
        "substrate": characterize().as_dict(),
    }


def to_json(bundle: Mapping[str, Any]) -> str:
    import json

    return json.dumps(bundle, indent=2, sort_keys=True, default=str) + "\n"