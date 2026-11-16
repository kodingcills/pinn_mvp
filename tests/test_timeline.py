"""M5.3: `haplous timeline` renders plots and decision markers from an episode log alone."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import rerun.chunk as rc

from benchmarks import episode_log as el
from evaluation import timeline as tl

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "results/recordings/registry"
M2_RUN = "m2-heb-insert_dropout_large"
LOGS = {
    "mujoco": (["timeline", M2_RUN, "--recordings-root", str(ROOT)], ROOT / "runs" / M2_RUN / "artifacts"),
    "robosuite": (["timeline", str(REPO / "results/m5/robosuite-log")], REPO / "results/m5/robosuite-log"),
    "reassemble": (["timeline", str(REPO / "results/m5/reassemble-log")], REPO / "results/m5/reassemble-log"),
}
# Loads a module in this process, runs the timeline renderer, then lists what got imported.
_IMPORT_PROBE = """
import runpy, sys
sys.argv = ["evaluation.timeline", *sys.argv[1:]]
try:
    runpy.run_module("evaluation.timeline", run_name="__main__")
except SystemExit as exc:
    assert exc.code == 0, exc.code
bad = ("mujoco", "haplous.runtime", "benchmarks.heb", "pegsim.control")
print("LOADED", sorted(m for m in sys.modules if any(m == f or m.startswith(f + ".") for f in bad)))
"""


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _cli(args: list[str], tmp: Path) -> tuple[subprocess.CompletedProcess, Path, Path]:
    rrd, png = tmp / "t.rrd", tmp / "t.png"
    res = subprocess.run([sys.executable, "-m", "haplous.cli", *args, "--rrd", str(rrd), "--png", str(png)],
                         cwd=REPO, capture_output=True, text=True)
    return res, rrd, png


def _rrd(rrd: Path) -> dict[str, dict[str, list]]:
    out: dict[str, dict[str, list]] = {}
    for chunk in rc.RrdReader(str(rrd)).stream():
        batch = chunk.to_record_batch()
        cols = out.setdefault(chunk.entity_path, {})
        for name in ("tick", "sim_time", "Scalars:scalars", "TextLog:text"):
            if name in batch.schema.names:
                column = batch.column(name)
                column = column.cast("int64") if name == "sim_time" else column  # duration -> ns
                cols.setdefault(name, []).extend(column.to_pylist())
    return out


def test_file_names_match_the_episode_log_format():
    assert (tl.TELEMETRY, tl.EVENTS, tl.METADATA) == (el.TELEMETRY, el.EVENTS, el.METADATA)


@pytest.mark.parametrize("backend", sorted(LOGS))
def test_timeline_series_and_markers_match_the_log(backend, tmp_path):
    args, log_dir = LOGS[backend]
    res, rrd, png = _cli(args, tmp_path)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["forbidden_modules_loaded"] == []
    meta = json.loads((log_dir / el.METADATA).read_text())
    rows = _jsonl(log_dir / el.TELEMETRY)
    obs = [r for r in rows if r["kind"] == "observation" and r["observation"] is not None]
    cmd = [r for r in rows if r["kind"] == "command"]
    got = _rrd(rrd)

    assert got["/plots/force_z_n"]["tick"] == [r["tick"] for r in obs]
    assert [v[0] for v in got["/plots/force_z_n"]["Scalars:scalars"]] == [r["observation"]["force_z_n"] for r in obs]
    for i, axis in enumerate("xyz"):
        assert [v[0] for v in got[f"/plots/pose_m/{axis}"]["Scalars:scalars"]] == [r["observation"]["pose_m"][i] for r in obs]
        assert got[f"/plots/command/target_pose_m/{axis}"]["tick"] == [r["tick"] for r in cmd]
        assert [v[0] for v in got[f"/plots/command/target_pose_m/{axis}"]["Scalars:scalars"]] == [
            r["command"]["target_pose_m"][i] for r in cmd]
    assert len(obs) == meta["n_observations"]

    events = _jsonl(log_dir / el.EVENTS)
    for source in {e["source"] for e in events}:
        marks = got[f"/events/{source}"]
        logged = [e for e in events if e["source"] == source]
        assert marks["tick"] == [e["tick"] for e in logged]
        assert all(abs(t / 1e9 - e["t_s"]) <= 1e-9 for t, e in zip(marks["sim_time"], logged, strict=True))
        assert [t[0].split(" ", 1)[0] for t in marks["TextLog:text"]] == [e["kind"] for e in logged]

    starts = [e for e in events if e["kind"].endswith("_window_start")]
    if backend == "mujoco":
        assert starts, "the M2 recording carries a dropout window"
    for e in starts:
        fault = got[f"/plots/faults/{e['kind'].removesuffix('_window_start')}"]
        dt = meta["timestep_s"]
        assert fault["tick"] == [round(e["payload"]["start_s"] / dt), round(e["payload"]["end_s"] / dt)]

    verify = subprocess.run([str(Path(sys.executable).with_name("rerun")), "rrd", "verify", str(rrd)],
                            capture_output=True, text=True)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("backend", sorted(LOGS))
def test_timeline_imports_no_backend(backend, tmp_path):
    _, log_dir = LOGS[backend]
    source = [M2_RUN, "--root", str(ROOT)] if backend == "mujoco" else [str(log_dir)]
    res = subprocess.run([sys.executable, "-c", _IMPORT_PROBE, *source, "--rrd", str(tmp_path / "t.rrd"),
                          "--png", str(tmp_path / "t.png")], cwd=REPO, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().splitlines()[-1] == "LOADED []"


def test_tampered_recording_is_refused(tmp_path):
    root = tmp_path / "registry"
    shutil.copytree(ROOT, root)
    events = root / "runs" / M2_RUN / "artifacts" / el.EVENTS
    events.write_text(events.read_text().replace('"tick": 2000', '"tick": 2001', 1))
    res, rrd, _ = _cli(["timeline", M2_RUN, "--recordings-root", str(root)], tmp_path)
    assert res.returncode == 2
    assert "M0 integrity check failed" in res.stderr
    assert not rrd.exists()
