"""Generic, resumable, provenance-carrying experiment infrastructure.

One model covers 10 episodes and 10,000,000 episodes: a validated
manifest, deterministic shards, a local executor that only ever adds
finished shards, and one canonical aggregation from finished shards. A
future GPU backend submits the same shard contract; it does not need a
new experiment model.

Boundaries:
- Haplous owns execution semantics (haplous/).
- The benchmark owns scenario truth, simulation, and task grading
  (benchmarks/heb/). This module depends on that interface, never the
  reverse.
- This module owns the experiment lifecycle: manifest, sharding, resume,
  provenance, canonical rows, aggregation.

Storage is deliberately small: manifests/metadata as JSON/YAML, episode
rows as JSONL per shard plus one canonical Parquet table. No database, no
tiny-file explosion.
"""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
import platform
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = "haplous-experiment-v1"
EPISODE_SCHEMA_VERSION = "haplous-episode-v1"

__all__ = [
    "ArtifactPolicy",
    "ExperimentClass",
    "ExperimentError",
    "ExperimentManifest",
    "LocalExecutor",
    "PopulationSpec",
    "ShardSpec",
    "ShardStatus",
    "aggregate_experiment",
    "canonical_episodes",
    "collect_provenance",
    "load_manifest",
    "plan_shards",
    "shard_scenarios",
]


class ExperimentError(ValueError):
    """Malformed manifest, inconsistent shard state, or invalid result."""


class ExperimentClass(StrEnum):
    DISCOVERY = "discovery"
    DEVELOPMENT = "development"
    QUALIFICATION = "qualification"
    FINAL_ESTIMATION = "final_estimation"


class ShardStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    INVALID = "INVALID"


class ArtifactPolicy(StrEnum):
    ALL_SUMMARIES = "all_summaries"
    ALL_SEVERE_FAILURES = "all_severe_failures"
    ALL_HARMFUL_INTERVENTIONS = "all_harmful_interventions"
    ALL_FALSE_REFUSALS = "all_false_refusals"
    SAMPLED_FAILURES = "sampled_failures"
    SAMPLED_SUCCESSES = "sampled_successes"
    OPTIONAL_VIDEO = "optional_video"


