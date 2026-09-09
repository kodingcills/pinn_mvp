"""HEB single-axis perturbation sweeps (S06): characterize major stress
dimensions separately and locate failure boundaries before any compound
stress. Each axis derives scenarios from a family base scenario; paired
B1/B2 share the machine realization per cell (same physics, corruption,
and fault schedule). Deterministic physics: one trial per cell — results
are coverage over the tested simulation envelope, not probabilities."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from benchmarks.heb import HEB_VERSION
from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.metrics import summarize
from benchmarks.heb.runner import evaluate
from benchmarks.heb.sliding import sliding_scenarios
from benchmarks.heb.systems import BareSystem, HaplousSystem

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def _base(scenarios, scenario_id):
    return next(s for s in scenarios if s.id == scenario_id)


def sweep_insertion_offset():
    base = _base(insertion_scenarios(), "insert_nominal")
    cells = []
    for offset_mm in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.3):
        cells.append(dataclasses.replace(
            base,
            id=f"sw_offset_{offset_mm:.1f}mm",
            initial_offset_m=offset_mm * 1e-3,
            initial_bearing_rad=1.0,
        ))
    return "insertion_offset_m", cells


def sweep_insertion_clearance():
    base = _base(insertion_scenarios(), "insert_offset_1mm")
    cells = []
    for delta_mm in (-0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2):
        # position_tolerance left unset: the condition factory derives it
        # from the realized clearance, so looser holes get looser
        # tolerances (an explicit override here would fail the task truth
        # on looser geometry).
        cells.append(dataclasses.replace(
            base,
            id=f"sw_clearance_{delta_mm:+.1f}mm",
            clearance_delta_m=delta_mm * 1e-3,
            position_tolerance_m=None,
        ))
    return "insertion_clearance_delta_m", cells


def sweep_insertion_facets():
    base = _base(insertion_scenarios(), "insert_offset_1mm")
    cells = [
        dataclasses.replace(base, id=f"sw_facets_{n}", facet_count=n)
        for n in (8, 16, 32, 64, 128)
    ]
    return "insertion_facet_count", cells


def sweep_insertion_stale():
    base = _base(insertion_scenarios(), "insert_offset_1mm")
    cells = []
    for delay_ms in (5, 10, 15, 19, 20, 25, 40):
        cells.append(dataclasses.replace(
            base,
            id=f"sw_stale_{delay_ms}ms",
            stale_windows_s=((0.5, 0.7, delay_ms * 1e-3),),
        ))
    return "insertion_mid_motion_stale_delay_s", cells


def sweep_insertion_dropout():
    base = _base(insertion_scenarios(), "insert_offset_1mm")
    cells = []
    for width_ms in (5, 10, 15, 20, 25, 30, 50):
        cells.append(dataclasses.replace(
            base,
            id=f"sw_dropout_{width_ms}ms",
            dropout_windows_s=((1.0, 1.0 + width_ms * 1e-3),),
        ))
    return "insertion_dropout_width_s", cells


def sweep_slide_kick():
    base = _base(sliding_scenarios(), "slide_nominal")
    cells = []
    for dv in (0.05, 0.1, 0.2, 0.3, 0.4, 0.5):
        cells.append(dataclasses.replace(
            base,
            id=f"sw_kick_dx_{int(dv * 100):02d}",
            disturbances=((1.1, (dv, 0.0, 0.0, 0.0, 0.0, 0.0)),),
            recoverable=True,
        ))
    return "slide_lateral_kick_dv_mps", cells


def sweep_slide_stale():
    base = _base(sliding_scenarios(), "slide_nominal")
    cells = []
    for delay_ms in (5, 10, 15, 19, 20, 25, 40):
        cells.append(dataclasses.replace(
            base,
            id=f"sw_slide_stale_{delay_ms}ms",
            stale_windows_s=((1.0, 1.2, delay_ms * 1e-3),),
        ))
    return "slide_mid_motion_stale_delay_s", cells


SWEEPS = [
    sweep_insertion_offset,
    sweep_insertion_clearance,
    sweep_insertion_facets,
    sweep_insertion_stale,
    sweep_insertion_dropout,
    sweep_slide_kick,
    sweep_slide_stale,
]


def main() -> None:
    systems = [HaplousSystem(), BareSystem()]
    all_summaries = {}
    for build in SWEEPS:
        axis, cells = build()
        records = evaluate(cells, systems)
        summaries = summarize(cells, records)
        per_cell = {}
        for r in records:
            per_cell.setdefault(r.scenario_id, {})[r.system] = {
                "reported": r.reported_disposition,
                "graded": r.graded_disposition,
                "bounded": r.bounded_success,
                "task_success": r.task_success,
                "reason": r.reason,
                "violations": len(r.hard_violations),
                "seating_time_s": r.task_metrics.get("seating_time_s"),
            }
        all_summaries[axis] = {
            "cells": per_cell,
            "mediated_bounded": summaries.get("haplous_mediated"),
            "bare_bounded": summaries.get("bare_behavior"),
        }
        print(f"== {axis}")
        for cid, by_system in per_cell.items():
            med = by_system.get("haplous_mediated", {})
            bar = by_system.get("bare_behavior", {})
            print(
                f"  {cid:28s} mediated={med.get('reported', '?'):8s} "
                f"bounded={str(med.get('bounded')):5s}   "
                f"bare={bar.get('reported', '?'):8s} "
                f"bounded={bar.get('bounded')}"
            )
    out = RESULTS / "heb_sweeps_dev.json"
    out.write_text(
        json.dumps(
            {
                "heb_version": HEB_VERSION,
                "sweeps": all_summaries,
            },
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
