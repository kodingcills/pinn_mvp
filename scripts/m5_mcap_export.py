"""M5.4 evidence: export the committed episode logs to MCAP and check the round trip.

    PYTHONPATH=. .venv/bin/python scripts/m5_mcap_export.py

For each committed log (MuJoCo M2 recording, robosuite Lift) it exports twice,
checks the two files are byte-identical and that `read_mcap` rebuilds the
JSONL files exactly, and writes results/m5/mcap_export.json. The robosuite
MCAP is committed as results/m5/robosuite-log.mcap; the MuJoCo one (about
0.4 MB compressed) goes to a temp dir unless --mujoco-out is given.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.episode_log import EVENTS, METADATA, TELEMETRY  # noqa: E402
from benchmarks.episode_mcap import export_mcap, read_mcap  # noqa: E402

LOGS = {
    "mujoco": "results/recordings/registry/runs/m2-heb-insert_dropout_large/artifacts",
    "robosuite": "results/m5/robosuite-log",
}


def check(log_dir: Path, out: Path) -> dict:
    counts = export_mcap(log_dir, out)
    first = out.read_bytes()
    export_mcap(log_dir, out)
    rebuilt = read_mcap(out)
    return {
        "log_dir": str(log_dir.relative_to(ROOT)),
        "messages": counts,
        "mcap_bytes": len(first),
        "mcap_sha256": hashlib.sha256(first).hexdigest(),
        "deterministic": out.read_bytes() == first,
        "round_trip_exact": {name: rebuilt[name] == (log_dir / name).read_text(encoding="utf-8")
                             for name in (TELEMETRY, EVENTS, METADATA)},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mujoco-out", type=Path, help="where the MuJoCo MCAP goes (default: a temp dir)")
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "m5" / "mcap_export.json")
    args = parser.parse_args(argv)
    outs = {"mujoco": args.mujoco_out or Path(tempfile.mkdtemp(prefix="m5-mcap-")) / "mujoco-log.mcap",
            "robosuite": ROOT / "results" / "m5" / "robosuite-log.mcap"}
    summary = {name: check(ROOT / rel, outs[name]) for name, rel in LOGS.items()}
    import mcap
    summary["mcap_version"] = mcap.__version__
    args.out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    ok = all(s["deterministic"] and all(s["round_trip_exact"].values()) for s in summary.values() if isinstance(s, dict))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
