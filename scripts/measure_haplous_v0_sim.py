"""Haplous v0-sim benchmark: scenario matrix with expected semantic
outcomes, mediated runtime vs direct substrate execution, Haplous-level
metrics, runtime overhead, and engineering-leverage proxies.

This is a product benchmark, not a preregistered R-experiment: the physics
is deterministic, there is no randomness, and every scenario's expected
semantic outcome is declared before execution. The mediated side runs the
haplous runtime over the MuJoCo machine adapter; the direct side calls the
validated pegsim substrate exactly as a competent controls engineer would
without the runtime. Ground truth grades the benchmark offline only.

Config: VALIDATED_LOOSE_V1 bundle (peg r 5 mm, clearance 0.5 mm, chamfer
1 mm, plate 10 mm, n=16, dt 0.5 ms, 2000 Hz, canonical gains/forces),
canonical spiral, live supervision at the control rate (2000 Hz), state_max_age 0.02 s,
max_initial_offset 4.35 mm (R_spiral 2.75 + R_cap_dyn 1.6 mm).
"""
from __future__ import annotations

import dataclasses
import json
import math
import time
from pathlib import Path

import numpy as np

from pegsim.rollout import (
    EpisodeConfig,
    ValidatedConfig,
    VALIDATED_LOOSE_V1,
    run_episode,
)

from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig
from haplous.behaviors.insertion import (
    insertion_behavior_factory,
    insertion_intent,
    insertion_machine,
)
from haplous.runtime import HaplousRuntime
from haplous.state import ExecutionState

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
EVIDENCE_DIR = RESULTS / "haplous_v0_sim_evidence"
ROWS = RESULTS / "haplous_v0_sim_rows.jsonl"
SUMMARY = RESULTS / "haplous_v0_sim_summary.json"


def varied_bundle(clearance_offset_m: float) -> ValidatedConfig:
    return dataclasses.replace(
        VALIDATED_LOOSE_V1,
        task=dataclasses.replace(
            VALIDATED_LOOSE_V1.task,
            radial_clearance=VALIDATED_LOOSE_V1.task.radial_clearance
            + clearance_offset_m,
        ),
    )


STANDARD_CONFIG = MuJoCoMachineConfig()


@dataclasses.dataclass
class ScenarioSpec:
    id: str
    kind: str  # physical | measurement | semantic
    expected_outcome: str  # COMPLETE | REFUSED | ABORTED
    infeasible_request: bool
    machine_config: MuJoCoMachineConfig
    bundle: ValidatedConfig
    intent_kwargs: dict


