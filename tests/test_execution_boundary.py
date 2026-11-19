"""ARCH-001 execution boundary (#16): the runtime never advances the world,
commands cross as ActionEnvelopes, and a machine with no step() runs on a
RealtimeTickSource."""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from haplous.adapters.legacy import CommandOutcome, LegacySession
from haplous.intent import SuccessCondition
from haplous.machine import (
    Ack,
    AckStatus,
    ActionEnvelope,
    CartesianImpedanceTarget,
    ClockDomain,
    MachineObservation,
    MachineSession,
    RealtimeTickSource,
    Stamp,
)
from haplous.runtime import HaplousRuntime
from haplous.state import ExecutionState
from tests.test_haplous_semantics import MAX_AGE, PERIOD, HoldBehavior, make_intent, std_caps

RUNTIME = Path(__file__).resolve().parents[1] / "haplous" / "runtime.py"


def test_runtime_never_calls_a_session_step():
    tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))
    callers = {
        ast.unparse(node.func.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "step"
    }
    assert callers == {"behavior"}, callers
    assert not hasattr(MachineSession, "step")


class FakeClock:
    """Monotonic seconds that advance only when slept or worked."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0.0, "never sleeps a negative interval"
        self.t += seconds


def test_realtime_ticks_land_on_the_period_grid_despite_work():
    clock = FakeClock()
    ticks = RealtimeTickSource(0.01, clock=clock, sleep=clock.sleep)
    times = []
    for work in (0.0, 0.003, 0.0099, 0.001):
        clock.t += work
        ticks.wait()
        times.append(clock.t)
    assert times == pytest.approx([0.01, 0.02, 0.03, 0.04])


def test_realtime_overrun_drops_missed_periods_instead_of_bursting():
    clock = FakeClock()
    ticks = RealtimeTickSource(0.01, clock=clock, sleep=clock.sleep)
    ticks.wait()  # 0.01
    clock.t += 0.025  # overran two deadlines
    ticks.wait()
    assert clock.t == pytest.approx(0.035), "late tick returns at once"
    ticks.wait()
    assert clock.t == pytest.approx(0.045), "grid re-anchored at the late tick"


def test_realtime_period_must_be_positive():
    with pytest.raises(ValueError):
        RealtimeTickSource(0.0)


def test_realtime_ticks_on_the_real_monotonic_clock_are_never_early_and_jitter_is_bounded():
    period, n = 0.002, 25
    ticks = RealtimeTickSource(period)
    t0 = time.monotonic()
    lateness = []
    for k in range(1, n + 1):
        ticks.wait()
        late = time.monotonic() - (t0 + k * period)
        assert late >= 0.0, f"tick {k} early by {-late}s"
        lateness.append(late)
    assert max(lateness) < 0.25, lateness


class ClockedMachine:
    """A product machine with no step(): time passes on `clock` and the
    session observes it. Records every envelope it is handed."""

    def __init__(self, clock: FakeClock, work_s: float = 0.0007) -> None:
        self.clock = clock
        self.work_s = work_s
        self.envelopes: list[tuple[float, ActionEnvelope]] = []

    def capabilities(self):
        return std_caps()

    def observe(self) -> MachineObservation:
        t = self.clock()
        return MachineObservation(
            stamp=Stamp(source_s=t, received_s=t, clock=ClockDomain.MONOTONIC),
            pose_m=(0.0, 0.0, -0.010),
            force_z_n=15.0,
            tilt_deg=0.0,
        )

    def open_session(self, *, start_position_m, horizon_s):
        return self

    def submit(self, envelope: ActionEnvelope) -> Ack:
        self.envelopes.append((self.clock(), envelope))
        self.clock.t += self.work_s  # decision-to-dispatch takes host time
        return Ack(envelope.command_id, AckStatus.CONTROLLER_ACCEPTED)

    def poll_acks(self) -> list[Ack]:
        return []

    def stop(self) -> None:
        pass

    ended = False
    fault = None

    def health(self):
        return {"status": "ok"}


def test_a_step_free_machine_runs_to_completion_on_a_realtime_tick_source():
    clock = FakeClock()
    machine = ClockedMachine(clock)
    runtime = HaplousRuntime(
        machine,
        behaviors={"motion": HoldBehavior},
        tick_source=RealtimeTickSource(PERIOD, clock=clock, sleep=clock.sleep),
    )
    intent = make_intent(success_condition=SuccessCondition(
        "ten_periods", lambda w: w[-1].stamp.source_s >= 10 * PERIOD - 1e-12))
    assert runtime.submit(intent).outcome == "SUPPORTED"
    runtime.execute()
    assert runtime.state is ExecutionState.COMPLETE
    sent = [env for _, env in machine.envelopes]
    assert [e.command_id for e in sent] == list(range(1, 11))
    assert {(e.lease_epoch, e.clock, e.correlation_id) for e in sent} == {
        (0, ClockDomain.MONOTONIC, "execution-0")}
    for t, env in machine.envelopes:
        assert env.origin_source_s == t, "decided on that tick's fresh observation"
        assert env.valid_until_s == pytest.approx(env.origin_source_s + MAX_AGE)
        assert isinstance(env.payload, CartesianImpedanceTarget)
    dispatch = [t for t, _ in machine.envelopes]
    gaps = [b - a for a, b in zip(dispatch[1:], dispatch[2:], strict=False)]
    assert gaps == pytest.approx([PERIOD] * len(gaps)), "ticks stay on the grid despite dispatch work"


class _SimSession:
    def __init__(self, outcome: CommandOutcome) -> None:
        self.outcome = outcome
        self.applied: list = []

    def apply(self, command):
        self.applied.append(command)
        return self.outcome


def _envelope(payload, command_id=7) -> ActionEnvelope:
    return ActionEnvelope(command_id=command_id, lease_epoch=0, origin_source_s=1.0,
                          clock=ClockDomain.SIM, valid_until_s=1.02, payload=payload,
                          correlation_id="execution-0")


TARGET = CartesianImpedanceTarget(target_pose_m=(0.0, 0.0, -0.01), k_z_n_per_m=500.0)


@pytest.mark.parametrize("outcome, status", [
    (CommandOutcome(accepted=True), AckStatus.CONTROLLER_ACCEPTED),
    (CommandOutcome(accepted=False, reason="command_rejected"), AckStatus.REJECTED),
])
def test_legacy_session_answers_each_envelope_synchronously(outcome, status):
    inner = _SimSession(outcome)
    ack = LegacySession(inner).submit(_envelope(TARGET))
    assert inner.applied == [TARGET]
    assert ack == Ack(7, status, outcome.reason)


def test_legacy_session_rejects_a_payload_it_does_not_declare():
    inner = _SimSession(CommandOutcome(accepted=True))
    ack = LegacySession(inner).submit(_envelope(object()))
    assert inner.applied == [], "no silent substitution"
    assert ack == Ack(7, AckStatus.REJECTED, "unsupported_payload")
