"""HEB MuJoCo backend: maps benchmark scenarios onto the MuJoCo machine
adapter and the validated substrate bundle. This is the only place HEB
touches backend-specific configuration; scenarios and grading stay
backend-neutral."""
from __future__ import annotations

import dataclasses

from pegsim.rollout import ValidatedConfig, VALIDATED_LOOSE_V1

from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig
from haplous.behaviors.insertion import insertion_behavior_factory

from benchmarks.heb.protocol import Realization, Scenario


def realize_bundle(scenario: Scenario) -> ValidatedConfig:
    """Base validated bundle with the scenario's geometry deltas applied."""
    bundle = VALIDATED_LOOSE_V1
    if scenario.clearance_delta_m:
        bundle = dataclasses.replace(
            bundle,
            task=dataclasses.replace(
                bundle.task,
                radial_clearance=bundle.task.radial_clearance
                + scenario.clearance_delta_m,
            ),
        )
    if scenario.facet_count is not None:
        bundle = dataclasses.replace(
            bundle,
            task=dataclasses.replace(
                bundle.task, n_segments=scenario.facet_count
            ),
        )
    if scenario.plate_thickness_m is not None:
        bundle = dataclasses.replace(
            bundle,
            task=dataclasses.replace(
                bundle.task, plate_thickness=scenario.plate_thickness_m
            ),
        )
    return bundle


def build_realization(scenario: Scenario) -> Realization:
    """One shared machine realization: the geometry bundle, the MuJoCo
    machine (physics + measurement corruption + fault schedule), and the
    behavior factory for the same bundle."""
    bundle = realize_bundle(scenario)
    config = MuJoCoMachineConfig(
        observation_decimation=1,
        state_max_age_s=scenario.state_max_age_s,
        receive_delay_s=scenario.receive_delay_s,
        dropout_windows_s=scenario.dropout_windows_s,
        stale_windows_s=scenario.stale_windows_s,
        command_rejection_windows_s=scenario.command_rejection_windows_s,
        fault_at_s=scenario.fault_at_s,
        max_sensed_force_n=100.0,
        max_sensed_tilt_deg=45.0,
        position_resolution_m=1e-6,
    )
    return Realization(
        machine=MuJoCoMachine(bundle, config=config),
        behavior_factory=insertion_behavior_factory(bundle),
        bundle=bundle,
    )
