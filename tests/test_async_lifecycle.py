"""#17: asynchronous command lifecycle and authority lease. Late,
duplicate, out-of-order and post-disconnect acks never authorize a
command; lost authority takes the stop/abort path."""
from __future__ import annotations

import itertools
from collections.abc import Callable

from hypothesis import given, settings
from hypothesis import strategies as st

from haplous.adapters.legacy import CommandOutcome, LegacySession
from haplous.capabilities import CommandMode
from haplous.intent import SuccessCondition
from haplous.lifecycle import CommandLedger, Verdict
from haplous.machine import (
    Ack,
    AckStatus,
    ActionEnvelope,
    CartesianImpedanceTarget,
    ClockDomain,
    LeasedSession,
    MachineObservation,
    Stamp,
)
from haplous.runtime import HaplousRuntime
from haplous.state import ExecutionState
from tests.test_haplous_semantics import MAX_AGE, PERIOD, HoldBehavior, make_intent, std_caps

A = AckStatus
CLOCK = ClockDomain.CONTROLLER
TARGET = CartesianImpedanceTarget(target_pose_m=(0.0, 0.0, -0.01), k_z_n_per_m=500.0)
# Epochs are granted by the session side, monotonically within one process.
_EPOCHS = itertools.count(1)

Plan = Callable[[ActionEnvelope], tuple[AckStatus, list[tuple[int, AckStatus]]]]


class AsyncFakeSession:
    """A leased, asynchronous session on a fake controller clock: tick k
    observes at k * PERIOD. plan(envelope) scripts the immediate ack and
    the later acks as (delay_ticks, status), which may repeat (duplication)
    or come in any order (reordering). While disconnected(tick) the session
    answers UNKNOWN for what it had outstanding and for every submit; acks
    it held are delivered after reconnection. At revoke_at another holder
    takes the lease. While stale(tick) observations arrive older than the
    freshness bound."""

    def __init__(self, plan: Plan, disconnected: Callable[[int], bool] = lambda t: False,
                 revoke_at: int | None = None, horizon_ticks: int = 300,
                 stale: Callable[[int], bool] = lambda t: False) -> None:
        self.plan, self.disconnected, self.revoke_at = plan, disconnected, revoke_at
        self.stale = stale
        self.horizon_ticks = horizon_ticks
        self.tick = 0
        self.queue: list[tuple[int, Ack]] = []
        self.submitted: list[tuple[int, ActionEnvelope]] = []
        self.stop_calls = 0
        self.fault = None
        self._epoch: int | None = None
        self._was_disconnected = False

    def acquire_lease(self) -> int:
        self._epoch = next(_EPOCHS)
        return self._epoch

    @property
    def lease_epoch(self) -> int | None:
        if self.revoke_at is not None and self.tick >= self.revoke_at and self._epoch is not None:
            self._epoch, self.revoke_at = next(_EPOCHS), None
        return self._epoch

    def observe(self) -> MachineObservation:
        t = self.tick * PERIOD
        source = t - 1.5 * MAX_AGE if self.stale(self.tick) else t
        return MachineObservation(Stamp(source, t, CLOCK), (0.0, 0.0, -0.010), 15.0, 0.0)

    def submit(self, envelope: ActionEnvelope) -> Ack:
        self.submitted.append((self.tick, envelope))
        if envelope.lease_epoch != self.lease_epoch:
            return Ack(envelope.command_id, A.REJECTED, "stale_lease_epoch")
        if self.disconnected(self.tick):
            return Ack(envelope.command_id, A.UNKNOWN, "disconnected")
        immediate, later = self.plan(envelope)
        self.queue += [(self.tick + d, Ack(envelope.command_id, s)) for d, s in later]
        return Ack(envelope.command_id, immediate)

    def poll_acks(self) -> list[Ack]:
        if self.disconnected(self.tick):
            if self._was_disconnected:
                return []
            self._was_disconnected = True
            outstanding = dict.fromkeys(ack.command_id for _, ack in self.queue)
            return [Ack(cid, A.UNKNOWN, "disconnected") for cid in outstanding]
        self._was_disconnected = False
        due = [ack for t, ack in self.queue if t <= self.tick]
        self.queue = [(t, ack) for t, ack in self.queue if t > self.tick]
        return due

    def stop(self) -> None:
        self.stop_calls += 1

    @property
    def ended(self) -> bool:
        return self.tick >= self.horizon_ticks