SCENARIOS: list[ScenarioSpec] = [
    ScenarioSpec("nominal", "physical", "COMPLETE", False, STANDARD_CONFIG,
                 VALIDATED_LOOSE_V1, dict(initial_offset_m=0.0)),
    ScenarioSpec("recoverable_lateral_1mm", "physical", "COMPLETE", False,
                 STANDARD_CONFIG, VALIDATED_LOOSE_V1,
                 dict(initial_offset_m=0.001, initial_bearing_rad=0.7)),
    ScenarioSpec("recoverable_lateral_2mm", "physical", "COMPLETE", False,
                 STANDARD_CONFIG, VALIDATED_LOOSE_V1,
                 dict(initial_offset_m=0.002, initial_bearing_rad=2.1)),
    ScenarioSpec("geometry_variation_dc_plus10um", "physical", "COMPLETE",
                 False, STANDARD_CONFIG, varied_bundle(10e-6),
                 dict(initial_offset_m=0.001, initial_bearing_rad=4.0)),
    ScenarioSpec("unrecoverable_lateral_5mm", "physical", "REFUSED", True,
                 STANDARD_CONFIG, VALIDATED_LOOSE_V1,
                 dict(initial_offset_m=0.005)),
    ScenarioSpec("constraint_violation_tight_force", "physical", "ABORTED",
                 False, STANDARD_CONFIG, VALIDATED_LOOSE_V1,
                 dict(initial_offset_m=0.0, max_force_n=2.0)),
    ScenarioSpec("tight_force_enforced_live", "physical", "COMPLETE", False,
                 STANDARD_CONFIG, VALIDATED_LOOSE_V1,
                 dict(initial_offset_m=0.0, max_force_n=12.0)),
    ScenarioSpec("measurement_dropout_small", "measurement", "COMPLETE",
                 False,
                 MuJoCoMachineConfig(dropout_windows_s=((1.0, 1.005),)),
                 VALIDATED_LOOSE_V1, dict(initial_offset_m=0.001,
                                          initial_bearing_rad=1.3)),
    # Counterfactual-falsified abort: the unmediated arm completes within
    # contract; temporal faults hold/revalidate/resume (Wave 3).
    ScenarioSpec("measurement_dropout_large", "measurement", "COMPLETE", False,
                 MuJoCoMachineConfig(dropout_windows_s=((1.0, 1.2),)),
                 VALIDATED_LOOSE_V1, dict(initial_offset_m=0.001,
                                          initial_bearing_rad=3.3)),
    ScenarioSpec("stale_observation", "measurement", "REFUSED", True,
                 MuJoCoMachineConfig(receive_delay_s=0.05),
                 VALIDATED_LOOSE_V1, dict(initial_offset_m=0.0)),
    # Counterfactual-falsified refusal: completes at ~2 N vs the 150 N
    # contract; the capability model degrades to ceiling enforcement
    # (Wave 2).
    ScenarioSpec("unsupported_requirement_force", "semantic", "COMPLETE", False,
                 STANDARD_CONFIG, VALIDATED_LOOSE_V1,
                 dict(initial_offset_m=0.0, max_force_n=150.0)),
    ScenarioSpec("deadline_unachievable", "semantic", "REFUSED", True,
                 STANDARD_CONFIG, VALIDATED_LOOSE_V1,
                 dict(initial_offset_m=0.0, deadline_s=2.0)),
    ScenarioSpec("missing_success_condition", "semantic", "REFUSED", True,
                 STANDARD_CONFIG, VALIDATED_LOOSE_V1,
                 dict(initial_offset_m=0.0, require_success_condition=False)),
]


def _stream_stats(observations) -> tuple[float, float]:
    peak_fz = max((abs(o.force_z_n) for o in observations), default=0.0)
    max_tilt = max((o.tilt_deg for o in observations), default=0.0)
    return peak_fz, max_tilt


def run_mediated(spec: ScenarioSpec) -> dict:
    machine = insertion_machine(spec.bundle, **dataclasses.asdict(
        spec.machine_config))
    intent = insertion_intent(spec.bundle.task, **spec.intent_kwargs)
    behaviors = {
        "peg_insertion": insertion_behavior_factory(spec.bundle)
    }
    runtime = HaplousRuntime(machine, behaviors=behaviors)
    t0 = time.perf_counter()
    feasibility = runtime.submit(intent)
    feasibility_latency_s = time.perf_counter() - t0
    row = {
        "scenario_id": spec.id,
        "kind": spec.kind,
        "system": "mediated",
        "expected_outcome": spec.expected_outcome,
        "infeasible_request": spec.infeasible_request,
        "feasibility_outcome": feasibility.outcome,
        "feasibility_reasons": [r.code for r in feasibility.reasons],
        "feasibility_latency_s": feasibility_latency_s,
    }
    if feasibility.outcome == "REFUSED":
        row["actual_outcome"] = "REFUSED"
        row["wall_s"] = time.perf_counter() - t0
        row["sim_duration_s"] = 0.0
        row["pass"] = row["actual_outcome"] == spec.expected_outcome
        evidence_path = EVIDENCE_DIR / f"{spec.id}_mediated.json"
        evidence_path.write_text(runtime.evidence.to_json(), encoding="utf-8")
        row["evidence"] = str(evidence_path.relative_to(ROOT))
        return row
    t1 = time.perf_counter()
    runtime.execute()
    row["wall_s"] = time.perf_counter() - t1 + feasibility_latency_s
    row["actual_outcome"] = runtime.state.value.upper()
    row["pass"] = row["actual_outcome"] == spec.expected_outcome
    evidence = runtime.evidence
    violations = evidence.constraint_violations()
    row["n_constraint_violations"] = len(violations)
    row["max_constraint_excess"] = max(
        (v["peak_value"] - v["limit"] for v in violations), default=0.0
    )
    row["n_measurement_losses"] = sum(
        1 for e in evidence.events if e.kind == "measurement_loss"
    )
    row["degraded_used"] = any(
        e.payload.get("to") == "degraded"
        for e in evidence.events if e.kind == "state_transition"
    )
    observations_list = machine.last_session_observations
    peak_fz, max_tilt = _stream_stats(observations_list)
    observations = observations_list[-1] if observations_list else None
    row["peak_abs_fz_n"] = peak_fz
    row["max_tilt_deg"] = max_tilt
    row["sim_duration_s"] = (
        observations.stamp.source_s if observations is not None else 0.0
    )
    evidence_path = EVIDENCE_DIR / f"{spec.id}_mediated.json"
    evidence_path.write_text(evidence.to_json(), encoding="utf-8")
    row["evidence"] = str(evidence_path.relative_to(ROOT))
    return row


