"""HEB final held-out evaluation (S15).

Procedure: the system, benchmark, baselines, and metrics were frozen at
tag haplous-prehardware-eval-v0 BEFORE this script was committed. At run
time it draws a fresh OS seed, deterministically generates the held-out
scenario manifest from that seed (sampling inside the S06-mapped VALIDATED
envelope, plus semantic and fault cells), records the manifest and its
SHA-256, runs the frozen systems (B1 bare, B2 Haplous-mediated), and
writes rows + summary. The manifest hash makes the run reproducible; no
scenario from this manifest existed in the repository before the freeze
tag.

Deviation from the ideal blind protocol (single-operator campaign): the
seed is recorded at generation time by this script, after the freeze tag;
the evaluated code never reads the manifest, and no module outside this
driver imports it."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import random
import time
from pathlib import Path

from benchmarks.heb import HEB_VERSION
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.metrics import summarize
from benchmarks.heb.runner import evaluate
from benchmarks.heb.sliding import _path_points, sliding_scenarios
from benchmarks.heb.systems import BareSystem, HaplousSystem

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

N_INSERTION_PHYSICAL = 18
N_SLIDE_PHYSICAL = 10
N_SEMANTIC_EACH = 3
N_FAULT_EACH = 2


def generate_manifest(seed: int) -> list:
    rng = random.Random(seed)
    ins_base = insertion_scenarios()
    slide_base = sliding_scenarios()
    nominal = next(s for s in ins_base if s.id == "insert_nominal")
    slide_nominal = next(s for s in slide_base if s.id == "slide_nominal")
    slide_start, slide_end = _path_points()
    scenarios: list = []

    for i in range(N_INSERTION_PHYSICAL):
        offset = rng.uniform(0.0, 0.003)
        if i == N_INSERTION_PHYSICAL - 1:
            offset = rng.uniform(0.0028, 0.0030)
        scenarios.append(
            dataclasses.replace(
                nominal,
                id=f"heldout_ins_{i:02d}",
                initial_offset_m=offset,
                initial_bearing_rad=rng.uniform(0.0, 2.0 * math.pi),
                facet_count=rng.choice([16, 32, 64]),
            )
        )

    for i in range(N_SEMANTIC_EACH):
        kind = i % 3
        if kind == 0:
            scenarios.append(dataclasses.replace(
                next(s for s in ins_base if s.id == "insert_offset_5mm_infeasible"),
                id=f"heldout_ins_infeasible_{i}",
            ))
        elif kind == 1:
            scenarios.append(dataclasses.replace(
                next(s for s in ins_base if s.id == "insert_force_limit_150n"),
                id=f"heldout_ins_refusal_{i}",
            ))
        else:
            scenarios.append(dataclasses.replace(
                next(s for s in ins_base if s.id == "insert_deadline_2s"),
                id=f"heldout_ins_deadline_{i}",
                deadline_s=rng.choice([1.0, 1.5, 2.0]),
            ))

    for i in range(N_FAULT_EACH):
        base = dataclasses.replace(nominal, initial_offset_m=0.0)
        if i == 0:
            scenarios.append(dataclasses.replace(
                base,
                id=f"heldout_ins_stale_{i}",
                stale_windows_s=((0.5, 0.6, rng.choice([0.03, 0.05])),),
            ))
        else:
            scenarios.append(dataclasses.replace(
                base,
                id=f"heldout_ins_dropout_{i}",
                dropout_windows_s=((1.0, 1.0 + rng.choice([0.03, 0.05])),),
            ))

    for i in range(N_SLIDE_PHYSICAL):
        half = math.radians(rng.uniform(40.0, 80.0)) / 2.0
        r = rng.uniform(0.00355, 0.00425)
        start = (r * math.cos(-half), r * math.sin(-half), 0.0)
        end = (r * math.cos(half), r * math.sin(half), 0.0)
        scenarios.append(
            dataclasses.replace(
                slide_nominal,
                id=f"heldout_slide_{i:02d}",
                path_start_m=start,
                path_end_m=end,
                start_position_m=start,
            )
        )

    for i in range(N_SEMANTIC_EACH):
        scenarios.append(
            dataclasses.replace(
                next(s for s in slide_base if s.id == "slide_deadline_infeasible"),
                id=f"heldout_slide_refusal_{i}",
                deadline_s=rng.choice([0.8, 1.0, 1.5]),
            )
        )

    for i in range(N_FAULT_EACH):
        base = dataclasses.replace(
            slide_nominal,
            path_start_m=slide_start,
            path_end_m=slide_end,
            start_position_m=slide_start,
        )
        if i == 0:
            scenarios.append(dataclasses.replace(
                base,
                id=f"heldout_slide_stale_{i}",
                stale_windows_s=((1.0, 1.1, rng.choice([0.03, 0.05])),),
            ))
        else:
            scenarios.append(dataclasses.replace(
                base,
                id=f"heldout_slide_reject_{i}",
                command_rejection_windows_s=((1.0, 1.02),),
            ))
    return scenarios


def main() -> None:
    seed = int.from_bytes(os.urandom(8), "big")
    scenarios = generate_manifest(seed)
    manifest_json = json.dumps(
        [dataclasses.asdict(s) for s in scenarios], sort_keys=True
    )
    manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()

    manifest_path = RESULTS / "heb_final_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "seed": seed,
                "seed_source": "os.urandom(8) at run time",
                "manifest_sha256": manifest_hash,
                "n_scenarios": len(scenarios),
                "scenarios": [dataclasses.asdict(s) for s in scenarios],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"seed={seed}")
    print(f"manifest_sha256={manifest_hash}")
    print(f"scenarios={len(scenarios)}")

    t0 = time.perf_counter()
    records = evaluate(scenarios, [HaplousSystem(), BareSystem()])
    wall = time.perf_counter() - t0

    rows_path = RESULTS / "heb_final_rows.jsonl"
    with rows_path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r.__dict__, sort_keys=True, default=str) + "\n")

    summaries = summarize(scenarios, records)
    expected = {s.id: s.expected_disposition for s in scenarios}
    conformance = {}
    for name, s in summaries.items():
        recs = [r for r in records if r.system == name]
        scored = [r for r in recs if expected.get(r.scenario_id)]
        conformance[name] = {
            "match": sum(
                1
                for r in scored
                if r.reported_disposition == expected[r.scenario_id]
            ),
            "scored": len(scored),
        }
    summary = {
        "heb_version": HEB_VERSION,
        "system_tag": "haplous-prehardware-eval-v0",
        "seed": seed,
        "manifest_sha256": manifest_hash,
        "n_scenarios": len(scenarios),
        "n_trials": len(records),
        "systems": {
            name: {
                "trials": s.trials,
                "coverage": s.coverage,
                "bounded_success_rate_among_accepted": s.bounded_success_rate,
                "task_successes": s.task_successes,
                "refusals": s.refusals,
                "refusal_precision": s.refusal_precision,
                "refusal_recall": s.refusal_recall,
                "hard_violation_trials": s.hard_violation_trials,
                "recovery_success_rate": s.recovery_success_rate,
                "completion_percentiles_s": s.completion_percentiles(),
            }
            for name, s in summaries.items()
        },
        "semantic_conformance": conformance,
        "wall_s": wall,
    }
    summary_path = RESULTS / "heb_final_summary.json"
    summary_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
