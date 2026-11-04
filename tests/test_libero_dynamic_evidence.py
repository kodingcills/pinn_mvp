"""Timing-semantics checks on committed LIBERO-Dynamic CPU runs (results/m0_registry).

Claims under test (upstream docs/realtime_runtime_v2.md @ ef12443):
  1. Fixed sync latency advances simulated time by exactly L (whole 2 ms substeps plus a
     residual substep), NOT rounded up to 20 Hz control periods.
  2. Simulated latency is decoupled from wall-clock time.
  3. Control steps are 0.05 s of simulated time.
Expected values come from each run's own manifest (config.latency_s), not from this file.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from evaluation.run_registry import Registry

ROOT = Path(__file__).resolve().parents[1] / "results" / "m0_registry"
CONTROL_DT = 0.05
TOL = 1e-6  # traces round to 6 decimals


def _runs():
    if not ROOT.exists():
        return []
    reg = Registry(ROOT)
    out = []
    for run_id in sorted({e["run_id"] for e in reg.events() if e["event"] == "registered"}):
        traces = sorted((reg.run_dir(run_id) / "artifacts" / "out").glob("*/*/traces/*.jsonl"))
        if traces:
            latency = reg.manifest(run_id)["config"].get("latency_s", 0.0)  # pre-flag runs used 0
            out.append(pytest.param(reg, run_id, latency, traces, id=run_id))
    return out


RUNS = _runs()


def test_both_latency_regimes_are_covered():
    latencies = {p.values[2] for p in RUNS}
    assert 0.0 in latencies and any(lat > 0 for lat in latencies)
    # 0.147199 s is not a whole number of 2 ms substeps, so it exercises the residual substep.
    assert any(not math.isclose((lat / 0.002) % 1, 0, abs_tol=1e-9) for lat in latencies)


@pytest.mark.parametrize("reg,run_id,latency,traces", RUNS)
def test_sync_latency_is_exact_not_quantized(reg, run_id, latency, traces):
    for trace in traces:
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        assert [r["step_idx"] for r in rows] == list(range(len(rows)))
        prev_t = 0.0
        for r in rows:
            held = r["sync_latency_before_action_s"]
            assert held == pytest.approx(latency if r["replan_boundary"] else 0.0, abs=TOL)
            assert r["sim_time_s"] - prev_t == pytest.approx(CONTROL_DT + held, abs=2 * TOL)
            prev_t = r["sim_time_s"]
        if latency > 0:
            quantized = math.ceil(latency / CONTROL_DT) * CONTROL_DT
            assert not math.isclose(latency, quantized)  # the alternative is distinguishable
            assert rows[0]["sim_time_s"] != pytest.approx(CONTROL_DT + quantized, abs=TOL)


@pytest.mark.parametrize("reg,run_id,latency,traces", RUNS)
def test_manifests_declare_fixed_decoupled_latency(reg, run_id, latency, traces):
    for path in sorted((reg.run_dir(run_id) / "artifacts" / "out").glob("*/*/manifests/*.json")):
        m = json.loads(path.read_text())
        rows = [json.loads(line) for line in (path.parent.parent / "traces" / f"{path.stem}--runtime-v2.jsonl").read_text().splitlines()]
        assert m["calibrated_latency_seconds"] == latency
        assert m["sim_latency_decoupled_from_wallclock"] is True
        expected_sim = len(rows) * CONTROL_DT + sum(r["sync_latency_before_action_s"] for r in rows)
        assert m["policy_rollout_sim_time_s"] == pytest.approx(expected_sim, abs=1e-5)
        for chunk in m["ready_chunks"]:
            md = chunk["metadata"]
            assert md["latency_source"] == "fixed"
            assert md["sim_latency_s"] == latency
            assert md["physics_time_quantized_to_control_steps"] is False
            assert md["latency_equivalent_control_steps"] == pytest.approx(latency / CONTROL_DT, abs=1e-6)
