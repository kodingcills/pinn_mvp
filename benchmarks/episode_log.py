"""Backend-neutral episode log (design M5-L,
docs/eval_program/designs/M5_backend_neutral_episode_log.md).

`LoggingMachine` wraps any Haplous `Machine`; its session writes what crosses
the machine-session boundary and changes nothing it passes through:

    telemetry.jsonl  every observation the session returned, every command
                     handed to it and its CommandOutcome, by session tick
    events.jsonl     machine events (faults) and every Haplous EvidenceEvent
    metadata.json    format, timestep, counts, clocks, outcome

No simulator is imported here. A backend adds its own extras on top:
`benchmarks.recording` adds the MuJoCo state stream, model and fault-window
events through `EpisodeLog` and `LoggingSession`.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from haplous.machine import (
    CommandOutcome,
    Machine,
    MachineObservation,
    MachineSession,
    ServoCommand,
)

FORMAT = "haplous-episode-log-v1"
TELEMETRY = "telemetry.jsonl"
EVENTS = "events.jsonl"
METADATA = "metadata.json"
# Metadata keys every episode log carries, whatever the backend.
METADATA_KEYS = ("clocks", "format", "n_observations", "n_ticks", "outcome", "system", "timestep_s")
_OPAQUE = ("extra", "payload")  # backend- or event-specific content; not part of the row shape


def _observation_row(obs: MachineObservation | None) -> dict[str, Any] | None:
    if obs is None:
        return None
    row = asdict(obs)
    row["extra"] = dict(obs.extra)
    return row


class EpisodeLog:
    """Owns the open telemetry and event files of one episode. `dt` maps
    ticks to seconds; a backend may set it after construction."""

    def __init__(self, out_dir: Path, dt: float = 0.0) -> None:
        self.out = out_dir
        self.dt = dt
        self.n_observations = 0
        out_dir.mkdir(parents=True, exist_ok=True)
        self._telemetry = open(out_dir / TELEMETRY, "w", encoding="utf-8")
        self._events = open(out_dir / EVENTS, "w", encoding="utf-8")

    def _line(self, fh, row: dict[str, Any]) -> None:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def observation(self, tick: int, obs: MachineObservation | None) -> None:
        self.n_observations += obs is not None
        self._line(self._telemetry, {"tick": tick, "host_monotonic_ns": time.monotonic_ns(),
                                     "kind": "observation", "observation": _observation_row(obs)})

    def command(self, tick: int, command: ServoCommand, outcome: CommandOutcome) -> None:
        self._line(self._telemetry, {"tick": tick, "host_monotonic_ns": time.monotonic_ns(),
                                     "kind": "command", "command": asdict(command), "outcome": asdict(outcome)})

    def machine_event(self, tick: int, kind: str, **payload: Any) -> None:
        self._line(self._events, {"source": "machine", "tick": tick, "t_s": tick * self.dt,
                                  "clock": "sim", "kind": kind, "payload": payload})

    def haplous_events(self, events: Iterable) -> None:
        for event in events:
            # Evidence t_s is the runtime's own time claim (observation source time).
            self._line(self._events, {"source": "haplous", "seq": event.seq, "tick": round(event.t_s / self.dt),
                                      "t_s": event.t_s, "clock": "sim", "kind": event.kind,
                                      "payload": event.payload})

    def close(self, metadata: dict[str, Any]) -> None:
        self._telemetry.close()
        self._events.close()
        (self.out / METADATA).write_text(json.dumps(metadata, indent=2, sort_keys=True, default=str) + "\n")


class LoggingSession:
    """A MachineSession that logs everything it passes through. `tick`
    counts session steps that advanced the machine: with `clock`, a step
    counts only when the clock value changed; without, every step counts."""

    def __init__(self, inner: MachineSession, log: EpisodeLog,
                 clock: Callable[[], float] | None = None) -> None:
        self.inner = inner
        self.log = log
        self._clock = clock
        self.tick = 0

    def observe(self) -> MachineObservation | None:
        obs = self.inner.observe()
        self.log.observation(self.tick, obs)
        return obs

    def apply(self, command: ServoCommand) -> CommandOutcome:
        outcome = self.inner.apply(command)
        self.log.command(self.tick, command, outcome)
        return outcome

    def step(self) -> None:
        fault_before = self.inner.fault
        clock_before = self._clock() if self._clock is not None else None
        self.inner.step()
        if self.inner.fault != fault_before:
            self.log.machine_event(self.tick, "machine_fault", fault=self.inner.fault)
        if self._clock is None or self._clock() != clock_before:
            self.tick += 1

    def stop(self) -> None:
        self.inner.stop()

    @property
    def ended(self) -> bool:
        return self.inner.ended

    @property
    def fault(self) -> str | None:
        return self.inner.fault


class LoggingMachine:
    """Machine view over any Machine whose one session is logged into `out_dir`."""

    def __init__(self, inner: Machine, out_dir: Path) -> None:
        self.inner = inner
        self.session: LoggingSession | None = None
        self.log = EpisodeLog(out_dir, dt=inner.capabilities().state_period_s)

    def capabilities(self):
        return self.inner.capabilities()

    def observe(self) -> MachineObservation | None:
        return self.inner.observe()

    def open_session(self, *, start_position_m, horizon_s) -> LoggingSession:
        if self.session is not None:
            raise RuntimeError("an episode log holds exactly one session")
        self.session = LoggingSession(
            self.inner.open_session(start_position_m=start_position_m, horizon_s=horizon_s), self.log)
        return self.session

    def stop(self) -> None:
        self.inner.stop()

    def health(self):
        return self.inner.health()

    def finish(self, evidence, *, system: str, outcome: dict[str, Any], **extra: Any) -> dict[str, Any]:
        """Write the Haplous evidence events and metadata, close the files."""
        self.log.haplous_events(evidence.events if evidence is not None else ())
        metadata = {
            "format": FORMAT,
            "system": system,
            "timestep_s": self.log.dt,
            "n_ticks": self.session.tick if self.session is not None else 0,
            "n_observations": self.log.n_observations,
            "clocks": {"tick": "session step index", "t_s": "machine seconds",
                       "host_monotonic_ns": "recording host time.monotonic_ns()"},
            "outcome": outcome,
            **extra,
        }
        self.log.close(metadata)
        return metadata


def _fields(row: dict[str, Any], prefix: str = "") -> Iterable[str]:
    for key, value in row.items():
        if isinstance(value, dict) and key not in _OPAQUE:
            yield from _fields(value, f"{prefix}{key}.")
        else:
            yield prefix + key


def _union(rows: Iterable[set[str]]) -> list[str]:
    fields = set().union(*rows)
    # A null where other rows carry a record (a dropped observation) is the same field.
    return sorted(f for f in fields if not any(g.startswith(f + ".") for g in fields))


def log_schema(out_dir: Path) -> dict[str, Any]:
    """Row shape of an episode log: field paths per telemetry kind and per
    event source, and which shared metadata keys are present. Two logs
    with equal schemas are the same log format."""
    by_group: dict[str, dict[str, list[set[str]]]] = {"telemetry": {}, "events": {}}
    for name, file, group_key in (("telemetry", TELEMETRY, "kind"), ("events", EVENTS, "source")):
        for line in (out_dir / file).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            by_group[name].setdefault(row[group_key], []).append(set(_fields(row)))
    metadata = json.loads((out_dir / METADATA).read_text(encoding="utf-8"))
    return {
        **{name: {group: _union(rows) for group, rows in sorted(groups.items())}
           for name, groups in by_group.items()},
        "metadata": [key for key in METADATA_KEYS if key in metadata],
    }
