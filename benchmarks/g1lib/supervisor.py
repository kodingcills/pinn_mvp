"""G1 supervision as a reusable library: one machine-agnostic supervisor plus a small adapter
protocol that each machine implements (docs/eval_program/designs/G1_incumbent.md).

Semantics, extracted from the G1 incumbent and generalised to asynchronous command surfaces:

- Admission before any session: the behaviour's required command modes must all be offered by
  exact name (an empty or missing contract is refused), every caller-supplied admission check must
  pass, then a fresh state sample must be available (sensor readiness).
- Live, every tick, in this order: machine health, control authority, command-mode loss,
  acknowledgement bookkeeping, state trust, limits (with optional budgeted recovery), completion
  on trusted state, then one command.
- Untrusted state (missing or older than ``max_state_age_s``) or an unconfirmed command (no
  in-time acknowledgement before its validity horizon) holds: no behaviour command is issued, the
  adapter's hold command is sent instead, and more than ``max_untrusted_ticks`` consecutive held
  ticks stop the machine. A late acknowledgement never restores trust; only an in-time one does.
- Rejected commands are resent each tick (the machine keeps its last accepted command); more
  consecutive rejections than ``max_untrusted_ticks`` stop the machine.
- Every stop calls ``adapter.stop()``. Every run returns structured, time-ordered evidence.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

GUARDS = frozenset({
    "command_mode",       # admission: required command modes offered
    "sensor_ready",       # admission: fresh state before motion
    "machine_fault",      # live: unhealthy machine or session fault -> stop
    "authority",          # live: exclusive control lost -> stop
    "capability_loss",    # live: a required command mode disappears -> stop
    "stale",              # live: stale sample -> hold, watchdog timeout
    "dropout",            # live: missing sample -> hold, watchdog timeout
    "ack_timeout",        # live: command unconfirmed past its validity -> hold, timeout
    "command_rejection",  # live: rejected commands -> resend, timeout
})


@dataclass(frozen=True)
class Sample:
    """One state sample: source time, age when read, and the machine's own value."""

    t_s: float
    age_s: float
    value: Any


@dataclass(frozen=True)
class Sent:
    """Immediate result of a send: ``accepted`` / ``rejected`` (synchronous) or ``pending``
    (an acknowledgement for ``key`` arrives later through ``poll_acks``)."""

    status: str
    key: Any = None


@dataclass(frozen=True)
class Ack:
    key: Any
    status: str  # accepted | rejected


class MachineAdapter(Protocol):
    def now(self) -> float: ...
    def peek(self) -> Sample | None: ...          # readiness read before any session
    def open(self) -> None: ...                   # open a session / acquire control; may raise
    def sample(self) -> Sample | None: ...        # None: no sample this tick
    def health(self) -> str | None: ...           # None when healthy, else a status
    def has_authority(self) -> bool: ...
    def command_modes(self) -> Collection: ...
    def send(self, command: Any, valid_until_s: float) -> Sent: ...
    def poll_acks(self) -> Sequence[Ack]: ...
    def hold_command(self) -> Any: ...            # sent while holding; None sends nothing
    def stop(self) -> None: ...
    def step(self) -> None: ...                   # advance one control period
    def ended(self) -> bool: ...
    def fault(self) -> str | None: ...


class Behavior(Protocol):
    def begin(self, value: Any) -> None: ...      # (re)start from the live state
    def step(self, value: Any) -> Any: ...        # next command, or None


@dataclass(frozen=True)
class Recovery:
    """Budgeted retry on a limit trip: restart the behaviour from the live state and skip that
    tick's command. Each new trip consumes one retry; a trip still open after ``settle_s`` stops."""

    max_actions: int
    settle_s: float = 0.0


@dataclass(frozen=True)
class Limit:
    name: str
    breached: Callable[[Any], bool]
    reason: str
    recovery: Recovery | None = None


@dataclass(frozen=True)
class Task:
    required_modes: Collection | None             # None or empty: undeclared contract
    offered_modes: Collection                     # command modes from the capability read
    max_state_age_s: float
    max_untrusted_ticks: int
    complete: Callable[[list], bool]              # on the sample history, trusted ticks only
    admission_checks: tuple[tuple[str, bool], ...] = ()   # (refusal code, failed)
    limits: tuple[Limit, ...] = ()
    command_validity_s: float = math.inf


@dataclass
class Outcome:
    disposition: str                              # COMPLETE | ABORTED | REFUSED
    reason: str | None
    stage: str                                    # admission | open | start | run
    codes: tuple[str, ...] = ()
    history: list = field(default_factory=list)
    events: list[dict] = field(default_factory=list)


