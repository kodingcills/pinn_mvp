"""Field-level reproducibility diff of two recorded LIBERO-Dynamic smoke runs.

Byte-level artifact comparison (evaluation.run_registry compare) flags upstream
JSON that embeds absolute paths and wall-clock timings. This lists exactly which
JSON fields differ (array indices collapsed), so wall-clock/path-only drift can
be distinguished from simulation drift.

Usage: python scripts/compare_libero_smoke_runs.py RUN_A RUN_B
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from evaluation.run_registry import Registry

ROOT = Path(__file__).resolve().parents[1]
# Matched against the last path component of a differing field.
WALLCLOCK_OR_PATH = re.compile(r"latency|elapsed|wallclock|wait_blocked|_path$|^manifest$|^step_trace$")


def load(path: Path):
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return json.loads(path.read_text())


def parsed(rel: str) -> bool:
    return rel.endswith((".json", ".jsonl"))


def diff_paths(a, b, p=""):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            yield from diff_paths(a.get(k), b.get(k), f"{p}/{k}")
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for x, y in zip(a, b, strict=True):
            yield from diff_paths(x, y, f"{p}[]")
    elif a != b:
        yield p


def main(run_a: str, run_b: str) -> int:
    reg = Registry(ROOT / "results" / "m0_registry")
    for r in (run_a, run_b):
        if reg.check(r):
            raise SystemExit(f"{r} fails integrity check: {reg.check(r)}")
    cmp_ = reg.compare(run_a, run_b)
    fields: set[str] = set()
    for rel in cmp_["artifacts_differ"]:
        if not parsed(rel):
            continue
        a = load(reg.run_dir(run_a) / "artifacts" / rel)
        b = load(reg.run_dir(run_b) / "artifacts" / rel)
        fields |= set(diff_paths(a, b))
    non_wallclock = sorted(f for f in fields if not WALLCLOCK_OR_PATH.search(re.split(r"[/\]\[]+", f.rstrip("[]"))[-1]))
    unparsed = [x for x in cmp_["artifacts_differ"] if not parsed(x)]
    out = {
        "runs": [run_a, run_b],
        "byte_identical_artifacts": cmp_["artifacts_identical"],
        "byte_differing_artifacts": cmp_["artifacts_differ"],
        "unparsed_differing_artifacts": unparsed,
        "differing_json_fields": sorted(fields),
        "differing_fields_not_wallclock_or_path": non_wallclock,
        "simulation_reproducible": not non_wallclock and not unparsed,
    }
    dest = ROOT / "results" / "libero_dynamic" / f"repro_{run_a}_vs_{run_b}.json"
    dest.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return 0 if out["simulation_reproducible"] else 1


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
