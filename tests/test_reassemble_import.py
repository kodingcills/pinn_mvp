"""M5.5: REASSEMBLE segments import into the episode log; MCAP and timeline read it."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from jsonschema import Draft202012Validator
from mcap.reader import make_reader

from benchmarks import episode_log as el
from benchmarks import episode_mcap as em
from benchmarks.realdata import reassemble_episode_log as rel

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "results/m5/reassemble-log"
TASKS = ["No action.", "Pick round peg 1.", "Insert round peg 1.", "Place square peg 2."]
# episode 7: segment 0 Pick (3 frames), 1 Insert (4, failed), 2 Place (2); episode 8 is a decoy
FRAMES = [(7, 0, 1, True)] * 3 + [(7, 1, 2, False)] * 4 + [(7, 2, 3, True)] * 2 + [(8, 0, 2, True)] * 2


@pytest.fixture
def parquet(tmp_path: Path) -> Path:
    (tmp_path / "meta").mkdir()
    pq.write_table(pa.table({"task_index": list(range(len(TASKS))), "task": TASKS}), tmp_path / "meta/tasks.parquet")
    n = len(FRAMES)
    frame = [i if ep == 7 else i - 9 for i, (ep, *_) in enumerate(FRAMES)]
    pose = [[0.4 + 0.01 * i, 0.1, 0.2 - 0.001 * i, 0.0, 1.0, 0.0, 0.0] for i in range(n)]  # tool pointing down
    table = pa.table({
        "episode_index": [f[0] for f in FRAMES], "segment.index": [f[1] for f in FRAMES],
        "task_index": [f[2] for f in FRAMES], "segment.success": [f[3] for f in FRAMES],
        "frame_index": frame, "timestamp": [k / 30 for k in frame],
        "observation.state": [[0.0] * 36] * n, "observation.state.ee_pose": pose,
        "observation.force": [[1.0, 2.0, -12.0]] * n, "observation.torque": [[0.0] * 3] * n,
        "observation.force.base": [[0.1 * i, -0.2, 0.5 * i] for i in range(n)],
        "observation.torque.base": [[0.01, 0.02, 0.03]] * n,
        "action": [p + [0.03, 0.03] for p in pose[1:] + [[0.47, 0.1, 0.2 - 0.011, 0.0, 1.0, 0.0, 0.0]]],
    }).cast(pa.schema([pa.field(c, pa.list_(pa.float32())) if c.startswith(("observation", "action"))
                       else pa.field(c, t) for c, t in [
                           ("episode_index", pa.int64()), ("segment.index", pa.int64()), ("task_index", pa.int64()),
                           ("segment.success", pa.bool_()), ("frame_index", pa.int64()), ("timestamp", pa.float32()),
                           ("observation.state", None), ("observation.state.ee_pose", None),
                           ("observation.force", None), ("observation.torque", None),
                           ("observation.force.base", None), ("observation.torque.base", None), ("action", None)]]))
    path = tmp_path / "data/chunk-000/file-000.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(table, path)
    return path


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _import(parquet: Path, out: Path, segments: list[int]) -> dict:
    return rel.import_segments(parquet, out, episode=7, segments=segments,
                               provenance={"repo": "synthetic", "file": parquet.name, "sha256": "0" * 64})


def test_insertion_segment_rows_events_and_metadata(parquet, tmp_path):
    out = tmp_path / "log"
    meta = _import(parquet, out, [1])
    rows = _jsonl(out / el.TELEMETRY)
    obs = [r["observation"] for r in rows if r["kind"] == "observation"]
    cmd = [r for r in rows if r["kind"] == "command"]
    assert [r["tick"] for r in rows] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert [o["force_z_n"] for o in obs] == pytest.approx([1.5, 2.0, 2.5, 3.0])  # force.base z of frames 3..6
    assert [o["pose_m"][0] for o in obs] == pytest.approx([0.43, 0.44, 0.45, 0.46])
    assert [o["stamp"]["source_s"] for o in obs] == pytest.approx([0.1, 4 / 30, 5 / 30, 0.2])
    assert {o["tilt_deg"] for o in obs} == {0.0}
    assert obs[0]["extra"]["quat_x"] == 1.0 and obs[0]["extra"]["torque_z_nm"] == pytest.approx(0.03)
    assert [c["command"]["target_pose_m"][0] for c in cmd] == pytest.approx([0.44, 0.45, 0.46, 0.47])
    assert all(c["outcome"] is None and c["host_monotonic_ns"] is None for c in cmd)
    events = _jsonl(out / el.EVENTS)
    assert [(e["source"], e["kind"], e["tick"], e["payload"]["label"]) for e in events] == [
        ("dataset", "segment_start", 0, "Insert round peg 1."), ("dataset", "segment_end", 4, "Insert round peg 1.")]
    assert events[0]["payload"]["segment_success"] is False and events[0]["payload"]["episode_frame"] == 3
    assert set(el.METADATA_KEYS) <= set(meta) and meta["n_ticks"] == meta["n_observations"] == 4
    assert meta["timestep_s"] == pytest.approx(1 / 30)
    assert meta["outcome"]["dataset_segment_success"] == {"1": False}
    assert meta["licence"] == "CC-BY-4.0" and "10.48436/0ewrv-8cb44" in meta["attribution"]
    assert meta["provenance"]["sha256"] == "0" * 64
    assert rel.claim_problems(out) == []


def test_label_changes_become_segment_events(parquet, tmp_path):
    _import(parquet, tmp_path / "log", [0, 1, 2])
    events = _jsonl(tmp_path / "log" / el.EVENTS)
    assert [(e["kind"], e["tick"], e["payload"]["label"]) for e in events] == [
        ("segment_start", 0, "Pick round peg 1."), ("segment_end", 3, "Pick round peg 1."),
        ("segment_start", 3, "Insert round peg 1."), ("segment_end", 7, "Insert round peg 1."),
        ("segment_start", 7, "Place square peg 2."), ("segment_end", 9, "Place square peg 2.")]


@pytest.mark.parametrize("segments", [[0, 2], [5]])
def test_gapped_or_missing_segments_are_refused(parquet, tmp_path, segments):
    with pytest.raises(ValueError):
        _import(parquet, tmp_path / "log", segments)


def test_schema_matches_the_backend_logs_where_the_robot_has_the_signal(parquet, tmp_path):
    _import(parquet, tmp_path / "log", [1])
    got, ref = el.log_schema(tmp_path / "log"), el.log_schema(REPO / "results/m5/robosuite-log")
    assert got["telemetry"]["observation"] == ref["telemetry"]["observation"]
    # no robot acceptance and no stiffness: outcome is one null leaf, command carries the full action in extra
    assert got["telemetry"]["command"] == ["command.extra", "command.k_z_n_per_m", "command.target_pose_m",
                                           "host_monotonic_ns", "kind", "outcome", "tick"]
    assert list(got["events"]) == ["dataset"] and got["metadata"] == ref["metadata"]


def test_mcap_round_trip_and_schemas(parquet, tmp_path):
    _import(parquet, tmp_path / "log", [0, 1, 2])
    counts = em.export_mcap(tmp_path / "log", tmp_path / "log.mcap")
    assert counts["/episode/dataset_events"] == 6 and counts["/episode/haplous_events"] == 0
    assert em.read_mcap(tmp_path / "log.mcap") == {
        n: (tmp_path / "log" / n).read_text() for n in (el.TELEMETRY, el.EVENTS, el.METADATA)}
    with open(tmp_path / "log.mcap", "rb") as fh:
        reader = make_reader(fh)
        summary = reader.get_summary()
        for _, channel, message in reader.iter_messages():
            Draft202012Validator(json.loads(summary.schemas[channel.schema_id].data)).validate(json.loads(message.data))


def test_timeline_renders_dataset_events_without_a_backend(parquet, tmp_path):
    _import(parquet, tmp_path / "log", [0, 1, 2])
    probe = ("import runpy, sys\nsys.argv = ['evaluation.timeline', *sys.argv[1:]]\n"
             "try:\n    runpy.run_module('evaluation.timeline', run_name='__main__')\n"
             "except SystemExit as exc:\n    assert exc.code == 0, exc.code\n"
             "bad = ('mujoco', 'haplous.runtime', 'benchmarks.heb', 'pegsim.control', 'benchmarks.realdata')\n"
             "print('LOADED', sorted(m for m in sys.modules if any(m == f or m.startswith(f + '.') for f in bad)))\n")
    res = subprocess.run([sys.executable, "-c", probe, str(tmp_path / "log"), "--rrd", str(tmp_path / "t.rrd"),
                          "--png", str(tmp_path / "t.png")], cwd=REPO, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().splitlines()[-1] == "LOADED []"
    report = json.loads(res.stdout[:res.stdout.rindex("LOADED")])
    assert [(e["source"], e["kind"]) for e in report["events"]][:2] == [
        ("dataset", "segment_start"), ("dataset", "segment_end")]
    assert report["series"] == {"force_z_n": 9, "pose_m": 9, "command.target_pose_m": 9}


@pytest.mark.parametrize("plant", ["haplous_event", "machine_event", "outcome", "truth", "metadata"])
def test_claim_guard_catches_planted_claims(parquet, tmp_path, plant):
    out = tmp_path / "log"
    _import(parquet, out, [1])
    tel, ev, meta = out / el.TELEMETRY, out / el.EVENTS, out / el.METADATA
    if plant in ("haplous_event", "machine_event"):
        with open(ev, "a") as fh:
            fh.write(json.dumps({"source": plant.split("_")[0], "tick": 0, "kind": "x", "payload": {}}) + "\n")
    elif plant == "outcome":
        tel.write_text(tel.read_text().replace('"outcome": null', '"outcome": {"accepted": true, "reason": null}', 1))
    elif plant == "truth":
        tel.write_text(tel.read_text().replace('"extra": {', '"extra": {"truth_z_m": 0.0, ', 1))
    else:
        meta.write_text(meta.read_text().replace('"haplous_evidence": null', '"haplous_evidence": {"events": 3}'))
    assert rel.claim_problems(out)


@pytest.mark.skipif(not SAMPLE.exists(), reason="committed sample not generated")
def test_committed_sample_is_an_unclaimed_reassemble_insertion():
    meta = json.loads((SAMPLE / el.METADATA).read_text())
    assert rel.claim_problems(SAMPLE) == []
    assert meta["provenance"]["repo"] == "robot-lev/reassemble" and len(meta["provenance"]["sha256"]) == 64
    assert [s["label"] for s in meta["segments"]][0].startswith("Insert")
    assert (SAMPLE / "ATTRIBUTION.txt").read_text().count("10.48436/0ewrv-8cb44") == 1
    assert sum(p.stat().st_size for p in SAMPLE.iterdir()) < 330_000  # brief: ~300 KB


def test_driver_refuses_a_file_whose_hash_is_not_pinned(tmp_path):
    sys.path.insert(0, str(REPO / "scripts"))
    import m55_reassemble_import as drv
    (tmp_path / "f").write_bytes(b"not the dataset")
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        drv.verify(tmp_path / "f", "0" * 64)
    shutil.rmtree(tmp_path)
