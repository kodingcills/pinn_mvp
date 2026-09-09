from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from haplous.cli import main
from haplous.cli.commands import _table
from haplous.cli.registry import RunMetadata, RunRegistry


def test_cli_imports_frameworks_only_inside_commands() -> None:
    cli_dir = Path(__file__).resolve().parents[1] / "haplous" / "cli"
    for path in cli_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in (item for item in tree.body if type(item) in (ast.Import, ast.ImportFrom)):
            if type(node) is ast.Import:
                roots = {alias.name.split(".")[0] for alias in node.names}
            else:
                roots = {(node.module or "").split(".")[0]}
            assert roots.isdisjoint({"mujoco", "pegsim", "benchmarks"})
        for node in (item for item in ast.walk(tree) if type(item) in (ast.Import, ast.ImportFrom)):
            if type(node) is ast.Import:
                roots = {alias.name.split(".")[0] for alias in node.names}
            else:
                roots = {(node.module or "").split(".")[0]}
            if not roots:
                continue
            assert roots.isdisjoint({"robosuite", "isaaclab", "isaacsim"})


def test_doctor_reports_optional_framework_status(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["doctor"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "mujoco" in output and "OK" in output
    assert "robosuite" in output and "NOT INSTALLED" in output


def test_list_prints_all_sections(capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAPLOUS_RESULTS_DIR", str(tmp_path / "results"))

    exit_code = main(["list"])

    output = capsys.readouterr().out
    assert exit_code == 0
    for section in ("benchmarks", "tasks", "systems", "controllers", "backends", "datasets", "recent runs"):
        assert section in output


def test_table_renders_headers_when_there_are_no_rows(capsys: pytest.CaptureFixture[str]) -> None:
    _table(("scenario_id", "reason"), [])

    output = capsys.readouterr().out
    assert "scenario_id" in output
    assert "reason" in output


def test_run_writes_consistent_registry_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results_root = tmp_path / "results"
    monkeypatch.setenv("HAPLOUS_RESULTS_DIR", str(results_root))

    exit_code = main([
        "run",
        "internal-insertion",
        "--system",
        "haplous_mediated",
        "--scenario",
        "insert_nominal",
        "--seed",
        "7",
    ])

    assert exit_code == 0
    run_dir = next((results_root / "internal-insertion").iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    required = {
        "haplous_version",
        "system_commit",
        "benchmark",
        "framework",
        "task_id",
        "system_variant",
        "controller_config_sha256",
        "runtime_config_sha256",
        "seed",
        "scenario_sha256",
        "started_at_utc",
        "artifacts",
        "metric_implementation_version",
    }
    assert required <= manifest.keys()
    hashes = json.loads((run_dir / "artifact_hashes.json").read_text(encoding="utf-8"))
    for filename, expected in hashes.items():
        assert hashlib.sha256((run_dir / filename).read_bytes()).hexdigest() == expected
    for artifact in ("summary", "trials"):
        entry = manifest["artifacts"][artifact]
        assert hashlib.sha256((run_dir / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]


def test_eval_writes_multi_system_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results_root = tmp_path / "results"
    monkeypatch.setenv("HAPLOUS_RESULTS_DIR", str(results_root))

    exit_code = main(["eval", "internal", "--systems", "direct_minimal,haplous_mediated", "--headless"])

    assert exit_code == 0
    run_dir = next((results_root / "internal").iterdir())
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert set(summary["systems"]) == {"direct_minimal", "haplous_mediated"}
    assert summary["n_scenarios"] == 4


@pytest.fixture
def fixture_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[RunRegistry, str, str]:
    results_root = tmp_path / "results"
    monkeypatch.setenv("HAPLOUS_RESULTS_DIR", str(results_root))
    registry = RunRegistry(results_root=results_root, artifacts_root=tmp_path / "artifacts")
    metadata = RunMetadata(
        benchmark_name="fixture",
        benchmark_version="1.0",
        framework="internal",
        task_id="fixture-task",
        system_variant="system-a",
        controller_config={"gain": 1.0},
        runtime_config={"headless": True},
        seed=3,
        scenarios=[{"id": "s1", "initial_offset_m": 0.001}],
        metric_implementation_version="fixture-metrics-1.0",
    )
    trials = [
        {"scenario_id": "s1", "system": "system-a", "bounded_success": False, "reason": "fixture_failure", "scenario_axes": {"initial_offset_m": 0.001}},
        {"scenario_id": "s2", "system": "system-a", "bounded_success": True, "reason": None, "scenario_axes": {"initial_offset_m": 0.002}},
    ]
    run_a = registry.write_run(metadata, trials, {"systems": {"system-a": {"bounded_success_rate_among_accepted": 0.5}}})
    run_b = registry.write_run(metadata, trials, {"systems": {"system-a": {"bounded_success_rate_among_accepted": 0.75}}})
    return registry, run_a.run_id, run_b.run_id


def test_analysis_commands_read_registry_fixture(
    fixture_registry: tuple[RunRegistry, str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    _, run_a, run_b = fixture_registry

    assert main(["compare", run_a, run_b]) == 0
    assert main(["envelope", run_a]) == 0
    assert main(["replay", run_a, "--failures", "--ui"]) == 0
    assert main(["report", run_a]) == 0

    output = capsys.readouterr().out
    assert "system-a" in output
    assert "initial_offset_m" in output
    assert "s1" in output
    assert "summary.json" in output
