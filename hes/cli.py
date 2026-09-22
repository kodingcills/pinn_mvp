"""HES command line.

Subcommands are grouped by the question they answer:

- ``doctor``: is the environment and the evidence package usable?
- ``test``: run HES self-tests (smoke by default).
- ``analyze``: produce the metric-vector bundle for a run directory.
- ``qualify``: evaluate hard gates and print PASS/FAIL.
- ``claims``: print the claim ledger.
- ``recommend-next``: print ranked next experiments.
- ``characterize``: print the substrate characterization.
- ``populations``: list versioned population classes.
- ``report``: render or drift-check the canonical report.
- ``migrate``: ingest an existing campaign package into HES form.
- ``replay``/``counterfactual``/``boundary``: require a live benchmark
  runner; report NOT AVAILABLE rather than pretending to run one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from hes.benchmark_health import benchmark_health
from hes.bundle import build_bundle, to_json
from hes.characterize import characterize
from hes.populations import list_populations
from hes.report import check_report, render_markdown
from hes.run import load_run


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _cmd_doctor(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {"python": sys.version.split()[0]}
    try:
        import mujoco  # noqa: F401

        from haplous import __version__ as haplous_version

        report["mujoco"] = True
        report["haplous_version"] = haplous_version
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        report["mujoco"] = False
        report["import_error"] = str(exc)
    root = Path(args.run) if args.run else None
    if root is not None:
        report["run_root"] = str(root)
        report["run_exists"] = root.is_dir()
        if root.is_dir():
            try:
                from hes.run import load_run

                run = load_run(root)
                report["n_episodes"] = len(run.rows)
                report["arms"] = list(run.arms)
                report["has_forks"] = bool(run.forks)
                report["experiment_id"] = run.experiment_id
            except Exception as exc:  # noqa: BLE001
                report["run_error"] = str(exc)
    _print_json(report)
    return 0


def _cmd_test(args: argparse.Namespace) -> int:
    import subprocess

    target = "tests/test_hes_smoke.py" if args.smoke else "tests"
    args_list = [sys.executable, "-m", "pytest", target, "-q"]
    if not args.smoke:
        args_list = [sys.executable, "-m", "pytest", "tests", "-q", "-k", "hes"]
    return subprocess.call(args_list)


def _cmd_analyze(args: argparse.Namespace) -> int:
    bundle = build_bundle(args.run, runtime_arm=args.arm)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(to_json(bundle), encoding="utf-8")
    if not args.quiet:
        _print_json(bundle["analysis"])
    return 0


def _cmd_qualify(args: argparse.Namespace) -> int:
    bundle = build_bundle(args.run, runtime_arm=args.arm)
    gates = bundle["hard_gates"]
    _print_json(gates)
    return 0 if gates["qualified"] else 1


def _cmd_claims(args: argparse.Namespace) -> int:
    bundle = build_bundle(args.run, runtime_arm=args.arm)
    for claim in bundle["claim_ledger"]["claims"]:
        print(
            f"{claim['claim_id']:>4} {claim['name']:<28} "
            f"registry={claim['registry_status']:<28} hes={claim['hes_status']}"
        )
        for note in claim["notes"]:
            print(f"       {note}")
    return 0


def _cmd_recommend_next(args: argparse.Namespace) -> int:
    bundle = build_bundle(args.run, runtime_arm=args.arm)
    _print_json({"recommendations": bundle["recommendations"]})
    return 0


def _cmd_characterize(_args: argparse.Namespace) -> int:
    _print_json(characterize().as_dict())
    return 0


def _cmd_benchmark_health(args: argparse.Namespace) -> int:
    run = load_run(args.run)
    health = benchmark_health(run.rows)
    _print_json(health)
    return 0 if health["healthy"] else 1


def _cmd_populations(_args: argparse.Namespace) -> int:
    _print_json(list_populations())
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    bundle = build_bundle(args.run, runtime_arm=args.arm)
    if args.check:
        ok, message = check_report(args.out, bundle)
        print(message)
        return 0 if ok else 1
    markdown = render_markdown(bundle)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    print(f"wrote {out}")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    try:
        from scripts.migrate_v3_campaign_to_hes import migrate
    except ImportError as exc:  # pragma: no cover - packaging dependent
        print(
            json.dumps(
                {
                    "command": "migrate",
                    "status": "NOT_AVAILABLE",
                    "reason": "scripts/ is not importable from here; run "
                    "`.venv/bin/python scripts/migrate_v3_campaign_to_hes.py` from the repo root",
                    "import_error": str(exc),
                },
                indent=2,
            )
        )
        return 2
    result = migrate(args.source, args.out, runtime_arm=args.arm)
    _print_json(result)
    return 0


def _unavailable_runner_command(name: str):
    def handler(args: argparse.Namespace) -> int:
        print(
            json.dumps(
                {
                    "command": name,
                    "status": "NOT_AVAILABLE",
                    "reason": "requires a live HEB benchmark runner; "
                    "HES v0 analyzes committed evidence only",
                    "run": args.run,
                },
                indent=2,
            )
        )
        return 2

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hes", description="Haplous Evaluation System v0")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="environment and evidence check")
    p.add_argument("--run", default=None)
    p.set_defaults(func=_cmd_doctor)

    p = sub.add_parser("test", help="run HES self-tests")
    p.add_argument("--smoke", action="store_true")
    p.set_defaults(func=_cmd_test)

    p = sub.add_parser("analyze", help="produce the metric-vector bundle")
    p.add_argument("run")
    p.add_argument("--arm", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=_cmd_analyze)

    p = sub.add_parser("qualify", help="evaluate hard gates")
    p.add_argument("run")
    p.add_argument("--arm", default=None)
    p.set_defaults(func=_cmd_qualify)

    p = sub.add_parser("claims", help="print the claim ledger")
    p.add_argument("run")
    p.add_argument("--arm", default=None)
    p.set_defaults(func=_cmd_claims)

    p = sub.add_parser("recommend-next", help="rank next experiments")
    p.add_argument("run")
    p.add_argument("--arm", default=None)
    p.set_defaults(func=_cmd_recommend_next)

    p = sub.add_parser("characterize", help="substrate characterization")
    p.set_defaults(func=_cmd_characterize)

    p = sub.add_parser("benchmark-health", help="is the benchmark instrument sound?")
    p.add_argument("run")
    p.set_defaults(func=_cmd_benchmark_health)

    p = sub.add_parser("populations", help="list versioned population classes")
    p.set_defaults(func=_cmd_populations)

    p = sub.add_parser("report", help="render or check the canonical report")
    p.add_argument("run")
    p.add_argument("--out", default="docs/HES_V0_REPORT.md")
    p.add_argument("--check", action="store_true")
    p.add_argument("--arm", default=None)
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("migrate", help="ingest an existing campaign into HES form")
    p.add_argument("source")
    p.add_argument("--out", required=True)
    p.add_argument("--arm", default=None)
    p.set_defaults(func=_cmd_migrate)

    for name, help_text in (
        ("replay", "replay a recorded scenario (requires a live runner)"),
        ("counterfactual", "run counterfactual forks (requires a live runner)"),
        ("boundary", "search a decision boundary (requires a live runner)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("run")
        p.set_defaults(func=_unavailable_runner_command(name))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())