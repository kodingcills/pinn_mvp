"""M5.4: the MCAP export of an episode log round-trips to the canonical JSONL."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from mcap.reader import make_reader

from benchmarks import episode_mcap as em
from benchmarks.episode_log import EVENTS, METADATA, TELEMETRY

REPO = Path(__file__).resolve().parents[1]
LOGS = {
    "mujoco": REPO / "results/recordings/registry/runs/m2-heb-insert_dropout_large/artifacts",
    "robosuite": REPO / "results/m5/robosuite-log",
}


@pytest.fixture(scope="module", params=sorted(LOGS))
def exported(request, tmp_path_factory):
    log_dir = LOGS[request.param]
    out = tmp_path_factory.mktemp(request.param) / "log.mcap"
    em.export_mcap(log_dir, out)
    with open(out, "rb") as fh:
        reader = make_reader(fh)
        summary = reader.get_summary()
        messages = list(reader.iter_messages(log_time_order=False))
        metadata = list(reader.iter_metadata())
    return log_dir, out, summary, messages, metadata


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_row_comes_back_in_order(exported):
    log_dir, out, _, messages, metadata = exported
    by_file: dict[str, list] = {TELEMETRY: [], EVENTS: []}
    for _, channel, message in messages:
        by_file[em._FILE_OF_TOPIC[channel.topic]].append((message.sequence, json.loads(message.data)))
    for file, rows in by_file.items():
        assert [row for _, row in sorted(rows, key=lambda r: r[0])] == _rows(log_dir / file), file
    assert [(m.name, m.metadata) for m in metadata] == [
        (em.METADATA_NAME, {METADATA: (log_dir / METADATA).read_text(encoding="utf-8")})]
    assert em.read_mcap(out) == {name: (log_dir / name).read_text(encoding="utf-8")
                                 for name in (TELEMETRY, EVENTS, METADATA)}


def test_each_channel_has_a_json_schema_its_messages_satisfy(exported):
    _, _, summary, messages, _ = exported
    assert {c.topic for c in summary.channels.values()} == {topic for topic, _ in em.CHANNELS.values()}
    validators = {}
    for channel in summary.channels.values():
        schema = summary.schemas[channel.schema_id]
        assert (channel.message_encoding, schema.encoding) == ("json", "jsonschema")
        validators[channel.id] = Draft202012Validator(json.loads(schema.data))
    for _, channel, message in messages:
        validators[channel.id].validate(json.loads(message.data))


def test_log_time_is_monotonic_sim_time(exported):
    log_dir, _, _, messages, _ = exported
    dt = json.loads((log_dir / METADATA).read_text())["timestep_s"]
    times = [message.log_time for _, _, message in messages]
    assert times == sorted(times)
    for _, _, message in messages:
        assert message.log_time == message.publish_time == round(json.loads(message.data)["tick"] * dt * 1e9)


def test_export_is_deterministic(exported, tmp_path):
    log_dir, out, *_ = exported
    em.export_mcap(log_dir, tmp_path / "again.mcap")
    assert (tmp_path / "again.mcap").read_bytes() == out.read_bytes()


def test_a_row_with_no_channel_is_refused(tmp_path):
    shutil.copytree(LOGS["robosuite"], tmp_path / "log")
    with open(tmp_path / "log" / EVENTS, "a") as fh:
        fh.write(json.dumps({"source": "operator", "tick": 0}) + "\n")
    with pytest.raises(ValueError, match="no MCAP channel for source='operator'"):
        em.export_mcap(tmp_path / "log", tmp_path / "log.mcap")
