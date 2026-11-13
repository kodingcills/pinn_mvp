"""Negative tests (M3.3, probe 4) on a recording: wrong units or frames, and the
events.jsonl limit.

Each test copies the committed M2 recording, plants one defect and re-seals M0
the way .claude/skills/verify-haplous-eval/plant_defect.py does, so M0's own
hash check cannot be what catches it.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pytest

from evaluation import replay
from evaluation.run_registry import Registry

TRUSTED = Path(__file__).resolve().parents[1] / "results" / "recordings" / "registry"
RUN = "m2-heb-insert_dropout_large"


def _resealed(dest: Path, plant) -> Path:
    src = Registry(TRUSTED)
    done = [e for e in src.events() if e["run_id"] == RUN and e["event"] == "completed"][-1]["payload"]
    reg = Registry(dest)
    reg.register((src.run_dir(RUN) / "manifest.json").read_bytes())
    reg.start(RUN)
    art = reg.run_dir(RUN) / "artifacts"
    shutil.copytree(src.run_dir(RUN) / "artifacts", art)
    plant(art)
    reg.finish(RUN, success=True, exit_code=done["exit_code"], summary=done["summary"])
    assert reg.verify(RUN) == [] and reg.state(RUN) == "VERIFIED"
    return dest


def _edit_jsonl(path: Path, edit) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows if edit(r) is not False))


def _edit_observations(art: Path, fn) -> None:
    def edit(r):
        if r["kind"] == "observation" and r["observation"]:
            r["observation"]["pose_m"] = fn(r["observation"]["pose_m"])
    _edit_jsonl(art / replay.TELEMETRY, edit)


def pose_in_mm(art):
    _edit_observations(art, lambda p: [v * 1000.0 for v in p])


def pose_in_hole_frame(art):
    # Tracked frame re-expressed 5 mm off the world origin.
    _edit_observations(art, lambda p: [p[0] - 0.005, p[1], p[2]])


def timestep_in_ms(art):
    meta = json.loads((art / replay.METADATA).read_text())
    meta["timestep_s"] *= 1000.0
    (art / replay.METADATA).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")


def sim_time_in_ms(art):
    path = art / replay.STATE
    reader = pa.ipc.open_file(str(path))
    schema, chunk, table = reader.schema, reader.get_batch(0).num_rows, reader.read_all()
    table = table.set_column(1, "sim_time_s", pc.multiply(table.column("sim_time_s"), 1000.0))
    with pa.ipc.new_file(str(path), schema) as writer:
        for batch in table.to_batches(max_chunksize=chunk):
            writer.write_batch(batch)


def event_time_in_ms(art):
    def edit(r):
        r["t_s"] *= 1000.0
    _edit_jsonl(art / replay.EVENTS, edit)


@pytest.mark.parametrize("plant", [pose_in_mm, pose_in_hole_frame], ids=lambda f: f.__name__)
def test_pose_in_wrong_units_or_frame_fails_replay(tmp_path, plant):
    root = _resealed(tmp_path / "registry", plant)
    rc = replay.main([RUN, "--root", str(root), "--report", str(tmp_path / "report.json")])
    report = json.loads((tmp_path / "report.json").read_text())
    assert rc == 1
    assert report["pose_check"]["pass"] is False
    assert report["pose_check"]["max_abs_error_m"] >= 1e-3


@pytest.mark.parametrize("plant, message", [
    (timestep_in_ms, "timestep"),
    (sim_time_in_ms, "sim_time_s"),
    (event_time_in_ms, "event"),
], ids=["timestep_in_ms", "sim_time_in_ms", "event_time_in_ms"])
def test_clock_in_wrong_units_is_refused(tmp_path, plant, message):
    root = _resealed(tmp_path / "registry", plant)
    with pytest.raises(replay.ReplayRefused, match=message):
        replay.open_recording(root, RUN)
    assert replay.main([RUN, "--root", str(root)]) == 2


def test_committed_recordings_pass_the_clock_checks():
    for run_id in ("m2-heb-insert_dropout_large", "m3-heb-slide_disturbance_recoverable_ceiling"):
        replay.open_recording(TRUSTED, run_id)


def _without_hold_events(art):
    removed = []

    def edit(r):
        if r["kind"] == "temporal_hold_entered":
            removed.append(r)
            return False
    _edit_jsonl(art / replay.EVENTS, edit)
    assert removed, "the committed recording has a temporal_hold_entered event"


def test_resealed_event_removal_is_caught_only_by_trust_root(tmp_path):
    """Pins a documented limit (verify-haplous-eval SKILL.md, "Which check catches
    what"). If M0 verify or replay starts catching this, update that table."""
    root = _resealed(tmp_path / "registry", _without_hold_events)
    assert Registry(root).check(RUN) == []
    report = replay.replay(root, RUN, rrd_path=None, mp4_path=None, report_path=None)
    assert report["pose_check"]["pass"] is True
    tampered, trusted = ({a["path"]: a["sha256"] for a in Registry(r).get(RUN)["artifacts"]} for r in (root, TRUSTED))
    assert sorted(p for p in tampered.keys() | trusted.keys() if tampered.get(p) != trusted.get(p)) == [replay.EVENTS]
