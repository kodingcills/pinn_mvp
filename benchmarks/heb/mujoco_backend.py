"""HEB MuJoCo backend: maps benchmark scenarios onto the MuJoCo machine
adapter. This is the only place HEB touches backend-specific
configuration; scenarios, grading, and the runner stay backend-neutral.
Task-family specifics (bundle construction, behavior factory, success
condition) resolve through the task registry — the backend and runner
contain no per-task branches."""
from __future__ import annotations

from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig

from benchmarks.heb import tasks
from benchmarks.heb.protocol import Realization, Scenario


def build_realization(
    scenario: Scenario,
    machine_factory=MuJoCoMachine,
    behavior_factory_override=None,
) -> Realization:
    impl = tasks.implementation_for(scenario.task_family)
    bundle = impl.bundle_builder(scenario)
    config = MuJoCoMachineConfig(
        observation_decimation=1,
        state_max_age_s=scenario.state_max_age_s,
        receive_delay_s=scenario.receive_delay_s,
        dropout_windows_s=scenario.dropout_windows_s,
        stale_windows_s=scenario.stale_windows_s,
        command_rejection_windows_s=scenario.command_rejection_windows_s,
        fault_at_s=scenario.fault_at_s,
        disturbances=scenario.disturbances,
        expected_duration_s=impl.expected_duration_s,
        max_sensed_force_n=100.0,
        max_sensed_tilt_deg=45.0,
        position_resolution_m=1e-6,
    )
    behavior_factory = (
        behavior_factory_override if behavior_factory_override is not None else impl.behavior_factory(bundle)
    )
    return Realization(
        machine=machine_factory(bundle, config=config),
        behavior_factory=behavior_factory,
        condition_factory=impl.condition_factory,
        bundle=bundle,
    )
