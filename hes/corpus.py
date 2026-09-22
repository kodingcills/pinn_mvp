"""Historical failure corpus.

Every serious discovered failure becomes a permanent, machine-readable
entry with its own regression test reference. A failure that is fixed but
not regression-tested is treated as unfixed. Entries record the scenario
digest, the runtime decision, the first causal divergence, the attribution,
severity, and the producing benchmark version so entries are never pooled
across benchmark versions by accident.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CORPUS_PATH = Path(__file__).resolve().parent.parent / "results" / "hes" / "failure_corpus.jsonl"


@dataclass(frozen=True)
class CorpusEntry:
    entry_id: str
    benchmark_version: str
    scenario_digest: str
    observed_runtime_behavior: str
    first_causal_divergence: str
    attribution: str
    severity: str
    expected_disposition: str
    regression_test: str
    status: str
    provenance: str
    notes: str = ""
    tags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "benchmark_version": self.benchmark_version,
            "scenario_digest": self.scenario_digest,
            "observed_runtime_behavior": self.observed_runtime_behavior,
            "first_causal_divergence": self.first_causal_divergence,
            "attribution": self.attribution,
            "severity": self.severity,
            "expected_disposition": self.expected_disposition,
            "regression_test": self.regression_test,
            "status": self.status,
            "provenance": self.provenance,
            "notes": self.notes,
            "tags": list(self.tags),
        }


@dataclass
class Corpus:
    entries: list[CorpusEntry] = field(default_factory=list)

    def add(self, entry: CorpusEntry) -> None:
        if any(e.entry_id == entry.entry_id for e in self.entries):
            raise ValueError(f"duplicate corpus entry {entry.entry_id}")
        self.entries.append(entry)

    def without_regression_test(self) -> list[CorpusEntry]:
        return [e for e in self.entries if not e.regression_test]

    def by_benchmark_version(self, version: str) -> list[CorpusEntry]:
        return [e for e in self.entries if e.benchmark_version == version]

    def by_severity(self, severity: str) -> list[CorpusEntry]:
        return [e for e in self.entries if e.severity == severity]

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": len(self.entries),
            "n_without_regression_test": len(self.without_regression_test()),
            "entries": [e.as_dict() for e in self.entries],
        }


def load_corpus(path: str | Path = CORPUS_PATH) -> Corpus:
    path = Path(path)
    corpus = Corpus()
    if not path.is_file():
        return corpus
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload.setdefault("tags", ())
        payload["tags"] = tuple(payload["tags"])
        corpus.add(CorpusEntry(**payload))
    return corpus


def append_corpus(entry: CorpusEntry, path: str | Path = CORPUS_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.as_dict(), sort_keys=True) + "\n")


def corpus_from_failures(
    failures: Sequence[Mapping[str, Any]],
    *,
    benchmark_version: str = "unknown",
    limit: int = 20,
) -> Corpus:
    """Bootstrap corpus entries from an analysis's highest-severity
    unattributed failures. These start with no regression test, so they are
    visibly unfixed."""
    corpus = Corpus()
    ordered = sorted(
        (f for f in failures if not f.get("bounded_success")),
        key=lambda r: (-int(r.get("n_hard_violations") or 0), str(r.get("scenario_id"))),
    )
    for row in ordered[:limit]:
        sid = str(row.get("scenario_id") or row.get("episode_id"))
        corpus.add(
            CorpusEntry(
                entry_id=f"{benchmark_version}:{sid}:{row.get('arm')}",
                benchmark_version=benchmark_version,
                scenario_digest=str(row.get("scenario_id") or sid),
                observed_runtime_behavior=str(row.get("reported_disposition")),
                first_causal_divergence=str(row.get("reason") or "unknown"),
                attribution=str(row.get("reason") or "unknown"),
                severity="S3" if int(row.get("n_hard_violations") or 0) > 0 else "S2",
                expected_disposition="COMPLETE",
                regression_test="",
                status="OPEN",
                provenance=str(row.get("experiment_id") or "unknown"),
                notes="bootstrapped from analysis; needs a regression test",
            )
        )
    return corpus