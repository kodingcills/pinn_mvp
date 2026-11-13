"""OD-1 parity (locked design OD1_command_mode_admission.md, section 6).

Every insertion and slide HEB scenario x every benchmark system, run on the
standard MuJoCoMachine, before (a `git archive` export of the pre-OD-1 head)
and after (this tree). Compared per trial on reported disposition, reason,
refusal codes, graded outcome and the observation stream (SHA-256 over the
exact repr of every observation).

    python scripts/od1_parity.py                  # before + after + compare
    python scripts/od1_parity.py collect OUT.json  # one side, current imports
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEFORE_REF = "c9997c6"
OUT = ROOT / "results" / "od1" / "parity.json"
SYSTEMS = ("direct_minimal", "bare_behavior", "competent_supervisor", "haplous_mediated")
COMPARED = (
    "reported_disposition", "reason", "refusal_codes", "graded_disposition",
    "task_success", "bounded_success", "hard_violations", "task_metrics",
    "n_observations", "observations_sha256",
)


def collect(out: Path) -> None:
    import haplous
    from benchmarks.heb import mujoco_backend
    from benchmarks.heb.insertion import insertion_scenarios
    from benchmarks.heb.metrics import grade_run
    from benchmarks.heb.sliding import sliding_scenarios
    from benchmarks.heb.systems import system_by_name

    rows = []
    for scenario in insertion_scenarios() + sliding_scenarios():
        realization = mujoco_backend.build_realization(scenario)
        for name in SYSTEMS:
            run = system_by_name(name).run(realization, scenario)
            graded, success, bounded, violations, metrics = grade_run(
                scenario, realization.bundle, run
            )
            blob = "\n".join(repr(o) for o in run.observations).encode()
            rows.append({
                "scenario_id": scenario.id,
                "system": run.system,
                "reported_disposition": run.disposition,
                "reason": run.reason,
                "refusal_codes": list(run.refusal_codes),
                "graded_disposition": graded,
                "task_success": success,
                "bounded_success": bounded,
                "hard_violations": violations,
                # wall_s is wall-clock time, never reproducible across runs.
                "task_metrics": {k: v for k, v in metrics.items() if k != "wall_s"},
                "n_observations": len(run.observations),
                "observations_sha256": hashlib.sha256(blob).hexdigest(),
            })
    out.write_text(json.dumps(
        {"haplous_file": haplous.__file__, "rows": rows}, default=repr, sort_keys=True
    ))


def _side(root: Path, out: Path) -> dict:
    env = dict(os.environ, PYTHONPATH=str(root), MUJOCO_GL=os.environ.get("MUJOCO_GL", "osmesa"))
    subprocess.run([sys.executable, str(Path(__file__).resolve()), "collect", str(out)],
                   cwd=root, env=env, check=True)
    side = json.loads(out.read_text())
    assert Path(side["haplous_file"]).resolve().is_relative_to(root.resolve()), side["haplous_file"]
    return side


def main() -> int:
    if sys.argv[1:2] == ["collect"]:
        collect(Path(sys.argv[2]))
        return 0
    before_sha = subprocess.check_output(
        ["git", "rev-parse", BEFORE_REF], cwd=ROOT, text=True).strip()
    after_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "haplous"], cwd=ROOT, text=True)
    with tempfile.TemporaryDirectory() as tmp:
        before_root = Path(tmp) / "before"
        before_root.mkdir()
        archive = subprocess.run(["git", "archive", before_sha], cwd=ROOT, check=True,
                                 capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(before_root)], input=archive, check=True)
        before = _side(before_root, Path(tmp) / "before.json")
        after = _side(ROOT, Path(tmp) / "after.json")

    key = lambda r: (r["scenario_id"], r["system"])  # noqa: E731
    b = {key(r): r for r in before["rows"]}
    a = {key(r): r for r in after["rows"]}
    assert b.keys() == a.keys(), sorted(b.keys() ^ a.keys())
    trials = []
    for k in sorted(b):
        diffs = [f for f in COMPARED if b[k][f] != a[k][f]]
        trials.append({"scenario_id": k[0], "system": k[1], "identical": not diffs,
                       "differing_fields": diffs,
                       "before": {f: b[k][f] for f in COMPARED if f != "task_metrics"},
                       "after_matches_before": not diffs})
    report = {
        "design": "docs/eval_program/designs/OD1_command_mode_admission.md section 6",
        "before_commit": before_sha,
        "after_commit": after_sha,
        "after_haplous_tree_dirty": bool(dirty.strip()),
        "machine": "standard MuJoCoMachine (mujoco_backend.build_realization)",
        "systems": list(SYSTEMS),
        "compared_fields": list(COMPARED),
        "n_trials": len(trials),
        "n_identical": sum(t["identical"] for t in trials),
        "identical": all(t["identical"] for t in trials),
        "trials": trials,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True, default=repr) + "\n")
    print(f"{report['n_identical']}/{report['n_trials']} identical -> {OUT.relative_to(ROOT)}")
    return 0 if report["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
