"""Negative test (M3.3, probe 6): privileged truth must not cross the execution boundary.

HEB owns the scenario's fault schedule and grading truth. A system under test
may see only what crosses the boundary: what the machine and its sessions
return, and the ExecutionIntent (with its success condition) built from the
scenario. Every such object is captured from a real MuJoCo episode and walked
structurally, closures included. No value of an evaluator-only Scenario field
may appear in it. The scenario carries canary values that cannot occur by
chance, so the check matches values, not field names.
"""
from __future__ import annotations

import dataclasses
import enum
import types
from collections.abc import Mapping
from unittest import mock

import pytest

from benchmarks.heb import mujoco_backend, systems
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.protocol import Scenario
from benchmarks.heb.sliding import sliding_scenarios
from haplous.machine import CommandOutcome

# Scenario fields a system may legitimately receive (requirements, geometry,
# start state, recovery policy, task family). Everything else is benchmark truth.
DECLARED = frozenset({
    "task_family", "facet_count", "clearance_delta_m", "plate_thickness_m", "peg_radius_m",
    "start_position_m", "initial_offset_m", "initial_bearing_rad", "path_start_m", "path_end_m",
    "min_force_n", "max_force_n", "max_tilt_deg", "deadline_s", "state_max_age_s",
    "position_tolerance_m", "persistence_s", "recovery_permitted_conditions",
    "max_recovery_actions", "recovery_settle_s",
})
# receive_delay_s is benchmark truth, but its effect is observable through Stamp by
# design, so the canary scenario leaves it at zero instead of planting a value.
EVALUATOR_ONLY = frozenset({
    "id", "receive_delay_s", "dropout_windows_s", "stale_windows_s",
    "command_rejection_windows_s", "fault_at_s", "disturbances", "recoverable",
    "expected_disposition", "infeasible_request", "tags",
})
CANARY = dict(
    id="canary-scn-7f3a9c",
    dropout_windows_s=((1.0000137, 1.2000291),),
    stale_windows_s=((0.6000233, 0.6000457, 0.0100359),),
    command_rejection_windows_s=((0.4000173, 0.4000419),),
    fault_at_s=9.0000311,
    disturbances=((1.5000419, (0.0, 0.0, 1.00000731e-7, 0.0, 0.0, 0.0)),),
    expected_disposition="CANARY_DISP_q81",
    tags=("canary_tag_w27",),
)
SYSTEMS = ("direct_minimal", "bare_behavior", "competent_supervisor", "haplous_mediated")


def _scenario(base_id: str) -> Scenario:
    base = {s.id: s for s in insertion_scenarios() + sliding_scenarios()}[base_id]
    return dataclasses.replace(base, receive_delay_s=0.0, **CANARY)


def _secrets(scenario: Scenario) -> list[tuple[str, object]]:
    """(field, value) for every non-trivial leaf of an evaluator-only field."""
    out = []
    for name in sorted(EVALUATOR_ONLY):
        for _, leaf in _leaves(getattr(scenario, name), name):
            if isinstance(leaf, str) or (isinstance(leaf, float) and leaf != 0.0):
                out.append((name, leaf))
    return out


def _leaves(obj, path, seen=None, depth=0):
    """Every str/number reachable from obj: dataclass fields, containers, enum
    values, plain object attributes, and function closures and defaults."""
    seen = set() if seen is None else seen
    if isinstance(obj, (str, bytes)):
        yield path, obj.decode() if isinstance(obj, bytes) else obj
        return
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        yield path, float(obj)
        return
    if id(obj) in seen or depth > 12:
        return
    seen.add(id(obj))
    if isinstance(obj, enum.Enum):
        yield from _leaves(obj.value, path, seen, depth + 1)
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            yield from _leaves(getattr(obj, f.name), f"{path}.{f.name}", seen, depth + 1)
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            yield from _leaves(k, f"{path}[key]", seen, depth + 1)
            yield from _leaves(v, f"{path}[{k!r}]", seen, depth + 1)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for i, v in enumerate(obj):
            yield from _leaves(v, f"{path}[{i}]", seen, depth + 1)
    elif isinstance(obj, types.FunctionType):
        for i, cell in enumerate(obj.__closure__ or ()):
            yield from _leaves(cell.cell_contents, f"{path}<closure{i}>", seen, depth + 1)
        yield from _leaves(obj.__defaults__ or (), f"{path}<defaults>", seen, depth + 1)
        yield from _leaves(obj.__kwdefaults__ or {}, f"{path}<kwdefaults>", seen, depth + 1)
    elif hasattr(obj, "__dict__") and not isinstance(obj, (type, types.ModuleType)):
        yield from _leaves(vars(obj), path, seen, depth + 1)


