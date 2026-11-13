"""M3.1: the M2 record → archive → replay pipeline, unchanged, on the second
HEB task family (constrained slide)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.recording import run_episode, scenario_by_id
from evaluation import replay as rp
from evaluation.run_registry import Registry
from tests.test_recording_replay import DEFECTS, _flip_byte, _reseal

REPO = Path(__file__).resolve().parents[1]
RUN_ID = "m3-test-slide"
SCENARIO = "slide_disturbance_recoverable_ceiling"


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("slide_recordings")
    proc = subprocess.run(
        [sys.executable, "scripts/record_episode.py", "--root", str(root), "--run-id", RUN_ID,
         "--scenario", SCENARIO, "--chunk-ticks", "512"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert Registry(root).state(RUN_ID) == "VERIFIED"
    return root


def test_slide_fresh_process_replay_loads_no_runtime_and_matches_pose(recorded, tmp_path):
    report_path = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "evaluation.replay", RUN_ID, "--root", str(recorded), "--report", str(report_path)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(report_path.read_text())
    assert report["forbidden_modules_loaded"] == []
    assert report["pose_check"]["pass"] and report["pose_check"]["observations_compared"] > 3000
    assert report["outcome"]["disposition"] == "COMPLETE"
    assert report["outcome"]["bounded_success"] is False, "the 1.5 N ceiling is breached by facet taps"
    kinds = {e["kind"] for e in rp._jsonl(Registry(recorded).run_dir(RUN_ID) / "artifacts" / rp.EVENTS)}
    assert {"disturbance_applied", "constraint_violation"} <= kinds


@pytest.mark.parametrize("scenario_id", [SCENARIO, "slide_machine_fault"])
def test_recording_does_not_perturb_the_slide_episode(scenario_id, tmp_path):
    scenario = scenario_by_id(scenario_id)
    plain, plain_real, _ = run_episode(scenario, None)
    recorded_run, rec_real, metadata = run_episode(scenario, tmp_path)
    assert recorded_run.observations == plain.observations
    assert recorded_run.detail["applied_command_times"] == plain.detail["applied_command_times"]
    assert (recorded_run.disposition, recorded_run.reason, recorded_run.refusal_codes) == (
        plain.disposition, plain.reason, plain.refusal_codes)
    assert float(rec_real.machine._data.time) == float(plain_real.machine._data.time)
    assert metadata["n_observations"] == len(plain.observations)


@pytest.mark.parametrize(("name", "mutate", "message"), DEFECTS)
def test_planted_defects_make_slide_replay_refuse(recorded, name, mutate, message):
    _reseal(recorded, f"defect-{name}", mutate, source=RUN_ID)
    with pytest.raises(rp.ReplayRefused, match=message):
        rp.replay(recorded, f"defect-{name}", rrd_path=None, mp4_path=None, report_path=None)


def test_tampered_slide_archive_is_refused_by_m0_check(recorded):
    _reseal(recorded, "defect-tampered", lambda art: None, source=RUN_ID)
    _flip_byte(Registry(recorded).run_dir("defect-tampered") / "artifacts" / rp.STATE, 100)
    with pytest.raises(rp.ReplayRefused, match="artifact hash mismatch: state.arrow"):
        rp.replay(recorded, "defect-tampered", rrd_path=None, mp4_path=None, report_path=None)
