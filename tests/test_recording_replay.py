"""M2.1: record one real HEB episode, replay it from the M0 archive alone."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pytest

from benchmarks.recording import run_episode, scenario_by_id
from evaluation import replay as rp
from evaluation.run_registry import Registry

REPO = Path(__file__).resolve().parents[1]
RUN_ID = "m2-test-dropout"


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("recordings")
    proc = subprocess.run(
        [sys.executable, "scripts/record_episode.py", "--root", str(root), "--run-id", RUN_ID,
         "--scenario", "insert_dropout_large", "--chunk-ticks", "512"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert Registry(root).state(RUN_ID) == "VERIFIED"
    return root


def test_fresh_process_replay_loads_no_runtime_benchmark_or_controller(recorded, tmp_path):
    report_path = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "evaluation.replay", RUN_ID, "--root", str(recorded),
         "--rrd", str(tmp_path / "r.rrd"), "--mp4", str(tmp_path / "r.mp4"), "--report", str(report_path)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(report_path.read_text())
    assert report["forbidden_modules_loaded"] == []
    assert report["pose_check"]["pass"] and report["pose_check"]["observations_compared"] > 3000
    assert report["outcome"]["disposition"] == "COMPLETE"


def test_cli_replays_an_m0_recording(recorded, tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "haplous.cli", "replay", RUN_ID, "--recordings-root", str(recorded),
         "--report", str(tmp_path / "rep.json")],
        cwd=REPO, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads((tmp_path / "rep.json").read_text())["pose_check"]["pass"]


@pytest.mark.parametrize("scenario_id", ["insert_dropout_large", "insert_machine_fault"])
def test_recording_does_not_perturb_the_episode(scenario_id, tmp_path):
    scenario = scenario_by_id(scenario_id)
    plain, plain_real, _ = run_episode(scenario, None)
    recorded_run, rec_real, metadata = run_episode(scenario, tmp_path)
    assert recorded_run.observations == plain.observations
    assert recorded_run.detail["applied_command_times"] == plain.detail["applied_command_times"]
    assert (recorded_run.disposition, recorded_run.reason, recorded_run.refusal_codes) == (
        plain.disposition, plain.reason, plain.refusal_codes)
    assert float(rec_real.machine._data.time) == float(plain_real.machine._data.time)
    assert metadata["n_observations"] == len(plain.observations)


def _reseal(root: Path, new_id: str, mutate, source: str = RUN_ID) -> None:
    """Copy the recording, plant a defect, and register it as a fresh,
    M0-intact run so replay's own checks (not only M0 hashes) must catch it."""
    registry = Registry(root)
    manifest = registry.manifest(source)
    manifest["run_id"] = new_id
    registry.register(manifest)
    registry.start(new_id)
    art = registry.run_dir(new_id) / "artifacts"
    shutil.copytree(registry.run_dir(source) / "artifacts", art)
    mutate(art)
    registry.finish(new_id, success=True, exit_code=0)
    assert registry.verify(new_id) == []


def _rewrite_stream(art: Path, keep) -> None:
    table = pa.ipc.open_file(str(art / rp.STATE)).read_all()
    table = table.filter(pa.array([keep(int(t)) for t in table.column("tick").to_pylist()]))
    with pa.ipc.new_file(str(art / rp.STATE), table.schema,
                         options=pa.ipc.IpcWriteOptions(compression="zstd")) as writer:
        writer.write_table(table, max_chunksize=512)


def _flip_byte(path: Path, offset: int) -> None:
    raw = bytearray(path.read_bytes())
    raw[offset] ^= 0xFF
    path.write_bytes(bytes(raw))


def _set_meta(art: Path, **changes) -> None:
    meta = json.loads((art / rp.METADATA).read_text())
    meta.update(changes)
    (art / rp.METADATA).write_text(json.dumps(meta))


DEFECTS = [
    ("corrupt-model", lambda art: _flip_byte(art / rp.MODEL_MJB, 4096), "model asset digest mismatch"),
    ("clipped-stream", lambda art: _rewrite_stream(art, lambda t: t < 3000), "state stream clipped"),
    ("truncated-file", lambda art: (art / rp.STATE).write_bytes((art / rp.STATE).read_bytes()[:-4096]),
     "state stream unreadable"),
    ("missing-sample", lambda art: _rewrite_stream(art, lambda t: t != 1500),
     "missing sample at tick 1500"),
    ("other-mujoco", lambda art: _set_meta(art, mujoco_version="0.0.0"), "mujoco version mismatch"),
]


@pytest.mark.parametrize(("name", "mutate", "message"), DEFECTS)
def test_planted_defects_make_replay_refuse(recorded, name, mutate, message):
    _reseal(recorded, f"defect-{name}", mutate)
    with pytest.raises(rp.ReplayRefused, match=message):
        rp.replay(recorded, f"defect-{name}", rrd_path=None, mp4_path=None, report_path=None)


def test_tampered_archive_is_refused_by_m0_check(recorded):
    _reseal(recorded, "defect-tampered", lambda art: None)
    _flip_byte(Registry(recorded).run_dir("defect-tampered") / "artifacts" / rp.STATE, 100)
    with pytest.raises(rp.ReplayRefused, match="artifact hash mismatch: state.arrow"):
        rp.replay(recorded, "defect-tampered", rrd_path=None, mp4_path=None, report_path=None)
