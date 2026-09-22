"""Reproduce the HES v0 campaign from committed evidence.

Pipeline (no physics is re-run):

1. migrate the historical v3 qualification package into a HES run directory
   (idempotent; the source is copied, never modified);
2. analyze and evaluate the hard gates;
3. render the canonical report and verify the digest;
4. write ``hes_gates.json`` next to the analysis bundle.

Usage:
    python scripts/run_hes_v0_campaign.py
    python scripts/run_hes_v0_campaign.py --source results/v3_hardening_qualification \
        --hes-root results/hes/v3_hardening_qualification --report docs/HES_V0_REPORT.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hes.bundle import build_bundle, to_json  # noqa: E402
from hes.report import check_report, render_markdown  # noqa: E402
from hes.run import load_run  # noqa: E402
from scripts.migrate_v3_campaign_to_hes import migrate  # noqa: E402


def run_campaign(
    source: str | Path,
    hes_root: str | Path,
    report_path: str | Path,
    *,
    runtime_arm: str | None = None,
) -> dict[str, Any]:
    source = Path(source)
    hes_root = Path(hes_root)
    report_path = Path(report_path)

    migration = migrate(source, hes_root, runtime_arm=runtime_arm)

    run = load_run(hes_root)
    bundle = build_bundle(run, runtime_arm=runtime_arm)
    (hes_root / "hes_analysis.json").write_text(to_json(bundle), encoding="utf-8")
    (hes_root / "hes_gates.json").write_text(
        json.dumps(bundle["hard_gates"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown(bundle), encoding="utf-8")
    ok, message = check_report(report_path, bundle)

    summary = {
        "source": str(source),
        "hes_root": str(hes_root),
        "report": str(report_path),
        "runtime_arm": bundle["runtime_arm"],
        "baseline_arm": bundle["analysis"]["baseline_arm"],
        "qualified": bundle["hard_gates"]["qualified"],
        "gate_failures": [
            g["gate_id"] for g in bundle["hard_gates"]["gates"] if not g["passed"]
        ],
        "report_digest_ok": ok,
        "report_digest_message": message,
        "historical_results_modified": migration["historical_results_modified"],
        "not_available_from_historical_evidence": migration[
            "not_available_from_historical_evidence"
        ],
    }
    (hes_root / "hes_campaign_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="results/v3_hardening_qualification")
    parser.add_argument("--hes-root", default="results/hes/v3_hardening_qualification")
    parser.add_argument("--report", default="docs/HES_V0_REPORT.md")
    parser.add_argument("--arm", default=None)
    args = parser.parse_args(argv)
    summary = run_campaign(args.source, args.hes_root, args.report, runtime_arm=args.arm)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["report_digest_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())