"""HEB benchmark contract: scenarios, systems under test, run records.

A Scenario encodes everything a fair paired comparison must share, in
backend-neutral quantities: task geometry deltas, initial state, hard
requirements, measurement/timing corruption, fault schedules, and (when
objectively knowable) the expected semantic disposition. The backend
layer maps a Scenario onto a concrete machine; systems under test run
against that machine through the public Machine protocol only."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

from haplous.machine import Machine, MachineObservation


@dataclass(frozen=True)
class Scenario:
    """One benchmark scenario. Geometry carries explicit geometry facts
    (facet count, plate depth, clearance delta) plus requirements and
    corruption schedules as benchmark truth, never runtime inputs."""

    id: str
    task_family: str
    # task geometry
    facet_count: int | None = None
    clearance_delta_m: float = 0.0
    plate_thickness_m: float | None = None
    peg_radius_m: float | None = None
    # initial state (start_position_m overrides offset/bearing)
    start_position_m: tuple[float, float, float] | None = None
    initial_offset_m: float = 0.0
    initial_bearing_rad: float = 0.0
    # constrained-path task (Task B): slide from path_start to path_end
    # while maintaining the declared contact-force band
    path_start_m: tuple[float, float, float] | None = None
    path_end_m: tuple[float, float, float] | None = None
    min_force_n: float | None = None
    # hard requirements (None -> not stated)
    max_force_n: float | None = None
    max_tilt_deg: float | None = None
    deadline_s: float | None = None
    state_max_age_s: float = 0.02
    position_tolerance_m: float | None = None
    persistence_s: float = 0.1
    recovery_permitted_conditions: tuple[str, ...] = ()
    max_recovery_actions: int = 0
    # measurement corruption and fault schedule (machine-model level)
    receive_delay_s: float = 0.0
    dropout_windows_s: tuple[tuple[float, float], ...] = ()
    stale_windows_s: tuple[tuple[float, float, float], ...] = ()
    command_rejection_windows_s: tuple[tuple[float, float], ...] = ()
    fault_at_s: float | None = None
    # benchmark-owned physical disturbances: (t_s, dv per machine dof),
    # applied once each at the first supervision step at or after t_s.
    disturbances: tuple[
        tuple[float, tuple[float, float, float, float, float, float]], ...
    ] = ()
    # recovery-policy bound for one recovery attempt (seconds); 0 = bounded
    # only by the execution horizon
    recovery_settle_s: float = 0.0
    # True when the scenario's disturbance is benchmark-designated as a
    # recoverable disturbance (feeds the recovery-success metric).
    recoverable: bool = False
    # semantics
    expected_disposition: str | None = None  # COMPLETE | ABORTED | REFUSED
    infeasible_request: bool = False
    tags: tuple[str, ...] = ()


@dataclass
class SystemRun:
    """What a system under test reports for one scenario attempt. The
    disposition is the system's own semantic outcome; the benchmark grades
    it independently."""

    system: str
    disposition: str  # COMPLETE | ABORTED | REFUSED
    reason: str | None = None
    refusal_codes: tuple[str, ...] = ()
    observations: tuple[MachineObservation, ...] = ()
    wall_s: float = 0.0
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Realization:
    """One shared machine realization for a scenario: the machine (physics
    + measurement corruption + fault schedule), the task behavior factory
    for the same realization, the task's success-condition factory, and
    the backend bundle the grader reads for geometry truth. Systems treat
    bundle as opaque geometry input."""

    machine: Machine
    behavior_factory: object
    condition_factory: object
    bundle: object


class SystemUnderTest(Protocol):
    """One comparable execution system. The benchmark builds one shared
    realization per scenario and hands it over; the system runs exactly
    once and returns its outcome."""

    name: str

    def run(self, realization: Realization, scenario: Scenario) -> SystemRun: ...


@dataclass
class TrialRecord:
    """One graded trial: the system's report plus the benchmark's
    independent evaluation."""

    scenario_id: str
    system: str
    reported_disposition: str
    reason: str | None
    refusal_codes: tuple[str, ...]
    wall_s: float
    # benchmark-owned evaluation
    graded_disposition: str
    task_success: bool
    bounded_success: bool
    hard_violations: tuple[dict, ...] = ()
    task_metrics: dict = field(default_factory=dict)
    tags: tuple[str, ...] = ()
