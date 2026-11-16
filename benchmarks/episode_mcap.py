"""MCAP export of an episode log (design M5-L, M5.4).

The JSONL episode log (`benchmarks.episode_log`) stays canonical; this is a
derived view that opens in Foxglove next to real-robot bags. Each JSONL row
becomes one JSON-encoded MCAP message whose data is the row's exact bytes,
on one of five channels, each with a JSON Schema. Log and publish time are
sim time, tick * timestep_s in ns, never host time. A message's `sequence` is
its line index in its source file, so `read_mcap` rebuilds both files exactly.
metadata.json goes in verbatim as the metadata record `episode_log`.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from mcap.reader import make_reader
from mcap.writer import Writer

from benchmarks.episode_log import EVENTS, METADATA, TELEMETRY

METADATA_NAME = "episode_log"
_NUM = {"type": "number"}
_INT = {"type": "integer"}
_VEC3 = {"type": "array", "items": _NUM, "minItems": 3, "maxItems": 3}
_HOST = {"type": ["integer", "null"],
         "description": "recording host time.monotonic_ns(); not the log clock; null for an imported log"}


def _row(kind_key: str, kind: Any, properties: dict[str, Any], title: str) -> dict[str, Any]:
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": title, "type": "object",
            "properties": {kind_key: kind, "tick": _INT, **properties},
            "required": sorted({kind_key, "tick", *properties})}


def _event(source: str, clock: str = "sim") -> dict[str, Any]:
    props = {"clock": {"const": clock}, "kind": {"type": "string"}, "t_s": _NUM, "payload": {"type": "object"}}
    schema = _row("source", {"const": source}, props, f"haplous.episode_log.{source}_event")
    if source == "haplous":
        schema["properties"]["seq"] = _INT
        schema["required"].append("seq")
    return schema


_OBSERVATION = {"type": ["object", "null"], "required": ["extra", "force_z_n", "pose_m", "stamp", "tilt_deg"],
                "properties": {"pose_m": _VEC3, "force_z_n": _NUM, "tilt_deg": _NUM,
                               "extra": {"type": "object", "additionalProperties": _NUM},
                               "stamp": {"type": "object", "required": ["received_s", "source_s"],
                                         "properties": {"received_s": _NUM, "source_s": _NUM}}}}
_COMMAND = {"type": "object", "required": ["k_z_n_per_m", "target_pose_m"],
            "properties": {"target_pose_m": _VEC3, "k_z_n_per_m": {"type": ["number", "null"]}}}
_OUTCOME = {"type": ["object", "null"], "required": ["accepted", "reason"],
            "properties": {"accepted": {"type": "boolean"}, "reason": {"type": ["string", "null"]}}}

# (source file, routing key, routing value) -> topic, JSON Schema
CHANNELS: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {
    (TELEMETRY, "kind", "observation"): ("/episode/observation", _row(
        "kind", {"const": "observation"}, {"host_monotonic_ns": _HOST, "observation": _OBSERVATION},
        "haplous.episode_log.observation")),
    (TELEMETRY, "kind", "command"): ("/episode/command", _row(
        "kind", {"const": "command"}, {"host_monotonic_ns": _HOST, "command": _COMMAND, "outcome": _OUTCOME},
        "haplous.episode_log.command")),
    (EVENTS, "source", "haplous"): ("/episode/haplous_events", _event("haplous")),
    (EVENTS, "source", "machine"): ("/episode/machine_events", _event("machine")),
    # Imported recordings (benchmarks.realdata.reassemble_episode_log): annotation events on the dataset clock.
    (EVENTS, "source", "dataset"): ("/episode/dataset_events", _event("dataset", clock="dataset")),
}
_ROUTE = {file: key for file, key, _ in CHANNELS}
_FILE_OF_TOPIC = {topic: file for (file, _, _), (topic, _) in CHANNELS.items()}


def _messages(log_dir: Path, dt_ns: float) -> Iterator[tuple[int, str, int, bytes]]:
    for file, key in _ROUTE.items():
        for seq, line in enumerate((log_dir / file).read_bytes().splitlines()):
            row = json.loads(line)
            if (file, key, row[key]) not in CHANNELS:
                raise ValueError(f"{file} line {seq}: no MCAP channel for {key}={row[key]!r}")
            yield round(row["tick"] * dt_ns), CHANNELS[file, key, row[key]][0], seq, line


def export_mcap(log_dir: Path, out_path: Path) -> dict[str, Any]:
    """Write the episode log in `log_dir` to `out_path`; return per-topic counts."""
    metadata_text = (log_dir / METADATA).read_text(encoding="utf-8")
    dt_ns = json.loads(metadata_text)["timestep_s"] * 1e9
    # Sorted by sim time; ties keep file then line order, so the output is a pure function of the log.
    messages = sorted(_messages(log_dir, dt_ns), key=lambda m: (m[0], _FILE_OF_TOPIC[m[1]], m[2]))
    counts = {topic: 0 for topic, _ in CHANNELS.values()}
    with open(out_path, "wb") as fh:
        writer = Writer(fh)
        writer.start(profile="", library="haplous-episode-mcap")
        channel_ids = {}
        for topic, schema in CHANNELS.values():
            schema_id = writer.register_schema(name=schema["title"], encoding="jsonschema",
                                               data=json.dumps(schema, sort_keys=True).encode())
            channel_ids[topic] = writer.register_channel(topic=topic, message_encoding="json", schema_id=schema_id)
        for log_time, topic, seq, data in messages:
            writer.add_message(channel_ids[topic], log_time=log_time, publish_time=log_time, data=data, sequence=seq)
            counts[topic] += 1
        writer.add_metadata(METADATA_NAME, {METADATA: metadata_text})
        writer.finish()
    return counts


def read_mcap(path: Path) -> dict[str, str]:
    """Rebuild the episode log files {name: text} from an exported MCAP."""
    rows: dict[str, list[tuple[int, bytes]]] = {TELEMETRY: [], EVENTS: []}
    files = {}
    with open(path, "rb") as fh:
        reader = make_reader(fh)
        for _, channel, message in reader.iter_messages(log_time_order=False):
            rows[_FILE_OF_TOPIC[channel.topic]].append((message.sequence, message.data))
        for record in reader.iter_metadata():
            if record.name == METADATA_NAME:
                files[METADATA] = record.metadata[METADATA]
    for file, lines in rows.items():
        lines.sort()
        if [seq for seq, _ in lines] != list(range(len(lines))):
            raise ValueError(f"{path}: {file} rows are not a contiguous sequence")
        files[file] = "".join(data.decode() + "\n" for _, data in lines)
    return files
