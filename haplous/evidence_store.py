"""Bounded evidence retention: sinks that durably store terminal
execution evidence so runtime RAM stays bounded over long operation.

Architecture (campaign spec sec. 5):

    hot bounded in-memory archive (runtime)
            |  evict-oldest-when-full, flush-before-evict
            v
    EvidenceSink (this module)
            v
    append-only JSONL segments, count-based rotation
            v
    replay() / read-back for evidence reconstruction

Requirements honored:
- bounded steady-state RAM: the runtime archive holds at most
  max_archived_evidence records once a sink is configured
- no silent evidence loss: eviction writes to the sink BEFORE dropping;
  sink write failures propagate (backpressure is an exception, never a
  drop); a bounded memory sink raises on overflow instead of evicting
- deterministic event ordering: one append per terminal execution, in
  reset order; replay yields records in write order
- replay compatibility: each record is the evidence's to_json_dict()
  plus the runtime's monotonic archive sequence, compact JSON per line
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Protocol

from haplous.evidence import ExecutionEvidence


class EvidenceSink(Protocol):
    def write(self, evidence: ExecutionEvidence, archive_seq: int) -> None: ...

    @property
    def entries_written(self) -> int: ...


class BoundedMemorySink:
    """In-memory sink with explicit backpressure: writing beyond maxsize
    raises OverflowError rather than silently evicting."""

    def __init__(self, maxsize: int) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self._maxsize = maxsize
        self._records: list[tuple[int, dict]] = []

    def write(self, evidence: ExecutionEvidence, archive_seq: int) -> None:
        if len(self._records) >= self._maxsize:
            raise OverflowError(
                f"evidence sink full ({self._maxsize} records); "
                "backpressure: flush or rotate before writing more"
            )
        record = dict(evidence.to_json_dict())
        record["archive_seq"] = archive_seq
        self._records.append((archive_seq, record))

    @property
    def entries_written(self) -> int:
        return len(self._records)

    def records(self) -> list[tuple[int, dict]]:
        return list(self._records)


class JsonlEvidenceStore:
    """Append-only JSONL evidence store with count-based rotation.

    Segments are named evidence-<ordinal>.jsonl under root. Each holds at
    most records_per_segment records; the final segment may be partial.
    Reopening an existing root continues append-only: the record count is
    recovered by counting lines (one-time scan), and the last segment is
    reopened in append mode when partial."""

    def __init__(self, root: str | Path, records_per_segment: int = 100_000) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        if records_per_segment <= 0:
            raise ValueError("records_per_segment must be positive")
        self._records_per_segment = records_per_segment
        self._count = 0
        self._file = None

        segments = sorted(self._root.glob("evidence-*.jsonl"))
        if segments:
            self._count = sum(
                1
                for path in segments
                for line in open(path, encoding="utf-8")
                if line.strip()
            )
            last_path = segments[-1]
            last_lines = sum(
                1 for line in open(last_path, encoding="utf-8") if line.strip()
            )
            if last_lines < self._records_per_segment:
                self._file = open(last_path, "a", encoding="utf-8")

    def _open_next_segment(self) -> None:
        if self._file is not None:
            self._file.close()
        ordinal = len(list(self._root.glob("evidence-*.jsonl")))
        path = self._root / f"evidence-{ordinal:08d}.jsonl"
        self._file = open(path, "a", encoding="utf-8")

    def write(self, evidence: ExecutionEvidence, archive_seq: int) -> None:
        if self._file is None:
            self._open_next_segment()
        record = dict(evidence.to_json_dict())
        record["archive_seq"] = archive_seq
        self._file.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        self._file.flush()
        self._count += 1
        if self._count % self._records_per_segment == 0:
            self._open_next_segment()

    @property
    def entries_written(self) -> int:
        return self._count

    def replay(self) -> Iterator[dict]:
        """Stream every stored record in write order (across rotations,
        including records written by prior store instances at this root)."""
        if self._file is not None:
            self._file.flush()
        for path in sorted(self._root.glob("evidence-*.jsonl")):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        yield json.loads(line)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None