class AsyncFakeMachine:
    def __init__(self, session: AsyncFakeSession) -> None:
        self.session = session

    def capabilities(self):
        return std_caps(leased_authority=True)

    def observe(self):
        return self.session.observe()

    def open_session(self, *, start_position_m, horizon_s):
        return self.session

    def stop(self) -> None:
        self.session.stop()

    def health(self):
        return {"status": "ok"}


class _Ticks:
    def __init__(self, session: AsyncFakeSession) -> None:
        self.session = session

    def wait(self) -> None:
        self.session.tick += 1


def run(session: AsyncFakeSession, complete_at: int | None = None):
    runtime = HaplousRuntime(AsyncFakeMachine(session), behaviors={"motion": HoldBehavior},
                             tick_source=_Ticks(session))
    done = (lambda w: False) if complete_at is None else (
        lambda w: w[-1].stamp.source_s >= complete_at * PERIOD - 1e-9)
    assert runtime.submit(make_intent(success_condition=SuccessCondition("done", done))).outcome == "SUPPORTED"
    evidence = runtime.execute()
    return runtime, evidence.events


def kinds(events, kind):
    return [e for e in events if e.kind == kind]


def tick_of(event) -> int:
    return round(event.t_s / PERIOD)


def submitted_tick(session: AsyncFakeSession, command_id: int) -> int:
    return next(t for t, env in session.submitted if env.command_id == command_id)


def dispatch(*later: tuple[int, AckStatus]) -> Plan:
    return lambda env: (A.ACCEPTED_FOR_DISPATCH, list(later))


# -- runtime behaviour under injected faults -----------------------------------


def test_the_fake_is_a_leased_session():
    assert isinstance(AsyncFakeSession(dispatch()), LeasedSession)


def test_timely_async_acceptance_runs_to_completion_under_the_granted_epoch():
    session = AsyncFakeSession(dispatch((1, A.CONTROLLER_ACCEPTED), (2, A.EXECUTING)))
    runtime, events = run(session, complete_at=30)
    assert runtime.state is ExecutionState.COMPLETE
    (granted,) = kinds(events, "authority_granted")
    assert {env.lease_epoch for _, env in session.submitted} == {granted.payload["epoch"]}
    assert not kinds(events, "command_rejected") and not kinds(events, "ack_ignored")


def test_acks_late_past_validity_never_restore_authority():
    def plan(env):
        late = submitted_tick(session, env.command_id) < 20
        return A.ACCEPTED_FOR_DISPATCH, [(6 if late else 1, A.CONTROLLER_ACCEPTED)]

    session = AsyncFakeSession(plan)
    runtime, events = run(session, complete_at=60)
    late = [e for e in kinds(events, "ack_ignored") if e.payload["reason"] == "late"]
    assert late and all(submitted_tick(session, e.payload["command_id"]) < 20 for e in late)
    assert {e.payload["reason"] for e in kinds(events, "command_rejected")} == {"expired"}
    (hold,) = kinds(events, "temporal_hold_entered")
    assert hold.payload["cause"] == "command_rejection"
    resumed = kinds(events, "temporal_revalidation_resumed")
    assert tick_of(resumed[0]) == 21, "authority returns only with the first timely acceptance"
    assert runtime.state is ExecutionState.COMPLETE


def test_a_duplicated_rejection_counts_once():
    def plan(env):
        if env.command_id <= 3:
            return A.ACCEPTED_FOR_DISPATCH, [(1, A.CONTROLLER_ACCEPTED)]
        return A.ACCEPTED_FOR_DISPATCH, [(1, A.REJECTED), (1, A.REJECTED)]

    session = AsyncFakeSession(plan, horizon_ticks=20)
    _, events = run(session)
    rejected = kinds(events, "command_rejected")[:3]
    assert [e.payload["streak"] for e in rejected] == [1, 2, 3]
    assert len({e.t_s for e in rejected}) == 3, "one rejection per command, not per copy"
    assert {e.payload["reason"] for e in kinds(events, "ack_ignored")} == {"duplicate"}


