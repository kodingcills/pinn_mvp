"""G1 incumbent (M4.1): every claimed guard acts on a HEB scenario, and the
same scenario run with that one guard removed shows the difference, so each
test proves it detects a missing guard. Derived scenarios (marked) exist only
where no committed HEB scenario exercises the guard."""
from __future__ import annotations

import dataclasses
import itertools

import pytest

from benchmarks.heb import mujoco_backend
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.metrics import grade_run
from benchmarks.heb.sliding import sliding_scenarios
from benchmarks.heb.systems import system_by_name
from benchmarks.incumbent import GUARDS, G1IncumbentSystem, unmet_command_modes
from haplous.capabilities import CommandMode, command_mode_admission

SCENARIOS = {s.id: s for s in insertion_scenarios() + sliding_scenarios()}
CI, FCD = CommandMode.CARTESIAN_IMPEDANCE, CommandMode.FORCE_CONTROLLED_DESCENT


class _Machine:
    """Wraps the real MuJoCo machine; overrides capabilities or health only."""

    def __init__(self, inner, caps=None, health=None) -> None:
        self._inner, self._caps, self._health = inner, caps, health

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def capabilities(self):
        return self._caps(self._inner) if self._caps else self._inner.capabilities()

    def health(self):
        return self._health(self._inner) if self._health else self._inner.health()


def run(scenario, disabled=(), wrap=None):
    """(SystemRun, graded record tuple, machine) for G1 with guards removed."""
    realization = mujoco_backend.build_realization(scenario)
    machine = realization.machine
    if wrap is not None:
        realization = dataclasses.replace(realization, machine=wrap(machine))
    result = G1IncumbentSystem(frozenset(disabled)).run(realization, scenario)
    _, task_success, bounded, violations, _ = grade_run(scenario, realization.bundle, result)
    return result, bounded, {v["code"] for v in violations}, machine


def derived(base, **changes):
    return dataclasses.replace(SCENARIOS[base], **changes)


def test_registered_name_and_unknown_guard_rejected():
    assert system_by_name("g1_incumbent").name == "g1_incumbent"
    with pytest.raises(ValueError):
        G1IncumbentSystem(frozenset({"not_a_guard"}))


# guard -> (scenario, expected G1 outcome, check on the guard-removed run)
PRE_MOTION = {
    "reach": ("insert_offset_5mm_infeasible", "start_outside_validated_envelope"),
    "cycle_time": ("slide_deadline_infeasible", "cycle_time_infeasible"),
    "sensor_ready": ("insert_stale_persistent", "sensor_not_ready"),
}


@pytest.mark.parametrize("guard", sorted(PRE_MOTION))
def test_pre_motion_guard_refuses_before_any_session(guard):
    scenario_id, code = PRE_MOTION[guard]
    result, _, _, machine = run(SCENARIOS[scenario_id])
    assert (result.disposition, result.refusal_codes) == ("REFUSED", (code,))
    assert machine.open_session_calls == []
    removed, _, _, machine = run(SCENARIOS[scenario_id], disabled={guard})
    assert removed.disposition != "REFUSED"
    assert len(machine.open_session_calls) == 1


def test_force_ceiling_stops_on_first_breach():
    result, _, _, _ = run(SCENARIOS["insert_force_limit_2n"])
    assert (result.disposition, result.reason) == ("ABORTED", "force_limit")
    removed, _, _, _ = run(SCENARIOS["insert_force_limit_2n"], disabled={"force_ceiling"})
    assert removed.reason != "force_limit"
    over = [o for o in removed.observations if abs(o.force_z_n) > 2.0]
    assert len(over) > 100, "without the ceiling the machine keeps pushing past 2 N"