class Supervisor:
    """``disabled`` removes named library guards (planted-removal tests only)."""

    def __init__(self, disabled: Collection[str] = frozenset()) -> None:
        unknown = set(disabled) - GUARDS
        if unknown:
            raise ValueError(f"unknown guards {sorted(unknown)}")
        self.on = GUARDS - frozenset(disabled)

    def run(self, adapter: MachineAdapter, behavior: Behavior, task: Task) -> Outcome:
        on = self.on
        events: list[dict] = []
        history: list = []

        def log(kind: str, **data) -> None:
            events.append({"t_s": adapter.now(), "kind": kind, **data})

        def finish(disposition, reason, stage, codes=()) -> Outcome:
            log("outcome", outcome=disposition, reason=reason)
            return Outcome(disposition, reason, stage, tuple(codes), history, events)

        codes: list[str] = []
        if "command_mode" in on:
            if not task.required_modes:
                codes.append("command_contract_undeclared")
            elif any(m not in task.offered_modes for m in task.required_modes):
                codes.append("controller_interface_unavailable")
        codes += [code for code, failed in task.admission_checks if failed]
        if not codes and "sensor_ready" in on:
            ready = adapter.peek()
            if ready is None or ready.age_s > task.max_state_age_s:
                codes.append("sensor_not_ready")
        if codes:
            log("refused", codes=list(codes))
            return finish("REFUSED", "pre_motion_refusal", "admission", codes)
        log("admitted")
        try:
            adapter.open()
        except Exception as exc:
            return finish("ABORTED", f"machine_error_{type(exc).__name__}", "open")
        log("session_open")
        sample = adapter.sample()
        if sample is None:
            adapter.stop()
            log("stop")
            return finish("ABORTED", "state_unavailable_at_start", "start")
        behavior.begin(sample.value)
        history.append(sample.value)
        last = sample
        untrusted = rejected = 0
        confirmed = True
        pending: dict = {}            # command key -> validity horizon
        episodes: dict[str, float] = {}
        retries: dict[str, int] = {}

        def stop(disposition, reason) -> Outcome:
            adapter.stop()
            log("stop", reason=reason)
            return finish(disposition, reason, "run")

        def rejection_timeout() -> bool:
            return "command_rejection" in on and rejected > task.max_untrusted_ticks

        while True:
            if "machine_fault" in on and (status := adapter.health()) is not None:
                log("machine_unhealthy", status=status)
                return stop("ABORTED", "machine_unhealthy")
            if "authority" in on and not adapter.has_authority():
                return stop("ABORTED", "authority_lost")
            if "capability_loss" in on:
                offered = adapter.command_modes()
                if task.required_modes and any(m not in offered for m in task.required_modes):
                    return stop("ABORTED", "controller_interface_lost")
            now = adapter.now()
            for ack in adapter.poll_acks():
                horizon = pending.pop(ack.key, None)
                if horizon is None:
                    log("ack_ignored", key=ack.key, status=ack.status)  # duplicate or after expiry
                elif ack.status != "accepted":
                    rejected += 1
                    log("command_rejected", key=ack.key)
                elif now <= horizon:
                    confirmed, rejected = True, 0
                else:
                    confirmed = False
                    log("ack_late", key=ack.key)
            if rejection_timeout():
                return stop("ABORTED", "command_rejection_timeout")
            for key in [k for k, horizon in pending.items() if now > horizon]:
                del pending[key]
                confirmed = False
                log("ack_expired", key=key)
            if sample is None and "dropout" not in on:
                sample = last  # guard removed: act on the last sample
            fresh = sample is not None and (sample.age_s <= task.max_state_age_s or "stale" not in on)
            acked = confirmed or "ack_timeout" not in on
            command = None
            if not (fresh and acked):
                untrusted += 1
                if untrusted == 1:
                    log("hold", cause="state" if not fresh else "ack")
                if untrusted > task.max_untrusted_ticks:
                    return stop("ABORTED", "watchdog_timeout" if not fresh else "ack_timeout")
                command = adapter.hold_command()
            else:
                if untrusted:
                    log("resume")
                untrusted = 0
                skip = False
                for limit in task.limits:
                    if not limit.breached(sample.value):
                        episodes.pop(limit.name, None)
                        continue
                    if limit.recovery is None:
                        return stop("ABORTED", limit.reason)
                    if limit.name not in episodes:
                        if retries.get(limit.name, 0) >= limit.recovery.max_actions:
                            return stop("ABORTED", "recovery_budget_exhausted")
                        retries[limit.name] = retries.get(limit.name, 0) + 1
                        episodes[limit.name] = sample.t_s
                        log("recovery", limit=limit.name, attempt=retries[limit.name])
                        behavior.begin(sample.value)
                        skip = True
                    elif limit.recovery.settle_s > 0.0 and sample.t_s - episodes[limit.name] > limit.recovery.settle_s:
                        return stop("ABORTED", "recovery_timeout")
                if not episodes and task.complete(history):
                    return stop("COMPLETE", None)
                if not skip:
                    command = behavior.step(sample.value)
            if command is not None:
                horizon = adapter.now() + task.command_validity_s
                sent = adapter.send(command, horizon)
                if sent.status == "pending":
                    pending[sent.key] = horizon
                elif sent.status == "accepted":
                    rejected = 0
                else:
                    rejected += 1
                    if rejected == 1:
                        log("command_rejected", key=sent.key)
                if rejection_timeout():
                    return stop("ABORTED", "command_rejection_timeout")
            adapter.step()
            if adapter.ended():
                break
            sample = adapter.sample()
            if sample is not None:
                history.append(sample.value)
                last = sample
        if "machine_fault" in on and (fault := adapter.fault()) is not None:
            return stop("ABORTED", f"machine_fault_{fault}")
        if task.complete(history):
            return stop("COMPLETE", None)
        return stop("ABORTED", "horizon_exhausted")
