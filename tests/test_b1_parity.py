"""#16 parity regression: haplous_mediated trials re-run through the
LegacySession / SimTickSource path reproduce, field for field, the
pre-#16 trials committed in results/b1/parity.json (driver:
scripts/od1_parity.py). The full 108-trial sweep is the driver's job;
these trials cover accepted and rejected acks, observation-loss ticks, a
machine fault and a slide."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.heb import mujoco_backend, tasks
from scripts.od1_parity import _digest, trial_row

REPORT = Path(__file__).resolve().parents[1] / "results" / "b1" / "parity.json"
TRIALS = ("insert_command_rejection_persistent", "insert_dropout_large", "insert_machine_fault",
          "slide_stale_mid_slide")


@pytest.mark.parametrize("scenario_id", TRIALS)
def test_haplous_mediated_matches_the_pre_16_trial(scenario_id):
    committed = {(t["scenario_id"], t["system"]): t for t in json.loads(REPORT.read_text())["trials"]}
    before = committed[(scenario_id, "haplous_mediated")]
    scenario = tasks.scenarios_by_id()[scenario_id]
    row = trial_row(mujoco_backend.build_realization(scenario), scenario, "haplous_mediated")
    assert {f: row[f] for f in before["before"]} == before["before"]
    assert _digest(row) == before["compared_sha256"]["before"]
    assert row["stamp_clocks"] == ["sim"]