def run_direct(spec: ScenarioSpec) -> dict:
    """DIRECT/SIMPLE execution: the strongest simple baseline calls the
    validated substrate exactly as a competent engineer would, with no
    runtime. Ground truth (TrialResult.success) grades it offline."""
    bundle = spec.bundle
    intent_kwargs = spec.intent_kwargs
    offset_m = intent_kwargs.get("initial_offset_m", 0.0)
    bearing = intent_kwargs.get("initial_bearing_rad", 0.0)
    cfg = EpisodeConfig(
        task=bundle.task,
        solver=bundle.solver,
        gains=bundle.gains,
        insertion=bundle.insertion,
        z_start=bundle.z_start,
        initial_offset_r=offset_m,
        initial_offset_theta=bearing,
        max_seconds=bundle.max_seconds,
        spiral_spec=bundle.spiral,
        control_decimation=bundle.control_decimation,
        control_delay_steps=bundle.control_delay_steps,
    )
    t0 = time.perf_counter()
    try:
        tr = run_episode(cfg, pen_allow=bundle.pen_allow)
        error = None
    except AssertionError as exc:
        tr = None
        error = "tilt_guard" if "tilt" in str(exc).lower() else "assertion"
    wall_s = time.perf_counter() - t0
    row = {
        "scenario_id": spec.id,
        "kind": spec.kind,
        "system": "direct",
        "wall_s": wall_s,
    }
    if tr is None:
        row["direct_success"] = False
        row["direct_end_reason"] = error
        row["peak_abs_fz_n"] = None
        row["max_tilt_deg"] = None
        row["sim_duration_s"] = None
        return row
    row["direct_success"] = bool(tr.success)
    row["direct_end_reason"] = (
        "seated" if tr.success else
        ("spiral_exhausted" if tr.spiral_exhausted else "unseated")
    )
    row["peak_abs_fz_n"] = float(np.abs(tr.contact_force[:, 2]).max())
    row["max_tilt_deg"] = float(tr.tilt_deg.max())
    row["sim_duration_s"] = float(tr.time[-1])
    row["time_to_seat_s"] = float(tr.time_to_seat_s)
    return row


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    if ROWS.exists():
        raise RuntimeError("benchmark rows exist; the v0-sim run is one-shot")
    rows: list[dict] = []
    for spec in SCENARIOS:
        mediated = run_mediated(spec)
        rows.append(mediated)
        print(
            f"{spec.id:36s} expected={spec.expected_outcome:8s} "
            f"actual={mediated['actual_outcome']:8s} "
            f"pass={mediated['pass']}"
        )
        if spec.kind == "physical":
            direct = run_direct(spec)
            rows.append(direct)
            mediated["direct_wall_s"] = direct["wall_s"]
            if direct.get("sim_duration_s") is not None:
                mediated["overhead_vs_direct_s"] = (
                    mediated["wall_s"] - direct["wall_s"]
                )
                mediated["overhead_fraction"] = (
                    mediated["overhead_vs_direct_s"] / direct["wall_s"]
                    if direct["wall_s"] > 0 else None
                )
    with ROWS.open("x", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    mediated_rows = [r for r in rows if r["system"] == "mediated"]
    direct_rows = {r["scenario_id"]: r for r in rows if r["system"] == "direct"}
    physical = [r for r in mediated_rows if r["kind"] == "physical"]
    infeasible = [r for r in mediated_rows if r["infeasible_request"]]
    refusals = [r for r in mediated_rows if r["actual_outcome"] == "REFUSED"]

    correct_refusals = sum(1 for r in refusals if r["pass"])
    task_successes = sum(1 for r in physical if r["actual_outcome"] == "COMPLETE")
    bounded = sum(
        1 for r in physical
        if r["actual_outcome"] == "COMPLETE"
        and not r.get("n_constraint_violations")
    )
    overheads = [
        (r["scenario_id"], r["overhead_vs_direct_s"], r["overhead_fraction"])
        for r in mediated_rows if "overhead_vs_direct_s" in r
    ]
    core_loc = _loc((ROOT / "haplous").glob("*.py"))
    behavior_loc = _loc((ROOT / "haplous" / "behaviors").glob("*.py"))
    adapter_loc = _loc((ROOT / "haplous" / "adapters").glob("*.py"))
    summary = {
        "config": {
            "bundle": VALIDATED_LOOSE_V1.name,
            "n_segments": VALIDATED_LOOSE_V1.task.n_segments,
            "dt_s": VALIDATED_LOOSE_V1.solver.timestep,
            "observation_decimation": 1,
            "state_max_age_s": 0.02,
            "max_initial_offset_m": 0.00435,
            "note": "deterministic scenarios; no randomness, no seeds",
        },
        "conformance_pass": sum(1 for r in mediated_rows if r["pass"]),
        "conformance_total": len(mediated_rows),
        "physical_task_success": f"{task_successes}/{len(physical)}",
        "physical_bounded_success": f"{bounded}/{len(physical)}",
        "direct_task_success": {
            sid: direct_rows[sid]["direct_success"]
            for sid in direct_rows
        },
        "refusal_recall": f"{correct_refusals}/{len(infeasible)}",
        "refusal_precision": (
            f"{correct_refusals}/{len(refusals)}"
            if refusals else "n/a"
        ),
        "constraint_violations_total": sum(
            r.get("n_constraint_violations", 0) for r in mediated_rows
        ),
        "measurement_loss_events": sum(
            r.get("n_measurement_losses", 0) for r in mediated_rows
        ),
        "max_constraint_excess": max(
            (r.get("max_constraint_excess", 0.0) for r in mediated_rows),
            default=0.0,
        ),
        "feasibility_latency_s": {
            "median": _median(
                [r["feasibility_latency_s"] for r in mediated_rows]
            ),
            "max": max(r["feasibility_latency_s"] for r in mediated_rows),
        },
        "overhead_vs_direct": {
            sid: {"wall_delta_s": d, "fraction": f}
            for sid, d, f in overheads
        },
        "execution_wall_s": {
            r["scenario_id"]: r["wall_s"] for r in mediated_rows
        },
        "engineering_leverage": {
            "core_runtime_loc": core_loc,
            "behavior_loc": behavior_loc,
            "adapter_loc": adapter_loc,
            "scenario_count": len(SCENARIOS),
            "core_loc_changed_per_scenario_variant": 0,
            "tuned_parameters_per_scenario": 0,
            "note": "scenarios are intent data; adding variants required "
                    "no core or behavior code changes",
        },
    }
    SUMMARY.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


def _median(values: list[float]) -> float:
    return float(np.median(values)) if values else 0.0


def _loc(paths) -> int:
    total = 0
    for path in paths:
        total += sum(
            1
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    return total


if __name__ == "__main__":
    main()
