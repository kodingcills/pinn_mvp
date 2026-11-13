"""A task pack registered in HEB's registry is found by the recorder with no recorder edit."""
from dataclasses import dataclass, replace
from collections.abc import Callable

import pytest

from benchmarks import recording
from benchmarks.heb import tasks
from benchmarks.heb.insertion import insertion_scenarios


@dataclass(frozen=True)
class _StubImpl(tasks.TaskImplementation):
    scenarios: Callable[[], list] | None = None


@pytest.fixture
def stub_family():
    base = tasks.implementation_for("peg_insertion")
    scenario = replace(insertion_scenarios()[0], id="stub_pack_scenario", task_family="stub_pack")
    tasks.register("stub_pack", _StubImpl(
        bundle_builder=base.bundle_builder, behavior_factory=base.behavior_factory,
        condition_factory=base.condition_factory, expected_duration_s=1.0,
        scenarios=lambda: [scenario]))
    try:
        yield scenario
    finally:
        tasks._REGISTRY.pop("stub_pack")  # no public unregister


def test_registered_stub_pack_is_found(stub_family):
    assert recording.scenario_by_id("stub_pack_scenario") == stub_family
    assert recording.scenario_by_id("stub_pack_scenario", 0.002).initial_offset_m == 0.002


def test_unregistered_after_cleanup_and_builtins_still_found():
    with pytest.raises(KeyError):
        recording.scenario_by_id("stub_pack_scenario")
    assert recording.scenario_by_id("insert_nominal").id == "insert_nominal"
    assert recording.scenario_by_id("slide_nominal").id == "slide_nominal"
