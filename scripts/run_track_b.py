"""Record a Track B v1.1 contract run through the M0 registry (canonical v1.1 manifest).

All digests are computed here from real bytes: fixture file, engine/driver source,
uv.lock, BASELINE_SPEC/SIGNING_PROTOCOL. Nothing VLA- or simulator-related is recorded
(track="contract"). Outputs (results.jsonl, summary.json, report.md) are hashed by the
registry at completion. The v1.1 ledger-event projection and a copy of the generated
report are written next to the registry.

Usage: python scripts/run_track_b.py --run-id track-b-v1.1-001
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from evaluation.run_registry import (
    Registry,
    canonical_bytes,
    export_v11_events,
    format_ts,
    sha256_hex,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "evaluation" / "spec" / "haplous_eval_v1_1"
FIXTURES = SPEC / "fixtures" / "track_b_scenarios.json"
INPUTS = [FIXTURES, SPEC / "BASELINE_SPEC.md", SPEC / "SIGNING_PROTOCOL.md",
          SPEC.parent / "haplous_eval_v1_1_starter.zip", ROOT / "evaluation" / "track_b.py",
          ROOT / "evaluation" / "run_registry.py", ROOT / "scripts" / "run_track_b.py", ROOT / "uv.lock"]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def file_digest(path: Path) -> str:
    return "sha256:" + sha256_hex(path.read_bytes())


def build_manifest(run_id: str, out_rel: str) -> dict:
    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--", ".", ":(exclude)results"))
    code = {p.relative_to(ROOT).as_posix(): sha256_hex(p.read_bytes())
            for p in (ROOT / "evaluation" / "track_b.py", ROOT / "scripts" / "run_track_b.py")}
    return {
        "schema_version": "1.1",
        "run_id": run_id,
        "tier": 1,
        "track": "contract",
        "created_at": format_ts(datetime.now(UTC)),
        "sources": {"haplous": {"uri": "https://github.com/kodingcills/pinn_mvp", "commit": commit,
                                "artifact_digest": file_digest(SPEC.parent / "haplous_eval_v1_1_starter.zip")}},
        "environment": {"python": sys.version.split()[0], "hardware": f"{platform.machine()} CPU ({platform.platform()})",
                        "clock_semantics": "not_applicable", "gpu": None,
                        "dependency_lock_digest": file_digest(ROOT / "uv.lock")},
        "run_command": [sys.executable, "-m", "evaluation.track_b", "--fixtures",
                        FIXTURES.relative_to(ROOT).as_posix(), "--out", out_rel],
        "analysis_code_digest": "sha256:" + sha256_hex(canonical_bytes(code)),
        "artifacts": [{"uri": p.relative_to(ROOT).as_posix(), "sha256": sha256_hex(p.read_bytes())} for p in INPUTS],
        "execution": {"stack": "evaluation.track_b", "mode": "deterministic_pure_function"},
        "contract_suite": {"name": "track_b_contract_interactions", "fixture_set_digest": file_digest(FIXTURES),
                           "truth_protocol_version": "1.1", "baseline_profile": "baseline_g0_minimal_guards"},
        "notes": f"git_dirty={dirty} (source paths excluding results/). Manifest artifacts are INPUTS; "
                 "outputs are hashed in the M0 completion event.",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--registry", type=Path, default=ROOT / "results" / "m0_registry")
    args = ap.parse_args()
    reg = Registry(args.registry)
    out = reg.run_dir(args.run_id) / "artifacts"
    out_arg = out.relative_to(ROOT).as_posix() if out.is_relative_to(ROOT) else str(out)
    manifest = build_manifest(args.run_id, out_arg)
    reg.register(manifest)
    reg.start(args.run_id)
    proc = subprocess.run(manifest["run_command"], cwd=ROOT, capture_output=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stdout.txt").write_bytes(proc.stdout)
    (out / "stderr.txt").write_bytes(proc.stderr)
    reg.finish(args.run_id, success=proc.returncode == 0, exit_code=proc.returncode)
    problems = reg.verify(args.run_id)
    dest = ROOT / "results" / "track_b"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{args.run_id}_ledger_v11.json").write_text(
        json.dumps(export_v11_events(reg, args.run_id), indent=2, sort_keys=True) + "\n")
    if (out / "report.md").exists():
        shutil.copyfile(out / "report.md", dest / f"{args.run_id}_report.md")
    print(proc.stdout.decode().strip(), "| state:", reg.state(args.run_id), "| problems:", problems)
    return 0 if proc.returncode == 0 and not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
