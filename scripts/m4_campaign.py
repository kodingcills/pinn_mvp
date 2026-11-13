"""M4 campaign driver: executes docs/eval_program/designs/M4_preregistration.md.

    .venv/bin/python scripts/m4_campaign.py run --phase dryrun [--workers 4]
    .venv/bin/python scripts/m4_campaign.py validate --phase dryrun

Every registered parameter comes from evaluation/m4_campaign.json. `run` and
`validate` refuse when that file's SHA-256 differs from the one written in the
pre-registration, or when a scoring or system source differs from the hash the
config pins (scoring-epoch drift).

`run` per population: an M0 run whose child (`execute`) writes canonical episode
rows through `evaluation.experiment.LocalExecutor`; then HES analysis
(`hes.run`, `hes.metrics`, `hes.regression`, `hes.gates`) plus the supplemental
estimands HES does not have; then M0-sealed recordings of the selected episodes
(`record-one`) into the gitignored store, with at most one harmed and one
rescued exemplar pair per population re-recorded into the committed registry;
then `validate`. `validate` re-derives everything from the sealed rows, runs the
independent `scripts/m4_recompute.py`, regenerates any recording absent from the
store and compares its host-clock-free digests with the committed index, and
checks every recording against its scored row (score/trace).
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("MUJOCO_GL", "osmesa")

CONFIG = REPO / "evaluation" / "m4_campaign.json"
PREREG = REPO / "docs" / "eval_program" / "designs" / "M4_preregistration.md"
RESULTS = REPO / "results" / "m4"
PREREG_HASH = re.compile(r"config_sha256:\s*`([0-9a-f]{64})`")
EXCLUSION_RULES = ("mesh_artifact_named", "mesh_sensitive_slide_disturbance", "g1_unimplemented_check")
MARGIN_NOTE = " (difference within the 0.05 equivalence margin: practically equivalent)"
EXEMPLAR_MAX_BYTES = 5 * 1024 * 1024
HOST_CLOCK = "host_monotonic_ns"
NOT_RECOMPUTED = ("hes_hard_gates", "exclusions")  # HES verdicts; pre-execution schedule facts


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def git_state() -> tuple[str | None, bool | None]:
    """HEAD and dirtiness, ignoring this driver's own output roots (results/m4,
    the recordings store included), as M0 ignores its registry root."""
    from evaluation import run_registry

    return run_registry._git_state(RESULTS)


def exclude_own_outputs_from_dirty(set_attr=setattr) -> None:
    """Route M0 and LocalExecutor provenance through `git_state`, so a run from a
    clean tree records git_dirty false while it writes results/m4."""
    from evaluation import experiment, run_registry

    collect = run_registry.collect_provenance
    set_attr(experiment, "_git_state", git_state)
    set_attr(run_registry, "collect_provenance",
             lambda dependencies=(), external_sources=(), exclude_from_dirty=None:
             collect(dependencies, external_sources, RESULTS))


def registration_problems(config_path: Path = CONFIG, prereg_path: Path = PREREG) -> list[str]:
    """Empty when the config is the registered one and no pinned source drifted."""
    problems: list[str] = []
    digest = sha256_bytes(config_path.read_bytes())
    found = PREREG_HASH.findall(prereg_path.read_text(encoding="utf-8")) if prereg_path.is_file() else []
    if found != [digest]:
        problems.append(f"config sha256 {digest} is not the single value registered in {prereg_path.name}: {found}")
    cfg = load_config(config_path)
    for group in ("scoring_sources", "system_sources"):
        for rel, expected in cfg["scoring_epoch"][group].items():
            actual = sha256_bytes((REPO / rel).read_bytes())
            if actual != expected:
                problems.append(f"{group} drift: {rel} {actual} != registered {expected}")
    from hes.populations import get_population

    for key, spec in cfg["populations"].items():
        cls = get_population(spec["hes_population"])
        seen = {"id": cls.id, "seed": cls.seed, "n": cls.n, "generator": cls.generator.__name__}
        want = {k: spec[k] for k in seen}
        if seen != want:
            problems.append(f"population {key}: registered {want}, hes populations has {seen}")
    return problems


# --------------------------------------------------------------------------
# Population schedule and exclusions (decided from scenario parameters only)
# --------------------------------------------------------------------------


def generate(spec: Mapping[str, Any]) -> list:
    from hes.populations import get_population

    return get_population(spec["hes_population"]).generate()


def exclusion_reasons(scenario, cfg: Mapping[str, Any]) -> list[str]:
    from benchmarks.heb import mujoco_backend
    from benchmarks.heb.systems import build_intent
    from haplous.capabilities import negotiate

    rules = cfg["exclusions"]
    reasons = []
    if scenario.id in rules["scenario_ids"]:
        reasons.append("mesh_artifact_named")
    if scenario.path_end_m is not None and any(any(dv[:3]) for _, dv in scenario.disturbances):
        reasons.append("mesh_sensitive_slide_disturbance")
    realization = mujoco_backend.build_realization(scenario)
    intent = build_intent(scenario, realization.condition_factory(scenario, realization.bundle))
    feasibility = negotiate(intent, realization.machine.capabilities(), realization.behavior_factory())
    reasons += [f"g1_unimplemented_check:{r.code}" for r in feasibility.reasons
                if feasibility.outcome == "REFUSED" and r.code in rules["g1_unimplemented_refusal_codes"]]
    return reasons


def schedule(pop: str, phase: str, cfg: Mapping[str, Any]) -> dict[str, Any]:
    spec = cfg["populations"][pop]
    scheduled, excluded = [], {}
    for scenario in generate(spec):
        reasons = exclusion_reasons(scenario, cfg)
        if reasons:
            excluded[scenario.id] = reasons
        else:
            scheduled.append(scenario.id)
    limit = cfg["phases"][phase]["n_per_population"]
    return {"population": spec["hes_population"], "phase": phase,
            "scheduled": scheduled[:limit] if limit else scheduled, "excluded": excluded}


def exclusion_summary(excluded: Mapping[str, Sequence[str]]) -> dict[str, dict[str, Any]]:
    """Count and scenario ids per registered exclusion rule (section 7), zero counts included."""
    out: dict[str, dict[str, Any]] = {}
    for rule in EXCLUSION_RULES:
        ids = sorted(sid for sid, reasons in excluded.items() if any(r.split(":")[0] == rule for r in reasons))
        out[rule] = {"count": len(ids), "scenario_ids": ids}
    return out


def shard_scenarios(root: str, manifest, shard) -> list:
    sched = json.loads((Path(root) / "schedule.json").read_text(encoding="utf-8"))
    from hes.populations import get_population

    by_id = {s.id: s for s in get_population(sched["population"].split("@")[0]).generate()}
    scenarios = [by_id[sid] for sid in sched["scheduled"]]
    return scenarios[shard.population_offset: shard.population_offset + shard.population_count]


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def commands_digest(times: Sequence[float]) -> str:
    return sha256_bytes(json.dumps([float(t) for t in times]).encode("utf-8"))


class _Timed:
    """Per-episode CPU cost of the system run; outcome untouched."""

    def __init__(self, inner, sink: dict) -> None:
        self.name, self._inner, self._sink = inner.name, inner, sink

    def run(self, realization, scenario):
        t0 = time.process_time()
        run = self._inner.run(realization, scenario)
        self._sink[(scenario.id, run.system)] = time.process_time() - t0
        return run


def evaluate_with_digest(scenarios, systems):
    """benchmarks.heb.runner.evaluate, plus the digest of the commands the
    machine applied (score side of the score/trace check) and CPU seconds."""
    from benchmarks.heb.runner import evaluate

    cpu: dict = {}
    records = evaluate(scenarios, [_Timed(s, cpu) for s in systems])
    for record in records:
        times = record.task_metrics.get("applied_command_times", ())
        record.task_metrics["applied_commands_sha256"] = commands_digest(times)
        record.task_metrics["cpu_s"] = cpu[(record.scenario_id, record.system)]
    return records


def episodes_run_id(phase: str, pop: str) -> str:
    return f"m4-{phase}-{pop}-episodes"


def cmd_execute(args: argparse.Namespace) -> int:
    import haplous
    from evaluation.experiment import (
        ExperimentClass,
        ExperimentManifest,
        LocalExecutor,
        PopulationSpec,
        ShardingSpec,
        ShardStatus,
    )
    from hes.populations import get_population

    cfg = load_config()
    spec = cfg["populations"][args.population]
    root = Path(os.environ["M0_ARTIFACT_DIR"]) / "experiment"
    root.mkdir(parents=True)
    sched = schedule(args.population, args.phase, cfg)
    (root / "schedule.json").write_text(json.dumps(sched, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cls = get_population(spec["hes_population"])
    manifest = ExperimentManifest(
        experiment_id=f"m4-{cfg['scoring_epoch']['name']}-{args.phase}-{args.population}",
        description=f"M4 {args.phase}: {cls.id}",
        benchmark="heb",
        benchmark_version=cfg["benchmark_version"],
        haplous_version=haplous.__version__,
        systems=tuple(cfg["arms"]["all"]),
        population=PopulationSpec(n=len(sched["scheduled"]), seed=cls.seed, slide_fraction=cls.slide_fraction),
        sharding=ShardingSpec(shard_size=cfg["sharding"]["shard_size"]),
        experiment_class=ExperimentClass.DEVELOPMENT,
        metrics=tuple(cfg["metrics"]),
        reference_arm=cfg["arms"]["direct"],
    )
    executor = LocalExecutor(
        root,
        evaluate_fn=evaluate_with_digest,
        scenario_fn=functools.partial(shard_scenarios, str(root)),
        row_metadata={
            "population_id": cls.id,
            "phase": args.phase,
            "scoring_epoch": cfg["scoring_epoch"]["name"],
            "config_sha256": sha256_bytes(CONFIG.read_bytes()),
            "m0_run_id": episodes_run_id(args.phase, args.population),
        },
    )
    for _ in range(cfg["stopping"]["max_attempts_per_shard"]):
        results = executor.run(manifest, workers=args.workers, verbose=True)
        if all(r.status is ShardStatus.COMPLETE for r in results):
            return 0
    return 1


def m0_record(registry, run_id: str, purpose: str, command: list[str], config: dict) -> dict:
    import mujoco

    from evaluation.run_registry import record_run

    return record_run(
        registry, run_id=run_id, track="sim_policy_free", purpose=purpose, command=command,
        config=config, simulator={"name": "mujoco", "version": mujoco.__version__},
        dependencies=("numpy", "mujoco", "pyarrow", "jsonschema", "cryptography"),
        cwd=REPO, env={"PYTHONPATH": str(REPO), "MUJOCO_GL": "osmesa"},
    )


def cmd_record_one(args: argparse.Namespace) -> int:
    from benchmarks.heb import mujoco_backend
    from benchmarks.heb.metrics import grade_run
    from benchmarks.heb.systems import system_by_name
    from benchmarks.recording import EpisodeRecorder, run_episode

    cfg = load_config()
    out = Path(os.environ["M0_ARTIFACT_DIR"])
    scenario = next(s for s in generate(cfg["populations"][args.population]) if s.id == args.scenario_id)
    if args.arm == "haplous_mediated":
        run, _, _ = run_episode(scenario, out)
    else:
        recorder = EpisodeRecorder(out)
        realization = mujoco_backend.build_realization(scenario, machine_factory=recorder.machine_factory)
        run = system_by_name(args.arm).run(realization, scenario)
        recorder.finish(scenario, run, None, grade_run(scenario, realization.bundle, run))
    times = [float(t) for t in run.detail.get("applied_command_times", ())]
    (out / "applied_commands.json").write_text(
        json.dumps({"applied_command_times": times, "sha256": commands_digest(times)}) + "\n", encoding="utf-8")
    return 0


# --------------------------------------------------------------------------
# Analysis: HES where it has the metric, supplemental estimands otherwise
# --------------------------------------------------------------------------


def _sid(row: Mapping[str, Any]) -> str:
    return str(row["scenario_id"])


def _refused(row: Mapping[str, Any]) -> bool:
    return row["reported_disposition"] == "REFUSED"


def duration_s(row: Mapping[str, Any]) -> float:
    """Simulated seconds of one episode (last observation's source time); 0 when refused."""
    if _refused(row):
        return 0.0
    return float((row.get("task_metrics") or {}).get("seating_time_s") or 0.0)


def check_aligned(rows: Sequence[Mapping[str, Any]], arms: Sequence[str]) -> None:
    """Exactly one row per (scenario, arm) for every registered arm."""
    seen: dict[str, set[str]] = {}
    for row in rows:
        arm_set = seen.setdefault(_sid(row), set())
        if row["arm"] in arm_set:
            raise ValueError(f"duplicate row {_sid(row)}/{row['arm']}")
        arm_set.add(row["arm"])
    bad = {s: sorted(a) for s, a in seen.items() if a != set(arms)}
    if bad:
        raise ValueError(f"unpaired scenarios: {dict(list(bad.items())[:5])}")


def time_per_bounded_success(rows: Sequence[Mapping[str, Any]], resets: Sequence[float]) -> dict[str, Any]:
    executed = sum(1 for r in rows if not _refused(r))
    successes = sum(1 for r in rows if r.get("bounded_success"))
    sim = sum(duration_s(r) for r in rows)
    return {
        "sim_seconds_all_episodes": sim,
        "n_executed": executed,
        "n_bounded_success": successes,
        "n_executed_without_duration": sum(
            1 for r in rows if not _refused(r) and (r.get("task_metrics") or {}).get("seating_time_s") is None),
        "by_reset_s": {str(reset): (sim + reset * executed) / successes if successes else None for reset in resets},
    }


def intervention_overhead(rows, direct_rows) -> dict[str, Any]:
    direct = {_sid(r): r for r in direct_rows}
    taxes = [duration_s(r) - duration_s(direct[_sid(r)]) for r in rows
             if r.get("bounded_success") and direct[_sid(r)].get("bounded_success")]
    return {"median_sim_s": statistics.median(taxes) if taxes else None, "n": len(taxes)}


def paired_classes(cand_rows, ref_rows) -> dict[str, Any]:
    ref = {_sid(r): bool(r.get("bounded_success")) for r in ref_rows}
    out: dict[str, Any] = {"rescued": [], "harmed": [], "both": 0, "neither": 0}
    for row in sorted(cand_rows, key=_sid):
        c, r = bool(row.get("bounded_success")), ref[_sid(row)]
        if c and not r:
            out["rescued"].append(_sid(row))
        elif r and not c:
            out["harmed"].append(_sid(row))
        else:
            out["both" if c else "neither"] += 1
    return out


def stratum(row: Mapping[str, Any]) -> str:
    tags = [str(t) for t in row.get("tags") or ()]
    return next((t for t in tags if t.startswith("stratum:")), next((t for t in tags if t.startswith("task_")), "none"))


def decide(lo: float, hi: float, margin: float) -> str:
    """Section 6: ordered rules, the first match wins."""
    if lo > 0:
        return "Haplous better" + (MARGIN_NOTE if hi <= margin else "")
    if hi < 0:
        return "G1 better" + (MARGIN_NOTE if lo >= -margin else "")
    if -margin <= lo and hi <= margin:
        return "tie (equivalent within 0.05)"
    return "inconclusive at this sample size"


def population_metrics(run, cfg: Mapping[str, Any], pop: str) -> dict[str, Any]:
    from hes.metrics import _prop, authority_metrics, noninterference_metrics, system_metrics
    from hes.model import AttainabilityAxis
    from hes.regression import paired_rate_delta

    arms_cfg, stats = cfg["arms"], cfg["statistics"]
    by_arm = run.by_arm
    check_aligned(run.rows, arms_cfg["all"])
    attainability = run.attainability()
    direct_rows = by_arm[arms_cfg["direct"]]
    arms: dict[str, Any] = {}
    for arm in arms_cfg["all"]:
        rows = by_arm[arm]
        sm, am = system_metrics(rows), authority_metrics(rows, attainability)
        ni = noninterference_metrics(rows, direct_rows) if arm != arms_cfg["direct"] else None
        recovery = [r for r in rows if "recovery_authorized" in (r.get("tags") or ())]
        arms[arm] = {
            "task_success": sm["task_success"],
            "bounded_success": sm["bounded_success"],
            "coverage": sm["coverage"],
            "violation_given_acceptance": am["violation_given_acceptance"],
            "false_refusal_rate": am["false_refusal_rate"],
            "refusals_attainability_unknown": sum(
                1 for r in rows if _refused(r) and attainability[_sid(r)].axis is AttainabilityAxis.UNKNOWN),
            "preservation_vs_direct": ni["preservation_rate"] if ni else None,
            "unnecessary_intervention_vs_direct": ni["unnecessary_intervention_rate"] if ni else None,
            "recovery_bounded_success": _prop(sum(1 for r in recovery if r.get("bounded_success")), len(recovery)),
            "time_per_bounded_success": time_per_bounded_success(rows, cfg["time"]["reset_s"]),
            "intervention_overhead_vs_direct": intervention_overhead(rows, direct_rows) if ni else None,
        }
    paired: dict[str, Any] = {}
    for cand, ref in cfg["comparisons"]:
        classes = paired_classes(by_arm[cand], by_arm[ref])
        delta = paired_rate_delta(by_arm[cand], by_arm[ref], field="bounded_success",
                                  resamples=stats["bootstrap_resamples"], seed=stats["bootstrap_seed"])
        paired[f"{cand}_vs_{ref}"] = {**classes, "delta_bounded_success": delta}
    strata: dict[str, Any] = {}
    for name in sorted({stratum(r) for r in run.rows}):
        group = {arm: [r for r in by_arm[arm] if stratum(r) == name] for arm in arms_cfg["all"]}
        strata[name] = {
            "n_scenarios": len(group[arms_cfg["direct"]]),
            "bounded_success": {arm: _prop(sum(1 for r in g if r.get("bounded_success")), len(g))
                                for arm, g in group.items()},
            "paired": {f"{c}_vs_{r}": {k: len(v) if isinstance(v, list) else v
                                       for k, v in paired_classes(group[c], group[r]).items()}
                       for c, r in cfg["comparisons"]},
        }
    counts: dict[str, int] = {}
    for value in attainability.values():
        counts[value.axis.value] = counts.get(value.axis.value, 0) + 1
    confirmatory = {}
    for item in cfg["confirmatory"]:
        if item["population"] == pop:
            pair = paired[f"{item['candidate']}_vs_{item['reference']}"]
            confirmatory[item["id"]] = {
                **item,
                "delta": pair["delta_bounded_success"],
                "decision": decide(pair["delta_bounded_success"]["ci_low"], pair["delta_bounded_success"]["ci_high"],
                                   stats["equivalence_margin"]),
                "n_harmed": len(pair["harmed"]),
                "n_rescued": len(pair["rescued"]),
            }
    return {"n_scenarios": len(run.by_scenario), "attainability_counts": dict(sorted(counts.items())),
            "arms": arms, "paired": paired, "strata": strata, "confirmatory": confirmatory}


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def phase_paths(phase: str) -> dict[str, Path]:
    root = RESULTS / phase
    return {"root": root, "m0": root / "m0", "rows": root / "raw_rows.jsonl",
            "official": root / "official_metrics.json", "replays": root / "replay_index.jsonl",
            "cost": root / "cost.json", "store": RESULTS / "recordings_store" / phase / "m0"}


def analyze(phase: str, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Official metrics and HES bundles from the M0-sealed rows. Pure function of the rows."""
    from evaluation.experiment import load_completed_episodes
    from evaluation.run_registry import Registry
    from hes.bundle import build_bundle
    from hes.run import load_run

    paths = phase_paths(phase)
    registry = Registry(paths["m0"])
    official: dict[str, Any] = {"phase": phase, "scoring_epoch": cfg["scoring_epoch"]["name"],
                                "config_sha256": sha256_bytes(CONFIG.read_bytes()), "populations": {}}
    bundles, all_rows = {}, []
    for pop in cfg["populations"]:
        root = registry.run_dir(episodes_run_id(phase, pop)) / "artifacts" / "experiment"
        rows = load_completed_episodes(root)
        run = load_run(root)
        if run.baseline_arm != cfg["arms"]["direct"] or len(run.rows) != len(rows):
            raise ValueError(f"{pop}: HES baseline {run.baseline_arm} or row count mismatch")
        bundle = build_bundle(run, runtime_arm=cfg["arms"]["runtime"])
        metrics = population_metrics(run, cfg, pop)
        metrics["hes_hard_gates"] = bundle["hard_gates"]
        excluded = json.loads((root / "schedule.json").read_text(encoding="utf-8"))["excluded"]
        metrics["exclusions"] = exclusion_summary(excluded)
        official["populations"][pop] = metrics
        bundles[pop] = bundle
        all_rows += rows
    all_rows.sort(key=lambda r: (r["metadata"]["population_id"], r["scenario_id"], r["arm"]))
    return {"official": _jsonable(official), "bundles": bundles, "rows": all_rows}


def write_analysis(phase: str, result: Mapping[str, Any]) -> None:
    from hes.bundle import to_json
    from hes.report import render_markdown

    paths = phase_paths(phase)
    for pop, bundle in result["bundles"].items():
        out = paths["root"] / pop
        out.mkdir(parents=True, exist_ok=True)
        (out / "hes_bundle.json").write_text(to_json(bundle), encoding="utf-8")
        (out / "HES_REPORT.md").write_text(render_markdown(bundle), encoding="utf-8")
    paths["rows"].write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in result["rows"]),
                             encoding="utf-8")
    paths["official"].write_text(json.dumps(result["official"], indent=2, sort_keys=True) + "\n", encoding="utf-8")


def select_recordings(phase: str, official: Mapping[str, Any], cfg: Mapping[str, Any],
                      scheduled: Mapping[str, Sequence[str]]) -> list[dict[str, Any]]:
    """Every harmed then rescued pair of the recorded comparisons, capped per
    population, then the phase's pipeline-probe pairs (first scheduled
    scenarios). Order is fixed by class then sha256(scenario_id), never by
    outcome magnitude."""
    rec = cfg["recording"]
    entries: dict[tuple[str, str, str], dict[str, Any]] = {}
    for pop, metrics in official["populations"].items():
        chosen = [(sid, f"{kind}:{cand}_vs_{ref}", (cand, ref))
                  for kind in ("harmed", "rescued") for cand, ref in rec["comparisons"]
                  for sid in sorted(metrics["paired"][f"{cand}_vs_{ref}"][kind],
                                    key=lambda s: sha256_bytes(s.encode()))]
        pairs: list[tuple[str, str, tuple[str, str]]] = []
        for item in chosen:
            if item[0] not in {p[0] for p in pairs} and len(pairs) < rec["cap_pairs_per_population"]:
                pairs.append(item)
        probe = tuple(rec["comparisons"][0])
        for sid in scheduled[pop][: cfg["phases"][phase]["probe_pairs_per_population"]]:
            if sid not in {p[0] for p in pairs}:
                pairs.append((sid, "pipeline_probe", probe))
        for sid, reason, arms in pairs:
            for arm in arms:
                entry = entries.setdefault((pop, sid, arm),
                                           {"population": pop, "scenario_id": sid, "arm": arm, "reasons": []})
                entry["reasons"].append(reason)
    return list(entries.values())


def recording_run_id(phase: str, entry: Mapping[str, Any]) -> str:
    return f"m4-{phase}-{entry['population']}-{entry['scenario_id']}-{entry['arm']}"


def record_args(entry: Mapping[str, Any]) -> list[str]:
    return ["record-one", "--population", entry["population"], "--scenario-id", entry["scenario_id"],
            "--arm", entry["arm"]]


def sim_digest(path: Path) -> str:
    """SHA-256 of a recording artifact without the recorder's host clock, so a
    regenerated deterministic episode reproduces it exactly."""
    if path.suffix == ".arrow":
        import pyarrow as pa

        table = pa.ipc.open_file(path).read_all()
        table = table.drop_columns([HOST_CLOCK]) if HOST_CLOCK in table.schema.names else table
        sink = pa.BufferOutputStream()
        with pa.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
        return sha256_bytes(sink.getvalue().to_pybytes())
    if path.suffix == ".jsonl":
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        return sha256_bytes(json.dumps([{k: v for k, v in x.items() if k != HOST_CLOCK} for x in lines],
                                       sort_keys=True).encode("utf-8"))
    return sha256_bytes(path.read_bytes())


def artifact_digests(artifacts: Path) -> dict[str, dict[str, Any]]:
    """Every file the recorder wrote (M0's stdout/stderr capture excluded)."""
    return {p.relative_to(artifacts).as_posix(): {"sha256": sha256_bytes(p.read_bytes()), "sim_sha256": sim_digest(p),
                                                  "bytes": p.stat().st_size}
            for p in sorted(artifacts.rglob("*")) if p.is_file() and p.name not in ("stdout.txt", "stderr.txt")}


def seal_recording(registry, phase: str, entry: Mapping[str, Any], config_sha: str) -> Path:
    run_id = recording_run_id(phase, entry)
    keys = ("population", "scenario_id", "arm", "reasons")
    result = m0_record(registry, run_id, f"M4 {phase} recording {entry['scenario_id']} ({entry['arm']})",
                       [sys.executable, str(REPO / "scripts" / "m4_campaign.py"), *record_args(entry)],
                       {**{k: entry[k] for k in keys}, "phase": phase, "config_sha256": config_sha})
    if result["state"] != "VERIFIED" or result["outcome"] != "completed":
        raise RuntimeError(f"recording {run_id} did not verify: {result['state']} {result['outcome']}")
    return registry.run_dir(run_id) / "artifacts"


def choose_exemplars(entries: Sequence[Mapping[str, Any]], cfg: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Per population, the first harmed and the first rescued pair of the primary
    comparison (selection order) whose recordings are each under 5 MB."""
    cand, ref = cfg["recording"]["comparisons"][0]
    chosen: list[Mapping[str, Any]] = []
    for pop in dict.fromkeys(e["population"] for e in entries):
        for kind in ("harmed", "rescued"):
            pairs: dict[str, list[Mapping[str, Any]]] = {}
            for e in entries:
                if e["population"] == pop and f"{kind}:{cand}_vs_{ref}" in e["reasons"]:
                    pairs.setdefault(e["scenario_id"], []).append(e)
            chosen += next((pair for pair in pairs.values()
                            if all(sum(a["bytes"] for a in e["artifacts"].values()) < EXEMPLAR_MAX_BYTES
                                   for e in pair)), [])
    return chosen


def record(phase: str, official: Mapping[str, Any], cfg: Mapping[str, Any],
           scheduled: Mapping[str, Sequence[str]]) -> None:
    """Bulk recordings go to the gitignored store; the committed index carries
    their M0 run ids, digests and regeneration commands. Exemplars are sealed a
    second time into the committed registry."""
    from evaluation.run_registry import Registry

    paths = phase_paths(phase)
    store, committed = Registry(paths["store"]), Registry(paths["m0"])
    entries = select_recordings(phase, official, cfg, scheduled)
    for entry in entries:
        run_id = recording_run_id(phase, entry)
        artifacts = seal_recording(store, phase, entry, official["config_sha256"])
        entry.update({
            "m0_run_id": run_id, "episodes_m0_run_id": episodes_run_id(phase, entry["population"]),
            "registry": paths["store"].relative_to(REPO).as_posix(), "exemplar_registry": None,
            "artifacts": artifact_digests(artifacts),
            "regenerate_command": "M0_ARTIFACT_DIR=<empty dir> MUJOCO_GL=osmesa PYTHONPATH=. .venv/bin/python "
                                  "scripts/m4_campaign.py " + " ".join(record_args(entry)),
            "replay_command": f".venv/bin/python -m evaluation.replay {run_id} --root "
                              f"{paths['store'].relative_to(REPO)} --mp4 replays/{run_id}.mp4"})
    for entry in choose_exemplars(entries, cfg):
        seal_recording(committed, phase, entry, official["config_sha256"])
        entry["exemplar_registry"] = paths["m0"].relative_to(REPO).as_posix()
    paths["replays"].write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in entries), encoding="utf-8")


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

_OUTCOME_FIELDS = (("reported_disposition", "disposition"), ("graded_disposition", "graded_disposition"),
                   ("task_success", "task_success"), ("bounded_success", "bounded_success"),
                   ("reason", "reason"), ("refusal_codes", "refusal_codes"))


def score_trace_problems(row: Mapping[str, Any], artifacts: Path) -> list[str]:
    """The scored row against the M0-sealed recording of the same (scenario, arm):
    outcome fields, violation count, and the commands the machine applied."""
    meta = json.loads((artifacts / "metadata.json").read_text(encoding="utf-8"))
    applied = json.loads((artifacts / "applied_commands.json").read_text(encoding="utf-8"))
    out = meta["outcome"]
    where = f"{row['scenario_id']}/{row['arm']}"
    problems = [f"{where}: {a}={row.get(a)!r} but trace {b}={out.get(b)!r}" for a, b in _OUTCOME_FIELDS
                if (list(row.get(a)) if a == "refusal_codes" else row.get(a)) != out.get(b)]
    if (meta["scenario_id"], meta["system"]) != (row["scenario_id"], row["arm"]):
        problems.append(f"{where}: trace is {meta['scenario_id']}/{meta['system']}")
    if int(row["n_hard_violations"]) != len(out["hard_violations"]):
        problems.append(f"{where}: n_hard_violations {row['n_hard_violations']} != trace {len(out['hard_violations'])}")
    if commands_digest(applied["applied_command_times"]) != applied["sha256"]:
        problems.append(f"{where}: applied_commands.json digest does not match its own times")
    if row["task_metrics"].get("applied_commands_sha256") != applied["sha256"]:
        problems.append(f"{where}: machine-applied commands differ between scored run and recording")
    return problems


def compare_metrics(a: Any, b: Any, path: str = "", tol: float = 1e-9) -> list[str]:
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        out = [f"{path}.{k}: missing on one side" for k in sorted(set(a) ^ set(b))]
        for k in sorted(set(a) & set(b)):
            out += compare_metrics(a[k], b[k], f"{path}.{k}", tol)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        return [p for i, (x, y) in enumerate(zip(a, b, strict=True)) for p in compare_metrics(x, y, f"{path}[{i}]", tol)]
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None or isinstance(a, bool) or isinstance(b, bool) or abs(a - b) > tol:
            return [f"{path}: {a!r} != {b!r}"]
        return []
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def validate(phase: str, cfg: Mapping[str, Any]) -> list[str]:
    from evaluation.run_registry import Registry
    from hes.report import check_report

    paths = phase_paths(phase)
    registry = Registry(paths["m0"])
    problems = registration_problems()
    for pop in cfg["populations"]:
        problems += [f"M0 {episodes_run_id(phase, pop)}: {p}" for p in registry.check(episodes_run_id(phase, pop))]
    if problems:
        return problems
    result = analyze(phase, cfg)
    committed_rows = [json.loads(x) for x in paths["rows"].read_text(encoding="utf-8").splitlines() if x.strip()]
    if committed_rows != _jsonable(result["rows"]):
        problems.append("raw_rows.jsonl differs from the M0-sealed shard rows")
    committed = json.loads(paths["official"].read_text(encoding="utf-8"))
    problems += [f"official_metrics.json vs re-analysis{p}" for p in compare_metrics(committed, result["official"])]
    for pop, bundle in result["bundles"].items():
        ok, message = check_report(paths["root"] / pop / "HES_REPORT.md", bundle)
        if not ok:
            problems.append(f"{pop}/HES_REPORT.md: {message}")
    recomputed = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "m4_recompute.py"), "--rows", str(paths["rows"]),
         "--config", str(CONFIG)], capture_output=True, text=True, check=False)
    if recomputed.returncode != 0:
        problems.append(f"m4_recompute failed: {recomputed.stderr[-2000:]}")
    else:
        independent = json.loads(recomputed.stdout)
        official = {pop: {k: v for k, v in m.items() if k not in NOT_RECOMPUTED}
                    for pop, m in committed["populations"].items()}
        problems += [f"independent recompute{p}" for p in compare_metrics(official, independent["populations"])]
    rows = {(r["metadata"]["population_id"], r["scenario_id"], r["arm"]): r for r in committed_rows}
    store = Registry(paths["store"])
    for line in paths["replays"].read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        row = rows.get((cfg["populations"][entry["population"]]["id"], entry["scenario_id"], entry["arm"]))
        if row is None:
            problems.append(f"{entry['m0_run_id']}: no scored row")
            continue
        if entry["exemplar_registry"]:
            problems += recording_problems(registry, entry, row)
        if store.state(entry["m0_run_id"]) is None:
            try:  # absent bulk recording: regenerate, then compare digests like a present one
                seal_recording(store, phase, entry, committed["config_sha256"])
            except RuntimeError as exc:
                problems.append(str(exc))
                continue
        problems += recording_problems(store, entry, row)
    return problems


def recording_problems(registry, entry: Mapping[str, Any], row: Mapping[str, Any]) -> list[str]:
    """M0 integrity, host-clock-free digests against the committed index, and score/trace."""
    run_id = entry["m0_run_id"]
    artifacts = registry.run_dir(run_id) / "artifacts"
    problems = [f"M0 {run_id}: {p}" for p in registry.check(run_id)]
    if problems:
        return problems
    actual = artifact_digests(artifacts)
    for path in sorted(actual.keys() | entry["artifacts"].keys()):
        if actual.get(path, {}).get("sim_sha256") != entry["artifacts"].get(path, {}).get("sim_sha256"):
            problems.append(f"{run_id} in {registry.root}: {path} digest differs from replay_index.jsonl")
    return problems + score_trace_problems(row, artifacts)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def cost_summary(phase: str, cfg: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from evaluation.run_registry import Registry

    registry = Registry(phase_paths(phase)["m0"])
    per_arm: dict[str, list[float]] = {}
    for row in rows:
        per_arm.setdefault(row["arm"], []).append(float(row["task_metrics"]["cpu_s"]))
    shard_wall, scenarios = 0.0, 0
    for pop in cfg["populations"]:
        root = registry.run_dir(episodes_run_id(phase, pop)) / "artifacts" / "experiment"
        for status in root.glob("shards/*/status.json"):
            data = json.loads(status.read_text(encoding="utf-8"))
            shard_wall += float(data["wall_s"])
        scenarios += len(json.loads((root / "schedule.json").read_text(encoding="utf-8"))["scheduled"])
    per_scenario = shard_wall / scenarios
    planned = sum(len(schedule(pop, "campaign", cfg)["scheduled"]) for pop in cfg["populations"])
    worst = sum(max(v) for v in per_arm.values())
    workers = (1, cfg["sharding"]["workers"])
    return {
        "note": "CPU cost only; outcomes are not interpreted in a dry run",
        "cpu_s_per_episode_by_arm": {a: {"mean": statistics.fmean(v), "max": max(v), "n": len(v)}
                                     for a, v in sorted(per_arm.items())},
        "shard_wall_s_per_scenario_all_arms": per_scenario,
        "campaign_scenarios_scheduled": planned,
        "projected_campaign_wall_h_by_workers": {str(w): planned * per_scenario / w / 3600.0 for w in workers},
        "worst_case_wall_h_by_workers": {
            "basis": "every scenario costs the slowest observed episode of every arm",
            **{str(w): planned * worst / w / 3600.0 for w in workers}},
    }


def cmd_run(args: argparse.Namespace) -> int:
    from evaluation.run_registry import Registry

    problems = registration_problems()
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    cfg = load_config()
    paths = phase_paths(args.phase)
    if paths["root"].exists():
        if not args.fresh:
            print(f"{paths['root']} exists; pass --fresh to replace it", file=sys.stderr)
            return 2
        shutil.rmtree(paths["root"])
    shutil.rmtree(paths["store"].parent, ignore_errors=True)  # bulk recordings of an earlier run of this phase
    registry = Registry(paths["m0"])
    first_ids: dict[str, list[str]] = {}
    for pop in cfg["populations"]:
        run_id = episodes_run_id(args.phase, pop)
        command = [sys.executable, str(REPO / "scripts" / "m4_campaign.py"), "execute",
                   "--phase", args.phase, "--population", pop, "--workers", str(args.workers)]
        result = m0_record(registry, run_id, f"M4 {args.phase} episodes: {pop}", command,
                           {"phase": args.phase, "population": pop, "config_sha256": sha256_bytes(CONFIG.read_bytes())})
        if result["state"] != "VERIFIED" or result["outcome"] != "completed":
            print(f"{run_id}: {result['state']} {result['outcome']}", file=sys.stderr)
            return 1
        sched = registry.run_dir(run_id) / "artifacts" / "experiment" / "schedule.json"
        first_ids[pop] = json.loads(sched.read_text(encoding="utf-8"))["scheduled"]
    result = analyze(args.phase, cfg)
    write_analysis(args.phase, result)
    record(args.phase, result["official"], cfg, first_ids)
    paths["cost"].write_text(json.dumps(cost_summary(args.phase, cfg, result["rows"]), indent=2, sort_keys=True)
                             + "\n", encoding="utf-8")
    problems = validate(args.phase, cfg)
    print(json.dumps({p: m["exclusions"] for p, m in result["official"]["populations"].items()}, indent=2))
    print("\n".join(problems) if problems else f"{args.phase}: pipeline ran; validation passed")
    return 1 if problems else 0


def cmd_validate(args: argparse.Namespace) -> int:
    problems = validate(args.phase, load_config())
    print("\n".join(problems) if problems else f"{args.phase}: validation passed")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="m4_campaign")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--phase", required=True, choices=("dryrun", "campaign"))
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--fresh", action="store_true")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("validate")
    p.add_argument("--phase", required=True, choices=("dryrun", "campaign"))
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser("execute", help="M0 child: run one population's episodes")
    p.add_argument("--phase", required=True)
    p.add_argument("--population", required=True)
    p.add_argument("--workers", type=int, default=1)
    p.set_defaults(func=cmd_execute)
    p = sub.add_parser("record-one", help="M0 child: record one (scenario, arm)")
    p.add_argument("--population", required=True)
    p.add_argument("--scenario-id", required=True)
    p.add_argument("--arm", required=True)
    p.set_defaults(func=cmd_record_one)
    args = parser.parse_args(argv)
    exclude_own_outputs_from_dirty()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
