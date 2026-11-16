"""M5.5 evidence: import one real REASSEMBLE insertion segment into the episode log.

    PYTHONPATH=. .venv/bin/python scripts/m55_reassemble_import.py --data-dir <dir>

<dir> holds (or receives, with --download) the pinned files of the Hugging Face
dataset robot-lev/reassemble at REVISION: meta/info.json, meta/tasks.parquet,
data/chunk-000/file-000.parquet (~45 MB). Each is checked against its pinned
sha256 before use; raw data never goes in the repo. The driver then imports
EPISODE / SEGMENT ("Insert round peg 2.") into results/m5/reassemble-log
(committed sample, with ATTRIBUTION.txt), exports it to MCAP twice, renders
the timeline in a subprocess, and writes results/m5/reassemble_import.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.episode_log import EVENTS, METADATA, TELEMETRY, log_schema  # noqa: E402
from benchmarks.episode_mcap import export_mcap, read_mcap  # noqa: E402
from benchmarks.realdata import reassemble_episode_log as rel  # noqa: E402

REPO_ID = "robot-lev/reassemble"
REVISION = "37d242d3532fb17b6f749a26dad6d7aaacf810cd"
PINNED = {
    "meta/info.json": "cca14484935320cbeca79647ca2212f720e9148632ede2b6885b2dc7c5636212",
    "meta/tasks.parquet": "ad2c9ee4738183a0b2d49a882a7e28ebdb279f92933657ca5435276a63b47e1e",
    "data/chunk-000/file-000.parquet": "310dddd1802a032374ae16e6f7adbaefc7b7f82d05212a475e2fba6b20b3fceb",
}
DATA_FILE = "data/chunk-000/file-000.parquet"
EPISODE, SEGMENT = 1, 2  # "Insert round peg 2.", 376 frames, annotated success
SAMPLE = ROOT / "results/m5/reassemble-log"
SUMMARY = ROOT / "results/m5/reassemble_import.json"


def verify(path: Path, expected: str) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise SystemExit(f"sha256 mismatch for {path}: {digest}, pinned {expected}")
    return digest


def download(data_dir: Path) -> None:
    for rel_path in PINNED:
        target = data_dir / rel_path
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://huggingface.co/datasets/{REPO_ID}/resolve/{REVISION}/{rel_path}"
            urllib.request.urlretrieve(url, target)


def _action_vs_next_pose(log_dir: Path) -> float:
    rows = [json.loads(line) for line in (log_dir / TELEMETRY).read_text(encoding="utf-8").splitlines()]
    pose = [r["observation"]["pose_m"] for r in rows if r["kind"] == "observation"]
    target = [r["command"]["target_pose_m"] for r in rows if r["kind"] == "command"]
    return max(abs(a - b) for t, p in zip(target[:-1], pose[1:], strict=True) for a, b in zip(t, p, strict=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True, help="dataset root outside the repo")
    parser.add_argument("--download", action="store_true", help="fetch missing pinned files first")
    args = parser.parse_args(argv)
    if args.download:
        download(args.data_dir)
    hashes = {name: verify(args.data_dir / name, sha) for name, sha in PINNED.items()}
    provenance = {
        "repo": REPO_ID, "revision": REVISION, "file": DATA_FILE, "sha256": hashes[DATA_FILE],
        "tasks_file": "meta/tasks.parquet", "tasks_sha256": hashes["meta/tasks.parquet"],
        "url": f"https://huggingface.co/datasets/{REPO_ID}/tree/{REVISION}",
        "original": "REASSEMBLE, TU Wien, https://doi.org/10.48436/0ewrv-8cb44",
        "episode_index": EPISODE, "segment_index": [SEGMENT],
    }
    meta = rel.import_segments(args.data_dir / DATA_FILE, SAMPLE, episode=EPISODE, segments=[SEGMENT],
                               provenance=provenance)
    (SAMPLE / "ATTRIBUTION.txt").write_text(
        f"Licence: {rel.LICENCE} (https://creativecommons.org/licenses/by/4.0/)\n\n{rel.ATTRIBUTION}\n\n"
        f"Source file: {REPO_ID}@{REVISION}/{DATA_FILE} (sha256 {hashes[DATA_FILE]}),\n"
        f"episode {EPISODE}, segment {SEGMENT}. Changes: rows cut to one segment and re-encoded as JSONL.\n")

    tmp = Path(tempfile.mkdtemp(prefix="m55-"))
    counts = export_mcap(SAMPLE, tmp / "a.mcap")
    export_mcap(SAMPLE, tmp / "b.mcap")
    rebuilt = read_mcap(tmp / "a.mcap")
    timeline = subprocess.run(
        [sys.executable, "-m", "evaluation.timeline", str(SAMPLE), "--rrd", str(tmp / "t.rrd"), "--png",
         str(tmp / "t.png")], cwd=ROOT, capture_output=True, text=True)
    report = json.loads(timeline.stdout) if timeline.returncode == 0 else {"stderr": timeline.stderr}
    summary = {
        "provenance": provenance,
        "downloaded_sha256": hashes,
        "sample_log": str(SAMPLE.relative_to(ROOT)),
        "sample_bytes": {p.name: p.stat().st_size for p in sorted(SAMPLE.iterdir())},
        "segments": meta["segments"],
        "n_ticks": meta["n_ticks"],
        "log_schema": log_schema(SAMPLE),
        # LeRobot convention: action[t] is the next frame's measured pose, so commands are hindsight targets.
        "command_minus_next_observed_position_max_abs_m": _action_vs_next_pose(SAMPLE),
        "claim_problems": rel.claim_problems(SAMPLE),
        "mcap": {"messages": counts, "deterministic": (tmp / "a.mcap").read_bytes() == (tmp / "b.mcap").read_bytes(),
                 "round_trip_exact": {n: rebuilt[n] == (SAMPLE / n).read_text(encoding="utf-8")
                                      for n in (TELEMETRY, EVENTS, METADATA)}},
        "timeline": {"returncode": timeline.returncode, "series": report.get("series"),
                     "events": report.get("events"), "forbidden_modules_loaded": report.get("forbidden_modules_loaded")},
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    ok = (not summary["claim_problems"] and summary["mcap"]["deterministic"]
          and all(summary["mcap"]["round_trip_exact"].values()) and timeline.returncode == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
