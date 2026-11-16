"""Dump real Haplous Studio API responses to JSON files, so frontend fixtures can be
checked against what the server actually returns.

    PYTHONPATH=. .venv/bin/python scripts/studio_fixtures.py --out <dir> [--studio-root results/studio]

Runs no simulation and starts no job: it reads committed artifacts (and the jobs and
registry under --studio-root, if any) through the FastAPI app in-process. The CPU-cap
409 is shown by asking for every catalog scenario x system with confirm=false under a
temporarily lowered cap (stated in the file), so nothing is launched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402

from studio import jobs  # noqa: E402
from studio.app import create_app  # noqa: E402
from studio.data import Paths  # noqa: E402

GETS = {
    "runs": "/api/runs",
    "run_m2_recording": "/api/runs/m2-heb-insert_dropout_large",
    "run_robosuite_log": "/api/runs/results/m5/robosuite-log",
    "run_reassemble_log": "/api/runs/results/m5/reassemble-log",
    "campaigns": "/api/campaigns",
    "pairs_control_haplous_vs_g1": "/api/campaigns/m4-campaign/pairs?a=haplous_mediated&b=g1_incumbent&population=control",
    "pairs_stress_haplous_vs_bare": "/api/campaigns/m4-campaign/pairs?a=haplous_mediated&b=bare_behavior&population=stress",
    "catalog": "/api/catalog",
    "jobs": "/api/jobs",
    "evidence": "/api/evidence",
    "error_unknown_run": "/api/runs/no-such-run",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--studio-root", type=Path, help="Studio jobs/registry root to read (default: results/studio)")
    args = parser.parse_args(argv)
    paths = Paths.default()
    if args.studio_root is not None:
        paths = Paths(paths.repo, args.studio_root.resolve(), paths.investigations, paths.replays)
    client = TestClient(create_app(paths), base_url="http://127.0.0.1:8765")
    args.out.mkdir(parents=True, exist_ok=True)
    index = {}

    def dump(name: str, method: str, url: str, response) -> None:
        body = response.json()
        (args.out / f"{name}.json").write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        index[name] = {"method": method, "url": url, "status": response.status_code}

    for name, url in GETS.items():
        dump(name, "GET", url, client.get(url))
    for job in client.get("/api/jobs").json()[:1]:
        dump("job", "GET", f"/api/jobs/{job['job_id']}", client.get(f"/api/jobs/{job['job_id']}"))
    dump("error_job_unknown_scenario", "POST", "/api/jobs",
         client.post("/api/jobs", json={"scenario_ids": ["fleet_insert_000137"], "systems": ["haplous_mediated"]}))
    catalog = client.get("/api/catalog").json()
    real_cap, jobs.CPU_CAP_S = jobs.CPU_CAP_S, 30.0
    try:
        dump("error_job_over_cap", "POST", "/api/jobs (cap lowered to 30 CPU-s for this dump)",
             client.post("/api/jobs", json={"scenario_ids": [s["id"] for s in catalog["scenarios"]],
                                            "systems": [s["id"] for s in catalog["systems"]], "confirm": False}))
    finally:
        jobs.CPU_CAP_S = real_cap
    (args.out / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(index, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
