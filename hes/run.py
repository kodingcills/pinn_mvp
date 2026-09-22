"""HES run: load canonical evidence and produce the metric vector.

A HES run is a directory containing canonical episode rows (Parquet or
JSONL), an experiment manifest, optional counterfactual fork rows, and
provenance. HES does not re-run physics here; it analyzes committed
evidence. ``hes run`` uses ``evaluation.experiment`` to produce the same
row contract.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hes.attribution import Attribution, summarize_attributions
from hes.metrics import (
    authority_metrics,
    failure_rows,
    intervention_metrics,
    noninterference_metrics,
    physical_metrics,
    risk_coverage_points,
    system_metrics,
    timing_metrics,
)
from hes.model import Attainability, build_attainability
from hes.pareto import build_pareto, responsibility_decomposition

_DEFAULT_BASELINE_ARMS = ("direct_minimal", "bare_behavior", "bare", "direct")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        table = pq.read_table(path).to_pylist()
        rows = []
        for row in table:
            for key in ("refusal_codes", "hard_violations", "task_metrics", "tags"):
                value = row.get(key)
                if isinstance(value, str):
                    try:
                        row[key] = json.loads(value)
                    except json.JSONDecodeError:
                        row[key] = value
            rows.append(row)
        return rows
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _find_rows(root: Path) -> list[dict[str, Any]]:
    parquet = root / "episodes.parquet"
    if parquet.is_file():
        return _load_rows(parquet)
    shard_rows: list[dict[str, Any]] = []
    for status_path in sorted(root.glob("shards/*/status.json")):
        status = _load_json(status_path)
        if status.get("status") != "COMPLETE":
            continue
        episodes = status_path.parent / "episodes.jsonl"
        if episodes.is_file():
            shard_rows.extend(_load_rows(episodes))
    if shard_rows:
        return shard_rows
    plain = root / "episodes.jsonl"
    if plain.is_file():
        return _load_rows(plain)
    raise FileNotFoundError(
        f"no canonical episode rows under {root} "
        "(expected episodes.parquet, episodes.jsonl, or COMPLETE shards)"
    )


def _load_forks(root: Path) -> list[dict[str, Any]]:
    for name in ("interventions.jsonl", "forks.jsonl", "counterfactuals.jsonl"):
        path = root / name
        if path.is_file():
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    return []


@dataclass
class Run:
    root: Path
    rows: list[dict[str, Any]]
    manifest: dict[str, Any]
    provenance: dict[str, Any]
    forks: list[dict[str, Any]] = field(default_factory=list)
    experiment_id: str = "unknown"
    arms: tuple[str, ...] = ()
    baseline_arm: str | None = None

    @property
    def by_arm(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for row in self.rows:
            out.setdefault(str(row["arm"]), []).append(row)
        return out

    @property
    def by_scenario(self) -> dict[str, dict[str, dict[str, Any]]]:
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for row in self.rows:
            sid = str(row.get("scenario_id") or row.get("episode_id"))
            out.setdefault(sid, {})[str(row["arm"])] = row
        return out

    def attainability(self) -> dict[str, Attainability]:
        fork_index = {str(f.get("scenario_id")): f for f in self.forks}
        infeasible = [
            str(row.get("scenario_id") or row.get("episode_id"))
            for row in self.rows
            if row.get("infeasible_request")
        ]
        return build_attainability(
            self.by_scenario,
            reference_arm=self.baseline_arm,
            fork_by_scenario=fork_index,
            infeasible_scenarios=infeasible,
        )

    def analysis(self) -> dict[str, Any]:
        by_arm = self.by_arm
        attainability = self.attainability()
        baseline = self.baseline_arm
        baseline_rows = by_arm.get(baseline or "", [])
        arms: dict[str, Any] = {}
        for arm, arm_rows in by_arm.items():
            failures = failure_rows(arm_rows, attainability)
            arms[arm] = {
                "system": system_metrics(arm_rows),
                "authority": authority_metrics(arm_rows, attainability),
                "non_interference": (
                    noninterference_metrics(arm_rows, baseline_rows)
                    if baseline_rows and arm != baseline
                    else {"note": "baseline arm has no non-interference comparison"}
                ),
                "physical": physical_metrics(arm_rows),
                "timing": timing_metrics(arm_rows),
                "attribution": summarize_attributions([a for _, a in failures]).as_dict(),
                "failure_pareto": build_pareto(failures, n_all_episodes=len(arm_rows)),
                "responsibility": (
                    responsibility_decomposition(failures, attainability)
                    if self._is_runtime_arm(arm)
                    else None
                ),
                "caveats": (
                    [
                        "completion-time comparison vs this baseline is confounded: "
                        "direct_minimal does not terminate on success, so it runs to horizon"
                    ]
                    if arm != baseline and baseline == "direct_minimal"
                    else []
                ),
            }
        return {
            "experiment_id": self.experiment_id,
            "baseline_arm": baseline,
            "arms": list(self.arms),
            "n_scenarios": len(self.by_scenario),
            "n_episodes": len(self.rows),
            "provenance": self.provenance,
            "attainability": _attainability_summary(attainability),
            "risk_coverage": risk_coverage_points(by_arm),
            "intervention": intervention_metrics(self.forks),
            "metric_arm_vector": arms,
            "slices": self._slices(attainability),
            "not_available": {
                "task_progress": "canonical rows carry no progress channel",
                "integrated_constraint_excursion": "violation rows are pruned per episode",
                "collision_metrics": "no collision channel in the internal substrate",
                "decision_latency": "runtime decision timing is not in canonical rows",
                "evidence_completeness": "not derived from canonical rows",
                "portability_engineering_leverage": "manifest-level metadata only",
            },
        }

    def _is_runtime_arm(self, arm: str) -> bool:
        return arm == self.manifest.get("reference_arm") or "haplous" in arm

    def _slices(self, attainability: Mapping[str, Attainability]) -> dict[str, Any]:
        """Slice results by task family and by the population's offset
        stratum, both carried in scenario tags."""
        dimensions: dict[str, dict[str, list[dict[str, Any]]]] = {}
        scenario_tags: dict[str, list[str]] = {}
        for row in self.rows:
            tags = [str(t) for t in (row.get("tags") or ())]
            sid = str(row.get("scenario_id") or row.get("episode_id"))
            scenario_tags.setdefault(sid, tags)
        for row in self.rows:
            sid = str(row.get("scenario_id") or row.get("episode_id"))
            tags = scenario_tags.get(sid, [])
            for tag in tags:
                if tag.startswith("task_"):
                    dimensions.setdefault("task_family", {}).setdefault(tag, []).append(row)
                elif tag.startswith("offset:"):
                    dimensions.setdefault("offset_stratum", {}).setdefault(tag, []).append(row)

        out: dict[str, Any] = {}
        for dimension, groups in dimensions.items():
            out[dimension] = {}
            for name, rows in groups.items():
                by_arm: dict[str, list[dict[str, Any]]] = {}
                for row in rows:
                    by_arm.setdefault(str(row["arm"]), []).append(row)
                out[dimension][name] = {
                    "n": len({str(r.get("scenario_id") or r.get("episode_id")) for r in rows}),
                    "arms": {
                        arm: {
                            "bounded_success": system_metrics(arm_rows)["bounded_success"],
                            "coverage": system_metrics(arm_rows)["coverage"],
                            "execution_capture": authority_metrics(arm_rows, attainability)[
                                "execution_capture"
                            ],
                        }
                        for arm, arm_rows in by_arm.items()
                    },
                }
        return out


def _attainability_summary(attainability: Mapping[str, Attainability]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    provenance: dict[str, int] = {}
    for value in attainability.values():
        counts[value.axis.value] = counts.get(value.axis.value, 0) + 1
        provenance[value.provenance.value] = provenance.get(value.provenance.value, 0) + 1
    return {"counts": counts, "provenance": provenance, "n": len(attainability)}


def load_run(root: str | Path, *, baseline_arm: str | None = None) -> Run:
    root = Path(root)
    rows = _find_rows(root)
    manifest_path = root / "manifest.json"
    manifest = _load_json(manifest_path) if manifest_path.is_file() else {}
    provenance_path = root / "provenance.json"
    provenance = _load_json(provenance_path) if provenance_path.is_file() else {}
    forks = _load_forks(root)
    arms = tuple(sorted({str(r["arm"]) for r in rows}))
    if baseline_arm is None:
        baseline_arm = next((a for a in _DEFAULT_BASELINE_ARMS if a in arms), None)
        if baseline_arm is None:
            baseline_arm = manifest.get("reference_arm")
    return Run(
        root=root,
        rows=rows,
        manifest=manifest,
        provenance=provenance,
        forks=forks,
        experiment_id=str(manifest.get("experiment_id") or root.name),
        arms=arms,
        baseline_arm=baseline_arm,
    )