def leaks(crossed: list[tuple[str, object]], scenario: Scenario) -> list[tuple[str, str]]:
    """(evaluator-only field, boundary path) for every secret value found."""
    secrets = _secrets(scenario)
    found = set()
    for where, obj in crossed:
        for path, leaf in _leaves(obj, where):
            for field, secret in secrets:
                hit = (secret in leaf) if isinstance(leaf, str) and isinstance(secret, str) else (
                    isinstance(leaf, float) and isinstance(secret, float) and leaf == secret)
                if hit:
                    found.add((field, path))
    return sorted(found)


class _Spy:
    """Records everything the wrapped machine or session returns to the system."""

    def __init__(self, inner, sink: list, label: str) -> None:
        self._inner, self._sink, self._label = inner, sink, label

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            self._sink.append((f"{self._label}.{name}", attr))
            return attr

        def call(*args, **kwargs):
            result = attr(*args, **kwargs)
            if name == "open_session":
                return _Spy(result, self._sink, "session")
            self._sink.append((f"{self._label}.{name}()", result))
            return result

        return call


def _run(scenario: Scenario, system: str, machine_wrapper=None) -> list[tuple[str, object]]:
    crossed: list[tuple[str, object]] = []
    realization = mujoco_backend.build_realization(scenario)
    machine = realization.machine if machine_wrapper is None else machine_wrapper(realization.machine, scenario)
    realization = dataclasses.replace(realization, machine=_Spy(machine, crossed, "machine"))
    real_build_intent = systems.build_intent

    def capture_intent(*args, **kwargs):
        intent = real_build_intent(*args, **kwargs)
        crossed.append(("intent", intent))
        return intent

    with mock.patch.object(systems, "build_intent", capture_intent):
        systems.system_by_name(system).run(realization, scenario)
    assert any(w == "intent" for w, _ in crossed), "intent was not captured"
    assert any(w == "session.observe()" for w, _ in crossed), "no observation crossed the boundary"
    return crossed


def test_scenario_fields_are_partitioned():
    names = {f.name for f in dataclasses.fields(Scenario)}
    assert DECLARED.isdisjoint(EVALUATOR_ONLY)
    assert DECLARED | EVALUATOR_ONLY == names, "classify every new Scenario field as declared or evaluator-only"


@pytest.mark.parametrize("system", SYSTEMS)
@pytest.mark.parametrize("base_id", ["insert_dropout_large", "slide_nominal"])
def test_no_evaluator_only_value_crosses_execution_boundary(base_id, system):
    scenario = _scenario(base_id)
    found = [hit for hit in leaks(_run(scenario, system), scenario) if hit[0] != "id"]
    assert found == []


class _LeakyMachine:
    """Planted defect: an adapter that forwards simulator truth to the system,
    the fault schedule as an observation channel and the expected disposition
    as a command-outcome reason."""

    def __init__(self, inner, scenario: Scenario) -> None:
        self._inner, self._scenario = inner, scenario

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def open_session(self, **kwargs):
        return _LeakySession(self._inner.open_session(**kwargs), self._scenario)


class _LeakySession(_LeakyMachine):
    def observe(self):
        obs = self._inner.observe()
        hint = (("window_hint", self._scenario.dropout_windows_s[0][0]),)
        return None if obs is None else dataclasses.replace(obs, extra=obs.extra + hint)

    def apply(self, command):
        outcome = self._inner.apply(command)
        return CommandOutcome(outcome.accepted, reason=self._scenario.expected_disposition)


@pytest.mark.parametrize("system", ["competent_supervisor", "haplous_mediated"])
def test_detector_catches_planted_truth_leak(system):
    scenario = _scenario("insert_dropout_large")
    found = leaks(_run(scenario, system, _LeakyMachine), scenario)
    fields = {f for f, _ in found}
    assert {"dropout_windows_s", "expected_disposition"} <= fields, found
    assert any(".extra[" in path for f, path in found if f == "dropout_windows_s"), found
    assert any(".reason" in path for f, path in found if f == "expected_disposition"), found


def test_scenario_id_does_not_cross_execution_boundary():
    scenario = _scenario("insert_dropout_large")
    assert [hit for hit in leaks(_run(scenario, "haplous_mediated"), scenario) if hit[0] == "id"] == []
