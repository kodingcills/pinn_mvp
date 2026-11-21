"""g1lib on an asynchronous machine: what the HEB incumbent (synchronous outcomes) cannot show."""
from __future__ import annotations

from benchmarks.g1lib import Ack, Sample, Sent, Supervisor, Task


class FakeMachine:
    """1 s ticks, fresh samples, acks after ``ack_delay`` ticks; ``duplicate`` repeats every ack."""

    def __init__(self, ack_delay=1, duplicate=False, authority_until=None, modes=("vel",)):
        self.t, self.ack_delay, self.duplicate = 0.0, ack_delay, duplicate
        self.authority_until, self.modes = authority_until, modes
        self.sent, self.stops, self.opened, self._queue, self._seq = [], [], False, [], 0

    def now(self):
        return self.t

    def peek(self):
        return Sample(self.t, 0.0, self.t)

    def open(self):
        self.opened = True

    sample = peek

    def health(self):
        return None

    def has_authority(self):
        return self.authority_until is None or self.t < self.authority_until

    def command_modes(self):
        return self.modes

    def send(self, command, valid_until_s):
        self._seq += 1
        self.sent.append((self.t, command))
        self._queue.append((self.t + self.ack_delay, self._seq))
        return Sent("pending", self._seq)

    def poll_acks(self):
        due = [seq for t, seq in self._queue if t <= self.t]
        self._queue = [(t, s) for t, s in self._queue if t > self.t]
        return [Ack(s, "accepted") for s in due for _ in range(2 if self.duplicate else 1)]

    def hold_command(self):
        return 0.0

    def stop(self):
        self.stops.append(self.t)

    def step(self):
        self.t += 1.0

    def ended(self):
        return self.t >= 20

    def fault(self):
        return None


class Mover:
    def begin(self, value):
        pass

    def step(self, value):
        return 1.0


def task(**kw):
    base = dict(required_modes={"vel"}, offered_modes=("vel",), max_state_age_s=0.5, max_untrusted_ticks=3,
                complete=lambda h: h[-1] >= 10, command_validity_s=2.0)
    return Task(**{**base, **kw})


def run(machine, **kw):
    return Supervisor().run(machine, Mover(), task(**kw))


def test_in_time_and_duplicate_acks_complete_without_interference():
    for duplicate in (False, True):
        m = FakeMachine(duplicate=duplicate)
        out = run(m)
        assert (out.disposition, m.sent) == ("COMPLETE", [(float(t), 1.0) for t in range(10)])
        assert any(e["kind"] == "ack_ignored" for e in out.events) == duplicate


def test_late_acks_hold_motion_and_time_out():
    m = FakeMachine(ack_delay=3)  # validity 2 s: every ack is late
    out = run(m)
    assert (out.disposition, out.reason) == ("ABORTED", "ack_timeout")
    first_expiry = next(e["t_s"] for e in out.events if e["kind"] in ("ack_late", "ack_expired"))
    assert all(cmd == 0.0 for t, cmd in m.sent if t >= first_expiry), "late acks never restore motion"
    assert m.stops and [e["t_s"] for e in out.events] == sorted(e["t_s"] for e in out.events)


def test_authority_loss_stops_at_once():
    m = FakeMachine(authority_until=4)
    out = run(m)
    assert (out.disposition, out.reason, m.stops) == ("ABORTED", "authority_lost", [4.0])


def test_missing_mode_refused_before_opening():
    m = FakeMachine(modes=())
    out = Supervisor().run(m, Mover(), task(offered_modes=()))
    assert (out.disposition, out.codes, m.opened, m.sent) == ("REFUSED", ("controller_interface_unavailable",),
                                                              False, [])
