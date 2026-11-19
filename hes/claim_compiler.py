"""Claim-to-experiment compiler (issue #20, Gate D).

A developer states an execution claim as a ``ClaimSpec`` (plain fields, JSON in
``evaluation/claim_specs/``; not a language). ``compile_claim`` returns either a
``Plan`` (a DEV-tier, paired experiment over existing HES/HEB parts) or
``Unsupported`` with machine-readable reasons. Nothing here is a new evaluator:

- populations are ``hes.populations.PopulationClass`` (HES ones plus the DEV
  classes in ``benchmarks/claim_populations.py``); sealed populations and any
  population sharing a scenario id or content with one are refused;
- machine profiles are ``MachineCapabilities`` overrides on the HEB MuJoCo
  machine's own capabilities;
- comparators are ``benchmarks.heb.systems.system_by_name`` arms, run through
  ``benchmarks.heb.runner.evaluate`` (one shared realization per scenario);
- operators are the ``hes.metamorphic`` transforms;
- estimands are HEB-graded row fields, metrics are ``hes.metrics`` and the
  paired deltas ``hes.regression.paired_rate_delta``;
- the CPU estimate is ``studio.jobs.cpu_per_episode`` (M4 measured means).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.claim_populations import admission_population, temporal_hold_population
from haplous.capabilities import CommandMode, MachineCapabilities
from hes import metamorphic
from hes.populations import POPULATIONS, PopulationClass

REPO = Path(__file__).resolve().parent.parent
DEV = "DEV"
SUBJECT = "haplous_mediated"
SEALED = ("qualification", "m4_control", "m4_stress")  # confirmatory / claim-bearing draws
CLAIM_POPULATIONS: dict[str, PopulationClass] = {
    "temporal_hold_dev": PopulationClass(
        name="temporal_hold_dev", version="v1", seed=20020, n=64,
        purpose="DEV: claim compiler, temporal hold below/above the hold budget",
        generator=temporal_hold_population, slide_fraction=0.0),
    "admission_dev": PopulationClass(
        name="admission_dev", version="v1", seed=20021, n=32,
        purpose="DEV: claim compiler, command-mode admission on fault-free insertions",
        generator=admission_population, slide_fraction=0.0),
}
CI, FCD = CommandMode.CARTESIAN_IMPEDANCE, CommandMode.FORCE_CONTROLLED_DESCENT
PROFILES: dict[str, dict[str, Any]] = {
    "mujoco_heb": {},
    "mujoco_fcd_only": {"command_modes": frozenset({FCD})},
    "mujoco_ci_only": {"command_modes": frozenset({CI})},
}
OPERATORS: dict[str, Callable] = {
    "irrelevant_perturbation": metamorphic.irrelevant_perturbation,
    "relax_requirements": metamorphic.relax_requirements,
    "tighten_requirements": metamorphic.tighten_requirements,
}
SCORING_SOURCES = ("hes/claim_compiler.py", "hes/metrics.py", "hes/regression.py",
                   "evaluation/stats.py", "benchmarks/heb/metrics.py")


@dataclass(frozen=True)
class ClaimSpec:
    property_id: str
    requirements: Mapping[str, Any]
    machine_profiles: tuple[str, ...]
    task_family: str
    comparators: tuple[str, ...]
    population: str
    n_scenarios: int
    budget_cpu_min: float
    seed: int
    operators: tuple[str, ...] = ()
    tier: str = DEV
    statement: str = ""

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ClaimSpec:
        tuples = ("machine_profiles", "comparators", "operators")
        return cls(**{k: tuple(v) if k in tuples else v for k, v in d.items()})


@dataclass(frozen=True)
class Reason:
    code: str
    detail: str


@dataclass(frozen=True)
class Unsupported:
    claim: ClaimSpec
    reasons: tuple[Reason, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"status": "UNSUPPORTED", "claim": _jsonable(self.claim),
                "reasons": [dataclasses.asdict(r) for r in self.reasons]}


@dataclass(frozen=True)
class Unit:
    """One pairing unit: every arm runs on this exact scenario and profile."""

    unit_id: str
    scenario_id: str
    profile: str
    operator: str | None
    stratum: str
    scenario_sha256: str
    lacks_required: bool = False


@dataclass(frozen=True)
class Plan:
    claim: ClaimSpec
    population: str
    arms: tuple[str, ...]
    units: tuple[Unit, ...]
    estimand: str
    better: str
    cpu_estimate_s: float
    cost_basis: Mapping[str, Any]
    scoring_sources: Mapping[str, str]
    tier: str = DEV

    def as_dict(self) -> dict[str, Any]:
        return {"status": "PLAN", **_jsonable(self)}

    @property
    def sha256(self) -> str:
        return _sha(self.as_dict())


# --------------------------------------------------------------------------
# Properties: what each claim measures and when it is testable
# --------------------------------------------------------------------------


def _hold_check(spec: ClaimSpec, caps: Mapping[str, MachineCapabilities], behavior) -> list[Reason]:
    budget = spec.requirements["hold_budget_s"]
    return [Reason("hold_budget_mismatch", f"profile {p} holds {c.max_hold_unobserved_s} s, claim states {budget} s")
            for p, c in caps.items() if c.max_hold_unobserved_s != budget]


def _required_modes(spec: ClaimSpec) -> set[str]:
    return set(spec.requirements["required_command_modes"])


def _admission_check(spec: ClaimSpec, caps: Mapping[str, MachineCapabilities], behavior) -> list[Reason]:
    required = _required_modes(spec)
    known = {m.value for m in CommandMode}
    offered = {m.value for c in caps.values() for m in c.command_modes}
    reasons = [Reason("unknown_capability", f"{m} is not a command mode any machine model can state ({sorted(known)})")
               for m in sorted(required - known)]
    reasons += [Reason("capability_absent_from_every_profile", f"no profile in {list(caps)} offers {m}")
                for m in sorted(required - offered)]
    declared = {m.value for m in getattr(behavior, "required_command_modes", ())}
    if required != declared:
        reasons.append(Reason("requirement_not_declared_by_task_behaviour",
                              f"{spec.task_family} behaviour declares {sorted(declared)}, claim requires {sorted(required)}"))
    lacking = [p for p, c in caps.items() if not required <= {m.value for m in c.command_modes}]
    if not reasons and (not lacking or len(lacking) == len(caps)):
        reasons.append(Reason("profiles_do_not_contrast",
                              "need at least one profile with and one without the required modes"))
    return reasons


def _stale_actuated(row: Mapping[str, Any], unit: Unit) -> bool:
    return any(v.get("kind") == "stale_span_actuated" for v in row.get("hard_violations") or ())


def _admission_correct(row: Mapping[str, Any], unit: Unit) -> bool:
    started = int((row.get("task_metrics") or {}).get("n_applied_commands", 0)) > 0
    if unit.lacks_required:
        return row["reported_disposition"] == "REFUSED" and not started
    return started


@dataclass(frozen=True)
class Property:
    task_families: frozenset[str]
    requirement: str
    estimand: str
    better: str
    check: Callable[[ClaimSpec, Mapping[str, MachineCapabilities], Any], list[Reason]]
    score: Callable[[Mapping[str, Any], Unit], bool]
    strata: tuple[str, ...] = field(default=())


PROPERTIES: dict[str, Property] = {
    # Graded by HEB (benchmarks/heb/metrics.py): a command applied strictly inside
    # a span where the system had no state fresher than the declared bound.
    "temporal_hold": Property(frozenset({"peg_insertion"}), "hold_budget_s", "stale_span_actuated", "lower",
                              _hold_check, _stale_actuated, ("below_budget", "above_budget")),
    # Correct admission: refused before any command when the profile lacks a
    # required mode; executed when it has them all.
    "od1_admission": Property(frozenset({"peg_insertion"}), "required_command_modes", "admission_correct", "higher",
                              _admission_check, _admission_correct, ("nominal",)),
}


# --------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    return json.loads(json.dumps(value, sort_keys=True, default=lambda o: sorted(o) if isinstance(o, frozenset) else str(o)))


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True).encode()).hexdigest()


def scenario_sha256(scenario) -> str:
    return _sha(scenario)


def file_sha256(rel: str) -> str:
    return hashlib.sha256((REPO / rel).read_bytes()).hexdigest()


def find_population(ref: str) -> PopulationClass | None:
    pops = {**{p.id: p for p in POPULATIONS.values()}, **{p.id: p for p in CLAIM_POPULATIONS.values()}}
    return pops.get(ref) or pops.get(f"{ref}@v1")


def sealed_overlap(scenarios: Sequence) -> list[str]:
    """Sealed populations sharing any scenario id or scenario content."""
    ids = {s.id for s in scenarios}
    digests = {scenario_sha256(s) for s in scenarios}
    out = []
    for name in SEALED:
        sealed = POPULATIONS[name].generate()
        if ids & {s.id for s in sealed} or digests & {scenario_sha256(s) for s in sealed}:
            out.append(POPULATIONS[name].id)
    return out


def _stratum(scenario) -> str:
    return next((t.split(":", 1)[1] for t in scenario.tags if t.startswith("stratum:")), "none")


def _select(scenarios: Sequence, strata: Sequence[str], n: int) -> list:
    """First n scenarios in generation order, balanced across strata."""
    pools = [[s for s in scenarios if _stratum(s) == k] for k in strata]
    picked = []
    for i in range(n):
        pool, j = pools[i % len(pools)], i // len(pools)
        if j < len(pool):
            picked.append(pool[j])
    return picked


def _known_arm(name: str) -> bool:
    from benchmarks.heb.systems import system_by_name

    try:
        system_by_name(name)
    except ValueError:
        return False
    return True


def compile_claim(spec: ClaimSpec) -> Plan | Unsupported:
    from benchmarks.heb import mujoco_backend
    from studio.jobs import cpu_per_episode

    reasons: list[Reason] = []
    if spec.tier != DEV:
        reasons.append(Reason("tier_not_dev", f"compiled plans are DEV only, not {spec.tier!r}"))
    prop = PROPERTIES.get(spec.property_id)
    if prop is None:
        return Unsupported(spec, (*reasons, Reason("property_unknown", f"known: {sorted(PROPERTIES)}")))
    if spec.task_family not in prop.task_families:
        reasons.append(Reason("task_family_unsupported", f"{spec.property_id} runs on {sorted(prop.task_families)}"))
    reasons += [Reason("comparator_unknown", a) for a in spec.comparators if not _known_arm(a)]
    if SUBJECT not in spec.comparators:
        reasons.append(Reason("subject_arm_missing", f"{SUBJECT} must be one of the comparators"))
    reasons += [Reason("profile_unknown", p) for p in spec.machine_profiles if p not in PROFILES]
    reasons += [Reason("operator_unknown", o) for o in spec.operators if o not in OPERATORS]
    if prop.requirement not in spec.requirements:
        reasons.append(Reason("requirement_missing", prop.requirement))

    pop = find_population(spec.population)
    scenarios: list = []
    if pop is None:
        reasons.append(Reason("population_unknown", spec.population))
    elif pop.name in SEALED:
        reasons.append(Reason("population_sealed", f"{pop.id} is confirmatory; plans never target it"))
    else:
        generated = [s for s in pop.generate() if s.task_family == spec.task_family]
        overlap = sealed_overlap(generated)
        if overlap:
            reasons.append(Reason("population_overlaps_sealed", f"{pop.id} shares scenarios with {overlap}"))
        scenarios = _select(generated, prop.strata, spec.n_scenarios)
        if len(scenarios) < spec.n_scenarios:
            reasons.append(Reason("population_too_small", f"{len(scenarios)} of {spec.n_scenarios} in strata {prop.strata}"))
    if reasons:
        return Unsupported(spec, tuple(reasons))

    realization = mujoco_backend.build_realization(scenarios[0])
    base = realization.machine.capabilities()
    caps = {p: dataclasses.replace(base, **PROFILES[p]) for p in spec.machine_profiles}
    reasons += prop.check(spec, caps, realization.behavior_factory())
    if reasons:
        return Unsupported(spec, tuple(reasons))

    units = []
    for s in scenarios:
        for p in spec.machine_profiles:
            lacks = (spec.property_id == "od1_admission"
                     and not _required_modes(spec) <= {m.value for m in caps[p].command_modes})
            for op in (None, *spec.operators):
                v = s if op is None else OPERATORS[op](s)
                units.append(Unit(f"{v.id}|{p}", v.id, p, op, _stratum(s), scenario_sha256(v), lacks))
    cost = cpu_per_episode(list(spec.comparators), REPO)
    estimate = len(units) * sum(c for c, _ in cost.values())
    if estimate > spec.budget_cpu_min * 60:
        return Unsupported(spec, (Reason("budget_exceeded",
                                         f"estimate {estimate:.0f} CPU-s > budget {spec.budget_cpu_min * 60:.0f} CPU-s"),))
    return Plan(spec, pop.id, tuple(spec.comparators), tuple(units), prop.estimand, prop.better, round(estimate, 1),
                {a: {"cpu_s_per_episode": round(c, 3), "source": src} for a, (c, src) in cost.items()},
                {rel: file_sha256(rel) for rel in SCORING_SOURCES})


# --------------------------------------------------------------------------
# Execution: HEB evaluate, one call per profile
# --------------------------------------------------------------------------


class ProfiledMachine:
    """The HEB machine with its stated capabilities replaced by a profile.
    Only capabilities() changes; everything else is the inner machine."""

    def __init__(self, inner, overrides: Mapping[str, Any]) -> None:
        self._inner, self._overrides = inner, dict(overrides)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def capabilities(self) -> MachineCapabilities:
        return dataclasses.replace(self._inner.capabilities(), **self._overrides)


class _ProfiledSystem:
    """Runs an arm on the profiled machine; records CPU and the digest of the
    scenario it actually ran (the pairing check's evidence)."""

    def __init__(self, arm: str, profile: str, sink: dict) -> None:
        from benchmarks.heb.systems import system_by_name

        self._inner, self._profile, self._sink = system_by_name(arm), profile, sink
        self.name = self._inner.name

    def run(self, realization, scenario):
        t0 = time.process_time()
        run = self._inner.run(dataclasses.replace(
            realization, machine=ProfiledMachine(realization.machine, PROFILES[self._profile])), scenario)
        self._sink[(scenario.id, run.system)] = (time.process_time() - t0, scenario_sha256(scenario))
        return run


def _materialize(plan: Plan) -> dict[str, Any]:
    by_id = {s.id: s for s in find_population(plan.population).generate()}
    out = {}
    for u in plan.units:
        base_id = u.scenario_id.removesuffix("#meta")
        s = by_id[base_id]
        out[u.unit_id] = s if u.operator is None else OPERATORS[u.operator](s)
    return out


def execute_plan(plan: Plan) -> list[dict[str, Any]]:
    """Canonical rows, one per (unit, arm). Refuses any plan that is not exactly
    what the compiler produces for its claim (no hand-edited or sealed plans)."""
    from benchmarks.heb.runner import evaluate

    again = compile_claim(plan.claim)
    if not isinstance(again, Plan) or again.sha256 != plan.sha256:
        raise ValueError("plan is not the compiler's output for its claim; refusing to execute")
    scenarios = _materialize(plan)
    units = {(u.scenario_id, u.profile): u for u in plan.units}
    rows = []
    for profile in plan.claim.machine_profiles:
        group = [scenarios[u.unit_id] for u in plan.units if u.profile == profile]
        sink: dict = {}
        for rec in evaluate(group, [_ProfiledSystem(a, profile, sink) for a in plan.arms]):
            unit = units[(rec.scenario_id, profile)]
            cpu, digest = sink[(rec.scenario_id, rec.system)]
            metrics = dict(rec.task_metrics)
            times = [float(t) for t in metrics.pop("applied_command_times", ())]
            metrics.update(n_applied_commands=len(times), cpu_s=cpu, applied_commands_sha256=hashlib.sha256(
                json.dumps(times).encode()).hexdigest())
            rows.append(_jsonable({
                "scenario_id": unit.unit_id, "base_scenario_id": rec.scenario_id, "profile": profile,
                "stratum": unit.stratum, "arm": rec.system, "scenario_sha256": digest,
                "reported_disposition": rec.reported_disposition, "reason": rec.reason,
                "refusal_codes": list(rec.refusal_codes), "graded_disposition": rec.graded_disposition,
                "task_success": rec.task_success, "bounded_success": rec.bounded_success,
                "hard_violations": list(rec.hard_violations), "n_hard_violations": len(rec.hard_violations),
                "task_metrics": metrics, "tags": list(rec.tags), "plan_sha256": plan.sha256,
            }))
    return rows


# --------------------------------------------------------------------------
# Scoring: pairing check, HES metrics, paired deltas
# --------------------------------------------------------------------------


def pairing_problems(plan: Plan, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Exactly one row per (unit, arm), and every row ran the unit's scenario."""
    units = {u.unit_id: u for u in plan.units}
    seen: dict[str, set[str]] = {}
    problems = []
    for r in rows:
        u = units.get(r["scenario_id"])
        if u is None:
            problems.append(f"row for unknown unit {r['scenario_id']}")
            continue
        if r["arm"] in seen.setdefault(u.unit_id, set()):
            problems.append(f"duplicate row {u.unit_id}/{r['arm']}")
        seen[u.unit_id].add(r["arm"])
        if r.get("scenario_sha256") != u.scenario_sha256:
            problems.append(f"{r['arm']} row labelled {u.unit_id} ran a different scenario")
    problems += [f"unit {uid} missing arms {sorted(set(plan.arms) - seen.get(uid, set()))}"
                 for uid in units if seen.get(uid, set()) != set(plan.arms)]
    return problems


def scoring_drift(plan: Plan) -> list[str]:
    return [f"scoring source {rel} changed since the plan was compiled"
            for rel, digest in plan.scoring_sources.items() if file_sha256(rel) != digest]


def score(plan: Plan, rows: Sequence[Mapping[str, Any]], *, resamples: int = 5000) -> dict[str, Any]:
    from hes.metrics import _prop, system_metrics
    from hes.regression import paired_rate_delta

    problems = pairing_problems(plan, rows) + scoring_drift(plan)
    if problems:
        raise ValueError("; ".join(problems[:5]))
    prop = PROPERTIES[plan.claim.property_id]
    units = {u.unit_id: u for u in plan.units}
    rows = [{**r, plan.estimand: prop.score(r, units[r["scenario_id"]])} for r in rows]
    by_arm = {a: [r for r in rows if r["arm"] == a] for a in plan.arms}

    def rate(group):
        return _prop(sum(1 for r in group if r[plan.estimand]), len(group))

    def dispositions(group):
        out: dict[str, int] = {}
        for r in group:
            out[r["graded_disposition"]] = out.get(r["graded_disposition"], 0) + 1
        return dict(sorted(out.items()))

    slices = sorted({(r["stratum"], r["profile"]) for r in rows})
    return {
        "claim": plan.claim.statement, "property_id": plan.claim.property_id, "tier": plan.tier,
        "population": plan.population, "plan_sha256": plan.sha256, "estimand": plan.estimand, "better": plan.better,
        "n_units": len(plan.units), "arms": list(plan.arms),
        "cpu_estimate_s": plan.cpu_estimate_s,
        "cpu_used_s": round(sum(r["task_metrics"]["cpu_s"] for r in rows), 1),
        "by_arm": {a: {plan.estimand: rate(g), "hes_system_metrics": system_metrics(g),
                       "graded_dispositions": dispositions(g)} for a, g in by_arm.items()},
        "by_slice": {f"{s}|{p}": {a: {plan.estimand: rate([r for r in g if (r["stratum"], r["profile"]) == (s, p)]),
                                      "graded_dispositions": dispositions(
                                          [r for r in g if (r["stratum"], r["profile"]) == (s, p)])}
                                  for a, g in by_arm.items()} for s, p in slices},
        "paired": {f"{SUBJECT}_vs_{a}": {
            plan.estimand: paired_rate_delta(by_arm[SUBJECT], by_arm[a], field=plan.estimand,
                                             resamples=resamples, seed=plan.claim.seed),
            "bounded_success": paired_rate_delta(by_arm[SUBJECT], by_arm[a], field="bounded_success",
                                                 resamples=resamples, seed=plan.claim.seed)}
            for a in plan.arms if a != SUBJECT},
        "interpretation_limit": "DEV population, small n: a development finding, not a real-world rate "
                                "and not confirmatory evidence",
    }
