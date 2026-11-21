"""E20 effort ledger: builders mark phase boundaries; the coordinator adds session usage.

    python scripts/e20_effort.py ARM PHASE start|end [note]
Appends one JSON line to results/e20/<ARM>/effort.jsonl with the wall-clock time.
"""
import json
import sys
import time
from pathlib import Path

arm, phase, mark = sys.argv[1:4]
assert arm in ("haplous", "g1") and mark in ("start", "end"), "usage: ARM(haplous|g1) PHASE start|end [note]"
path = Path(__file__).resolve().parents[1] / "results" / "e20" / arm / "effort.jsonl"
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a") as fh:
    fh.write(json.dumps({"event": f"phase_{mark}", "phase": phase, "t": time.time(),
                         "note": " ".join(sys.argv[4:])}) + "\n")
print(f"{arm} {phase} {mark}")
