"""Substrate characterization and capability profile.

Before instrumenting machinery, HES characterizes the substrate: which
metrics are cheap, which are unobservable, and where the artifact (the
simulator/adapter), not the runtime, sets the observed value. This is what
lets a result be read correctly and what prevents a simulator artifact from
being reported as a Haplous defect.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# Distinguish artifact-caused from runtime-caused: a metric at its ceiling
# in the artifact is a saturated measurement, not a runtime achievement.
ARTIFACT_SEPARATION_METRICS = {
    "sim_step_time": "artifact: simulator step cost",
    "force_proxy": "artifact: force is a proxy in the internal substrate",
    "collision_geometry": "artifact: collision model is simulator-specific",
    "determinism": "artifact+runtime: run twice and compare canonical rows",
}

SUBSTRATE_METRICS: dict[str, dict[str, Any]] = {
    "step_latency_us": {"observable": True, "source": "artifact", "cost": "low"},
    "force_measurement": {"observable": True, "source": "artifact", "cost": "low"},
    "tilt_measurement": {"observable": True, "source": "artifact", "cost": "low"},
    "pose_precision": {"observable": True, "source": "artifact", "cost": "low"},
    "contact_events": {"observable": False, "source": "artifact", "cost": "unknown"},
    "collision_severity": {"observable": False, "source": "artifact", "cost": "unknown"},
    "exact_fault_injection": {"observable": True, "source": "benchmark", "cost": "low"},
    "state_freshness_true": {"observable": True, "source": "benchmark", "cost": "low"},
    "oracle_clearance": {"observable": True, "source": "benchmark", "cost": "low"},
    "bounded_success": {"observable": True, "source": "benchmark", "cost": "low"},
}


@dataclass
class CapabilityProfile:
    covers: list[str] = field(default_factory=list)
    limited: list[str] = field(default_factory=list)
    unobservable: list[str] = field(default_factory=list)
    artifact_dominated: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "covers": self.covers,
            "limited": self.limited,
            "unobservable": self.unobservable,
            "artifact_dominated": self.artifact_dominated,
        }


@dataclass
class SubstrateCharacterization:
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    capability: CapabilityProfile = field(default_factory=CapabilityProfile)
    determinism: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics,
            "capability": self.capability.as_dict(),
            "determinism": self.determinism,
            "notes": self.notes,
        }


def characterize(
    *,
    rows_twice: tuple[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]] | None = None,
    metric_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> SubstrateCharacterization:
    metrics = {name: dict(spec) for name, spec in SUBSTRATE_METRICS.items()}
    if metric_overrides:
        for name, spec in metric_overrides.items():
            metrics[name] = {**metrics.get(name, {}), **spec}

    profile = CapabilityProfile()
    for name, spec in metrics.items():
        if not spec.get("observable", True):
            profile.unobservable.append(name)
        elif spec.get("source") == "artifact" and spec.get("cost") == "high":
            profile.artifact_dominated.append(name)
        elif spec.get("limited"):
            profile.limited.append(name)
        else:
            profile.covers.append(name)

    determinism: dict[str, Any] = {"checked": False}
    if rows_twice is not None:
        first, second = rows_twice
        key = lambda r: (str(r.get("scenario_id")), str(r.get("arm")))  # noqa: E731
        index_a = {key(r): r for r in first}
        index_b = {key(r): r for r in second}
        shared = sorted(set(index_a) & set(index_b))
        differing = [
            {"scenario_id": k[0], "arm": k[1]}
            for k in shared
            if index_a[k].get("reported_disposition") != index_b[k].get("reported_disposition")
            or bool(index_a[k].get("bounded_success")) != bool(index_b[k].get("bounded_success"))
        ]
        determinism = {
            "checked": True,
            "n_pairs": len(shared),
            "n_differing": len(differing),
            "deterministic": not differing,
            "differences": differing[:20],
        }

    return SubstrateCharacterization(
        metrics=metrics,
        capability=profile,
        determinism=determinism,
        notes=[
            "force is a proxy in the internal substrate; magnitudes are not comparable across engines",
            "collision channels are not implemented; collision metrics report NOT_AVAILABLE",
        ],
    )