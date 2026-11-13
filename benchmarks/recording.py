"""Bounded recording of one real HEB MuJoCo episode into the replay format
defined in `evaluation.replay`.

The recorder attaches through `mujoco_backend.build_realization(
machine_factory=...)`. `RecordingMachine` is a `MuJoCoMachine` whose session
is wrapped in the backend-neutral `benchmarks.episode_log.LoggingSession`
(telemetry and events) plus the MuJoCo-only add-ons: the state stream read
after every physics step, the model, disturbance and fault-window events. It
never writes physics state, so recording cannot change the episode
(tests/test_recording_replay.py proves this A/A).

    python -m benchmarks.recording --scenario insert_dropout_large

writes into $M0_ARTIFACT_DIR (or --out). `scripts/record_episode.py` wraps
this in an M0 run.
"""
from __future__ import annotations

import os

# Read when pyarrow first loads. Under mimalloc, recorder peak-RSS overhead grew 2.8 MB
# from the 1.1 s to the 10.9 s slide horizon; under the system pool 0.4 MB (measured).
# pa.set_memory_pool() after import did not help (measured).
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

import argparse
import json
import math
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

import mujoco
import numpy as np
import pyarrow as pa

from benchmarks.episode_log import EpisodeLog, LoggingSession
from benchmarks.heb import mujoco_backend, systems, tasks
from benchmarks.heb.metrics import grade_run
from benchmarks.heb.protocol import Scenario, SystemRun
from evaluation import replay as fmt
from haplous.adapters.mujoco_adapter import MuJoCoMachine, _MuJoCoSession

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


class EpisodeRecorder:
    """Owns the open files of one recording. One session per recorder."""

    def __init__(self, out_dir: Path, chunk_ticks: int = 1024) -> None:
        self.out = out_dir
        self.chunk_ticks = chunk_ticks
        self.log = EpisodeLog(out_dir)
        self.stream: _StateStream | None = None
        self.machine: RecordingMachine | None = None

    def machine_factory(self, bundle, *, config) -> RecordingMachine:
        if self.machine is not None:
            raise RuntimeError("one machine per recorder")
        self.machine = RecordingMachine(bundle, config=config, recorder=self)
        return self.machine

    def attach_model(self, model: mujoco.MjModel) -> None:
        mujoco.mj_saveLastXML(str(self.out / fmt.MODEL_XML), model)
        mujoco.mj_saveModel(model, str(self.out / fmt.MODEL_MJB), None)
        self.log.dt = float(model.opt.timestep)
        self.stream = _StateStream(self.out / fmt.STATE, model, self.chunk_ticks)

    def _tick_at(self, t_s: float) -> int:
        return int(math.ceil(t_s / self.log.dt - 1e-6))

    def finish(self, scenario: Scenario, run: SystemRun, evidence, graded: tuple) -> dict[str, Any]:
        assert self.machine is not None and self.stream is not None
        config = self.machine._config
        windows = [("dropout", lo, hi) for lo, hi in config.dropout_windows_s]
        windows += [("stale", lo, hi) for lo, hi, _ in config.stale_windows_s]
        windows += [("command_rejection", lo, hi) for lo, hi in config.command_rejection_windows_s]
        log = self.log
        for name, lo, hi in windows:
            log.machine_event(self._tick_at(lo), f"{name}_window_start", start_s=lo, end_s=hi)
            log.machine_event(int(math.floor(hi / log.dt + 1e-6)), f"{name}_window_end", start_s=lo, end_s=hi)
        if config.fault_at_s is not None:
            log.machine_event(self._tick_at(config.fault_at_s), "fault_scheduled", fault_at_s=config.fault_at_s)
        log.haplous_events(evidence.events if evidence is not None else ())
        self.stream.close()
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
            "timestep_s": log.dt,
            "n_ticks": self.stream.total,
            "n_observations": log.n_observations,
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
        log.close(metadata)
        return metadata


class _RecordingSession(LoggingSession):
    """Neutral logging plus the MuJoCo add-ons. A tick is one physics step
    (a fault step does not advance MjData.time, so it is not a tick)."""

    def __init__(self, inner: _MuJoCoSession, recorder: EpisodeRecorder) -> None:
        super().__init__(inner, recorder.log, clock=lambda: float(inner._data.time))
        self._stream = recorder.stream
        self._stream.append(0, inner._data)

    def step(self) -> None:
        tick, disturbances_before = self.tick, set(self.inner._applied_disturbances)
        super().step()
        for t_s in sorted(self.inner._applied_disturbances - disturbances_before):
            self.log.machine_event(tick, "disturbance_applied", scheduled_s=t_s)
        if self.tick != tick:
            self._stream.append(self.tick, self.inner._data)


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
        return _RecordingSession(super().open_session(start_position_m=start_position_m, horizon_s=horizon_s),
                                 self.recorder)


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
        run = systems.HaplousSystem().run(realization, scenario)
        grade_run(scenario, realization.bundle, run)  # same work as the recorded path, so RSS comparisons isolate recording
        return run, realization, None
    recorder = EpisodeRecorder(out_dir, chunk_ticks)
    realization = mujoco_backend.build_realization(scenario, machine_factory=recorder.machine_factory)
    with _capture_runtime() as runtimes:
        run = systems.HaplousSystem().run(realization, scenario)
    graded = grade_run(scenario, realization.bundle, run)
    metadata = recorder.finish(scenario, run, runtimes[0].evidence if runtimes else None, graded)
    return run, realization, metadata


def scenario_by_id(scenario_id: str, offset_m: float | None = None) -> Scenario:
    registered = tasks.scenarios_by_id()
    if scenario_id not in registered:
        raise KeyError(f"unknown scenario id {scenario_id!r}; no registered task family declares it")
    found = registered[scenario_id]
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