def test_an_acceptance_arriving_after_the_rejection_never_restores_authority():
    def plan(env):
        if env.command_id <= 3:
            return A.ACCEPTED_FOR_DISPATCH, [(1, A.CONTROLLER_ACCEPTED)]
        return A.ACCEPTED_FOR_DISPATCH, [(1, A.REJECTED), (2, A.CONTROLLER_ACCEPTED)]

    session = AsyncFakeSession(plan)
    runtime, events = run(session)
    assert kinds(events, "temporal_hold_entered")
    assert not kinds(events, "temporal_revalidation_resumed")
    assert {e.payload["reason"] for e in kinds(events, "ack_ignored")} == {"out_of_order"}
    assert events[-1].payload["reason"] == "temporal_hold_budget_exhausted"


def test_completed_before_controller_accepted_counts_as_one_acceptance():
    session = AsyncFakeSession(dispatch((1, A.COMPLETED), (2, A.CONTROLLER_ACCEPTED)))
    runtime, events = run(session, complete_at=30)
    assert runtime.state is ExecutionState.COMPLETE
    assert not kinds(events, "command_rejected")
    ignored = kinds(events, "ack_ignored")
    assert ignored and {(e.payload["status"], e.payload["reason"]) for e in ignored} == {
        (A.CONTROLLER_ACCEPTED, "out_of_order")}


def test_an_acceptance_never_restores_authority_on_stale_state():
    def plan(env):
        if 4 <= env.command_id <= 6:
            return A.REJECTED, []
        return A.ACCEPTED_FOR_DISPATCH, [(2 if env.command_id == 7 else 1, A.CONTROLLER_ACCEPTED)]

    session = AsyncFakeSession(plan, stale=lambda t: 8 <= t < 12)
    runtime, events = run(session, complete_at=40)
    (hold,) = kinds(events, "temporal_hold_entered")
    assert hold.payload["cause"] == "command_rejection"
    resumed = kinds(events, "temporal_revalidation_resumed")
    # Tick 12 is fresh but follows a freshness gap; tick 13 submits; 14 accepts.
    assert tick_of(resumed[0]) == 14, "acceptances held while state is stale; resumes on fresh state"
    assert runtime.state is ExecutionState.COMPLETE


def test_revocation_mid_motion_stops_the_machine_and_aborts_with_evidence():
    session = AsyncFakeSession(dispatch((1, A.CONTROLLER_ACCEPTED)), revoke_at=10)
    runtime, events = run(session, complete_at=60)
    assert runtime.state is ExecutionState.ABORTED
    assert session.stop_calls >= 1
    (granted,) = kinds(events, "authority_granted")
    (revoked,) = kinds(events, "authority_revoked")
    assert revoked.payload["held_epoch"] == granted.payload["epoch"]
    assert revoked.payload["session_epoch"] > granted.payload["epoch"]
    assert events[-1].payload == {"result": "ABORTED", "reason": "authority_revoked"}
    assert max(t for t, _ in session.submitted) < 10, "nothing submitted after revocation"


def test_disconnect_answers_unknown_and_held_acks_never_restore_authority():
    session = AsyncFakeSession(dispatch((3, A.CONTROLLER_ACCEPTED)),
                               disconnected=lambda t: 10 <= t < 12)
    runtime, events = run(session, complete_at=60)
    reasons = [e.payload["reason"] for e in kinds(events, "command_rejected")]
    assert reasons[:3] == ["disconnected"] * 3, "UNKNOWN counts as not accepted"
    (hold,) = kinds(events, "temporal_hold_entered")
    assert tick_of(hold) == 11
    stale = [e for e in kinds(events, "ack_ignored")
             if e.payload["reason"] == "post_disconnect" and e.payload["status"] == A.CONTROLLER_ACCEPTED]
    assert stale and all(tick_of(e) == 12 for e in stale), "held pre-disconnect acks delivered on reconnect"
    resumed = kinds(events, "temporal_revalidation_resumed")
    assert tick_of(resumed[0]) == 15, "resumes on a command issued after reconnection"
    assert runtime.state is ExecutionState.COMPLETE


