"""Local MuJoCo qualification for the hardened Haplous v3 candidate.

Produces one self-contained evidence package:

    results/v3_hardening_qualification/
        manifest.yaml            validated experiment manifest
        manifest.json            same, canonical JSON (written by the executor)
        provenance.json          commit/dirty/platform/deps/schema versions
        shards/                  per-shard status + episodes.jsonl (resumable)
        episodes.parquet         canonical episode table (all arms)
        metrics.json             per-arm summaries + paired comparisons
        summary.json             compact machine-readable headline
        failures.parquet         failed-episode table with taxonomy class
        interventions.parquet    fork classifications (intervention/refusal)
        parity/parity.json       core-semantics parity evidence
        plots/                   figures generated from the canonical tables

Usage:
    python scripts/run_v3_hardening_qualification.py --n 600 --seed 20260916
    python scripts/run_v3_hardening_qualification.py --mode parity
    python scripts/run_v3_hardening_qualification.py --mode forks --fork-scenarios 150

This is a local readiness gate, not the product-scale GPU campaign.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


from evaluation.experiment import (  # noqa: E402
    ArtifactPolicy,
    ExperimentClass,
    ExperimentManifest,
    LocalExecutor,
    PopulationSpec,
    ShardingSpec,
    aggregate_experiment,
    load_completed_episodes,
    metric_dictionary,
    plan_shards,
    write_canonical_table,
)

DEFAULT_ROOT = ROOT / "results" / "v3_hardening_qualification"
CORE_MODULES = (
    "haplous/runtime.py",
    "haplous/capabilities.py",
    "haplous/state.py",
    "haplous/constraints.py",
    "haplous/evidence.py",
    "haplous/evidence_store.py",
    "haplous/intent.py",
    "haplous/machine.py",
    "haplous/behavior.py",
    "haplous/adapters/mujoco_adapter.py",
    "haplous/adapters/external.py",
    "haplous/behaviors/insertion.py",
    "haplous/behaviors/sliding.py",
)


def build_manifest(n: int, seed: int, shard_size: int = 20) -> ExperimentManifest:
    import haplous

    return ExperimentManifest(
        experiment_id="v3_hardening_qualification",
        description=(
            "Local MuJoCo 3-arm paired qualification of the hardened v3 "
            "candidate: direct behavior, competent supervisor, Haplous-mediated."
        ),
        benchmark="internal-heb",
        benchmark_version="0.2.0",
        haplous_version=haplous.__version__,
        systems=("direct_minimal", "competent_supervisor", "haplous_mediated"),
        population=PopulationSpec(n=n, seed=seed),
        sharding=ShardingSpec(shard_size=shard_size),
        experiment_class=ExperimentClass.QUALIFICATION,
        metrics=(
            "task_success",
            "system_bsr",
            "conditional_bsr",
            "coverage",
            "violation_rate",
            "violation_given_acceptance",
            "recovery_success_rate",
        ),
        reference_arm="haplous_mediated",
        artifact_policy=(
            ArtifactPolicy.ALL_SUMMARIES,
            ArtifactPolicy.ALL_SEVERE_FAILURES,
            ArtifactPolicy.ALL_FALSE_REFUSALS,
        ),
    )


# --------------------------------------------------------------------------
# Parity: core Haplous source unchanged from the baseline commit
# --------------------------------------------------------------------------


def run_parity(root: Path) -> dict:
    import subprocess

    (root / "parity").mkdir(parents=True, exist_ok=True)
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.split()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.split()
    core_changed = [path for path in changed if path in CORE_MODULES]
    core_untracked = [path for path in untracked if path in CORE_MODULES]
    hashes = {}
    for rel in CORE_MODULES:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True
        ).stdout
        disk = (ROOT / rel).read_bytes()
        hashes[rel] = {
            "head_sha256": hashlib.sha256(blob).hexdigest(),
            "disk_sha256": hashlib.sha256(disk).hexdigest(),
            "identical": blob == disk,
        }
    report = {
        "method": (
            "Byte-level comparison of every Haplous core semantics module "
            "against the baseline commit (HEAD at campaign start). The "
            "hardening pass is additive: evaluation/, CLI inspection, and "
            "tests. Any core-module divergence invalidates behavioral parity."
        ),
        "baseline_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip(),
        "core_modules_identical": all(entry["identical"] for entry in hashes.values()),
        "core_modules_changed": core_changed,
        "core_modules_untracked": core_untracked,
        "module_hashes": hashes,
        "characterization_tests": (
            "tests/test_haplous_semantics.py, tests/test_haplous_sliding.py, "
            "tests/test_haplous_faults.py, tests/test_haplous_live_equivalence.py, "
            "tests/test_r05.py"
        ),
    }
    (root / "parity" / "parity.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


# --------------------------------------------------------------------------
# Fork pass: intervention value + false refusals on a bounded scenario slice
# --------------------------------------------------------------------------


def _fork_worker(scenario) -> dict:
    sys.path.insert(0, str(ROOT / "scripts"))
    from measure_fleet import evaluate_scenario

    return evaluate_scenario(scenario)


def run_forks(root: Path, n: int, seed: int, fork_scenarios: int, workers: int) -> dict:
    from benchmarks.heb.population import population

    scenarios = population(n, seed)[:fork_scenarios]
    rows: list[dict] = []
    t0 = time.perf_counter()
    context = multiprocessing.get_context("spawn")
    with context.Pool(min(workers, len(scenarios))) as pool:
        for i, row in enumerate(pool.imap_unordered(_fork_worker, scenarios, chunksize=2)):
            rows.append(row)
            if workers > 1 and (i + 1) % 25 == 0:
                print(f"  forks {i + 1}/{len(scenarios)}", flush=True)
    wall = time.perf_counter() - t0

    out = root / "interventions.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for row in sorted(rows, key=lambda r: r["scenario_id"]):
            handle.write(json.dumps(row, default=str, sort_keys=True) + "\n")

    intervention_classes: dict[str, int] = {}
    refusal_classes: dict[str, int] = {}
    for row in rows:
        cf = row.get("counterfactuals", {})
        if "intervention" in cf:
            key = cf["intervention"].get("classification", "UNKNOWN")
            intervention_classes[key] = intervention_classes.get(key, 0) + 1
        if "refusal" in cf:
            key = cf["refusal"].get("classification", "UNKNOWN")
            refusal_classes[key] = refusal_classes.get(key, 0) + 1
    return {
        "fork_scenarios": len(rows),
        "wall_s": wall,
        "interventions": intervention_classes,
        "refusals": refusal_classes,
    }


# --------------------------------------------------------------------------
# Tables and plots
# --------------------------------------------------------------------------


def write_tables(root: Path, manifest: ExperimentManifest, rows: list[dict]) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from evaluation.taxonomy import classify, summarize

    write_canonical_table(rows, root / "episodes.parquet")

    failures = []
    for row in rows:
        fc = classify(row)
        if fc is None:
            continue
        failures.append(
            {
                "experiment_id": row["experiment_id"],
                "shard_id": row["shard_id"],
                "arm": row["arm"],
                "episode_id": row["episode_id"],
                "graded_disposition": row["graded_disposition"],
                "reported_disposition": row["reported_disposition"],
                "reason": row.get("reason"),
                "severity": fc.severity,
                "root_cause": fc.root_cause,
                "n_hard_violations": row.get("n_hard_violations", len(row.get("hard_violations") or [])),
                "task_success": row["task_success"],
                "bounded_success": row["bounded_success"],
            }
        )
    if failures:
        pq.write_table(pa.Table.from_pylist(failures), root / "failures.parquet", compression="zstd")
    else:
        pq.write_table(pa.table({"arm": []}), root / "failures.parquet")

    failure_mass = {
        arm: summarize([dict(r) for r in rows if r["arm"] == arm])
        for arm in manifest.systems
    }
    return {"failures": failures, "failure_mass": failure_mass}


def _wilson_ci(k: int, n: int) -> tuple[float, float]:
    from evaluation.stats import wilson_interval

    return wilson_interval(k, n) if n else (0.0, 0.0)


def make_plots(root: Path, manifest: ExperimentManifest, rows: list[dict], fork_summary: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plots = root / "plots"
    plots.mkdir(parents=True, exist_ok=True)
    arms = list(manifest.systems)
    colors = {"direct_minimal": "#c0392b", "competent_supervisor": "#2980b9", "haplous_mediated": "#27ae60"}

    def arm_rates(arm: str) -> dict[str, tuple[float, tuple[float, float], int, int]]:
        subset = [r for r in rows if r["arm"] == arm]
        n = len(subset)
        accepted = [r for r in subset if not str(r["reported_disposition"]).startswith("REFUSED")]
        out = {}
        for name, k in (
            ("task_success", sum(r["task_success"] for r in subset)),
            ("system_bsr", sum(r["bounded_success"] for r in subset)),
            ("coverage", len(accepted)),
        ):
            out[name] = (k / n, _wilson_ci(k, n), k, n)
        return out

    # 1. success / bsr / coverage
    fig, ax = plt.subplots(figsize=(8, 4.5))
    metrics = ("task_success", "system_bsr", "coverage")
    width = 0.25
    for i, arm in enumerate(arms):
        rates = arm_rates(arm)
        positions = [j + i * width for j in range(len(metrics))]
        values = [rates[m][0] for m in metrics]
        errors = [
            [rates[m][0] - rates[m][1][0] for m in metrics],
            [rates[m][1][1] - rates[m][0] for m in metrics],
        ]
        ax.bar(positions, values, width, yerr=errors, capsize=3, label=arm, color=colors.get(arm))
    ax.set_xticks([j + width for j in range(len(metrics))])
    ax.set_xticklabels(["Task success", "System BSR", "Coverage"])
    ax.set_ylabel("proportion (Wilson 95% CI)")
    ax.set_title(f"Primary outcomes, N={len(rows) // len(arms)} scenarios/arm")
    ax.set_ylim(0, 1.05)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(plots / "success_bsr_coverage.png", dpi=140)
    plt.close(fig)

    # 2. violations
    fig, ax = plt.subplots(figsize=(6, 4))
    for i, arm in enumerate(arms):
        subset = [r for r in rows if r["arm"] == arm]
        n = len(subset)
        accepted = [r for r in subset if not str(r["reported_disposition"]).startswith("REFUSED")]
        k_any = sum(1 for r in subset if r["hard_violations"])
        k_acc = sum(1 for r in accepted if r["hard_violations"])
        values = [k_any / n, k_acc / len(accepted) if accepted else 0.0]
        cis = [_wilson_ci(k_any, n), _wilson_ci(k_acc, len(accepted)) if accepted else (0.0, 0.0)]
        errors = [
            [value - ci[0] for value, ci in zip(values, cis, strict=True)],
            [ci[1] - value for value, ci in zip(values, cis, strict=True)],
        ]
        positions = [j + i * width for j in range(2)]
        ax.bar(positions, values, width, yerr=errors, capsize=3, label=arm, color=colors.get(arm))
    ax.set_xticks([j + width for j in range(2)])
    ax.set_xticklabels(["Any violation", "Violation | accepted"], fontsize=9)
    ax.set_ylabel("proportion (Wilson 95% CI)")
    ax.set_title("Hard-requirement violations")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(plots / "violations.png", dpi=140)
    plt.close(fig)

    # 3. intervention value
    values = fork_summary.get("interventions", {})
    if values:
        fig, ax = plt.subplots(figsize=(6, 4))
        order = ["BENEFICIAL", "HARMFUL", "UNNECESSARY", "AMBIGUOUS"]
        counts = [values.get(k, 0) for k in order]
        ax.bar(order, counts, color=["#27ae60", "#c0392b", "#7f8c8d", "#f39c12"])
        ax.set_ylabel("fork count")
        ax.set_title("Intervention value (attributable forks; AMBIGUOUS excluded)")
        fig.tight_layout()
        fig.savefig(plots / "intervention_value.png", dpi=140)
        plt.close(fig)

    # 4. failure taxonomy Pareto (haplous arm)
    failures = [r for r in rows if r["arm"] == "haplous_mediated" and r["reason"]]
    counts: dict[str, int] = {}
    for row in failures:
        counts[str(row["reason"])] = counts.get(str(row["reason"]), 0) + 1
    if counts:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ordered = sorted(counts.items(), key=lambda kv: -kv[1])
        ax.bar([k for k, _ in ordered], [v for _, v in ordered], color="#27ae60")
        ax.set_ylabel("episodes")
        ax.set_title("Haplous terminal-reason Pareto (non-success)")
        plt.xticks(rotation=35, ha="right", fontsize=8)
        fig.tight_layout()
        fig.savefig(plots / "failure_taxonomy.png", dpi=140)
        plt.close(fig)

    # 5. latency ECDF
    fig, ax = plt.subplots(figsize=(7, 4))
    for arm in arms:
        values = sorted(r["wall_s"] for r in rows if r["arm"] == arm)
        if not values:
            continue
        n = len(values)
        ys = [(i + 1) / n for i in range(n)]
        ax.plot(values, ys, label=f"{arm} (N={n})", color=colors.get(arm))
    ax.set_xlabel("episode wall time (s)")
    ax.set_ylabel("ECDF")
    ax.set_title("Execution latency distribution")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(plots / "latency.png", dpi=140)
    plt.close(fig)


# --------------------------------------------------------------------------
# Artifact slimming (post-hoc)
# --------------------------------------------------------------------------


def _prune_metrics(metrics: dict) -> dict:
    pruned = {}
    for key, value in (metrics or {}).items():
        if key == "applied_command_times":
            pruned["n_applied_commands"] = len(value or ())
        elif isinstance(value, (str, int, float, bool)) or value is None:
            pruned[key] = value
    return pruned


def slim_artifacts(root: Path) -> dict:
    """Drop unbounded per-step command traces from already-written shard
    rows and the fork file, then fix the shard hashes. Metrics are
    unaffected: aggregation never reads those traces."""
    from evaluation.experiment import _sha256_file

    saved_shards = 0
    saved_bytes = 0
    for status_path in sorted(root.glob("shards/*/status.json")):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("status") != "COMPLETE":
            continue
        episodes = status_path.parent / "episodes.jsonl"
        before = episodes.stat().st_size
        rows = [json.loads(line) for line in episodes.read_text(encoding="utf-8").splitlines() if line.strip()]
        for row in rows:
            row["task_metrics"] = _prune_metrics(row.get("task_metrics") or {})
            violations = row.get("hard_violations") or []
            row["n_hard_violations"] = row.get("n_hard_violations", len(violations))
            row["hard_violations"] = violations[:5]
        episodes.write_text(
            "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows),
            encoding="utf-8",
        )
        after = episodes.stat().st_size
        status["episodes_sha256"] = _sha256_file(episodes)
        status["episodes"] = len(rows)
        status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        saved_shards += 1
        saved_bytes += before - after

    fork_path = root / "interventions.jsonl"
    fork_before = fork_path.stat().st_size if fork_path.is_file() else 0
    if fork_path.is_file():
        slim_rows = []
        for line in fork_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            slim_rows.append(
                {
                    "scenario_id": row.get("scenario_id"),
                    "task_family": row.get("task_family"),
                    "tags": row.get("tags"),
                    "axes": row.get("axes"),
                    "arms": {
                        arm: {
                            k: v
                            for k, v in payload.items()
                            if k in ("disposition", "reason", "graded", "task_success", "bounded_success", "wall_s")
                        }
                        | {"n_hard_violations": len(payload.get("hard_violations") or [])}
                        for arm, payload in (row.get("arms") or {}).items()
                    },
                    "counterfactuals": row.get("counterfactuals") or {},
                }
            )
        fork_path.write_text(
            "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in slim_rows),
            encoding="utf-8",
        )
    fork_after = fork_path.stat().st_size if fork_path.is_file() else 0
    return {
        "shards_slimmed": saved_shards,
        "shards_bytes_saved": saved_bytes,
        "forks_bytes_saved": fork_before - fork_after,
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def run_episodes(args, root: Path, manifest: ExperimentManifest) -> dict:
    from evaluation.experiment import dump_manifest

    root.mkdir(parents=True, exist_ok=True)
    dump_manifest(manifest, root / "manifest.yaml")
    print(f"manifest {manifest.sha256()[:12]} n={manifest.population.n} seed={manifest.population.seed}")
    shards = plan_shards(manifest)
    print(f"shards={len(shards)} shard_size={manifest.sharding.shard_size} workers={args.workers}")

    executor = LocalExecutor(root)
    started = time.perf_counter()
    results = executor.run(manifest, resume=True, workers=args.workers, verbose=args.verbose)
    wall = time.perf_counter() - started
    complete = sum(1 for r in results if r.status.value == "COMPLETE")
    failed = [r.shard.shard_id for r in results if r.status.value == "FAILED"]
    print(f"shards complete={complete} failed={len(failed)} wall={wall:.1f}s")
    if failed:
        print(f"FAILED shards (resume to retry): {failed}")

    rows = load_completed_episodes(root)
    print(f"loaded {len(rows)} canonical episodes")
    aggregation = aggregate_experiment(manifest, rows)
    tables = write_tables(root, manifest, rows)
    aggregation["failure_mass_by_arm"] = tables["failure_mass"]
    (root / "metrics.json").write_text(
        json.dumps(aggregation, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )

    # Compact machine-readable summary.
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    summary = {
        "schema_version": aggregation["schema_version"],
        "experiment_id": manifest.experiment_id,
        "manifest_sha256": manifest.sha256(),
        "n_scenarios": manifest.population.n,
        "n_episodes": len(rows),
        "requested_n": manifest.population.n,
        "completed_shards": complete,
        "failed_shards": failed,
        "seed": manifest.population.seed,
        "arms": {
            arm: {
                **data,
                "latency_s": _latency(rows, arm),
            }
            for arm, data in aggregation["systems"].items()
        },
        "paired": aggregation["paired"],
        "provenance": {
            "git_commit": provenance.get("git_commit"),
            "git_dirty": provenance.get("git_dirty"),
            "python": provenance.get("python"),
            "platform": provenance.get("platform"),
            "dependencies": provenance.get("dependencies"),
        },
        "metric_dictionary": metric_dictionary(),
        "wall_s": wall,
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return {"rows": rows, "aggregation": aggregation, "summary": summary, "tables": tables}


def _latency(rows: list[dict], arm: str) -> dict:
    from evaluation.stats import tail_percentiles

    values = [r["wall_s"] for r in rows if r["arm"] == arm]
    return tail_percentiles(values) if values else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("all", "episodes", "parity", "forks", "report", "slim"), default="all")
    parser.add_argument("--n", type=int, default=600)
    parser.add_argument("--seed", type=int, default=20260916)
    parser.add_argument("--shard-size", type=int, default=20)
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 4))
    parser.add_argument("--fork-scenarios", type=int, default=150)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    root = args.root
    manifest = build_manifest(args.n, args.seed, args.shard_size)
    result: dict = {}

    if args.mode == "slim":
        print(slim_artifacts(root))
        return 0

    if args.mode in ("all", "parity"):
        parity = run_parity(root)
        print(f"parity core_modules_identical={parity['core_modules_identical']}")

    if args.mode in ("all", "episodes"):
        result = run_episodes(args, root, manifest)

    fork_summary = {}
    if args.mode in ("all", "forks"):
        fork_summary = run_forks(root, args.n, args.seed, args.fork_scenarios, args.workers)
        (root / "forks_summary.json").write_text(
            json.dumps(fork_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"forks {fork_summary}")

    if args.mode in ("all", "report") or result:
        if not result:
            rows = load_completed_episodes(root)
            result = {"rows": rows}
        if not fork_summary and (root / "forks_summary.json").is_file():
            fork_summary = json.loads((root / "forks_summary.json").read_text(encoding="utf-8"))
        if fork_summary:
            (root / "forks_summary.json").write_text(
                json.dumps(fork_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        make_plots(root, manifest, result["rows"], fork_summary)
        print(f"plots written to {root / 'plots'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
