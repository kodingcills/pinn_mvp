"""M5.2: one episode log format at the machine-session boundary, for any backend."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pyarrow as pa
import pytest

from benchmarks import episode_log as el
from benchmarks.recording import run_episode, scenario_by_id
from evaluation import replay as rp
from haplous import HaplousRuntime
from haplous.adapters.external import ExternalMachineAdapter
from haplous.machine import ServoCommand
from tests.test_external_adapter import FakeExternalTask, _fake_intent, _HoldBehavior

REPO = Path(__file__).resolve().parents[1]
M2_RECORDING = REPO / "results/recordings/registry/runs/m2-heb-insert_dropout_large/artifacts"
M5 = REPO / "results/m5"
_HOST_NS = re.compile(r'"host_monotonic_ns": \d+')


def _same_format(a: dict, b: dict) -> None:
    assert a["telemetry"] == b["telemetry"]
    assert a["metadata"] == b["metadata"] == list(el.METADATA_KEYS)
    shared = a["events"].keys() & b["events"].keys()
    assert "haplous" in shared
    assert {k: a["events"][k] for k in shared} == {k: b["events"][k] for k in shared}


def _fake_episode(log_dir: Path | None):
    task = FakeExternalTask()
    task.reset(seed=3)
    machine = ExternalMachineAdapter(task)
    if log_dir is not None:
        machine = el.LoggingMachine(machine, log_dir)
    seen = []

    class _Seeing(_HoldBehavior):
        def step(self, observation):
            seen.append(observation)
            return super().step(observation)

    runtime = HaplousRuntime(machine, behaviors={"external_slide": _Seeing})
    runtime.submit(_fake_intent())
    evidence = runtime.execute()
    if log_dir is not None:
        machine.finish(evidence, system="haplous_mediated", outcome={"disposition": runtime.state.name})
    return seen, [(e.kind, e.t_s, e.payload) for e in evidence.events], task.observe(), dict(task.task_result())


def test_file_names_match_the_replay_format():
    assert (el.TELEMETRY, el.EVENTS, el.METADATA) == (rp.TELEMETRY, rp.EVENTS, rp.METADATA)


def test_mujoco_recording_through_the_neutral_log_matches_the_committed_m2_recording(tmp_path):
    run_episode(scenario_by_id("insert_dropout_large"), tmp_path)
    assert (tmp_path / rp.EVENTS).read_bytes() == (M2_RECORDING / rp.EVENTS).read_bytes()
    assert (tmp_path / rp.METADATA).read_bytes() == (M2_RECORDING / rp.METADATA).read_bytes()
    fresh, committed = ((d / rp.TELEMETRY).read_text() for d in (tmp_path, M2_RECORDING))
    assert _HOST_NS.sub("", fresh) == _HOST_NS.sub("", committed)
    fresh_t, committed_t = (pa.ipc.open_file(str(d / rp.STATE)).read_all() for d in (tmp_path, M2_RECORDING))
    for column in ("tick", "sim_time_s", "state"):
        assert fresh_t.column(column).equals(committed_t.column(column)), column


def test_mujoco_fault_step_is_not_a_tick(tmp_path):
    _, _, metadata = run_episode(scenario_by_id("insert_machine_fault"), tmp_path)
    table = pa.ipc.open_file(str(tmp_path / rp.STATE)).read_all()
    times = table.column("sim_time_s").to_pylist()
    assert table.column("tick").to_pylist() == list(range(metadata["n_ticks"]))
    assert times == sorted(set(times)), "sim time strictly increases per tick"
    events = [json.loads(line) for line in (tmp_path / rp.EVENTS).read_text().splitlines()]
    assert [e["tick"] for e in events if e["kind"] == "machine_fault"] == [metadata["n_ticks"] - 1]


def test_logging_does_not_perturb_an_external_episode(tmp_path):
    assert _fake_episode(tmp_path) == _fake_episode(None)
    telemetry = [json.loads(line) for line in (tmp_path / el.TELEMETRY).read_text().splitlines()]
    assert {row["kind"] for row in telemetry} == {"observation", "command"}


def test_external_log_has_the_format_of_the_committed_mujoco_recording(tmp_path):
    _fake_episode(tmp_path)
    _same_format(el.log_schema(tmp_path), el.log_schema(M2_RECORDING))


def test_session_fault_is_logged_as_a_machine_event(tmp_path):
    task = FakeExternalTask()
    machine = el.LoggingMachine(ExternalMachineAdapter(task), tmp_path)
    session = machine.open_session(start_position_m=(0.0, 0.0, 0.005), horizon_s=5.0)
    while not session.ended:
        session.apply(ServoCommand(session.observe().pose_m, 500.0))
        session.step()
    machine.finish(None, system="none", outcome={})
    events = [json.loads(line) for line in (tmp_path / el.EVENTS).read_text().splitlines()]
    assert [(e["source"], e["kind"], e["tick"], e["payload"]) for e in events] == [
        ("machine", "machine_fault", 119, {"fault": "horizon_exhausted"})]


@pytest.mark.parametrize("backend", ["mujoco", "robosuite"])
def test_committed_backend_runs_pass_aa(backend):
    summary = json.loads((M5 / f"episode_log_{backend}.json").read_text())
    assert summary["aa_identical"] and all(summary["aa"].values())
    assert summary["outcome"]["disposition"] == "COMPLETE"


def test_committed_mujoco_and_robosuite_logs_share_one_format():
    mujoco, robosuite = (json.loads((M5 / f"episode_log_{b}.json").read_text())["schema"]
                         for b in ("mujoco", "robosuite"))
    _same_format(mujoco, robosuite)
    assert el.log_schema(M5 / "robosuite-log") == robosuite
    assert el.log_schema(M2_RECORDING) == mujoco
