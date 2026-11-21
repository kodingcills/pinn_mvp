"""Run the frozen E20 acceptance suite against an arm and log the runtime (excluded from effort).

    MUJOCO_GL=osmesa <robosuite venv>/bin/python -I scripts/e20_acceptance.py MODULE:FUNC OUT.json [LEDGER.jsonl]
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.e20_shared import direct_arm  # noqa: E402
from benchmarks.e20_shared.acceptance import run_suite  # noqa: E402


def main() -> int:
    mod, func = sys.argv[1].split(":")
    arm = getattr(importlib.import_module(mod), func)
    t0 = time.time()
    result = run_suite(arm, direct_arm.run)
    result.update(arm=sys.argv[1], wall_s=round(time.time() - t0, 1))
    Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)
    Path(sys.argv[2]).write_text(json.dumps(result, indent=1) + "\n")
    if len(sys.argv) > 3:  # effort ledger: acceptance runtime is excluded from active effort
        with open(sys.argv[3], "a") as fh:
            fh.write(json.dumps({"event": "acceptance_run", "arm": sys.argv[1], "start": t0,
                                 "wall_s": result["wall_s"], "passed": result["passed"], "total": result["total"]}) + "\n")
    for c in result["cases"]:
        print(f"{'PASS' if c['pass'] else 'FAIL'} {c['case']:22s} seed={c['seed']} outcome={c['outcome']} {'; '.join(c['problems'])}")
    print(f"{result['passed']}/{result['total']} passed in {result['wall_s']} s")
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
