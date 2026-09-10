"""Load and validate the claim and metric registries.

The registries are data, not code: evaluation runs read them, they do
not read evaluation code. Validation is structural; a malformed registry
entry raises with its claim_id / metric name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

EVAL_DIR = Path(__file__).resolve().parent

CLAIM_STATUS_VALUES = (
    "SUPPORTED",
    "SUPPORTED_WITH_CAVEATS",
    "MIXED",
    "WEAKENED",
    "REFUTED_WITHIN_TESTED_SCOPE",
    "UNKNOWN",
    "SUPPORTED_WITHIN_TESTED_SCOPE",
)

_CLAIM_REQUIRED_FIELDS = (
    "claim_id",
    "name",
    "statement",
    "epistemic_status",
    "primary_metrics",
    "benchmarks",
    "baselines",
    "support_condition",
    "weakening_condition",
    "falsification_condition",
    "known_confounders",
    "current_evidence",
)

_METRIC_REQUIRED_FIELDS = (
    "name",
    "definition",
    "unit",
    "source_of_truth",
    "aggregation",
    "scope",
    "failure_handling",
    "ci_method",
    "limitations",
)

_METRIC_SOURCES = ("benchmark", "recorded", "derived")


@dataclass(frozen=True)
class Claim:
    claim_id: str
    name: str
    statement: str
    epistemic_status: str
    primary_metrics: list
    benchmarks: list
    baselines: list
    support_condition: str
    weakening_condition: str
    falsification_condition: str
    known_confounders: list
    current_evidence: list
    caveat: str | None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def load_claims(path: Path | None = None) -> list[Claim]:
    path = path or EVAL_DIR / "claims.yaml"
    raw = yaml.safe_load(path.read_text())
    claims = []
    seen: set[str] = set()
    for entry in raw.get("claims", []):
        missing = [f for f in _CLAIM_REQUIRED_FIELDS if f not in entry]
        if missing:
            raise ValueError(f"claim {entry.get('claim_id', '?')}: missing fields {missing}")
        cid = entry["claim_id"]
        if cid in seen:
            raise ValueError(f"duplicate claim_id {cid}")
        seen.add(cid)
        status = entry["epistemic_status"]
        if status not in CLAIM_STATUS_VALUES:
            raise ValueError(f"claim {cid}: unknown epistemic_status {status!r}")
        claims.append(Claim(caveat=entry.get("caveat"), **{k: entry[k] for k in _CLAIM_REQUIRED_FIELDS}))
    if len(claims) < 12:
        raise ValueError(f"expected >=12 claims, found {len(claims)}")
    return claims


def load_metrics(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or EVAL_DIR / "metrics.yaml"
    raw = yaml.safe_load(path.read_text())
    metrics = []
    seen: set[str] = set()
    for entry in raw.get("metrics", []):
        missing = [f for f in _METRIC_REQUIRED_FIELDS if f not in entry]
        if missing:
            raise ValueError(f"metric {entry.get('name', '?')}: missing fields {missing}")
        name = entry["name"]
        if name in seen:
            raise ValueError(f"duplicate metric {name}")
        seen.add(name)
        if entry["source_of_truth"] not in _METRIC_SOURCES:
            raise ValueError(f"metric {name}: unknown source_of_truth {entry['source_of_truth']!r}")
        metrics.append(entry)
    return metrics