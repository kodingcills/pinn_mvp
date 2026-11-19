"""Command lifecycle ledger (ARCH-001 decision 4, issue #17). Every
acknowledgement a session produces, immediately from submit() or later
from poll_acks(), is judged here before the runtime may treat it as
acceptance. An ack authorizes a command only when it is the first
acceptance of a live command: issued under the current lease epoch, not
superseded by a newer accepted command, not closed by a disconnect, and
still inside its envelope's validity horizon on the envelope's own clock.
Everything else is progress, a not-accepted verdict, or ignored."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from haplous.machine import Ack, AckStatus, ActionEnvelope, ClockDomain


class Verdict(StrEnum):
    ACCEPTED = "accepted"
    NOT_ACCEPTED = "not_accepted"
    PROGRESS = "progress"
    IGNORED = "ignored"


@dataclass(frozen=True)
class Judgement:
    command_id: int
    verdict: Verdict
    reason: str | None = None


# Lifecycle order. An ack that does not move a command strictly forward is
# a duplicate (same status) or out of order (anything else).
_RANK = {
    AckStatus.ACCEPTED_FOR_DISPATCH: 1,
    AckStatus.CONTROLLER_ACCEPTED: 2,
    AckStatus.EXECUTING: 3,
    AckStatus.COMPLETED: 4,
    AckStatus.REJECTED: 4,
    AckStatus.CANCELED: 4,
    AckStatus.FAULTED: 4,
    AckStatus.UNKNOWN: 4,
}
# A controller that executed or completed a command accepted it, even when
# its CONTROLLER_ACCEPTED ack arrives later or never.
_ACCEPTING = frozenset(
    {AckStatus.CONTROLLER_ACCEPTED, AckStatus.EXECUTING, AckStatus.COMPLETED}
)


@dataclass
class _Command:
    envelope: ActionEnvelope
    last: AckStatus | None = None
    accepted: bool = False
    closed: bool = False


class CommandLedger:
    """Judges acks for the envelopes one execution issued under `epoch`."""

    def __init__(self, epoch: int) -> None:
        self.epoch = epoch
        self._commands: dict[int, _Command] = {}
        self._pending: dict[int, _Command] = {}
        self._last_issued = 0
        self._newest_accepted = 0
        self._disconnected_through = 0

    def issued(self, envelope: ActionEnvelope) -> None:
        assert envelope.command_id > self._last_issued, "command ids increase"
        self._commands[envelope.command_id] = self._pending[envelope.command_id] = _Command(envelope)
        self._last_issued = envelope.command_id

    def judge(self, ack: Ack, now_s: float, clock: ClockDomain) -> Judgement:
        """Judge one ack received at `now_s` on `clock`."""
        cid = ack.command_id
        cmd = self._commands.get(cid)
        if cmd is None:
            return Judgement(cid, Verdict.IGNORED, "unknown_command")
        if cid <= self._disconnected_through:
            return Judgement(cid, Verdict.IGNORED, "post_disconnect")
        if ack.status is AckStatus.UNKNOWN:
            # The session lost track: everything issued so far is closed,
            # and nothing it later says about those commands counts.
            self._disconnected_through = self._last_issued
            self._pending.clear()
        if cmd.envelope.lease_epoch != self.epoch:
            return Judgement(cid, Verdict.IGNORED, "stale_epoch")
        if cmd.last is not None and _RANK[ack.status] <= _RANK[cmd.last]:
            reason = "duplicate" if ack.status is cmd.last else "out_of_order"
            return Judgement(cid, Verdict.IGNORED, reason)
        cmd.last = ack.status
        if ack.status is AckStatus.UNKNOWN:
            return Judgement(cid, Verdict.NOT_ACCEPTED, ack.reason or "unknown")
        if cid < self._newest_accepted:
            return Judgement(cid, Verdict.IGNORED, "superseded")
        if not _timely(cmd.envelope, now_s, clock):
            return Judgement(cid, Verdict.IGNORED, "late")
        if cmd.closed:
            return Judgement(cid, Verdict.IGNORED, "closed")
        if ack.status in _ACCEPTING:
            if cmd.accepted:
                return Judgement(cid, Verdict.PROGRESS)
            cmd.accepted = True
            self._pending.pop(cid, None)
            self._newest_accepted = cid
            return Judgement(cid, Verdict.ACCEPTED)
        if ack.status is AckStatus.ACCEPTED_FOR_DISPATCH:
            return Judgement(cid, Verdict.PROGRESS)
        cmd.closed = True
        self._pending.pop(cid, None)
        return Judgement(cid, Verdict.NOT_ACCEPTED, ack.reason)

    def expire(self, now_s: float, clock: ClockDomain) -> list[Judgement]:
        """Close every command still awaiting acceptance whose validity
        horizon has passed: it was never accepted in time."""
        expired = [cid for cid, cmd in self._pending.items()
                   if not _timely(cmd.envelope, now_s, clock)]
        for cid in expired:
            self._pending.pop(cid).closed = True
        return [Judgement(cid, Verdict.NOT_ACCEPTED, "expired") for cid in expired]


def _timely(envelope: ActionEnvelope, now_s: float, clock: ClockDomain) -> bool:
    # Times on different clocks are never compared: timeliness unprovable.
    return clock is envelope.clock and now_s <= envelope.valid_until_s
