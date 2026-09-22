"""Experiment specification v2.

A superset of the existing experiment manifest: it adds the hypothesis, the
named population class, the metric-vector contract, the hard gates, the
escalation rule, and the analysis plan. It serializes to the manifest shape
``evaluation.experiment`` already understands, so it is additive.

Locked specs are immutable: a changed locked spec is a new version, and
results must not be pooled across versions.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REQUIRED = ("experiment_id", "hypothesis", "population", "arms", "metrics")


@dataclass
class ExperimentSpec:
    experiment_id: str
    hypothesis: str
    population: str
    arms: tuple[str, ...]
    metrics: tuple[str, ...]
    gates: tuple[str, ...] = ()
    seeds: tuple[int, ...] = ()
    locked: bool = False
    version: str = "v1"
    escalation_rule: str = "escalate to the larger population when a gate is within 10% of its threshold"
    analysis_plan: str = "hes analyze --run <dir>"
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self, *, known_populations: Mapping[str, Any] | None = None) -> list[str]:
        problems: list[str] = []
        for field_name in _REQUIRED:
            value = getattr(self, field_name)
            if not value:
                problems.append(f"missing required field {field_name!r}")
        if known_populations is not None and self.population not in known_populations:
            problems.append(f"unknown population {self.population!r}")
        if len(set(self.arms)) != len(self.arms):
            problems.append("duplicate arms")
        if self.locked and not self.version:
            problems.append("locked spec must declare a version")
        return problems

    def to_manifest(self) -> dict[str, Any]:
        """Shape compatible with evaluation.experiment manifests."""
        manifest = {
            "experiment_id": self.experiment_id,
            "hypothesis": self.hypothesis,
            "population": self.population,
            "arms": list(self.arms),
            "metrics": list(self.metrics),
            "gates": list(self.gates),
            "seeds": list(self.seeds),
            "locked": self.locked,
            "version": self.version,
            "escalation_rule": self.escalation_rule,
            "analysis_plan": self.analysis_plan,
            "notes": self.notes,
        }
        manifest.update(self.extra)
        return manifest

    def as_dict(self) -> dict[str, Any]:
        return self.to_manifest()

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        problems = self.validate()
        if problems:
            raise ValueError(f"invalid experiment spec: {problems}")
        path.write_text(json.dumps(self.to_manifest(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    @classmethod
    def from_manifest(cls, payload: Mapping[str, Any]) -> ExperimentSpec:
        return cls(
            experiment_id=str(payload["experiment_id"]),
            hypothesis=str(payload.get("hypothesis", "")),
            population=str(payload.get("population", "")),
            arms=tuple(payload.get("arms", ())),
            metrics=tuple(payload.get("metrics", ())),
            gates=tuple(payload.get("gates", ())),
            seeds=tuple(payload.get("seeds", ())),
            locked=bool(payload.get("locked", False)),
            version=str(payload.get("version", "v1")),
            escalation_rule=str(payload.get("escalation_rule", "")),
            analysis_plan=str(payload.get("analysis_plan", "")),
            notes=str(payload.get("notes", "")),
        )


def locked_spec_conflicts(
    candidate: ExperimentSpec,
    existing: Sequence[ExperimentSpec],
) -> list[str]:
    """A locked spec with the same id must be byte-identical; otherwise the
    results must not be pooled. Returns human-readable conflicts."""
    conflicts: list[str] = []
    for other in existing:
        if other.locked and other.experiment_id == candidate.experiment_id:
            if other.as_dict() != candidate.as_dict():
                conflicts.append(
                    f"locked spec {candidate.experiment_id!r} differs from {other.version!r}"
                )
    return conflicts