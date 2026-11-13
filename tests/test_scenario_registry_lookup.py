"""Scenario discovery goes through HEB's task registry: a pack that registers
its scenarios is found by the recorder with no recorder edit, and every edge
case (no scenarios, duplicate ids, unknown id) has one defined outcome."""
from dataclasses import replace

import pytest

from benchmarks import recording
from benchmarks.heb import tasks
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.sliding import sliding_scenarios

BUILTIN = {s.id: s for s in insertion_scenarios() + sliding_scenarios()}  # the pre-registry lookup table


def _impl(scenarios) -> tasks.TaskImplementation:
    return replace(tasks.implementation_for("peg_insertion"), scenarios=scenarios)


@pytest.fixture
def registered():
    families = []

    def add(family, impl):
        tasks.register(family, impl)
        families.append(family)

    yield add
    for family in families:
        tasks.unregister(family)


def test_builtin_lookups_match_pre_registry_table():
    assert set(tasks.scenarios_by_id()) == set(BUILTIN)
    for scenario_id, scenario in BUILTIN.items():
        assert recording.scenario_by_id(scenario_id) == scenario


def test_stub_third_pack_found_without_recorder_edit(registered):
    stub = replace(BUILTIN["insert_nominal"], id="stub_pack_scenario", task_family="stub_pack")
    registered("stub_pack", _impl(scenarios=lambda: [stub]))
    assert recording.scenario_by_id("stub_pack_scenario") == stub
    assert recording.scenario_by_id("stub_pack_scenario", 0.002).initial_offset_m == 0.002
    assert recording.scenario_by_id("insert_nominal") == BUILTIN["insert_nominal"]


def test_family_without_scenarios_attribute_contributes_none(registered):
    # P1: a plain TaskImplementation constructed without the field.
    base = tasks.implementation_for("peg_insertion")
    registered("bare_pack", tasks.TaskImplementation(
        bundle_builder=base.bundle_builder, behavior_factory=base.behavior_factory,
        condition_factory=base.condition_factory, expected_duration_s=1.0))
    assert set(tasks.scenarios_by_id()) == set(BUILTIN)
    assert recording.scenario_by_id("slide_nominal") == BUILTIN["slide_nominal"]


def test_family_with_scenarios_none_contributes_none(registered):
    # P2: an explicit None must not break lookup of the built-ins.
    registered("none_pack", _impl(scenarios=None))
    assert set(tasks.scenarios_by_id()) == set(BUILTIN)
    assert recording.scenario_by_id("insert_nominal") == BUILTIN["insert_nominal"]


def test_duplicate_id_across_families_raises_naming_both(registered):
    # P3: reusing a built-in id never shadows it silently.
    clash = replace(BUILTIN["insert_nominal"], task_family="clash_pack")
    registered("clash_pack", _impl(scenarios=lambda: [clash]))
    with pytest.raises(ValueError, match=r"'insert_nominal'.*'peg_insertion'.*'clash_pack'"):
        recording.scenario_by_id("insert_nominal")


def test_unknown_id_raises_clear_error():
    with pytest.raises(KeyError, match="unknown scenario id 'no_such_scenario'"):
        recording.scenario_by_id("no_such_scenario")
