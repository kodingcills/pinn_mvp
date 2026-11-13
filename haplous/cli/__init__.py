from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from haplous.cli.commands import doctor_command, list_command
from haplous.cli.demo import demo_command
from haplous.cli.execution import eval_command, run_command
from haplous.cli.inspection import (
    compare_command,
    envelope_command,
    inspect_command,
    replay_command,
    report_command,
    timeline_command,
)


def _version() -> str:
    import haplous

    return haplous.__version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haplous")
    parser.add_argument(
        "--version", action="version", version=f"haplous {_version()}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor")
    doctor.add_argument("--full", action="store_true", help="include dependency/asset/GPU matrix")
    doctor.set_defaults(handler=doctor_command)

    listing = commands.add_parser("list")
    listing.set_defaults(handler=list_command)

    demo = commands.add_parser("demo", help="run the three-leg reference execution (success, refusal, bounded abort)")
    demo.set_defaults(handler=demo_command)

    run = commands.add_parser("run")
    run.add_argument("benchmark")
    run.add_argument("--system", default="haplous_mediated")
    run.add_argument("--scenario")
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--ui", action="store_true")
    run.set_defaults(handler=run_command)

    evaluate = commands.add_parser("eval")
    evaluate.add_argument("suite", choices=("internal", "external"))
    evaluate.add_argument("--systems", default="direct_minimal,haplous_mediated")
    evaluate.add_argument("--headless", action="store_true")
    evaluate.set_defaults(handler=eval_command)

    compare = commands.add_parser("compare")
    compare.add_argument("run_a")
    compare.add_argument("run_b")
    compare.set_defaults(handler=compare_command)

    report = commands.add_parser("report")
    report.add_argument("run_id")
    report.add_argument("--open", action="store_true")
    report.set_defaults(handler=report_command)

    replay = commands.add_parser("replay")
    replay.add_argument("run_id")
    replay.add_argument("--failures", action="store_true")
    replay.add_argument("--ui", action="store_true", help="for an M0 recording: export .rrd and .mp4")
    replay.add_argument("--recordings-root", type=Path, help="M0 registry root holding recordings")
    replay.add_argument("--rrd", type=Path, help="recording: .rrd output path")
    replay.add_argument("--mp4", type=Path, help="recording: .mp4 output path")
    replay.add_argument("--report", type=Path, help="recording: replay report JSON path")
    replay.set_defaults(handler=replay_command)

    envelope = commands.add_parser("envelope")
    envelope.add_argument("run_id")
    envelope.set_defaults(handler=envelope_command)

    inspect = commands.add_parser("inspect", help="explain one execution: request, decision, timeline, terminal result")
    inspect.add_argument("run_id")
    inspect.add_argument("--json", action="store_true", help="machine-readable output")
    inspect.set_defaults(handler=inspect_command)

    timeline = commands.add_parser(
        "timeline", help="execution timeline from a run or evidence JSON; plots (.rrd + .png) from an episode log")
    timeline.add_argument("source", help="run id, evidence JSON, M0 recording run id, or episode-log directory")
    timeline.add_argument("--json", action="store_true", help="machine-readable output")
    timeline.add_argument("--recordings-root", type=Path, help="M0 registry root holding recordings")
    timeline.add_argument("--rrd", type=Path, help="episode log: .rrd output path")
    timeline.add_argument("--png", type=Path, help="episode log: .png output path")
    timeline.set_defaults(handler=timeline_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)


__all__ = ["main"]
