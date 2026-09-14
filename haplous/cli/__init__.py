from __future__ import annotations

import argparse
from collections.abc import Sequence

from haplous.cli.commands import doctor_command, list_command
from haplous.cli.demo import demo_command
from haplous.cli.execution import eval_command, run_command
from haplous.cli.inspection import (
    compare_command,
    envelope_command,
    replay_command,
    report_command,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haplous")
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
    replay.add_argument("--ui", action="store_true")
    replay.set_defaults(handler=replay_command)

    envelope = commands.add_parser("envelope")
    envelope.add_argument("run_id")
    envelope.set_defaults(handler=envelope_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)


__all__ = ["main"]
