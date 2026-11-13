"""Track B engine: the 38 v1.1 fixtures plus independent property/adversarial tests.

The property tests do not consult the fixtures' expected labels. They use an
independent catalogue of single-fault injections, each with its own expected
(level, decision, reason) from BASELINE_SPEC.md, and check how faults compose.
"""

from __future__ import annotations

import importlib.util
import itertools
import random
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from evaluation import track_b as tb
from evaluation.run_registry import strict_loads

SPEC = Path(__file__).resolve().parents[1] / "evaluation" / "spec" / "haplous_eval_v1_1"
FIXTURES = strict_loads((SPEC / "fixtures" / "track_b_scenarios.json").read_bytes())
DEFAULTS = FIXTURES["defaults"]


def base(**over):
    return {**DEFAULTS, **over}


# ---------------- fixture truth ----------------


def test_all_38_fixture_cases_pass():
    rows, summary = tb.evaluate_fixtures(FIXTURES)
    assert len(rows) == 38 and len({r["id"] for r in rows}) == 38
    assert summary["correctness"]["failed_case_ids"] == []
    assert summary["correctness"]["passed"] == 38


def test_terminate_and_substitution_traces_covered():
    rows = {r["id"]: r for r in tb.evaluate_fixtures(FIXTURES)[0]}
    assert {r["observed"]["decision"] for r in rows.values()} == set(tb.DECISIONS)
    sub = rows["behavioral_native_force_approved_and_verified"]
    assert sub["observed"]["selected_mode"] == "native_force"
    assert any(t["check"] == "BEHAVIORAL_SUBSTITUTION" for t in sub["trace"])


def test_engine_source_contains_no_fixture_case_ids():
    src = Path(tb.__file__).read_text()
    leaked = [c["id"] for c in FIXTURES["cases"] if c["id"] in src]
    assert leaked == []


def test_judge_is_not_vacuous(monkeypatch):
    # An always-ADMIT engine must be scored as failing every non-ADMIT case.
    monkeypatch.setattr(tb, "decide", lambda x: tb.Decision("ADMIT", "OK", x["required_mode"], (), ()))
    rows, summary = tb.evaluate_fixtures(FIXTURES)
    n_admit = sum(c["expected"] == "ADMIT" for c in FIXTURES["cases"])
    c = summary["correctness"]
    assert c["false_admission"] == 38 - n_admit
    assert c["passed"] < n_admit  # the substitution case also fails (no trace)


def test_judge_flags_wrong_reason_and_terminal(monkeypatch):
    real = tb.decide

    def broken(x):
        d = real(x)
        if d.decision == "TERMINATE":
            return tb.Decision("REFUSE", d.reason, d.selected_mode, d.faults, d.trace)
        if d.reason == "COMMAND_EXPIRED":
            return tb.Decision(d.decision, "STATE_STALE", d.selected_mode, d.faults, d.trace)
        return d

    monkeypatch.setattr(tb, "decide", broken)
    c = tb.evaluate_fixtures(FIXTURES)[1]["correctness"]
    assert c["failed_terminal_transition"] == sum(x["expected"] == "TERMINATE" for x in FIXTURES["cases"])
    assert c["wrong_reason"] == sum(x["reason"] == "COMMAND_EXPIRED" for x in FIXTURES["cases"])


def test_runs_are_deterministic():
    a, b = tb.evaluate_fixtures(FIXTURES), tb.evaluate_fixtures(strict_loads((SPEC / "fixtures" / "track_b_scenarios.json").read_bytes()))
    assert a == b


# ---------------- G0 ----------------


