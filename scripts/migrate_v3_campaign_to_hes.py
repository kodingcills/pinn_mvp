"""Migrate a historical campaign package into HES form without altering it.

The historical package is copied byte-for-byte; HES adds a manifest,
provenance, and an analysis bundle next to it. Every metric that cannot be
computed from the historical evidence is reported as
``NOT_AVAILABLE_FROM_HISTORICAL_EVIDENCE`` rather than back-filled.

Usage:
    python scripts/migrate_v3_campaign_to_hes.py <source> --out <dir>
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def _copy(source: Path, target: Path) -> list[str]:
    copied: list[str] = []
    target.mkdir(parents=True, exist_ok=True)
    for pattern in (
        "episodes.parquet",
        "failures.parquet",
        "interventions.jsonl",
        "forks_summary.json",
        "metrics.json",
        "summary.json",
        "manifest.json",
        "manifest.yaml",
        "provenance.json",
    ):
        candidate = source / pattern
        if candidate.is_file():
            shutil.copy2(candidate, target / pattern)
            copied.append(pattern)
    shards = source / "shards"
    if shards.is_dir():
        shutil.copytree(shards, target / "shards", dirs_exist_ok=True)
        copied.append("shards/")
    parity = source / "parity"
    if parity.is_dir():
        shutil.copytree(parity, target / "parity", dirs_exist_ok=True)
        copied.append("parity/")
    return copied


def migrate(
    source: str | Path,
    out: str | Path,
    *,
    runtime_arm: str | None = None,
) -> dict[str, Any]:
    source = Path(source)
    out = Path(out)
    if not source.is_dir():
        raise FileNotFoundError(f"source campaign {source} is not a directory")

    copied = _copy(source, out)
    manifest_path = out / "manifest.json"
    manifest: dict[str, Any] = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    )
    manifest.setdefault("schema_version", "hes-run-v1")
    manifest.setdefault("experiment_id", source.name)
    manifest.setdefault("reference_arm", manifest.get("reference_arm") or "haplous_mediated")
    manifest["hes_migrated_from"] = str(source)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    provenance_path = out / "provenance.json"
    provenance: dict[str, Any] = (
        json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.is_file() else {}
    )
    provenance["hes_migration"] = {
        "source": str(source),
        "copied_files": copied,
        "historical_results_modified": False,
        "new_metrics_policy": "NOT_AVAILABLE_FROM_HISTORICAL_EVIDENCE",
    }
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    from hes.bundle import build_bundle, runtime_arm_name, to_json
    from hes.run import load_run

    run = load_run(out, baseline_arm=None)
    arm = runtime_arm or runtime_arm_name(run)
    bundle = build_bundle(run, runtime_arm=arm)
    (out / "hes_analysis.json").write_text(to_json(bundle), encoding="utf-8")

    analysis = bundle["analysis"]
    not_available = sorted((analysis.get("not_available") or {}).keys())
    report = {
        "source": str(source),
        "out": str(out),
        "copied_files": copied,
        "runtime_arm": arm,
        "baseline_arm": analysis.get("baseline_arm"),
        "n_episodes": analysis.get("n_episodes"),
        "n_scenarios": analysis.get("n_scenarios"),
        "historical_results_modified": False,
        "not_available_from_historical_evidence": not_available,
        "new_metrics_policy": (
            "metrics that require channels absent from the historical rows are "
            "reported NOT_AVAILABLE_FROM_HISTORICAL_EVIDENCE"
        ),
    }
    (out / "hes_migration_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--out", required=True)
    parser.add_argument("--arm", default=None)
    args = parser.parse_args(argv)
    report = migrate(args.source, args.out, runtime_arm=args.arm)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())