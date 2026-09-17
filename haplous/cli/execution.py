from __future__ import annotations

import dataclasses
from argparse import Namespace
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Sequence

from haplous.cli.benchmarks import get_benchmark, registered_benchmarks
from haplous.cli.commands import SYSTEM_NAMES
from haplous.cli.registry import JsonMapping, JsonValue, RunMetadata, RunRegistry


METRIC_IMPLEMENTATION_VERSION = "heb-metrics-0.2.0"


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    benchmark_name: str
    benchmark_version: str
    framework: str
    task_id: str
    system_names: Sequence[str]
    scenarios: Sequence
    seed: int
    runtime_config: JsonMapping


def _system(name: str):
    from benchmarks.heb.systems import system_by_name

    return system_by_name(name)


def _scenario_axes(scenario) -> dict[str, JsonValue]:
    axes = {}
    for name in (
        "initial_offset_m", "initial_bearing_rad", "facet_count",
        "clearance_delta_m", "plate_thickness_m", "max_force_n",
        "min_force_n", "deadline_s", "receive_delay_s",
    ):
        value = getattr(scenario, name)
        if value is not None:
            axes[name] = value
    return axes


def _trial_rows(scenarios, records) -> list[JsonMapping]:
    by_id = {scenario.id: scenario for scenario in scenarios}
    rows = []
    for record in records:
        row = dataclasses.asdict(record)
        row["scenario_axes"] = _scenario_axes(by_id[record.scenario_id])
        rows.append(row)
    return rows


def _summary_json(scenarios, records, *, benchmark_version: str) -> JsonMapping:
    from benchmarks.heb.metrics import summarize

    systems = {}
    for name, summary in summarize(scenarios, records).items():
        systems[name] = {
            "trials": summary.trials,
            "coverage": summary.coverage,
            "bounded_success_rate_among_accepted": summary.bounded_success_rate,
            "task_successes": summary.task_successes,
            "refusals": summary.refusals,
            "refusal_precision": summary.refusal_precision,
            "refusal_recall": summary.refusal_recall,
            "completed_with_hard_violation_trials": summary.completed_with_hard_violation_trials,
            "breach_trials": summary.breach_trials,
            "recovery_success_rate": summary.recovery_success_rate,
            "completion_percentiles_s": summary.completion_percentiles(),
        }
    return {
        "benchmark_version": benchmark_version,
        "n_scenarios": len(scenarios),
        "n_trials": len(records),
        "systems": systems,
    }


def _write_evaluation(request: EvaluationRequest):
    from benchmarks.heb.runner import evaluate

    started_at = datetime.now(UTC).isoformat()
    records = evaluate(request.scenarios, [_system(name) for name in request.system_names])
    summary = _summary_json(
        request.scenarios, records, benchmark_version=request.benchmark_version
    )
    metadata = RunMetadata(
        benchmark_name=request.benchmark_name,
        benchmark_version=request.benchmark_version,
        framework=request.framework,
        task_id=request.task_id,
        system_variant=",".join(request.system_names),
        controller_config={"systems": list(request.system_names)},
        runtime_config=request.runtime_config,
        seed=request.seed,
        scenarios=[dataclasses.asdict(scenario) for scenario in request.scenarios],
        metric_implementation_version=METRIC_IMPLEMENTATION_VERSION,
        started_at_utc=started_at,
    )
    return RunRegistry().write_run(metadata, _trial_rows(request.scenarios, records), summary)


def _harness_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("benchmarks") is not None


_HARNESS_MESSAGE = (
    "the run/eval commands execute the HEB evaluation harness, which is not\n"
    "  part of the installed package. Use the self-contained quickstart:\n"
    "      haplous demo\n"
    "  or run from a repository checkout that provides the benchmarks package."
)


def run_command(args: Namespace) -> int:
    if not _harness_available():
        print(f"benchmark harness not installed:\n{_HARNESS_MESSAGE}")
        return 3
    try:
        benchmark = get_benchmark(args.benchmark)
    except KeyError:
        print(f"unknown benchmark: {args.benchmark}")
        return 2
    scenarios = benchmark.load_scenarios()
    scenarios = (
        [scenario for scenario in scenarios if scenario.id == args.scenario]
        if args.scenario else scenarios[:1]
    )
    if not scenarios:
        print(f"scenario not found: {args.scenario}")
        return 2
    if args.system not in SYSTEM_NAMES:
        print(f"unknown system: {args.system}")
        return 2
    if args.ui:
        print("viewer: not wired for this benchmark")
    run = _write_evaluation(EvaluationRequest(
        benchmark.name, benchmark.version, benchmark.framework,
        benchmark.task_id, (args.system,), scenarios, args.seed, {"ui": args.ui},
    ))
    print(f"run_id: {run.run_id}")
    print(f"summary: {run.summary}")
    return 0


def eval_command(args: Namespace) -> int:
    if not _harness_available():
        print(f"benchmark harness not installed:\n{_HARNESS_MESSAGE}")
        return 3
    external = [item for item in registered_benchmarks() if item.framework != "internal"]
    if args.suite == "external" and not external:
        print("external benchmarks: none registered")
        return 0
    if args.suite != "internal":
        print(f"unknown suite: {args.suite}")
        return 2
    system_names = tuple(name.strip() for name in args.systems.split(",") if name.strip())
    if any(name not in SYSTEM_NAMES for name in system_names):
        print("unknown system in --systems")
        return 2
    insertion = get_benchmark("internal-insertion")
    slide = get_benchmark("internal-slide")
    scenarios = insertion.load_scenarios()[:2] + slide.load_scenarios()[:2]
    run = _write_evaluation(EvaluationRequest(
        "internal", insertion.version, "internal", "heb-dev-small",
        system_names, scenarios, 0,
        {"headless": args.headless, "scenario_count": len(scenarios)},
    ))
    print(f"run_id: {run.run_id}")
    print(f"summary: {run.summary}")
    return 0