def test_recovery_retries_authorized_ceiling_trips():
    # Mechanism evidence only: this scenario is scientifically excluded
    # (mesh artifact, KNOWN_ISSUES); the retry path is what is tested.
    scenario = SCENARIOS["slide_disturbance_recoverable_ceiling"]
    result, _, _, _ = run(scenario)
    assert result.disposition == "COMPLETE"
    removed, _, _, _ = run(scenario, disabled={"recovery"})
    assert (removed.disposition, removed.reason) == ("ABORTED", "force_limit")


def test_recovery_needs_authorization():
    result, _, _, _ = run(SCENARIOS["slide_disturbance_no_recovery"])
    assert (result.disposition, result.reason) == ("ABORTED", "force_limit")


def test_tilt_guard_stops_on_breach():
    scenario = derived("insert_nominal", max_tilt_deg=5e-5)  # derived: no HEB scenario declares tilt
    result, _, _, _ = run(scenario)
    assert (result.disposition, result.reason) == ("ABORTED", "tilt_limit")
    removed, _, removed_codes, _ = run(scenario, disabled={"tilt"})
    assert removed.reason != "tilt_limit" and "tilt" in removed_codes


def test_deadline_guard_stops_when_deadline_passes():
    # derived: passes the 2.0 s cycle-time check, seats at ~2.6 s
    scenario = derived("insert_offset_2mm", deadline_s=2.0)
    result, _, _, _ = run(scenario)
    assert (result.disposition, result.reason) == ("ABORTED", "deadline_exceeded")
    removed, bounded, codes, _ = run(scenario, disabled={"deadline"})
    assert removed.disposition == "COMPLETE" and not bounded and "deadline" in codes


@pytest.mark.parametrize(
    "guard, scenario_id",
    [("stale", "insert_stale_mid_motion"), ("stale", "slide_stale_mid_slide"), ("dropout", "insert_dropout_large")],
)
def test_freshness_guard_holds_instead_of_acting_on_untrusted_state(guard, scenario_id):
    result, bounded, codes, _ = run(SCENARIOS[scenario_id])
    assert result.disposition == "COMPLETE" and bounded and "state_freshness" not in codes
    removed, removed_bounded, removed_codes, _ = run(SCENARIOS[scenario_id], disabled={guard})
    assert "state_freshness" in removed_codes and not removed_bounded


def test_watchdog_times_out_a_dropout_longer_than_the_hold_budget():
    scenario = derived("insert_dropout_large", dropout_windows_s=((1.0, 1.7),))  # derived: 0.7 s > 0.5 s hold
    result, _, _, _ = run(scenario)
    assert (result.disposition, result.reason) == ("ABORTED", "watchdog_timeout")
    removed, _, removed_codes, _ = run(scenario, disabled={"dropout"})
    assert removed.reason != "watchdog_timeout" and "state_freshness" in removed_codes


def test_command_rejection_guard_times_out_persistent_rejection():
    result, bounded, _, _ = run(SCENARIOS["insert_command_rejection_persistent"])
    assert result.disposition == "COMPLETE" and bounded, "a 20 ms rejection is held through"
    scenario = derived("insert_command_rejection_persistent", command_rejection_windows_s=((0.3, 1.0),))  # derived
    result, _, _, _ = run(scenario)
    assert (result.disposition, result.reason) == ("ABORTED", "command_rejection_timeout")
    removed, _, _, _ = run(scenario, disabled={"command_rejection"})
    assert removed.reason != "command_rejection_timeout"


def test_machine_fault_guard_diagnoses_the_fault():
    result, _, _, _ = run(SCENARIOS["insert_machine_fault"])
    assert (result.disposition, result.reason) == ("ABORTED", "machine_fault_machine_fault")
    removed, _, _, _ = run(SCENARIOS["insert_machine_fault"], disabled={"machine_fault"})
    assert removed.reason == "horizon_exhausted", "without the guard the fault is misreported"


