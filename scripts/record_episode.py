"""Record one real HEB haplous_mediated episode (any registered task family) as an M0 run.

    .venv/bin/python scripts/record_episode.py --root results/recordings/registry \
        --run-id m2-heb-insert_dropout_large

The episode runs in a child process (`python -m benchmarks.recording`)
that writes into the run's artifact dir and exits; M0 then hashes the
artifacts, completes and verifies the run. Replay it with
`haplous replay <run-id> --recordings-root <root> --ui`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import mujoco

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evaluation.run_registry import REPO_ROOT, Registry, record_run  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scenario", default="insert_dropout_large")
    parser.add_argument("--chunk-ticks", type=int, default=1024)
    args = parser.parse_args(argv)
    command = [sys.executable, "-m", "benchmarks.recording",
               "--scenario", args.scenario, "--chunk-ticks", str(args.chunk_ticks)]
    result = record_run(
        Registry(args.root),
        run_id=args.run_id,
        track="sim_policy_free",
        purpose=f"Recording of HEB scenario {args.scenario} (haplous_mediated)",
        command=command,
        config={"scenario": args.scenario, "system": "haplous_mediated", "chunk_ticks": args.chunk_ticks},
        simulator={"name": "mujoco", "version": mujoco.__version__},
        dependencies=("numpy", "mujoco", "pyarrow", "jsonschema", "cryptography"),
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(REPO_ROOT)},
    )
    print(json.dumps({k: result[k] for k in ("run_id", "state", "outcome", "manifest_sha256")}, indent=2))
    return 0 if result["state"] == "VERIFIED" and result["outcome"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
