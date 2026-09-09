from __future__ import annotations

import json
import webbrowser
from argparse import Namespace
from pathlib import Path
from typing import Mapping

from haplous.cli.commands import _table
from haplous.cli.registry import JsonValue, RunRegistry


def _load_json(path: Path) -> Mapping[str, JsonValue]:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_command(args: Namespace) -> int:
    registry = RunRegistry()
    left = _load_json(registry.resolve(args.run_a).summary).get("systems", {})
    right = _load_json(registry.resolve(args.run_b).summary).get("systems", {})
    rows = []
    for system in sorted(set(left) | set(right)):
        left_metrics = left.get(system, {})
        right_metrics = right.get(system, {})
        for metric in sorted(set(left_metrics) | set(right_metrics)):
            left_value = left_metrics.get(metric, "-")
            right_value = right_metrics.get(metric, "-")
            if isinstance(left_value, (str, int, float, bool)) or left_value is None:
                rows.append((system, metric, str(left_value), str(right_value)))
    _table(("system", "metric", args.run_a, args.run_b), rows)
    return 0


def report_command(args: Namespace) -> int:
    summary = RunRegistry().resolve(args.run_id).summary
    print(summary)
    if args.open:
        try:
            opened = webbrowser.open(summary.resolve().as_uri())
        except webbrowser.Error:
            opened = False
        if not opened:
            print("browser: unavailable")
    return 0


def _load_trials(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def replay_command(args: Namespace) -> int:
    rows = _load_trials(RunRegistry().resolve(args.run_id).trials)
    selected = [row for row in rows if not row.get("bounded_success")] if args.failures else rows
    _table(
        ("scenario_id", "system", "reason"),
        [
            (str(row.get("scenario_id", "")), str(row.get("system", "")), str(row.get("reason") or "-"))
            for row in selected
        ],
    )
    if args.ui:
        print("viewer: not wired")
    return 0


def envelope_command(args: Namespace) -> int:
    rows = _load_trials(RunRegistry().resolve(args.run_id).trials)
    grouped = {}
    for row in rows:
        for dimension, value in row.get("scenario_axes", {}).items():
            key = (str(dimension), str(value))
            successes, total = grouped.get(key, (0, 0))
            grouped[key] = (successes + int(bool(row.get("bounded_success"))), total + 1)
    table_rows = [
        (dimension, value, f"{successes}/{total}", f"{successes / total:.3f}")
        for (dimension, value), (successes, total) in sorted(grouped.items())
    ]
    _table(("dimension", "value", "bounded", "rate"), table_rows)
    return 0