def test_sessions_reject_envelopes_under_a_stale_epoch():
    session = AsyncFakeSession(dispatch(), revoke_at=0)
    held = session.acquire_lease()
    env = ActionEnvelope(1, held, 0.0, CLOCK, MAX_AGE, TARGET, "x")
    assert session.submit(env) == Ack(1, A.REJECTED, "stale_lease_epoch")


class _Applied:
    def __init__(self) -> None:
        self.applied: list = []

    def apply(self, command):
        self.applied.append(command)
        return CommandOutcome(accepted=True)


def test_legacy_session_holds_the_constant_epoch_zero():
    inner = _Applied()
    session = LegacySession(inner, frozenset({CommandMode.CARTESIAN_IMPEDANCE}))
    stale = ActionEnvelope(7, 1, 1.0, ClockDomain.SIM, 1.02, TARGET, "x")
    assert session.submit(stale) == Ack(7, A.REJECTED, "stale_lease_epoch")
    assert inner.applied == []
    assert session.poll_acks() == []


# -- ledger: each guard ---------------------------------------------------------


def _env(cid: int, epoch: int = 1, at: float = 0.0, clock: ClockDomain = CLOCK) -> ActionEnvelope:
    return ActionEnvelope(cid, epoch, at, clock, at + MAX_AGE, TARGET, "x")


def _ledger(*envelopes: ActionEnvelope, epoch: int = 1) -> CommandLedger:
    ledger = CommandLedger(epoch)
    for env in envelopes:
        ledger.issued(env)
    return ledger


def verdict(ledger: CommandLedger, cid: int, status: AckStatus, now: float = 0.0,
            clock: ClockDomain = CLOCK) -> tuple[Verdict, str | None]:
    j = ledger.judge(Ack(cid, status), now, clock)
    return j.verdict, j.reason


def test_ledger_accepts_a_timely_acceptance_once():
    ledger = _ledger(_env(1))
    assert verdict(ledger, 1, A.ACCEPTED_FOR_DISPATCH) == (Verdict.PROGRESS, None)
    assert verdict(ledger, 1, A.CONTROLLER_ACCEPTED) == (Verdict.ACCEPTED, None)
    assert verdict(ledger, 1, A.EXECUTING) == (Verdict.PROGRESS, None)
    assert verdict(ledger, 1, A.EXECUTING) == (Verdict.IGNORED, "duplicate")


def test_ledger_ignores_a_late_acceptance_and_expires_the_command():
    ledger = _ledger(_env(1))
    assert verdict(ledger, 1, A.CONTROLLER_ACCEPTED, now=MAX_AGE + 0.001) == (Verdict.IGNORED, "late")
    (j,) = ledger.expire(MAX_AGE + 0.001, CLOCK)
    assert (j.command_id, j.verdict, j.reason) == (1, Verdict.NOT_ACCEPTED, "expired")
    assert ledger.expire(1.0, CLOCK) == [], "expires once"


def test_ledger_never_compares_times_across_clock_domains():
    ledger = _ledger(_env(1))
    assert verdict(ledger, 1, A.CONTROLLER_ACCEPTED, clock=ClockDomain.WALL) == (Verdict.IGNORED, "late")


def test_ledger_ignores_a_stale_epoch():
    ledger = _ledger(_env(1, epoch=1), epoch=2)
    assert verdict(ledger, 1, A.CONTROLLER_ACCEPTED) == (Verdict.IGNORED, "stale_epoch")


def test_ledger_ignores_a_superseded_command():
    ledger = _ledger(_env(1), _env(2))
    assert verdict(ledger, 2, A.CONTROLLER_ACCEPTED) == (Verdict.ACCEPTED, None)
    assert verdict(ledger, 1, A.CONTROLLER_ACCEPTED) == (Verdict.IGNORED, "superseded")


