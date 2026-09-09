from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Final


@dataclass(frozen=True, slots=True)
class Benchmark:
    name: str
    version: str
    framework: str
    task_id: str
    load_scenarios: Callable[[], list]


def _load_insertion() -> list:
    from benchmarks.heb.insertion import insertion_scenarios

    return insertion_scenarios()


def _load_slide() -> list:
    from benchmarks.heb.sliding import sliding_scenarios

    return sliding_scenarios()


_BENCHMARKS: Final[dict[str, Benchmark]] = {}


def register_benchmark(benchmark: Benchmark) -> None:
    _BENCHMARKS[benchmark.name] = benchmark


def get_benchmark(name: str) -> Benchmark:
    return _BENCHMARKS[name]


def registered_benchmarks() -> tuple[Benchmark, ...]:
    return tuple(_BENCHMARKS[name] for name in sorted(_BENCHMARKS))


register_benchmark(Benchmark("internal-insertion", "0.2.0", "internal", "peg_insertion", _load_insertion))
register_benchmark(Benchmark("internal-slide", "0.2.0", "internal", "constrained_slide", _load_slide))
