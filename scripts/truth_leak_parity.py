"""Behaviour-freeze proof for M3.5: build_intent no longer puts the scenario id
into goal_description. Runs every insertion and slide scenario against the four
systems under the legacy description (id included) and the current one, and
compares what evaluate reports. Writes results/truth_leak/parity_report.json."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

from benchmarks.heb import systems
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.runner import evaluate
from benchmarks.heb.sliding import sliding_scenarios

FIELDS = (
    "reported_disposition", "reason", "refusal_codes", "graded_disposition",
    "task_success", "bounded_success", "hard_violations", "observations",
)


def _arm(scenarios, legacy: bool):
    real_build = systems.build_intent
    seen: dict[str, str] = {}
    obs: dict[tuple[str, str], str] = {}

    def build(scenario, cond):
        intent = real_build(scenario, cond)
        if legacy:
            intent = dataclasses.replace(intent, goal_description=f"HEB scenario {scenario.id}")
        seen[scenario.id] = intent.goal_description
        return intent

    def wrap(system):
        run = system.run

        def tapped(realization, scenario):
            result = run(realization, scenario)
            obs[(scenario.id, result.system)] = hashlib.sha256(
                repr(result.observations).encode()).hexdigest()
            return result

        system.run = tapped
        return system

    systems.build_intent = build
    try:
        sysx = [wrap(systems.HaplousSystem()), wrap(systems.SupervisedSystem()),
                wrap(systems.BareSystem()), wrap(systems.BareSystem(evaluate_success=False))]
        records = evaluate(scenarios, sysx)
    finally:
        systems.build_intent = real_build
    return records, seen, obs


def _row(rec, obs):
    d = {f: getattr(rec, f) for f in FIELDS if f != "observations"}
    d["observations"] = obs[(rec.scenario_id, rec.system)]
    return json.loads(json.dumps(d, sort_keys=True, default=str))


SCALARS = tuple(f for f in FIELDS if f not in ("hard_violations", "observations"))


def _digest(v) -> str:
    return hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest()


def _stored(r: dict) -> dict:
    d = {f: r[f] for f in SCALARS}
    d["hard_violations_count"] = len(r["hard_violations"])
    d["hard_violations_sha256"] = _digest(r["hard_violations"])
    d["observations_sha256"] = r["observations"]
    return d


def main() -> int:
    scenarios = insertion_scenarios() + sliding_scenarios()
    before, _, obs_b = _arm(scenarios, legacy=True)
    after, desc_a, obs_a = _arm(scenarios, legacy=False)
    rows, diffs = [], 0
    for b, a in zip(before, after, strict=True):
        assert (b.scenario_id, b.system) == (a.scenario_id, a.system)
        rb, ra = _row(b, obs_b), _row(a, obs_a)
        diff = sorted(k for k in rb if rb[k] != ra[k])
        diffs += bool(diff)
        rows.append({"scenario": a.scenario_id, "system": a.system, "identical": not diff,
                     "differing_fields": diff,
                     "before": _stored(rb), "after": _stored(ra)})
    leaked = any(s in d for s, d in desc_a.items())
    report = {
        "fields_compared": list(FIELDS),
        "fields_excluded": ["wall_s", "task_metrics", "tags"],
        "legitimately_differing": {
            "ExecutionIntent.goal_description": "legacy 'HEB scenario <id>' vs task-level text",
            "evidence.intent_summary.goal_description": "copy of the above in runtime evidence",
        },
        "n_scenarios": len(scenarios), "n_trials": len(rows), "n_differing": diffs,
        "goal_description_after": desc_a,
        "id_in_any_description_after": leaked,
        "verdict": "PASS" if diffs == 0 and not leaked else "FAIL",
        "trials": rows,
    }
    out = Path("results/truth_leak/parity_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(report["verdict"], len(rows) - diffs, "/", len(rows))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