def test_ledger_closes_everything_issued_before_an_unknown():
    ledger = _ledger(_env(1), _env(2))
    assert verdict(ledger, 2, A.UNKNOWN) == (Verdict.NOT_ACCEPTED, "unknown")
    assert verdict(ledger, 1, A.CONTROLLER_ACCEPTED) == (Verdict.IGNORED, "post_disconnect")
    assert ledger.expire(1.0, CLOCK) == [], "closed commands do not expire"
    ledger.issued(_env(3))
    assert verdict(ledger, 3, A.CONTROLLER_ACCEPTED) == (Verdict.ACCEPTED, None)


def test_ledger_never_accepts_a_rejected_command():
    ledger = _ledger(_env(1))
    assert verdict(ledger, 1, A.REJECTED) == (Verdict.NOT_ACCEPTED, None)
    assert verdict(ledger, 1, A.CONTROLLER_ACCEPTED) == (Verdict.IGNORED, "out_of_order")


def test_ledger_never_accepts_an_expired_command():
    ledger = _ledger(_env(1))
    assert ledger.expire(0.0, ClockDomain.WALL)
    assert verdict(ledger, 1, A.CONTROLLER_ACCEPTED) == (Verdict.IGNORED, "closed")


def test_ledger_ignores_an_ack_for_a_command_it_never_issued():
    assert verdict(_ledger(), 4, A.CONTROLLER_ACCEPTED) == (Verdict.IGNORED, "unknown_command")


# -- property: no ordering authorizes an invalid command ---------------------------

# Biased toward small id ranges, accepting statuses and the envelope's own
# clock, so that orderings reaching an acceptance are common.
_STATUS = st.sampled_from([*AckStatus, A.CONTROLLER_ACCEPTED, A.CONTROLLER_ACCEPTED, A.EXECUTING,
                           A.COMPLETED])
_SAME_CLOCK = st.sampled_from([True, True, True, False])
_EVENT = st.one_of(
    st.tuples(st.just("issue")),
    st.tuples(st.just("advance"), st.sampled_from([0.0, PERIOD, PERIOD, MAX_AGE, 2 * MAX_AGE]),
              _SAME_CLOCK),
    st.tuples(st.just("ack"), st.integers(1, 4), _STATUS, _SAME_CLOCK),
    st.tuples(st.just("ack"), st.integers(1, 4), _STATUS, _SAME_CLOCK),
    st.tuples(st.just("regrant")),
)


@settings(max_examples=3000, deadline=None)
@given(st.lists(_EVENT, max_size=40))
def test_no_event_ordering_authorizes_an_expired_stale_superseded_or_disconnected_command(events):
    ledger = CommandLedger(epoch=1)
    now, epoch, mark, newest = 0.0, 1, 0, 0
    issued: dict[int, ActionEnvelope] = {}
    closed: set[int] = set()

    def check(cid, j, ack_clock):
        nonlocal newest
        if j.verdict is Verdict.NOT_ACCEPTED:
            closed.add(cid)
        if j.verdict is not Verdict.ACCEPTED:
            return
        env = issued[cid]
        assert env.lease_epoch == epoch, "stale epoch authorized"
        assert ack_clock is env.clock and now <= env.valid_until_s, "expired envelope authorized"
        assert cid > mark, "post-disconnect command authorized"
        assert cid > newest, "superseded or already-authorized command authorized"
        assert cid not in closed, "rejected, failed or expired command authorized"
        newest = cid

    for event in events:
        if event[0] == "issue":
            cid = len(issued) + 1
            issued[cid] = _env(cid, epoch=epoch, at=now)
            ledger.issued(issued[cid])
        elif event[0] == "advance":
            now += event[1]
            clock = CLOCK if event[2] else ClockDomain.WALL
            for j in ledger.expire(now, clock):
                check(j.command_id, j, clock)
        elif event[0] == "regrant":
            epoch += 1
            ledger.epoch = epoch
        else:
            _, cid, status, same_clock = event
            clock = CLOCK if same_clock else ClockDomain.WALL
            if status is AckStatus.UNKNOWN and mark < cid <= len(issued):
                mark = len(issued)  # a disconnect closes everything issued so far
            j = ledger.judge(Ack(cid, status), now, clock)
            if cid in issued:
                check(cid, j, clock)
            else:
                assert j.verdict is Verdict.IGNORED