@dataclass(frozen=True, slots=True)
class PopulationSpec:
    n: int
    seed: int
    slide_fraction: float = 0.28

    def __post_init__(self) -> None:
        if self.n <= 0:
            raise ExperimentError("population.n must be positive")
        if not 0.0 <= self.slide_fraction <= 1.0:
            raise ExperimentError("population.slide_fraction must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class ShardingSpec:
    shard_size: int = 100

    def __post_init__(self) -> None:
        if self.shard_size <= 0:
            raise ExperimentError("sharding.shard_size must be positive")


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    experiment_id: str
    description: str
    benchmark: str
    benchmark_version: str
    haplous_version: str
    systems: tuple[str, ...]
    population: PopulationSpec
    sharding: ShardingSpec
    experiment_class: ExperimentClass
    metrics: tuple[str, ...]
    reference_arm: str
    artifact_policy: tuple[ArtifactPolicy, ...] = (ArtifactPolicy.ALL_SUMMARIES,)
    claims: tuple[str, ...] = ()
    policy_checkpoint: str | None = None
    schema_version: str = MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.experiment_id:
            raise ExperimentError("experiment_id must not be empty")
        if not self.systems:
            raise ExperimentError("systems must not be empty")
        if self.reference_arm not in self.systems:
            raise ExperimentError("reference_arm must be one of systems")
        if len(set(self.systems)) != len(self.systems):
            raise ExperimentError("systems must be unique")
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ExperimentError(
                f"unsupported manifest schema {self.schema_version!r}"
            )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["experiment_class"] = self.experiment_class.value
        data["artifact_policy"] = [item.value for item in self.artifact_policy]
        return data

    def sha256(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ShardSpec:
    experiment_id: str
    shard_id: str
    shard_index: int
    seed: int
    population_offset: int
    population_count: int
    systems: tuple[str, ...]

    @property
    def key(self) -> str:
        return self.shard_id


def _require(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ExperimentError(f"{where}: missing required field {key!r}")
    return mapping[key]


def load_manifest(path: str | Path) -> ExperimentManifest:
    import yaml

    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    if not isinstance(raw, Mapping):
        raise ExperimentError("manifest must be a mapping")
    pop = _require(raw, "population", "manifest")
    shard = raw.get("sharding", {"shard_size": 100})
    try:
        experiment_class = ExperimentClass(_require(raw, "experiment_class", "manifest"))
    except ValueError as exc:
        raise ExperimentError(str(exc)) from exc
    try:
        artifact_policy = tuple(
            ArtifactPolicy(item) for item in raw.get("artifact_policy", ["all_summaries"])
        )
    except ValueError as exc:
        raise ExperimentError(str(exc)) from exc
    return ExperimentManifest(
        experiment_id=_require(raw, "experiment_id", "manifest"),
        description=raw.get("description", ""),
        benchmark=_require(raw, "benchmark", "manifest"),
        benchmark_version=_require(raw, "benchmark_version", "manifest"),
        haplous_version=raw.get("haplous_version", "unknown"),
        systems=tuple(_require(raw, "systems", "manifest")),
        population=PopulationSpec(
            n=_require(pop, "n", "manifest.population"),
            seed=_require(pop, "seed", "manifest.population"),
            slide_fraction=pop.get("slide_fraction", 0.28),
        ),
        sharding=ShardingSpec(shard_size=shard.get("shard_size", 100)),
        experiment_class=experiment_class,
        metrics=tuple(raw.get("metrics", ())),
        reference_arm=_require(raw, "reference_arm", "manifest"),
        artifact_policy=artifact_policy,
        claims=tuple(raw.get("claims", ())),
        policy_checkpoint=raw.get("policy_checkpoint"),
        schema_version=raw.get("schema_version", MANIFEST_SCHEMA_VERSION),
    )


def dump_manifest(manifest: ExperimentManifest, path: str | Path) -> None:
    import yaml

    Path(path).write_text(
        yaml.safe_dump(manifest.as_dict(), sort_keys=True), encoding="utf-8"
    )


def plan_shards(manifest: ExperimentManifest) -> list[ShardSpec]:
    """Deterministic shard plan. Shard k owns population indices
    [k*size, (k+1)*size). Same manifest -> same plan, always."""
    n = manifest.population.n
    size = manifest.sharding.shard_size
    n_shards = math.ceil(n / size)
    shards = []
    for k in range(n_shards):
        offset = k * size
        count = min(size, n - offset)
        shards.append(
            ShardSpec(
                experiment_id=manifest.experiment_id,
                shard_id=f"{manifest.experiment_id}-s{k:05d}",
                shard_index=k,
                seed=manifest.population.seed,
                population_offset=offset,
                population_count=count,
                systems=manifest.systems,
            )
        )
    return shards


def shard_scenarios(manifest: ExperimentManifest, shard: ShardSpec) -> list:
    """The exact scenario population for one shard. Generated from the
    whole seeded population then sliced, so shards tile the population
    with no gaps and no overlap, independent of execution order."""
    from benchmarks.heb.population import population

    scenarios = population(
        manifest.population.n,
        manifest.population.seed,
        slide_fraction=manifest.population.slide_fraction,
    )
    return scenarios[shard.population_offset : shard.population_offset + shard.population_count]


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def _git_state() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip() or None
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return None, None


def _module_version(name: str) -> str | None:
    try:
        module = __import__(name)
        return getattr(module, "__version__", None)
    except Exception:
        return None


def collect_provenance(manifest: ExperimentManifest) -> dict[str, Any]:
    commit, dirty = _git_state()
    return {
        "manifest_sha256": manifest.sha256(),
        "manifest_schema_version": manifest.schema_version,
        "episode_schema_version": EPISODE_SCHEMA_VERSION,
        "haplous_version": manifest.haplous_version,
        "git_commit": commit,
        "git_dirty": dirty,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "dependencies": {
            name: _module_version(name)
            for name in ("numpy", "mujoco", "pyarrow", "yaml", "matplotlib")
        },
        "benchmark": manifest.benchmark,
        "benchmark_version": manifest.benchmark_version,
        "policy_checkpoint": manifest.policy_checkpoint,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
    }


# --------------------------------------------------------------------------
# Canonical episode rows
# --------------------------------------------------------------------------


def _prune_task_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Keep scalar task metrics; replace unbounded per-step traces with
    their length. A canonical episode table is a results table, not a
    trace store; applied_command_times alone is ~0.6 MB per episode."""
    pruned: dict[str, Any] = {}
    for key, value in metrics.items():
        if key == "applied_command_times":
            pruned["n_applied_commands"] = len(value or ())
        elif isinstance(value, (str, int, float, bool)) or value is None:
            pruned[key] = value
    return pruned


def _prune_violations(violations: Sequence[Any]) -> tuple[list[dict[str, Any]], int]:
    """A violation is logged every control step while a breach persists, so
    a single episode can carry tens of thousands of near-identical entries.
    The canonical table keeps a bounded sample plus the true count."""
    items = [dict(v) for v in violations]
    return items[:5], len(items)


def canonical_episodes(
    manifest: ExperimentManifest,
    shard: ShardSpec,
    records: Sequence[Any],
    metadata: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """TrialRecord stream -> canonical episode rows, one row per
    (shard, system, scenario). Nested values stay JSON-encoded so a
    single Parquet table can hold every arm and task family. ``metadata``
    (campaign provenance) is added under a ``metadata`` key only when given,
    so rows without it keep the original contract."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        arm = record.system
        key = (arm, record.scenario_id)
        if key in seen:
            raise ExperimentError(f"duplicate episode {key} in shard {shard.shard_id}")
        seen.add(key)
        violations, n_violations = _prune_violations(record.hard_violations)
        rows.append(
            {
                "schema_version": EPISODE_SCHEMA_VERSION,
                "experiment_id": manifest.experiment_id,
                "shard_id": shard.shard_id,
                "shard_index": shard.shard_index,
                "arm": arm,
                "episode_id": record.scenario_id,
                "scenario_id": record.scenario_id,
                "seed": manifest.population.seed,
                "graded_disposition": record.graded_disposition,
                "reported_disposition": record.reported_disposition,
                "reason": record.reason,
                "refusal_codes": list(record.refusal_codes),
                "task_success": bool(record.task_success),
                "bounded_success": bool(record.bounded_success),
                "hard_violations": violations,
                "n_hard_violations": n_violations,
                "task_metrics": _prune_task_metrics(dict(record.task_metrics)),
                "tags": list(record.tags),
                "wall_s": float(record.wall_s),
            }
        )
        if metadata is not None:
            rows[-1]["metadata"] = dict(metadata)
    return rows


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# Local executor
# --------------------------------------------------------------------------


@dataclass
class ShardResult:
    shard: ShardSpec
    status: ShardStatus
    attempts: int
    episodes: int
    wall_s: float
    error: str | None = None


class LocalExecutor:
    """Executes a manifest with independent, resumable shards on one
    machine. Completed shards are never re-run and never double-counted;
    failed or missing shards are retried on the next pass. This is the
    only execution backend; a GPU backend can implement the same shard
    contract later."""

    def __init__(
        self,
        root: str | Path,
        *,
        evaluate_fn: Callable[..., Sequence[Any]] | None = None,
        scenario_fn: Callable[[ExperimentManifest, ShardSpec], list] | None = None,
        system_fn: Callable[[str], Any] | None = None,
        fail_shards: Iterable[str] = (),
        row_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._evaluate = evaluate_fn
        self._scenarios = scenario_fn or shard_scenarios
        self._system = system_fn
        self._fail_shards = set(fail_shards)
        self._row_metadata = row_metadata

    # -- paths -------------------------------------------------------------
    def shard_dir(self, shard: ShardSpec) -> Path:
        return self.root / "shards" / shard.shard_id

    def _status_path(self, shard: ShardSpec) -> Path:
        return self.shard_dir(shard) / "status.json"

    def _episodes_path(self, shard: ShardSpec) -> Path:
        return self.shard_dir(shard) / "episodes.jsonl"

    def read_status(self, shard: ShardSpec) -> dict[str, Any] | None:
        path = self._status_path(shard)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def _write_status(self, shard: ShardSpec, **payload: Any) -> None:
        directory = self.shard_dir(shard)
        directory.mkdir(parents=True, exist_ok=True)
        tmp = self._status_path(shard).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self._status_path(shard))

    # -- execution ---------------------------------------------------------
    def run(
        self,
        manifest: ExperimentManifest,
        *,
        resume: bool = True,
        max_shards: int | None = None,
        workers: int = 1,
        verbose: bool = False,
    ) -> list[ShardResult]:
        self._write_manifest_and_provenance(manifest)
        shards = plan_shards(manifest)
        results: list[ShardResult] = []
        pending: list[ShardSpec] = []
        for shard in shards:
            status = self.read_status(shard)
            if (
                resume
                and status is not None
                and status.get("status") == ShardStatus.COMPLETE.value
                and self._episodes_path(shard).is_file()
            ):
                results.append(
                    ShardResult(
                        shard,
                        ShardStatus.COMPLETE,
                        int(status.get("attempts", 1)),
                        int(status.get("episodes", 0)),
                        float(status.get("wall_s", 0.0)),
                    )
                )
                if verbose:
                    print(f"skip complete {shard.shard_id}")
                continue
            pending.append(shard)
            if max_shards is not None and len(pending) >= max_shards:
                break

        if workers > 1 and len(pending) > 1:
            context = multiprocessing.get_context("spawn")
            payload = [self._worker_args(manifest, shard, verbose) for shard in pending]
            with context.Pool(min(workers, len(pending))) as pool:
                for result in pool.imap_unordered(_execute_shard_worker, payload):
                    results.append(result)
                    if verbose:
                        print(f"{result.status.value} {result.shard.shard_id}")
        else:
            for shard in pending:
                results.append(self._run_shard(manifest, shard, verbose=verbose))
        return results

    def _worker_args(
        self, manifest: ExperimentManifest, shard: ShardSpec, verbose: bool
    ) -> tuple:
        return (
            str(self.root),
            manifest,
            shard,
            self._evaluate,
            self._scenarios,
            self._system,
            tuple(self._fail_shards),
            verbose,
            self._row_metadata,
        )

    def _write_manifest_and_provenance(self, manifest: ExperimentManifest) -> None:
        manifest_path = self.root / "manifest.json"
        payload = json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n"
        manifest_path.write_text(payload, encoding="utf-8")
        (self.root / "provenance.json").write_text(
            json.dumps(collect_provenance(manifest), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _run_shard(
        self, manifest: ExperimentManifest, shard: ShardSpec, *, verbose: bool = False
    ) -> ShardResult:
        previous = self.read_status(shard) or {}
        attempts = int(previous.get("attempts", 0)) + 1
        self._write_status(
            shard,
            experiment_id=manifest.experiment_id,
            shard_id=shard.shard_id,
            status=ShardStatus.RUNNING.value,
            attempts=attempts,
            started_at_utc=datetime.now(UTC).isoformat(),
        )
        started = time.perf_counter()
        try:
            if shard.shard_id in self._fail_shards:
                raise RuntimeError("injected shard failure")
            scenarios = self._scenarios(manifest, shard)
            records = self._run_scenarios(manifest, scenarios)
            rows = canonical_episodes(manifest, shard, records, self._row_metadata)
            if not rows:
                raise ExperimentError("shard produced no episodes")
            self._write_episodes(shard, rows)
            wall = time.perf_counter() - started
            self._write_status(
                shard,
                experiment_id=manifest.experiment_id,
                shard_id=shard.shard_id,
                status=ShardStatus.COMPLETE.value,
                attempts=attempts,
                episodes=len(rows),
                wall_s=wall,
                episodes_sha256=_sha256_file(self._episodes_path(shard)),
                completed_at_utc=datetime.now(UTC).isoformat(),
            )
            if verbose:
                print(f"complete {shard.shard_id}: {len(rows)} episodes in {wall:.1f}s")
            return ShardResult(shard, ShardStatus.COMPLETE, attempts, len(rows), wall)
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            wall = time.perf_counter() - started
            self._write_status(
                shard,
                experiment_id=manifest.experiment_id,
                shard_id=shard.shard_id,
                status=ShardStatus.FAILED.value,
                attempts=attempts,
                wall_s=wall,
                error=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
                failed_at_utc=datetime.now(UTC).isoformat(),
            )
            if verbose:
                print(f"failed {shard.shard_id}: {exc}")
            return ShardResult(shard, ShardStatus.FAILED, attempts, 0, wall, str(exc))

    def _run_scenarios(self, manifest: ExperimentManifest, scenarios: Sequence[Any]) -> Sequence[Any]:
        if self._evaluate is not None:
            evaluate = self._evaluate
        else:
            from benchmarks.heb.runner import evaluate
        if self._system is not None:
            systems = [self._system(name) for name in manifest.systems]
        else:
            from benchmarks.heb.systems import system_by_name

            systems = [system_by_name(name) for name in manifest.systems]
        return evaluate(list(scenarios), systems)

    def _write_episodes(self, shard: ShardSpec, rows: Sequence[Mapping[str, Any]]) -> None:
        directory = self.shard_dir(shard)
        directory.mkdir(parents=True, exist_ok=True)
        tmp = self._episodes_path(shard).with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        tmp.replace(self._episodes_path(shard))


# --------------------------------------------------------------------------
# Loading and aggregation
# --------------------------------------------------------------------------


def _execute_shard_worker(args: tuple) -> ShardResult:
    """Top-level multiprocessing worker: one process, one independent
    shard directory, no shared mutable state."""
    (
        root,
        manifest,
        shard,
        evaluate_fn,
        scenario_fn,
        system_fn,
        fail_shards,
        verbose,
        row_metadata,
    ) = args
    executor = LocalExecutor(
        root,
        evaluate_fn=evaluate_fn,
        scenario_fn=scenario_fn,
        system_fn=system_fn,
        fail_shards=fail_shards,
        row_metadata=row_metadata,
    )
    return executor._run_shard(manifest, shard, verbose=verbose)


def load_completed_episodes(root: str | Path) -> list[dict[str, Any]]:
    """Every episode row from COMPLETE shards only, one row per
    (experiment, shard, arm, episode). FAILED/INVALID/RUNNING shards are
    excluded so partial results cannot masquerade as complete."""
    root = Path(root)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for status_path in sorted(root.glob("shards/*/status.json")):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("status") != ShardStatus.COMPLETE.value:
            continue
        episodes_path = status_path.parent / "episodes.jsonl"
        if not episodes_path.is_file():
            raise ExperimentError(
                f"{status_path.parent.name}: status COMPLETE but episodes.jsonl missing"
            )
        expected = status.get("episodes_sha256")
        if expected and _sha256_file(episodes_path) != expected:
            raise ExperimentError(
                f"{status_path.parent.name}: episodes.jsonl hash mismatch "
                "(corrupt or edited shard)"
            )
        for line in episodes_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["shard_id"], row["arm"], row["episode_id"])
            if key in seen:
                raise ExperimentError(f"double-counted episode {key}")
            seen.add(key)
            rows.append(row)
    return rows


def _rows_by_arm(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_arm: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_arm.setdefault(row["arm"], []).append(dict(row))
    return by_arm


def aggregate_experiment(
    manifest: ExperimentManifest, rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """One canonical aggregation. Every arm uses the same formulas;
    paired statistics compare each arm to the reference arm over the
    shared realization identities only (missing/duplicate pairs are
    rejected, not silently dropped)."""
    from evaluation.aggregate import arm_summary, paired_comparison

    by_arm = _rows_by_arm(rows)
    missing_arms = [arm for arm in manifest.systems if arm not in by_arm]
    if missing_arms:
        raise ExperimentError(f"no episodes for arms: {missing_arms}")
    force_limit = 25.0
    summaries = {arm: arm_summary(by_arm[arm], force_limit_n=force_limit) for arm in manifest.systems}

    # Paired: align on episode_id. A realization present in one arm and
    # missing in another is a defect, not a smaller sample.
    reference = manifest.reference_arm
    populations = {
        arm: {row["episode_id"] for row in by_arm[arm]} for arm in manifest.systems
    }
    reference_ids = populations[reference]
    paired: dict[str, Any] = {}
    for arm in manifest.systems:
        if arm == reference:
            continue
        other = populations[arm]
        only_ref = sorted(reference_ids - other)
        only_arm = sorted(other - reference_ids)
        if only_ref or only_arm:
            raise ExperimentError(
                f"unaligned realizations for {reference} vs {arm}: "
                f"{len(only_ref)} only-in-reference, {len(only_arm)} only-in-arm"
            )
        paired[f"{arm}_vs_{reference}"] = paired_comparison(
            by_arm[arm], by_arm[reference]
        )
    return {
        "schema_version": EPISODE_SCHEMA_VERSION,
        "experiment_id": manifest.experiment_id,
        "manifest_sha256": manifest.sha256(),
        "reference_arm": reference,
        "n_episodes": len(rows),
        "systems": summaries,
        "paired": paired,
    }


def write_canonical_table(rows: Sequence[Mapping[str, Any]], path: str | Path) -> None:
    """One Parquet table for all episode rows. Nested values are stored
    as canonical JSON strings; lists/dicts are rehydrated on read."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    columns = (
        "schema_version",
        "experiment_id",
        "shard_id",
        "shard_index",
        "arm",
        "episode_id",
        "scenario_id",
        "seed",
        "graded_disposition",
        "reported_disposition",
        "reason",
        "task_success",
        "bounded_success",
        "wall_s",
        "n_hard_violations",
    )
    nested = ("refusal_codes", "hard_violations", "task_metrics", "tags")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, list[Any]] = {name: [] for name in columns}
    for name in nested:
        data[name] = []
    for row in rows:
        for name in columns:
            data[name].append(row.get(name))
        for name in nested:
            data[name].append(json.dumps(row.get(name), sort_keys=True))
    table = pa.table({name: pa.array(values) for name, values in data.items()})
    pq.write_table(table, path, compression="zstd")


# --------------------------------------------------------------------------
# Metric dictionary
# --------------------------------------------------------------------------


def metric_dictionary() -> list[dict[str, str]]:
    """Canonical metric definitions. The report cites this dictionary; it
    is the single place a metric's meaning and limits are stated."""
    return [
        {
            "name": "task_success",
            "meaning": "Benchmark-graded task completion (geometry success).",
            "numerator": "episodes graded task_success",
            "denominator": "all presented episodes",
            "unit": "proportion",
            "better": "higher",
            "establishes": "task-level usefulness on the tested population",
            "does_not_establish": "hardware safety or cross-task generality",
        },
        {
            "name": "system_bsr",
            "meaning": "Presented episodes that both completed and stayed within declared bounds.",
            "numerator": "bounded_success episodes",
            "denominator": "all presented episodes",
            "unit": "proportion",
            "better": "higher",
            "establishes": "combined usefulness and boundedness over the declared population",
            "does_not_establish": "production reliability",
        },
        {
            "name": "conditional_bsr",
            "meaning": "Bounded success among accepted (non-refused) episodes.",
            "numerator": "bounded_success episodes among accepted",
            "denominator": "accepted episodes",
            "unit": "proportion",
            "better": "higher",
            "establishes": "execution quality once the system commits",
            "does_not_establish": "coverage; a system can refuse everything and score 1.0",
        },
        {
            "name": "coverage",
            "meaning": "Fraction of presented requests the system accepted and attempted.",
            "numerator": "accepted episodes",
            "denominator": "all presented episodes",
            "unit": "proportion",
            "better": "higher, unless refusals prevent unacceptable execution",
            "establishes": "how much of the presented population was served",
            "does_not_establish": "that acceptance was wise (pair with violations)",
        },
        {
            "name": "violation_rate",
            "meaning": "Episodes with any benchmark-graded hard requirement violation.",
            "numerator": "episodes with >=1 hard violation",
            "denominator": "all presented episodes",
            "unit": "proportion",
            "better": "lower",
            "establishes": "constraint exposure over the population",
            "does_not_establish": "severity without the taxonomy",
        },
        {
            "name": "violation_given_acceptance",
            "meaning": "Hard violations among accepted episodes.",
            "numerator": "accepted episodes with >=1 hard violation",
            "denominator": "accepted episodes",
            "unit": "proportion",
            "better": "lower",
            "establishes": "constraint risk conditional on committing",
            "does_not_establish": "anything about refused requests",
        },
        {
            "name": "false_refusal_rate",
            "meaning": "Refusals of episodes the benchmark deems physically feasible.",
            "numerator": "refused episodes flagged physically feasible",
            "denominator": "physically feasible presented episodes",
            "unit": "proportion",
            "better": "lower",
            "establishes": "productivity loss from over-conservatism",
            "does_not_establish": "safety of the refusals",
        },
        {
            "name": "recovery_success_rate",
            "meaning": "Recovery-success rate where the benchmark designates the disturbance recoverable.",
            "numerator": "recoverable episodes ending in bounded success",
            "denominator": "recoverable presented episodes",
            "unit": "proportion",
            "better": "higher",
            "establishes": "value of authorized recovery",
            "does_not_establish": "recovery on disturbances outside the tested class",
        },
    ]
