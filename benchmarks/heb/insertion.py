"""HEB task family A: peg-in-bore insertion.

Scenarios are data, in normalized quantities: geometry deltas from the
family's base geometry (facet count, clearance, plate depth), initial
state, requirements, measurement corruption and fault schedules. The
family's success condition is benchmark truth shared by every system;
graders never read Haplous state."""
from __future__ import annotations

import dataclasses

from pegsim.rollout import VALIDATED_LOOSE_V1, ValidatedConfig

from benchmarks.heb.protocol import Scenario
from benchmarks.heb.metrics import PEN_ALLOW_M

BASE_POSITION_TOLERANCE_M = 0.0005 + PEN_ALLOW_M


def insertion_scenarios() -> list[Scenario]:
    """Development scenario set: nominal paths, geometry variants,
    semantic infeasibility, and mid-execution fault schedules."""
    scenarios: list[Scenario] = [
        Scenario(
            id="insert_nominal",
            task_family="peg_insertion",
            initial_offset_m=0.0,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="COMPLETE",
            tags=("task_a", "physical"),
        ),
        Scenario(
            id="insert_offset_1mm",
            task_family="peg_insertion",
            initial_offset_m=0.001,
            initial_bearing_rad=0.7,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="COMPLETE",
            tags=("task_a", "physical"),
        ),
        Scenario(
            id="insert_offset_2mm",
            task_family="peg_insertion",
            initial_offset_m=0.002,
            initial_bearing_rad=2.1,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="COMPLETE",
            tags=("task_a", "physical"),
        ),
        Scenario(
            id="insert_offset_5mm_infeasible",
            task_family="peg_insertion",
            initial_offset_m=0.005,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="REFUSED",
            infeasible_request=True,
            tags=("task_a", "semantic"),
        ),
        Scenario(
            id="insert_force_limit_2n",
            task_family="peg_insertion",
            max_force_n=2.0,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="ABORTED",
            tags=("task_a", "physical", "constraint"),
        ),
        Scenario(
            id="insert_force_limit_12n",
            task_family="peg_insertion",
            max_force_n=12.0,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="COMPLETE",
            tags=("task_a", "physical", "constraint"),
        ),
        Scenario(
            id="insert_force_limit_150n",
            task_family="peg_insertion",
            max_force_n=150.0,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            # Counterfactual-falsified infeasibility (results/
            # false_refusal_summary.json): the request completes at
            # 2.18 N peak, far inside the 150 N contract; the capability
            # model now degrades to ceiling enforcement instead of
            # refusing.
            expected_disposition="COMPLETE",
            infeasible_request=False,
            tags=("task_a", "semantic"),
        ),
        Scenario(
            id="insert_deadline_2s",
            task_family="peg_insertion",
            deadline_s=2.0,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            # Counterfactual-falsified infeasibility: nominal seating
            # completes at ~1.62 s, inside the 2 s contract; the 4.0 s
            # duration declaration was never a measured quantity and is
            # calibrated to 2.0 s (see benchmarks/heb/tasks.py).
            expected_disposition="COMPLETE",
            infeasible_request=False,
            tags=("task_a", "semantic"),
        ),
        Scenario(
            id="insert_dropout_small",
            task_family="peg_insertion",
            initial_offset_m=0.001,
            initial_bearing_rad=1.3,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            dropout_windows_s=((1.0, 1.005),),
            expected_disposition="COMPLETE",
            tags=("task_a", "measurement"),
        ),
        Scenario(
            id="insert_dropout_large",
            task_family="peg_insertion",
            initial_offset_m=0.001,
            initial_bearing_rad=3.3,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            dropout_windows_s=((1.0, 1.2),),
            expected_disposition="ABORTED",
            tags=("task_a", "measurement"),
        ),
        Scenario(
            id="insert_stale_persistent",
            task_family="peg_insertion",
            receive_delay_s=0.05,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="REFUSED",
            infeasible_request=True,
            tags=("task_a", "measurement"),
        ),
        Scenario(
            id="insert_stale_mid_motion",
            task_family="peg_insertion",
            stale_windows_s=((0.3, 0.5, 0.05),),
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="ABORTED",
            tags=("task_a", "timing"),
        ),
        Scenario(
            id="insert_command_rejection_persistent",
            task_family="peg_insertion",
            command_rejection_windows_s=((0.3, 0.32),),
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="ABORTED",
            tags=("task_a", "capability"),
        ),
        Scenario(
            id="insert_command_rejection_transient",
            task_family="peg_insertion",
            command_rejection_windows_s=((0.3, 0.3005),),
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="COMPLETE",
            tags=("task_a", "capability"),
        ),
        Scenario(
            id="insert_machine_fault",
            task_family="peg_insertion",
            fault_at_s=0.4,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="ABORTED",
            tags=("task_a", "fault"),
        ),
        # geometry variants (materially different contact geometry)
        Scenario(
            id="insert_geometry_fine_facets",
            task_family="peg_insertion",
            facet_count=64,
            initial_offset_m=0.001,
            initial_bearing_rad=4.0,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="COMPLETE",
            tags=("task_a", "geometry"),
        ),
        Scenario(
            id="insert_geometry_tight_clearance",
            task_family="peg_insertion",
            clearance_delta_m=-0.0002,
            initial_offset_m=0.001,
            initial_bearing_rad=1.0,
            position_tolerance_m=0.0003 + PEN_ALLOW_M,
            expected_disposition="COMPLETE",
            tags=("task_a", "geometry"),
        ),
        Scenario(
            id="insert_geometry_deep_plate",
            task_family="peg_insertion",
            plate_thickness_m=0.02,
            position_tolerance_m=0.0005 + PEN_ALLOW_M,
            expected_disposition="COMPLETE",
            tags=("task_a", "geometry"),
        ),
    ]
    return scenarios


BEHAVIOR_NAME = "peg_insertion"


def insertion_condition_factory(scenario, bundle):
    """Task-family truth for one realization: the seated condition built
    from the realized geometry and the scenario's requirements. Identical
    for every system under test."""
    from haplous.behaviors.insertion import seated_condition

    task = bundle.task
    tolerance = scenario.position_tolerance_m or (
        task.radial_clearance + 12e-6
    )
    return seated_condition(
        goal=(0.0, 0.0, 0.0),
        tolerance_m=tolerance,
        target_depth_m=task.plate_thickness - 140e-6,
        max_force_n=scenario.max_force_n if scenario.max_force_n is not None else 25.0,
        persistence_s=scenario.persistence_s,
    )


def realize_insertion_bundle(scenario: Scenario) -> ValidatedConfig:
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
