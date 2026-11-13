"""Bounded recording of one real HEB MuJoCo episode into the replay format
defined in `evaluation.replay`.

The recorder attaches through `mujoco_backend.build_realization(
machine_factory=...)`. `RecordingMachine` is a `MuJoCoMachine` whose session
reads state after every physics step and copies what the session returned to
the runtime. It never writes physics state, so recording cannot change the
episode (tests/test_recording_replay.py proves this A/A).

    python -m benchmarks.recording --scenario insert_dropout_large

writes into $M0_ARTIFACT_DIR (or --out). `scripts/record_episode.py` wraps
this in an M0 run.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from unittest import mock

import mujoco
import numpy as np
import pyarrow as pa

from benchmarks.heb import mujoco_backend, systems
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.metrics import grade_run
from benchmarks.heb.protocol import Scenario, SystemRun
from evaluation import replay as fmt
from haplous.adapters.mujoco_adapter import MuJoCoMachine, _MuJoCoSession
from haplous.machine import CommandOutcome, MachineObservation, ServoCommand

TRACKED_SITE = "peg_tip"


class _StateStream:
    """Preallocated chunk buffers flushed as zstd Arrow record batches, so
    memory is fixed by `chunk_ticks`, not by rollout length."""

    def __init__(self, path: Path, model: mujoco.MjModel, chunk_ticks: int) -> None:
        self.size = mujoco.mj_stateSize(model, fmt.STATE_SPEC)
        self.schema = fmt.state_schema(self.size)
        self._model = model
        self._tick = np.empty(chunk_ticks, np.int64)
        self._time = np.empty(chunk_ticks, np.float64)
        self._host = np.empty(chunk_ticks, np.int64)
        self._state = np.empty((chunk_ticks, self.size), np.float64)
        self._n = 0
        self.total = 0
        # Threaded compression gives each pool thread its own zstd context and
        # allocator heap; peak RSS then grew with rollout length (measured).
        options = pa.ipc.IpcWriteOptions(compression="zstd", use_threads=False)
        self._writer = pa.ipc.new_file(str(path), self.schema, options=options)

    def append(self, tick: int, data: mujoco.MjData) -> None:
        i = self._n
        self._tick[i], self._time[i], self._host[i] = tick, data.time, time.monotonic_ns()
        mujoco.mj_getState(self._model, data, self._state[i], fmt.STATE_SPEC)
        self._n += 1
        self.total += 1
        if self._n == len(self._tick):
            self.flush()

    def flush(self) -> None:
        n = self._n
        if n == 0:
            return
        states = pa.FixedSizeListArray.from_arrays(pa.array(self._state[:n].ravel()), self.size)
        self._writer.write_batch(pa.record_batch(
            [pa.array(self._tick[:n]), pa.array(self._time[:n]), pa.array(self._host[:n]), states],
            schema=self.schema,
        ))
        self._n = 0

    def close(self) -> None:
        self.flush()
        self._writer.close()


def _observation_row(obs: MachineObservation | None) -> dict[str, Any] | None:
    if obs is None:
        return None
    row = asdict(obs)
    row["extra"] = dict(obs.extra)
    return row


class EpisodeRecorder:
    """Owns the open files of one recording. One session per recorder."""

    def __init__(self, out_dir: Path, chunk_ticks: int = 1024) -> None:
        self.out = out_dir
        self.chunk_ticks = chunk_ticks
        self.out.mkdir(parents=True, exist_ok=True)
        self._telemetry = open(self.out / fmt.TELEMETRY, "w", encoding="utf-8")
        self._events = open(self.out / fmt.EVENTS, "w", encoding="utf-8")
        self.stream: _StateStream | None = None
        self.machine: RecordingMachine | None = None
        self.n_observations = 0
        self.dt = 0.0

    def machine_factory(self, bundle, *, config) -> RecordingMachine:
        if self.machine is not None:
            raise RuntimeError("one machine per recorder")
        self.machine = RecordingMachine(bundle, config=config, recorder=self)
        return self.machine

    def attach_model(self, model: mujoco.MjModel) -> None:
        mujoco.mj_saveLastXML(str(self.out / fmt.MODEL_XML), model)
        mujoco.mj_saveModel(model, str(self.out / fmt.MODEL_MJB), None)
        self.dt = float(model.opt.timestep)
        self.stream = _StateStream(self.out / fmt.STATE, model, self.chunk_ticks)

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

    def _tick_at(self, t_s: float) -> int:
        return int(math.ceil(t_s / self.dt - 1e-6))

    def finish(self, scenario: Scenario, run: SystemRun, evidence, graded: tuple) -> dict[str, Any]:
        assert self.machine is not None and self.stream is not None
        config = self.machine._config
        windows = [("dropout", lo, hi) for lo, hi in config.dropout_windows_s]
        windows += [("stale", lo, hi) for lo, hi, _ in config.stale_windows_s]
        windows += [("command_rejection", lo, hi) for lo, hi in config.command_rejection_windows_s]
        for name, lo, hi in windows:
            self.machine_event(self._tick_at(lo), f"{name}_window_start", start_s=lo, end_s=hi)
            self.machine_event(int(math.floor(hi / self.dt + 1e-6)), f"{name}_window_end", start_s=lo, end_s=hi)
        if config.fault_at_s is not None:
            self.machine_event(self._tick_at(config.fault_at_s), "fault_scheduled", fault_at_s=config.fault_at_s)
        for event in evidence.events if evidence is not None else ():
            # Evidence t_s is the runtime's own sim-time claim (observation source time).
            self._line(self._events, {"source": "haplous", "seq": event.seq, "tick": round(event.t_s / self.dt),
                                      "t_s": event.t_s, "clock": "sim", "kind": event.kind,
                                      "payload": event.payload})
        self.stream.close()
        self._telemetry.close()
        self._events.close()
        disposition, task_success, bounded, violations, _ = graded
        model = self.machine._model
        metadata = {
            "format": fmt.FORMAT,
            "scenario_id": scenario.id,
            "system": run.system,
            "mujoco_version": mujoco.__version__,
            "model_sha256": fmt.sha256_file(self.out / fmt.MODEL_MJB),
            "model_xml_sha256": fmt.sha256_file(self.out / fmt.MODEL_XML),
            "state_spec": int(fmt.STATE_SPEC),
            "state_spec_name": "mjSTATE_INTEGRATION",
            "state_size": self.stream.size,
            "timestep_s": self.dt,
            "n_ticks": self.stream.total,
            "n_observations": self.n_observations,
            "chunk_ticks": self.chunk_ticks,
            "tracked_site": TRACKED_SITE,
            "nq": model.nq, "nv": model.nv,
            "clocks": {"tick": "physics step index (sim)", "sim_time_s": "MjData.time (sim)",
                       "t_s": "sim seconds", "host_monotonic_ns": "recording host time.monotonic_ns()"},
            "outcome": {
                "disposition": run.disposition, "reason": run.reason, "refusal_codes": list(run.refusal_codes),
                "graded_disposition": disposition, "task_success": task_success,
                "bounded_success": bounded, "hard_violations": list(violations),
            },
        }
        (self.out / fmt.METADATA).write_text(json.dumps(metadata, indent=2, sort_keys=True, default=str) + "\n")
        return metadata


class _RecordingSession(_MuJoCoSession):
    def __init__(self, machine: RecordingMachine, *, start_position_m, horizon_s) -> None:
        super().__init__(machine, start_position_m=start_position_m, horizon_s=horizon_s)
        self._recorder = machine.recorder
        self._tick = 0
        self._recorder.stream.append(0, self._data)

    def observe(self) -> MachineObservation | None:
        obs = super().observe()
        self._recorder.observation(self._tick, obs)
        return obs

    def apply(self, command: ServoCommand) -> CommandOutcome:
        outcome = super().apply(command)
        self._recorder.command(self._tick, command, outcome)
        return outcome

    def step(self) -> None:
        time_before, fault_before = self._data.time, self._fault
        disturbances_before = set(self._applied_disturbances)
        super().step()
        for t_s in sorted(self._applied_disturbances - disturbances_before):
            self._recorder.machine_event(self._tick, "disturbance_applied", scheduled_s=t_s)
        if self._fault != fault_before:
            self._recorder.machine_event(self._tick, "machine_fault", fault=self._fault)
        if self._data.time != time_before:
            self._tick += 1
            self._recorder.stream.append(self._tick, self._data)


class RecordingMachine(MuJoCoMachine):
    def __init__(self, bundle, *, config, recorder: EpisodeRecorder) -> None:
        if config.observation_decimation != 1:
            raise ValueError("recording assumes one physics step per session step (observation_decimation=1)")
        super().__init__(bundle, config=config)
        self.recorder = recorder
        recorder.attach_model(self._model)

    def open_session(self, *, start_position_m, horizon_s) -> _RecordingSession:
        if self._session is not None:
            raise RuntimeError("a recording holds exactly one session")
        self.open_session_calls.append((start_position_m, horizon_s))
        self._session = _RecordingSession(self, start_position_m=start_position_m, horizon_s=horizon_s)
        return self._session


@contextmanager
def _capture_runtime() -> Iterator[list]:
    """HaplousSystem keeps its runtime local; this captures it so the
    evidence events can be recorded without changing the system's code path.
    shortcut: patches systems.HaplousRuntime; expose evidence on SystemRun instead once systems.py may change."""
    captured: list = []

    class _Capturing(systems.HaplousRuntime):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            captured.append(self)

    with mock.patch.object(systems, "HaplousRuntime", _Capturing):
        yield captured


def run_episode(scenario: Scenario, out_dir: Path | None, chunk_ticks: int = 1024):
    """Run `haplous_mediated` on `scenario`, recording into `out_dir` when
    given. Returns (SystemRun, realization, metadata or None)."""
    if out_dir is None:
        realization = mujoco_backend.build_realization(scenario)
        return systems.HaplousSystem().run(realization, scenario), realization, None
    recorder = EpisodeRecorder(out_dir, chunk_ticks)
    realization = mujoco_backend.build_realization(scenario, machine_factory=recorder.machine_factory)
    with _capture_runtime() as runtimes:
        run = systems.HaplousSystem().run(realization, scenario)
    graded = grade_run(scenario, realization.bundle, run)
    metadata = recorder.finish(scenario, run, runtimes[0].evidence if runtimes else None, graded)
    return run, realization, metadata


def scenario_by_id(scenario_id: str, offset_m: float | None = None) -> Scenario:
    found = {s.id: s for s in insertion_scenarios()}[scenario_id]
    return found if offset_m is None else replace(found, id=f"{found.id}@offset={offset_m}", initial_offset_m=offset_m)


def _peak_rss_kb() -> int:
    # VmHWM, not ru_maxrss: ru_maxrss survives exec, so a child would report its parent's peak.
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1])
    raise RuntimeError("peak RSS needs Linux /proc/self/status")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.recording")
    parser.add_argument("--scenario", default="insert_dropout_large")
    parser.add_argument("--offset-m", type=float, help="override initial offset (longer horizons for RSS checks)")
    parser.add_argument("--out", type=Path, default=os.environ.get("M0_ARTIFACT_DIR"))
    parser.add_argument("--chunk-ticks", type=int, default=1024)
    parser.add_argument("--no-record", action="store_true", help="same episode without the recorder")
    args = parser.parse_args(argv)
    if args.out is None and not args.no_record:
        parser.error("--out or $M0_ARTIFACT_DIR is required")
    scenario = scenario_by_id(args.scenario, args.offset_m)
    run, realization, _ = run_episode(scenario, None if args.no_record else args.out, args.chunk_ticks)
    machine = realization.machine
    sim_s = float(machine._data.time)
    files = sorted(args.out.iterdir()) if not args.no_record else []
    print(json.dumps({
        "scenario_id": scenario.id,
        "recorded": not args.no_record,
        "disposition": run.disposition,
        "reason": run.reason,
        "sim_s": sim_s,
        "peak_rss_kb": _peak_rss_kb(),
        "artifact_bytes": sum(p.stat().st_size for p in files),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
