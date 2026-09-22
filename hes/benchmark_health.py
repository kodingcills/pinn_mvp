"""Benchmark health: is the evaluation instrument itself sound?

A campaign can be undermined by the benchmark rather than the runtime.
These checks look at the evidence from the grader's side:

- discriminating power: do the arms actually disagree, or does every
  scenario grade identically for all systems (a dead benchmark)?
- degenerate scenarios: is any scenario trivially solvable or trivially
  impossible for every arm?
- population balance: are task families and offset strata represented, or
  is a "global" claim really one slice?
- grader stability: the same scenario graded for different arms must be
  graded by the same rule (no arm-specific grading).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HealthFinding:
    name: str
    healthy: bool
    detail: str
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "healthy": self.healthy,
            "detail": self.detail,
            "evidence": self.evidence,
        }


def _sid(row: Mapping[str, Any]) -> str:
    return str(row.get("scenario_id") or row.get("episode_id"))


def _by_scenario(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        out.setdefault(_sid(row), []).append(row)
    return out


def discriminating_power(rows: Sequence[Mapping[str, Any]]) -> HealthFinding:
    by_scenario = _by_scenario(rows)
    if not by_scenario:
        return HealthFinding("discriminating_power", False, "no scenarios", {})
    all_agree = 0
    for arm_rows in by_scenario.values():
        successes = {bool(r.get("bounded_success")) for r in arm_rows}
        if len(successes) == 1:
            all_agree += 1
    fraction = all_agree / len(by_scenario)
    return HealthFinding(
        "discriminating_power",
        fraction < 0.98,
        f"{fraction:.3f} of scenarios grade identically for every arm",
        {"n_scenarios": len(by_scenario), "all_agree": all_agree},
    )


def degenerate_scenarios(rows: Sequence[Mapping[str, Any]]) -> HealthFinding:
    by_scenario = _by_scenario(rows)
    trivial_success: list[str] = []
    trivial_failure: list[str] = []
    for sid, arm_rows in by_scenario.items():
        successes = [bool(r.get("bounded_success")) for r in arm_rows]
        if successes and all(successes):
            trivial_success.append(sid)
        if successes and not any(successes):
            trivial_failure.append(sid)
    return HealthFinding(
        "degenerate_scenarios",
        True,
        f"{len(trivial_success)} trivially solved and {len(trivial_failure)} "
        "unsolved by every arm (informational; not necessarily a defect)",
        {
            "n_trivially_solved": len(trivial_success),
            "n_trivially_unsolved": len(trivial_failure),
            "examples_solved": trivial_success[:5],
            "examples_unsolved": trivial_failure[:5],
        },
    )


def population_balance(rows: Sequence[Mapping[str, Any]]) -> HealthFinding:
    families: dict[str, set[str]] = {}
    strata: dict[str, set[str]] = {}
    for row in rows:
        sid = _sid(row)
        for tag in row.get("tags") or ():
            tag = str(tag)
            if tag.startswith("task_"):
                families.setdefault(tag, set()).add(sid)
            elif tag.startswith("offset:"):
                strata.setdefault(tag, set()).add(sid)
    family_counts = {k: len(v) for k, v in sorted(families.items())}
    stratum_counts = {k: len(v) for k, v in sorted(strata.items())}
    n_scenarios = len({_sid(r) for r in rows})
    healthy = len(families) >= 2 and len(strata) >= 2
    return HealthFinding(
        "population_balance",
        healthy,
        f"{len(families)} task families, {len(strata)} offset strata across "
        f"{n_scenarios} scenarios",
        {
            "task_families": family_counts,
            "offset_strata": stratum_counts,
            "n_scenarios": n_scenarios,
        },
    )


def grader_consistency(rows: Sequence[Mapping[str, Any]]) -> HealthFinding:
    """The grader must apply one rule per scenario, not per arm. We can only
    check that violations are reported consistently with bounded success."""
    inconsistent: list[dict[str, Any]] = []
    for row in rows:
        n_viol = int(row.get("n_hard_violations") or 0)
        bounded = bool(row.get("bounded_success"))
        if bounded and n_viol > 0:
            inconsistent.append({"scenario_id": _sid(row), "arm": row.get("arm")})
    return HealthFinding(
        "grader_consistency",
        not inconsistent,
        "bounded success never coexists with a recorded hard violation"
        if not inconsistent
        else f"{len(inconsistent)} rows report bounded success with violations",
        {"n_inconsistent": len(inconsistent), "examples": inconsistent[:10]},
    )


def benchmark_health(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    findings = [
        discriminating_power(rows),
        degenerate_scenarios(rows),
        population_balance(rows),
        grader_consistency(rows),
    ]
    return {
        "healthy": all(f.healthy for f in findings),
        "findings": [f.as_dict() for f in findings],
    }