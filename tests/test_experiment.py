"""Experiment infrastructure tests: manifest, sharding, resume, alignment.

No physics: a synthetic evaluate_fn returns TrialRecord objects so the
lifecycle (shard/status/resume/double-count/alignment) is tested fast and
deterministically. Real MuJoCo coverage lives in the qualification driver.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from benchmarks.heb.protocol import TrialRecord
from evaluation.experiment import (
    ArtifactPolicy,
    ExperimentClass,
    ExperimentError,
    ExperimentManifest,
    LocalExecutor,
    PopulationSpec,
    ShardingSpec,
    aggregate_experiment,
    load_manifest,
    plan_shards,
    shard_scenarios,
    write_canonical_table,
)


def _manifest(
    n: int = 25,
    shard_size: int = 10,
    systems: tuple[str, ...] = ("direct_minimal", "haplous_mediated"),
    reference: str = "direct_minimal",
) -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id="fixture",
        description="fixture",
        benchmark="internal",
        benchmark_version="0.2.0",
        haplous_version="0.5.0",
        systems=systems,
        population=PopulationSpec(n=n, seed=7),
        sharding=ShardingSpec(shard_size=shard_size),
        experiment_class=ExperimentClass.DEVELOPMENT,
        metrics=("task_success", "system_bsr"),
        reference_arm=reference,
        artifact_policy=(ArtifactPolicy.ALL_SUMMARIES,),
    )


class _FakeSystem:
    def __init__(self, name: str) -> None:
        self.name = name


def _fake_evaluate(scenarios, systems):
    """Deterministic synthetic grading: haplous succeeds on even offsets."""
    records = []
    for scenario in scenarios:
        for system in systems:
            success = system.name == "haplous_mediated" or scenario.initial_offset_m < 0.001
            records.append(
                TrialRecord(
                    scenario_id=scenario.id,
                    system=system.name,
                    reported_disposition="COMPLETE" if success else "REFUSED",
                    reason=None if success else "sync",
                    refusal_codes=() if success else ("feasibility",),
                    wall_s=0.01,
                    graded_disposition="COMPLETE" if success else "REFUSED",
                    task_success=success,
                    bounded_success=success,
                    hard_violations=(),
                    task_metrics={"peak_abs_force_n": 1.0},
                    tags=scenario.tags,
                )
            )
    return records


@pytest.fixture
def executor(tmp_path: Path) -> LocalExecutor:
    return LocalExecutor(
        tmp_path / "run",
        evaluate_fn=_fake_evaluate,
        system_fn=_FakeSystem,
    )


def test_shard_plan_tiles_population_exactly():
    manifest = _manifest(n=25, shard_size=10)
    shards = plan_shards(manifest)
    assert [s.population_count for s in shards] == [10, 10, 5]
    seen: list[str] = []
    for shard in shards:
        seen.extend(s.id for s in shard_scenarios(manifest, shard))
    assert len(seen) == 25 and len(set(seen)) == 25


def test_shard_plan_is_deterministic_and_stable():
    manifest = _manifest()
    assert [s.shard_id for s in plan_shards(manifest)] == [s.shard_id for s in plan_shards(manifest)]


def test_run_then_resume_does_not_recount(executor: LocalExecutor):
    manifest = _manifest()
    first = executor.run(manifest, verbose=False)
    assert all(result.status.value == "COMPLETE" for result in first)
    second = executor.run(manifest, verbose=False)
    assert all(result.status.value == "COMPLETE" for result in second)  # skipped
    from evaluation.experiment import load_completed_episodes

    rows = load_completed_episodes(executor.root)
    assert len(rows) == 25 * len(manifest.systems)  # not double-counted


def test_failed_shard_reruns_and_completes(tmp_path: Path):
    manifest = _manifest()
    failing = plan_shards(manifest)[1].shard_id
    executor = LocalExecutor(
        tmp_path / "run",
        evaluate_fn=_fake_evaluate,
        system_fn=_FakeSystem,
        fail_shards={failing},
    )
    results = executor.run(manifest)
    statuses = {r.shard.shard_id: r.status.value for r in results}
    assert statuses[failing] == "FAILED"

    resumed = LocalExecutor(
        tmp_path / "run", evaluate_fn=_fake_evaluate, system_fn=_FakeSystem
    )
    rerun = resumed.run(manifest)
    statuses = {r.shard.shard_id: r.status.value for r in rerun}
    assert statuses[failing] == "COMPLETE"


def test_aggregate_rejects_unaligned_realizations(executor: LocalExecutor):
    manifest = _manifest()
    executor.run(manifest)
    from evaluation.experiment import load_completed_episodes

    rows = load_completed_episodes(executor.root)
    # Drop one arm's episode for a single realization: alignment must fail.
    trimmed = [r for r in rows if not (r["arm"] == "direct_minimal" and r["episode_id"] == rows[0]["episode_id"])]
    with pytest.raises(ExperimentError, match="unaligned"):
        aggregate_experiment(manifest, trimmed)


def test_load_rejects_corrupt_shard(executor: LocalExecutor):
    manifest = _manifest()
    executor.run(manifest)
    from evaluation.experiment import load_completed_episodes

    episodes = sorted((executor.root / "shards").glob("*/episodes.jsonl"))[0]
    episodes.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ExperimentError, match="hash mismatch"):
        load_completed_episodes(executor.root)


def test_aggregate_uses_shared_formulas(executor: LocalExecutor):
    manifest = _manifest()
    executor.run(manifest)
    from evaluation.experiment import load_completed_episodes

    summary = aggregate_experiment(manifest, load_completed_episodes(executor.root))
    assert summary["reference_arm"] == "direct_minimal"
    for metric in ("task_success", "bsr_system", "coverage"):
        assert "ci_low" in summary["systems"]["haplous_mediated"][metric]
    assert "haplous_mediated_vs_direct_minimal" in summary["paired"]


def test_write_canonical_table_roundtrips(executor: LocalExecutor, tmp_path: Path):
    import pyarrow.parquet as pq

    manifest = _manifest()
    executor.run(manifest)
    from evaluation.experiment import load_completed_episodes

    rows = load_completed_episodes(executor.root)
    out = tmp_path / "episodes.parquet"
    write_canonical_table(rows, out)
    table = pq.read_table(out)
    assert table.num_rows == len(rows)
    assert "hard_violations" in table.column_names


def test_manifest_yaml_roundtrip(tmp_path: Path):
    manifest = _manifest()
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest.as_dict(), sort_keys=True), encoding="utf-8")
    loaded = load_manifest(path)
    assert loaded == manifest


def test_manifest_rejects_bad_fields():
    with pytest.raises(ExperimentError):
        ExperimentManifest(
            experiment_id="x",
            description="",
            benchmark="b",
            benchmark_version="1",
            haplous_version="0",
            systems=("a",),
            population=PopulationSpec(n=0, seed=1),
            sharding=ShardingSpec(),
            experiment_class=ExperimentClass.DEVELOPMENT,
            metrics=(),
            reference_arm="a",
        )
    with pytest.raises(ExperimentError):
        _manifest(systems=("a", "b"), reference="c")
    with pytest.raises(ExperimentError):
        _manifest(systems=("a", "a"), reference="a")


def test_parallel_executor_matches_serial(tmp_path: Path):
    from evaluation.experiment import load_completed_episodes

    manifest = _manifest(n=25, shard_size=10)
    serial = LocalExecutor(tmp_path / "serial", evaluate_fn=_fake_evaluate, system_fn=_FakeSystem)
    parallel = LocalExecutor(tmp_path / "parallel", evaluate_fn=_fake_evaluate, system_fn=_FakeSystem)
    serial.run(manifest)
    parallel.run(manifest, workers=3)

    def _key(r):
        return (r["shard_id"], r["arm"], r["episode_id"])

    a = sorted(load_completed_episodes(serial.root), key=_key)
    b = sorted(load_completed_episodes(parallel.root), key=_key)
    assert a == b


def test_provenance_has_schema_and_manifest_hash(executor: LocalExecutor):
    manifest = _manifest()
    executor.run(manifest)
    provenance = json.loads((executor.root / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["manifest_sha256"] == manifest.sha256()
    assert provenance["episode_schema_version"] == "haplous-episode-v1"
    assert "python" in provenance


def test_canonical_episodes_bounds_unbounded_payloads():
    """A per-step violation log and a per-step command trace must not be
    copied verbatim into the canonical table (they were ~1 GB for N=1000)."""
    from evaluation.experiment import canonical_episodes, plan_shards

    manifest = _manifest(n=1)
    shard = plan_shards(manifest)[0]
    record = TrialRecord(
        scenario_id="s0",
        system="haplous_mediated",
        reported_disposition="ABORTED",
        reason="force",
        refusal_codes=(),
        wall_s=0.5,
        graded_disposition="ABORTED",
        task_success=False,
        bounded_success=False,
        hard_violations=tuple(
            {"code": "process_force", "t_s": float(i), "value": 1.0} for i in range(1000)
        ),
        task_metrics={"peak_abs_force_n": 1.0, "applied_command_times": list(range(5000))},
        tags=("insertion",),
    )
    row = canonical_episodes(manifest, shard, [record])[0]
    assert row["n_hard_violations"] == 1000
    assert len(row["hard_violations"]) == 5
    assert "applied_command_times" not in row["task_metrics"]
    assert row["task_metrics"]["n_applied_commands"] == 5000
    assert row["task_metrics"]["peak_abs_force_n"] == 1.0