def _package_g0():
    spec = importlib.util.spec_from_file_location("pkg_validate", SPEC / "tools" / "validate_package.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.g0_decision


def test_g0_matches_fixture_labels_and_package_reference():
    ref = _package_g0()
    for c in FIXTURES["cases"]:
        x = base(**c["overrides"])
        assert tb.g0_decision(x) == c["expected_baseline_g0"] == ref(x), c["id"]


@settings(max_examples=300, deadline=None)
@given(stop=st.booleans(), req=st.sampled_from(["a", "b", "c"]), avail=st.lists(st.sampled_from(["a", "b"])),
       age=st.integers(-50, 400), deadline=st.integers(0, 300))
def test_g0_matches_package_reference_on_random_inputs(stop, req, avail, age, deadline):
    x = base(oem_stop_active=stop, required_mode=req, available_modes=avail, command_age_ms=age,
             command_deadline_ms=deadline)
    assert tb.g0_decision(x) == _package_g0()(x)


# ---------------- independent fault catalogue ----------------

# name: (mutation, level, decision, reason, exclusive-group or None)
CATALOGUE = {
    "stop": ({"oem_stop_active": True}, 0, "TERMINATE", "OEM_STOP_ACTIVE", None),
    "active_revoke": ({"execution_state": "ACTIVE", "event": "REVOKE"}, 1, "TERMINATE", "AUTHORITY_REVOKED", "event"),
    "active_lease": ({"execution_state": "ACTIVE", "event": "LEASE_EXPIRED", "lease_valid": False}, 1, "TERMINATE",
                     "AUTHORITY_LEASE_EXPIRED", "event"),
    "owner": ({"command_owner": "intruder"}, 1, "REFUSE", "AUTHORITY_CONFLICT", None),
    "resource": ({"command_resource_id": "robot_9"}, 1, "REFUSE", "AUTHORITY_RESOURCE_MISMATCH", None),
    "old_epoch": ({"command_epoch": 1}, 1, "REFUSE", "AUTHORITY_REVOKED", None),
    "idle_lease": ({"lease_valid": False}, 1, "REFUSE", "AUTHORITY_LEASE_EXPIRED", "event"),
    "stale_ack": ({"event": "ACK", "ack_epoch": 1}, 1, "REFUSE", "STALE_ACK", "event"),
    "idle_revoke": ({"event": "REVOKE"}, 1, "REFUSE", "AUTHORITY_REVOKED", "event"),  # no fixture covers this
    "torque": ({"required_mode": "joint_torque"}, 2, "REFUSE", "CAPABILITY_UNSUPPORTED", None),
    "sensor": ({"required_sensors": ["wrench"]}, 2, "REFUSE", "OBSERVATION_UNAVAILABLE", None),
    "calibration": ({"calibration_valid": False}, 2, "REFUSE", "CALIBRATION_INVALID", None),
    "domain": ({"observation_clock_domain": "unmapped"}, 3, "REFUSE", "CLOCK_DOMAIN_MISMATCH", None),
    "future": ({"observation_age_ms": -50}, 3, "REFUSE", "CLOCK_INVALID", "obs_age"),
    "expired": ({"command_age_ms": 500}, 4, "REFUSE", "COMMAND_EXPIRED", None),
    "stale": ({"observation_age_ms": 500}, 5, "SUSPEND", "STATE_STALE", "obs_age"),
    "handover": ({"mode_switch_confirmed": False}, 6, "SUSPEND", "MODE_SWITCH_UNCONFIRMED", None),
}
NAMES = sorted(CATALOGUE)


def compatible(names):
    groups = [CATALOGUE[n][4] for n in names if CATALOGUE[n][4]]
    return len(groups) == len(set(groups))


def inject(names):
    x = base()
    for n in names:
        x.update(CATALOGUE[n][0])
    return x


@pytest.mark.parametrize("name", NAMES)
def test_each_single_fault_alone(name):
    _, level, decision, reason, _ = CATALOGUE[name]
    out = tb.decide(inject([name]))
    assert (out.decision, out.reason) == (decision, reason)
    assert [f.reason for f in out.faults] == [reason]


def test_every_compatible_pair_composes_by_precedence():
    checked = 0
    for a, b in itertools.combinations(NAMES, 2):
        if not compatible([a, b]):
            continue
        out = tb.decide(inject([a, b]))
        top = min((CATALOGUE[a], CATALOGUE[b]), key=lambda e: e[1])
        assert out.decision == top[2], (a, b)
        assert {f.reason for f in out.faults} == {CATALOGUE[a][3], CATALOGUE[b][3]}, (a, b)
        if CATALOGUE[a][1] != CATALOGUE[b][1]:
            assert out.reason == top[3], (a, b)
        checked += 1
    assert checked > 100


@settings(max_examples=400, deadline=None)
@given(st.sets(st.sampled_from(NAMES), min_size=1, max_size=7))
def test_random_fault_sets_compose_by_precedence(names):
    names = sorted(names)
    if not compatible(names):
        return
    out = tb.decide(inject(names))
    min_level = min(CATALOGUE[n][1] for n in names)
    top = [CATALOGUE[n] for n in names if CATALOGUE[n][1] == min_level]
    assert out.decision in {t[2] for t in top} and out.reason in {t[3] for t in top}
    assert {f.reason for f in out.faults} == {CATALOGUE[n][3] for n in names}  # complete, nothing invented
    assert out.trace[-1]["check"] == "DECISION"
    shadowed = [t for t in out.trace if t.get("role") == "shadowed"]
    assert len(shadowed) == len(out.faults) - 1


@settings(max_examples=200, deadline=None)
@given(st.sets(st.sampled_from(NAMES), max_size=7), st.randoms(use_true_random=False))
def test_decision_is_deterministic_and_key_order_independent(names, rnd):
    x = inject(sorted(names)) if compatible(sorted(names)) else base()
    keys = list(x)
    rnd.shuffle(keys)
    assert tb.decide(x) == tb.decide({k: x[k] for k in keys})


# ---------------- lifecycle / revocation invariants ----------------


@settings(max_examples=300, deadline=None)
@given(st.sets(st.sampled_from([n for n in NAMES if CATALOGUE[n][4] != "event"]), max_size=8))
def test_active_revoke_always_terminates(names):
    out = tb.decide(inject(sorted(names) + ["active_revoke"]))
    assert out.decision == "TERMINATE"


@settings(max_examples=300, deadline=None)
@given(st.sets(st.sampled_from([n for n in NAMES if n not in ("stop", "active_revoke", "active_lease")]), max_size=8))
def test_idle_never_terminates_without_external_stop(names):
    names = sorted(names)
    if compatible(names):
        assert tb.decide(inject(names)).decision != "TERMINATE"


@pytest.mark.parametrize("event", tb.EVENTS)
def test_active_lease_loss_terminates_for_every_event(event):
    assert tb.decide(base(execution_state="ACTIVE", event=event, lease_valid=False)).decision == "TERMINATE"


def test_external_stop_dominates_even_invalid_input():
    x = base(oem_stop_active=True)
    del x["clock_domain"]
    x["typo_field"] = 1
    out = tb.decide(x)
    assert (out.decision, out.reason) == ("TERMINATE", "OEM_STOP_ACTIVE")


# ---------------- input completeness: never silently pass ----------------


@pytest.mark.parametrize("key", sorted(tb.INPUT_KEYS - {"oem_stop_active"}))
def test_missing_any_key_refuses(key):
    x = base()
    del x[key]
    out = tb.decide(x)
    assert (out.decision, out.reason) == ("REFUSE", "INPUT_INVALID")


@pytest.mark.parametrize("bad", [
    {"lease_vaild": False},  # misspelling must not be ignored
    {"lease_valid": "false"}, {"observation_age_ms": 10.5}, {"command_epoch": True},
    {"available_modes": "cartesian_position"}, {"event": "PING"}, {"execution_state": "PAUSED"},
    {"mode_specific_max_obs_age_ms": {"native_force": -1}}, {"command_deadline_ms": -1},
    {"allowed_mode_equivalents": {"x": "native_force"}}, {"required_mode": ""},
])
def test_ill_typed_or_unknown_input_refuses(bad):
    out = tb.decide(base(**bad))
    assert (out.decision, out.reason) == ("REFUSE", "INPUT_INVALID")


# ---------------- substitution: no silent downgrade ----------------

MODES = ["joint_torque", "native_force", "cartesian_position", "bounded_cartesian_compliance"]


@settings(max_examples=500, deadline=None)
@given(req=st.sampled_from(MODES), avail=st.sets(st.sampled_from(MODES)), allowed=st.lists(st.sampled_from(MODES)),
       verified=st.sets(st.sampled_from(MODES)), nonsub=st.sets(st.sampled_from(MODES)))
def test_no_silent_downgrade(req, avail, allowed, verified, nonsub):
    x = base(required_mode=req, available_modes=sorted(avail), allowed_mode_equivalents={req: allowed},
             verified_mode_equivalents={req: sorted(verified)}, non_substitutable_modes=sorted(nonsub))
    out = tb.decide(x)
    substituted = any(t["check"] == "BEHAVIORAL_SUBSTITUTION" for t in out.trace)
    if req in avail:
        assert out.decision == "ADMIT" and out.selected_mode == req and not substituted
    elif out.decision == "ADMIT":
        assert req not in nonsub
        assert out.selected_mode in set(allowed) & verified & avail
        assert substituted and out.reason == "AUTHORIZED_EQUIVALENCE"
    else:
        assert out.reason in ("CAPABILITY_UNSUPPORTED", "CONTROL_AUTHORITY_NOT_EQUIVALENT")
        assert not (req not in nonsub and set(allowed) & verified & avail)  # a valid substitute was not refused


def test_substitute_uses_selected_modes_freshness_bound():
    x = base(required_mode="bounded_cartesian_compliance", available_modes=["native_force"],
             allowed_mode_equivalents={"bounded_cartesian_compliance": ["native_force"]},
             verified_mode_equivalents={"bounded_cartesian_compliance": ["native_force"]},
             mode_specific_max_obs_age_ms={"native_force": 20, "bounded_cartesian_compliance": 500},
             observation_age_ms=50)
    out = tb.decide(x)
    assert (out.decision, out.reason) == ("SUSPEND", "STATE_STALE_FOR_MODE")


# ---------------- boundaries ----------------


@settings(max_examples=200, deadline=None)
@given(limit=st.integers(0, 10_000), tol=st.integers(0, 1000), deadline=st.integers(0, 10_000))
def test_boundaries_are_inclusive(limit, tol, deadline):
    ok = base(contract_max_obs_age_ms=limit, future_skew_tolerance_ms=tol, command_deadline_ms=deadline,
              command_age_ms=deadline, observation_age_ms=limit)
    assert tb.decide(ok).decision == "ADMIT"
    assert tb.decide({**ok, "observation_age_ms": limit + 1}).reason == "STATE_STALE"
    assert tb.decide({**ok, "command_age_ms": deadline + 1}).reason == "COMMAND_EXPIRED"
    assert tb.decide({**ok, "observation_age_ms": -tol}).decision == "ADMIT"
    assert tb.decide({**ok, "observation_age_ms": -tol - 1}).reason == "CLOCK_INVALID"


def test_defaults_admit_false_refusal_control():
    assert (tb.decide(base()).decision, tb.decide(base()).reason) == ("ADMIT", "OK")


def test_random_scenarios_never_crash():
    rnd = random.Random(0)
    for _ in range(2000):
        x = base()
        for k in rnd.sample(sorted(tb.INPUT_KEYS), 3):
            x[k] = rnd.choice([None, 0, -1, True, "x", [], {}, [1], {"a": 1}])
        assert tb.decide(x).decision in tb.DECISIONS
