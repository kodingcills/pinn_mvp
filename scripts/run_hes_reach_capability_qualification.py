"""Isolated capability-calibration qualification rerun.

Runs only if the envelope campaign selects a reach-capability calibration.
It changes nothing but the reach constant and re-runs the runtime arm over
the frozen qualification population (n=1000, seed=20260916) with the
selected ``max_initial_offset_m``. The unmediated and supervisor arms are
reused from the frozen qualification run: the realisation is deterministic
and independent of the reach constant, so those arms are bit-identical.

Then it recomputes the HES metric vector, the hard gates, and the
regression comparison against the frozen qualification analysis.

Outputs: results/hes_reach_capability_envelope/qualification/
  episodes.parquet, manifest.json, provenance.json, analysis.json,
  gates.json, regression.json, comparison.md, summary.json
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.heb.metrics import grade_run  # noqa: E402
from benchmarks.heb.mujoco_backend import build_realization  # noqa: E402
from benchmarks.heb.population import population  # noqa: E402
from benchmarks.heb.protocol import Scenario  # noqa: E402
from benchmarks.heb.systems import HaplousSystem  # noqa: E402
from hes.gates import evaluate_hard_gates  # noqa: E402
from hes.regression import (  # noqa: E402
    compare_metric_vectors,
    paired_rate_delta,
    slice_gates,
)
from hes.run import Run  # noqa: E402

FROZEN = ROOT / "results" / "hes" / "v3_hardening_qualification"
ENVELOPE = Path(
    os.environ.get("HES_ENVELOPE_RESULTS", ROOT / "results" / "hes_reach_capability_envelope")
)
OUT = ENVELOPE / "qualification"
RAW = OUT / "haplous_rows.jsonl"
N = 1000
SEED = 20260916
RUNTIME_ARM = "haplous_mediated"
BASELINE_ARM = "direct_minimal"
EXPERIMENT_ID = "reach_capability_qualification"
SHARD_SIZE = 20
NESTED = ("refusal_codes", "hard_violations", "task_metrics", "tags")


def selected_cap() -> float:
    dec = json.loads((ENVELOPE / "decision.json").read_text(encoding="utf-8"))
    cap = dec.get("selected_cap_m")
    if cap is None:
        raise SystemExit("envelope campaign selected no calibration; refusing to qualify")
    return float(cap)


def implemented_cap() -> float:
    from benchmarks.heb.tasks import implementation_for

    return float(implementation_for("peg_insertion").max_initial_offset_m)


def _row(arm: str, scenario: Scenario, run, realization, shard_of: dict) -> dict:
    graded, task_success, bounded, violations, metrics = grade_run(
        scenario, realization.bundle, run
    )
    shard_index = shard_of.get(scenario.id, 0)
    return {
        "schema_version": "haplous-episode-v1",
        "experiment_id": EXPERIMENT_ID,
        "shard_id": f"{EXPERIMENT_ID}-s{shard_index:05d}",
        "shard_index": shard_index,
        "arm": arm,
        "episode_id": scenario.id,
        "scenario_id": scenario.id,
        "seed": SEED,
        "graded_disposition": graded,
        "reported_disposition": run.disposition,
        "reason": run.reason,
        "task_success": bool(task_success),
        "bounded_success": bool(bounded),
        "wall_s": run.wall_s,
        "n_hard_violations": len(violations),
        # Nested fields stay as Python objects in memory; _write_parquet
        # serialises them for the parquet column type. The frozen rows are
        # decoded on read, so combined analysis sees one representation.
        "refusal_codes": list(run.refusal_codes),
        "hard_violations": [
            {
                "code": v.get("code"),
                "value": v.get("value"),
                "limit": v.get("limit"),
                "t_s": v.get("t_s"),
            }
            for v in violations
        ],
        "task_metrics": metrics,
        "tags": list(scenario.tags or []),
    }


def _scenario_payload(s: Scenario) -> str:
    fields = (
        "id", "task_family", "facet_count", "clearance_delta_m", "plate_thickness_m",
        "peg_radius_m", "start_position_m", "initial_offset_m", "initial_bearing_rad",
        "path_start_m", "path_end_m", "min_force_n", "max_force_n", "max_tilt_deg",
        "deadline_s", "state_max_age_s", "position_tolerance_m", "persistence_s",
        "recovery_permitted_conditions", "max_recovery_actions", "receive_delay_s",
        "dropout_windows_s", "stale_windows_s", "command_rejection_windows_s",
        "fault_at_s", "disturbances", "recovery_settle_s", "recoverable",
        "expected_disposition", "infeasible_request", "tags",
    )
    return json.dumps({k: getattr(s, k) for k in fields}, sort_keys=True)


def _run_worker(payload: str) -> dict:
    scenario = Scenario(**json.loads(payload))
    realization = build_realization(scenario)
    run = HaplousSystem().run(realization, scenario)
    shard_index = int(scenario.id.rsplit("_", 1)[-1]) // SHARD_SIZE
    return _row(RUNTIME_ARM, scenario, run, realization, {scenario.id: shard_index})


def run_haplous(workers: int, limit: int | None) -> list[dict]:
    scenarios = population(N, SEED)
    if limit:
        scenarios = scenarios[:limit]
    OUT.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if RAW.is_file():
        for line in RAW.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["scenario_id"])
    todo = [s for s in scenarios if s.id not in done]
    if todo:
        print(f"running {len(todo)} haplous episodes (workers={workers})", flush=True)
        payloads = [_scenario_payload(s) for s in todo]
        with open(RAW, "a", encoding="utf-8") as fh:
            if workers <= 1:
                for payload in payloads:
                    fh.write(json.dumps(_run_worker(payload), sort_keys=True) + "\n")
                    fh.flush()
            else:
                with multiprocessing.Pool(workers) as pool:
                    for row in pool.imap_unordered(_run_worker, payloads):
                        fh.write(json.dumps(row, sort_keys=True) + "\n")
                        fh.flush()
    return [
        json.loads(line)
        for line in RAW.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def frozen_rows() -> list[dict]:
    import pyarrow.parquet as pq

    rows = pq.read_table(FROZEN / "episodes.parquet").to_pylist()
    for row in rows:
        for key in NESTED:
            value = row.get(key)
            if isinstance(value, str):
                try:
                    row[key] = json.loads(value)
                except json.JSONDecodeError:
                    pass
    return rows


def _manifest() -> dict:
    manifest = json.loads((FROZEN / "manifest.json").read_text(encoding="utf-8"))
    manifest["experiment_id"] = EXPERIMENT_ID
    manifest["description"] = (
        "Isolated reach-capability calibration of the hardened v3 candidate: "
        "Haplous-mediated rerun over the frozen qualification population with "
        "the selected max_initial_offset_m; direct and supervisor arms reused "
        "from the frozen qualification run (deterministic, reach-independent)."
    )
    manifest["derived_from"] = {
        "qualification": "results/hes/v3_hardening_qualification",
        "envelope_campaign": "results/hes_reach_capability_envelope",
        "selected_max_initial_offset_m": implemented_cap(),
    }
    return manifest


def _provenance() -> dict:
    provenance = json.loads((FROZEN / "provenance.json").read_text(encoding="utf-8"))
    provenance.pop("hes_migration", None)
    provenance["recorded_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    try:
        provenance["git_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        provenance["git_dirty"] = bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    provenance["calibration"] = {
        "selected_max_initial_offset_m": implemented_cap(),
        "declared_max_initial_offset_m_before": 0.003,
        "scope": "reach capability only; no other runtime semantics changed",
        "baseline_arms_source": "results/hes/v3_hardening_qualification/episodes.parquet",
    }
    return provenance


def _write_parquet(rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    encoded = []
    for row in rows:
        out = dict(row)
        for key in NESTED:
            if key in out and not isinstance(out[key], str):
                out[key] = json.dumps(out[key])
        encoded.append(out)
    pq.write_table(pa.Table.from_pylist(encoded), OUT / "episodes.parquet")


def build(workers: int, limit: int | None) -> dict:
    cap = selected_cap()
    impl = implemented_cap()
    assert abs(cap - impl) < 1e-12, (
        f"runtime implements {impl} but the envelope selected {cap}; "
        "apply the single-constant calibration before qualifying"
    )
    haplous = run_haplous(workers, limit)
    frozen = frozen_rows()
    others = [r for r in frozen if str(r["arm"]) != RUNTIME_ARM]
    combined = others + haplous
    combined.sort(key=lambda r: (str(r["scenario_id"]), str(r["arm"])))

    manifest = _manifest()
    provenance = _provenance()
    run = Run(
        root=OUT,
        rows=combined,
        manifest=manifest,
        provenance=provenance,
        experiment_id=EXPERIMENT_ID,
        arms=tuple(sorted({str(r["arm"]) for r in combined})),
        baseline_arm=BASELINE_ARM,
    )
    analysis = run.analysis()
    gates = evaluate_hard_gates(analysis, runtime_arm=RUNTIME_ARM, rows=combined)

    reference = json.loads((FROZEN / "hes_analysis.json").read_text(encoding="utf-8"))["analysis"]
    candidate_vector = analysis["metric_arm_vector"][RUNTIME_ARM]
    reference_vector = reference["metric_arm_vector"][RUNTIME_ARM]
    regression = compare_metric_vectors(candidate_vector, reference_vector)
    frozen_haplous = [r for r in frozen if str(r["arm"]) == RUNTIME_ARM]
    paired = paired_rate_delta(haplous, frozen_haplous, field="bounded_success")
    slices = slice_gates(
        analysis.get("slices", {}), reference.get("slices", {}),
        arm=RUNTIME_ARM, metric="bounded_success", min_slice_n=30,
    )
    frozen_gates = json.loads((FROZEN / "hes_gates.json").read_text(encoding="utf-8"))
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "selected_max_initial_offset_m": cap,
        "n_scenarios": len({str(r["scenario_id"]) for r in combined}),
        "n_episodes": len(combined),
        "n_haplous_rerun": len(haplous),
        "runtime_arm": RUNTIME_ARM,
        "baseline_arm": BASELINE_ARM,
        "analysis": analysis,
        "hard_gates": gates,
        "regression": regression,
        "paired_bounded_success_delta": paired,
        "slice_gates": slices,
        "frozen_hard_gates": frozen_gates,
        "frozen_haplous_vector": reference_vector,
        "candidate_haplous_vector": candidate_vector,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    _write_parquet(combined)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "analysis.json").write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "gates.json").write_text(json.dumps(gates, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "regression.json").write_text(json.dumps(regression, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "comparison.md").write_text(_comparison(summary), encoding="utf-8")
    return summary


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{100 * v:.1f}%"


def _comparison(summary: dict) -> str:
    a = summary["analysis"]["metric_arm_vector"]
    ref = summary["frozen_haplous_vector"]
    lines = ["# Reach-Capability Calibration Qualification", ""]
    lines.append(
        f"Selected `max_initial_offset_m = {summary['selected_max_initial_offset_m']}` m; "
        f"{summary['n_scenarios']} scenarios, {summary['n_episodes']} episodes."
    )
    lines.append("")
    lines.append("## Hard gates")
    lines.append("")
    for gate in summary["hard_gates"].get("gates", []):
        lines.append(
            f"- {gate['gate_id']} {gate['name']}: "
            f"{'PASS' if gate['passed'] else 'FAIL'} "
            f"(observed={gate['observed']}, threshold={gate['threshold']})"
        )
    lines.append("")
    lines.append("## Runtime arm metric deltas vs frozen qualification")
    lines.append("")
    lines.append("| metric | candidate | frozen | delta | direction | gate |")
    lines.append("|---|---|---|---|---|---|")
    for gate in summary["regression"]["gates"]:
        lines.append(
            f"| {gate['metric']} | {gate['candidate']} | {gate['reference']} | "
            f"{gate['delta']} | {gate['direction']} | "
            f"{'pass' if gate['passed'] else 'FAIL'} |"
        )
    lines.append("")
    pair = summary["paired_bounded_success_delta"]
    lines.append(
        f"Paired bounded-success delta (candidate - frozen): "
        f"{pair.get('delta')} over {pair.get('n_pairs')} pairs "
        f"(95% CI {pair.get('ci_low')}..{pair.get('ci_high')})."
    )
    lines.append("")
    lines.append("## Slice gates")
    lines.append("")
    lines.append(
        f"passed={summary['slice_gates']['passed']}, "
        f"checked={summary['slice_gates']['n_checked']}, "
        f"violations={len(summary['slice_gates']['violations'])}, "
        f"underpowered={len(summary['slice_gates']['underpowered'])}"
    )
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    lines.append(
        f"- system BSR: {_pct(a[RUNTIME_ARM]['system']['bounded_success']['value'])} "
        f"(frozen {_pct(ref['system']['bounded_success']['value'])})"
    )
    lines.append(
        f"- coverage: {_pct(a[RUNTIME_ARM]['system']['coverage']['value'])} "
        f"(frozen {_pct(ref['system']['coverage']['value'])})"
    )
    lines.append(
        f"- preservation: "
        f"{_pct(a[RUNTIME_ARM]['non_interference']['preservation_rate']['value'])} "
        f"(frozen {_pct(ref['non_interference']['preservation_rate']['value'])})"
    )
    lines.append(
        f"- false refusal: "
        f"{_pct(a[RUNTIME_ARM]['authority']['false_refusal_rate']['value'])} "
        f"(frozen {_pct(ref['authority']['false_refusal_rate']['value'])})"
    )
    lines.append(
        f"- violation given acceptance: "
        f"{_pct(a[RUNTIME_ARM]['authority']['violation_given_acceptance']['value'])} "
        f"(frozen {_pct(ref['authority']['violation_given_acceptance']['value'])})"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 2))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    summary = build(args.workers, args.limit)
    print(json.dumps({
        "selected_cap_m": summary["selected_max_initial_offset_m"],
        "hard_gates_qualified": summary["hard_gates"].get("qualified"),
        "regression_passed": summary["regression"]["passed"],
        "n_episodes": summary["n_episodes"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())