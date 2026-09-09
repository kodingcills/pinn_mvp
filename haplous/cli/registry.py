from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, Sequence

from haplous import __version__


JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonMapping = Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class RunMetadata:
    benchmark_name: str
    benchmark_version: str
    framework: str
    task_id: str
    system_variant: str
    controller_config: JsonMapping
    runtime_config: JsonMapping
    seed: int
    scenarios: Sequence[JsonMapping]
    metric_implementation_version: str
    started_at_utc: str | None = None


@dataclass(frozen=True, slots=True)
class RunPaths:
    run_id: str
    run_dir: Path
    artifact_dir: Path

    @property
    def manifest(self) -> Path:
        return self.run_dir / "manifest.json"

    @property
    def summary(self) -> Path:
        return self.run_dir / "summary.json"

    @property
    def trials(self) -> Path:
        return self.run_dir / "trials.jsonl"


def canonical_sha256(value: JsonValue | JsonMapping | Sequence[JsonMapping]) -> str:
    payload = json.dumps(value, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RunRegistry:
    def __init__(
        self,
        results_root: Path | None = None,
        artifacts_root: Path | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        configured_results = os.environ.get("HAPLOUS_RESULTS_DIR")
        self.results_root = results_root or (
            Path(configured_results)
            if configured_results
            else project_root / "results" / "external_eval"
        )
        self.artifacts_root = artifacts_root or project_root / "artifacts" / "evals"

    def write_run(
        self,
        metadata: RunMetadata,
        trials: Sequence[JsonMapping],
        summary: JsonMapping,
    ) -> RunPaths:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + f"-{secrets.token_hex(3)}"
        run_dir = self.results_root / metadata.benchmark_name / run_id
        artifact_dir = self.artifacts_root / metadata.benchmark_name / run_id
        run_dir.mkdir(parents=True)
        artifact_dir.mkdir(parents=True)
        paths = RunPaths(run_id, run_dir, artifact_dir)

        trial_text = "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in trials
        )
        paths.trials.write_text(trial_text, encoding="utf-8")
        paths.summary.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        artifact_entries = {
            "trials": {
                "path": "trials.jsonl",
                "sha256": file_sha256(paths.trials),
            },
            "summary": {
                "path": "summary.json",
                "sha256": file_sha256(paths.summary),
            },
            "large_artifacts": {"path": str(artifact_dir)},
        }
        manifest = {
            "haplous_version": __version__,
            "system_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parents[2],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "benchmark": {
                "name": metadata.benchmark_name,
                "version": metadata.benchmark_version,
            },
            "framework": metadata.framework,
            "task_id": metadata.task_id,
            "system_variant": metadata.system_variant,
            "controller_config": metadata.controller_config,
            "controller_config_sha256": canonical_sha256(metadata.controller_config),
            "runtime_config": metadata.runtime_config,
            "runtime_config_sha256": canonical_sha256(metadata.runtime_config),
            "seed": metadata.seed,
            "scenario_sha256": canonical_sha256(metadata.scenarios),
            "started_at_utc": metadata.started_at_utc or datetime.now(UTC).isoformat(),
            "artifacts": artifact_entries,
            "metric_implementation_version": metadata.metric_implementation_version,
        }
        paths.manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        hashes = {
            name: file_sha256(run_dir / name)
            for name in ("manifest.json", "summary.json", "trials.jsonl")
        }
        (run_dir / "artifact_hashes.json").write_text(
            json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return paths

    def resolve(self, run_id: str) -> RunPaths:
        direct = Path(run_id)
        if direct.is_dir():
            return RunPaths(direct.name, direct, self.artifacts_root / direct.parent.name / direct.name)
        matches = list(self.results_root.glob(f"*/{run_id}"))
        if len(matches) != 1:
            raise FileNotFoundError(f"run not found or ambiguous: {run_id}")
        run_dir = matches[0]
        return RunPaths(run_id, run_dir, self.artifacts_root / run_dir.parent.name / run_id)

    def recent(self, limit: int = 10) -> list[RunPaths]:
        if not self.results_root.exists():
            return []
        directories = [path for path in self.results_root.glob("*/*") if path.is_dir()]
        return [
            RunPaths(path.name, path, self.artifacts_root / path.parent.name / path.name)
            for path in sorted(directories, reverse=True)[:limit]
        ]
