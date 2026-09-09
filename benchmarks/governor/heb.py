"""HEB system adapter and observation-derived governor campaign metrics."""
from __future__ import annotations

import dataclasses
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import TypedDict

from benchmarks.governor.g1 import GovernorConfig, GovernorMachine
from benchmarks.governor.timing import TimingMachine, TimingStressConfig
from benchmarks.heb.protocol import Realization, Scenario, SystemRun
from benchmarks.heb.systems import HaplousSystem
from haplous.machine import Machine, MachineObservation


class ExecutionMetrics(TypedDict):
    peak_abs_force_n: float
    force_integral_n_s: float
    contact_force_std_n: float
    maximum_observation_age_s: float
    maximum_source_gap_s: float


def execution_metrics(
    observations: tuple[MachineObservation, ...],
) -> ExecutionMetrics:
    forces = [abs(item.force_z_n) for item in observations]
    force_integral = sum(
        0.5
        * (abs(previous.force_z_n) + abs(current.force_z_n))
        * (current.stamp.source_s - previous.stamp.source_s)
        for previous, current in pairwise(observations)
    )
    contact_forces = [
        abs(item.force_z_n)
        for item in observations
        if (item.extra_value("in_contact") or 0.0) > 0.0
    ]
    source_gaps = [
        current.stamp.source_s - previous.stamp.source_s
        for previous, current in pairwise(observations)
    ]
    return ExecutionMetrics(
        peak_abs_force_n=max(forces, default=0.0),
        force_integral_n_s=force_integral,
        contact_force_std_n=(
            statistics.pstdev(contact_forces) if len(contact_forces) > 1 else 0.0
        ),
        maximum_observation_age_s=max(
            (item.stamp.age_s for item in observations), default=0.0
        ),
        maximum_source_gap_s=max(source_gaps, default=0.0),
    )


@dataclass(frozen=True, slots=True)
class GovernorParameters:
    activation_fraction: float = 0.8
    minimum_stiffness_scale: float = 0.25
    window_samples: int = 20


class GovernorBenchmarkSystem:
    def __init__(
        self,
        name: str,
        *,
        governor_parameters: GovernorParameters | None = None,
        force_limits_n: Mapping[str, float] | None = None,
        timing_configs: Mapping[str, TimingStressConfig] | None = None,
    ) -> None:
        self.name = name
        self._governor_parameters = governor_parameters
        self._force_limits_n = force_limits_n or {}
        self._timing_configs = timing_configs or {}

    def run(self, realization: Realization, scenario: Scenario) -> SystemRun:
        machine: Machine = realization.machine
        timing = self._timing_configs.get(scenario.id)
        if timing is not None:
            machine = TimingMachine(machine, timing)
        governed: GovernorMachine | None = None
        if self._governor_parameters is not None:
            parameters = self._governor_parameters
            force_limit = scenario.max_force_n or self._force_limits_n[scenario.task_family]
            governed = GovernorMachine(
                machine,
                GovernorConfig(
                    force_limit_n=force_limit,
                    activation_fraction=parameters.activation_fraction,
                    minimum_stiffness_scale=parameters.minimum_stiffness_scale,
                    window_samples=parameters.window_samples,
                ),
            )
            machine = governed
        delegate = HaplousSystem()
        delegate.name = self.name
        run = delegate.run(dataclasses.replace(realization, machine=machine), scenario)
        run.detail.update(execution_metrics(run.observations))
        if governed is not None:
            run.detail.update(governed.last_session_stats)
        return run