def test_machine_fault_guard_stops_on_unhealthy_machine():
    wrap = lambda m: _Machine(m, health=lambda inner: {**inner.health(), "status": "fault"})  # noqa: E731
    result, _, _, _ = run(SCENARIOS["insert_nominal"], wrap=wrap)
    assert (result.disposition, result.reason) == ("ABORTED", "machine_unhealthy")
    removed, _, _, _ = run(SCENARIOS["insert_nominal"], disabled={"machine_fault"}, wrap=wrap)
    assert removed.disposition == "COMPLETE"


def _only(*modes):
    return lambda inner: dataclasses.replace(inner.capabilities(), command_modes=frozenset(modes))


@pytest.mark.parametrize("scenario_id", ["insert_nominal", "slide_nominal"])
def test_incompatible_command_mode_refused_before_motion(scenario_id):
    """OD-1-consistent: a descent-only machine is refused for this task
    before any session opens; the same machine is not rejected outright."""
    wrap = lambda m: _Machine(m, caps=_only(FCD))  # noqa: E731
    result, _, _, machine = run(SCENARIOS[scenario_id], wrap=wrap)
    assert (result.disposition, result.refusal_codes) == ("REFUSED", ("controller_interface_unavailable",))
    assert machine.open_session_calls == [] and machine.stop_calls == 0
    removed, _, _, machine = run(SCENARIOS[scenario_id], disabled={"command_mode"}, wrap=wrap)
    assert removed.disposition != "REFUSED" and len(machine.open_session_calls) == 1


def test_undeclared_command_contract_refused():
    scenario = SCENARIOS["insert_nominal"]
    realization = mujoco_backend.build_realization(scenario)
    realization = dataclasses.replace(realization, behavior_factory=lambda: object())
    result = G1IncumbentSystem().run(realization, scenario)
    assert result.refusal_codes == ("command_contract_undeclared",)
    assert realization.machine.open_session_calls == []


def test_capability_loss_mid_run_stops():
    scenario = SCENARIOS["insert_nominal"]  # derived machine: CI disappears after 1000 queries (~0.5 s)

    def wrap(m):
        calls = []

        def caps(inner):
            calls.append(None)
            live = inner.capabilities()
            return dataclasses.replace(live, command_modes=frozenset({FCD})) if len(calls) > 1000 else live

        return _Machine(m, caps=caps)

    result, _, _, _ = run(scenario, wrap=wrap)
    assert (result.disposition, result.reason) == ("ABORTED", "controller_interface_lost")
    assert 0.4 < result.observations[-1].stamp.source_s < 0.6
    removed, _, _, _ = run(scenario, disabled={"capability_loss"}, wrap=wrap)
    assert removed.disposition == "COMPLETE"


def test_g1_admission_matches_od1_without_adapters_and_refuses_adapters():
    """Differential against haplous OD-1 admission. Without verified adapters
    the decisions are identical; an authorized, verified adapter is admitted
    by Haplous and refused by G1 (disclosed asymmetry)."""
    behavior = mujoco_backend.build_realization(SCENARIOS["insert_nominal"]).behavior_factory()
    base = mujoco_backend.build_realization(SCENARIOS["insert_nominal"]).machine.capabilities()
    for n in range(3):
        for modes in itertools.combinations([CI, FCD], n):
            caps = dataclasses.replace(base, command_modes=frozenset(modes))
            od1_refuses = bool(command_mode_admission(behavior, caps)[0])
            assert od1_refuses == bool(unmet_command_modes(behavior, caps)), modes
    adapted = dataclasses.replace(base, command_modes=frozenset({CI}), verified_adapters=frozenset({(FCD, CI)}))
    behavior.authorized_substitutes = frozenset({(FCD, CI)})
    assert command_mode_admission(behavior, adapted)[0] == []
    assert unmet_command_modes(behavior, adapted) == [FCD.value]


def test_every_guard_has_a_removal_test():
    tested = set(PRE_MOTION) | {
        "force_ceiling", "recovery", "tilt", "deadline", "stale", "dropout",
        "command_rejection", "machine_fault", "command_mode", "capability_loss",
    }
    assert tested == GUARDS
