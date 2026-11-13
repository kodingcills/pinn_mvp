"""A/A driver: pass-through vs direct on every HEB scenario of both families.
Writes results/passthrough/aa_report.json."""
from __future__ import annotations

import json
from pathlib import Path

from benchmarks.heb.insertion import insertion_scenarios
from benchmarks.heb.population import population
from benchmarks.heb.sliding import sliding_scenarios
from benchmarks.passthrough import COMPARED_FIELDS, aa_compare

POP_N, POP_SEED = 12, 0


def scenarios():
    return insertion_scenarios() + sliding_scenarios() + population(POP_N, POP_SEED)


def main() -> int:
    # Full horizon (16.3 s): non-completing runs take ~40 s per arm; total ~30 min.
    rows = [aa_compare(s) for s in scenarios()]
    report = {
        "fields_compared": list(COMPARED_FIELDS),
        "population": {"n": POP_N, "seed": POP_SEED},
        "n_scenarios": len(rows),
        "n_identical": sum(r["identical"] for r in rows),
        "verdict": "PASS" if all(r["identical"] for r in rows) else "FAIL",
        "scenarios": rows,
    }
    out = Path("results/passthrough/aa_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(report["verdict"], report["n_identical"], "/", report["n_scenarios"])
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
