"""Bounded evidence retention tests (campaign spec sec. 5-6)."""

import json

import pytest

from haplous.evidence_store import BoundedMemorySink, JsonlEvidenceStore
from haplous.runtime import HaplousRuntime

from tests.test_haplous_semantics import (
    ScriptedMachine,
    make_intent,
    obs,
    seated_samples,
    stream,
)

BEHAVIORS = None


def _behaviors():
    from tests.test_haplous_reliability import BEHAVIORS

    return BEHAVIORS


def test_bounded_memory_sink_backpressure_raises():
    sink = BoundedMemorySink(maxsize=2)
    sink.write(_evidence(1), 1)
    sink.write(_evidence(2), 2)
    with pytest.raises(OverflowError):
        sink.write(_evidence(3), 3)
    assert sink.entries_written == 2


def _evidence(n_events: int = 3):
    from haplous.evidence import ExecutionEvidence

    ev = ExecutionEvidence()
    for i in range(n_events):
        ev.append("tick", float(i), x=i)
    return ev


def test_jsonl_store_roundtrip_and_rotation(tmp_path):
    store = JsonlEvidenceStore(tmp_path, records_per_segment=3)
    for i in range(7):
        store.write(_evidence(2 + i), i + 1)
    store.close()
    assert store.entries_written == 7
    segments = sorted(tmp_path.glob("evidence-*.jsonl"))
    assert len(segments) == 3  # 3 + 3 + 1 rotation

    reopened = JsonlEvidenceStore(tmp_path, records_per_segment=3)
    assert reopened.entries_written == 7
    seqs = [rec["archive_seq"] for rec in reopened.replay()]
    assert seqs == list(range(1, 8))


def test_runtime_with_sink_bounded_archive_no_loss():
    machine = ScriptedMachine(observation=obs(0.0), script=stream(seated_samples(10)))
    sink = BoundedMemorySink(maxsize=10_000)
    runtime = HaplousRuntime(machine, behaviors=_behaviors(), evidence_sink=sink, max_archived_evidence=8)
    intent = make_intent()
    cycles = 100
    for cycle in range(cycles):
        runtime.submit(intent)
        runtime.execute()
        runtime.reset()
        assert runtime.evidence_retained <= 8
    assert runtime.evidence_retained == 8
    assert runtime.flushed_evidence_count == cycles - 8
    assert sink.entries_written == cycles - 8
    assert runtime.flushed_evidence_count + runtime.evidence_retained == cycles
    records = sink.records()
    assert [seq for seq, _ in records] == list(range(9, cycles + 1))


def test_runtime_rejects_bounded_archive_without_sink():
    machine = ScriptedMachine(observation=obs(0.0), script=stream(seated_samples(10)))
    with pytest.raises(ValueError, match="requires an evidence_sink"):
        HaplousRuntime(machine, behaviors=_behaviors(), max_archived_evidence=8)


def test_runtime_default_unbounded_unchanged():
    machine = ScriptedMachine(observation=obs(0.0), script=stream(seated_samples(10)))
    runtime = HaplousRuntime(machine, behaviors=_behaviors())
    intent = make_intent()
    for cycle in range(20):
        runtime.submit(intent)
        runtime.execute()
        runtime.reset()
    assert len(runtime.archived_evidence) == 20
    assert runtime.flushed_evidence_count == 0


def test_jsonl_store_record_shape_replay_compatible(tmp_path):
    store = JsonlEvidenceStore(tmp_path)
    ev = _evidence(3)
    store.write(ev, 42)
    store.close()
    line = next(iter(store.replay()))
    assert line["archive_seq"] == 42
    assert line["intent_behavior"] == ev.intent_behavior
    assert [e["seq"] for e in line["events"]] == [0, 1, 2]
    json.dumps(line)  # records are plain JSON