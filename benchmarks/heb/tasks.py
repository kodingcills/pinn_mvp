"""Task-implementation registry for HEB. Each task family registers how a
scenario maps onto a bundle, a behavior factory, and a success-condition
factory; the runner and the systems under test stay task-agnostic (they
consume the registry's outputs through the Realization contract). Adding a
task family is one registry entry plus its scenario list — no benchmark
core branches."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pegsim.rollout import ValidatedConfig

from haplous.behaviors.insertion import insertion_behavior_factory
from haplous.behaviors.sliding import sliding_behavior_factory
from haplous.intent import SuccessCondition

from benchmarks.heb.insertion import (
    insertion_condition_factory,
    realize_insertion_bundle,
)
from benchmarks.heb.protocol import Scenario
from benchmarks.heb.sliding import slide_bundle, slide_condition_factory


@dataclass(frozen=True)
class TaskImplementation:
    bundle_builder: Callable[[Scenario], ValidatedConfig]
    behavior_factory: Callable[[ValidatedConfig], Callable[[], object]]
    condition_factory: Callable[[Scenario, ValidatedConfig], SuccessCondition]
    expected_duration_s: float
    # Measured-success reach for the refusal boundary; None reports the
    # bundle's geometric envelope.
    max_initial_offset_m: float | None = None


_REGISTRY: dict[str, TaskImplementation] = {}


def register(task_family: str, impl: TaskImplementation) -> None:
    _REGISTRY[task_family] = impl


def implementation_for(task_family: str) -> TaskImplementation:
    return _REGISTRY[task_family]


register(
    "peg_insertion",
    TaskImplementation(
        bundle_builder=realize_insertion_bundle,
        behavior_factory=insertion_behavior_factory,
        condition_factory=insertion_condition_factory,
        # Calibrated from measured completions (results/
        # competent_supervisor_dev_rows.jsonl): nominal-path seating
        # 1.62-1.68 s across 9 nominal dev trials; offset-path seating
        # up to 2.62 s. 2.0 s is the nominal-path expectation; larger
        # offsets carry deadline risk enforced mid-execution.
        expected_duration_s=2.0,
        # Reach capability envelope (results/hes_reach_capability_envelope/, 664
        # paired scenarios over 3.0-5.0 mm, seed 20260916). The scalar is
        # adequate as a refusal/safety boundary: P(hard violation | authorised)
        # rises with offset (0.49 -> 0.64) and bounded capture collapses after
        # the physical knee (~4.0 mm). It is not a bounded-success predictor:
        # facet_count, max_force_n, n_stale and position_tolerance_m shift
        # capture at matched offset. 0.0035 m is the smallest cap clearing the
        # declared G3 preservation floor (0.786 -> 0.969) and G4 false-refusal
        # ceiling while holding conditional violation at the declared 0.003 m
        # rate; the previous 0.003 m was the measured single-point dev boundary.
        max_initial_offset_m=0.0035,
    ),
)
register(
    "constrained_slide",
    TaskImplementation(
        bundle_builder=slide_bundle,
        behavior_factory=sliding_behavior_factory,
        condition_factory=slide_condition_factory,
        expected_duration_s=2.5,
    ),
)
