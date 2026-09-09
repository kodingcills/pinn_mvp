from __future__ import annotations

import importlib
import importlib.util
import os
from argparse import Namespace
from pathlib import Path
from typing import Sequence

import haplous

from haplous.cli.benchmarks import registered_benchmarks
from haplous.cli.registry import RunRegistry


SYSTEM_NAMES = ("direct_minimal", "bare_behavior", "haplous_mediated")


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [
        max([len(header), *(len(row[index]) for row in rows)])
        for index, header in enumerate(headers)
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def doctor_command(_args: Namespace) -> int:
    checks = [("haplous core", "OK", haplous.__version__)]
    if importlib.util.find_spec("mujoco") is None:
        checks.append(("mujoco", "NOT INSTALLED", ""))
    else:
        mujoco = importlib.import_module("mujoco")
        checks.append(("mujoco", "OK", str(mujoco.__version__)))
    for label, modules in (
        ("robosuite", ("robosuite",)),
        ("isaaclab/isaacsim", ("isaaclab", "isaacsim")),
        ("rerun", ("rerun",)),
        ("ContactBench", ("contactbench",)),
    ):
        available = any(importlib.util.find_spec(module) is not None for module in modules)
        checks.append((label, "OK" if available else "NOT INSTALLED", ""))
    data_path = Path(os.environ.get("HAPLOUS_DATA_DIR", "datasets"))
    checks.append(("datasets", "OK" if data_path.is_dir() else "MISSING", str(data_path)))
    result_path = RunRegistry().results_root
    checks.append(("result directory", "OK" if result_path.is_dir() else "MISSING", str(result_path)))
    _table(("check", "status", "detail"), checks)
    return 0


def list_command(_args: Namespace) -> int:
    benchmarks = registered_benchmarks()
    sections = {
        "benchmarks": [benchmark.name for benchmark in benchmarks],
        "tasks": sorted({benchmark.task_id for benchmark in benchmarks}),
        "systems": list(SYSTEM_NAMES),
        "controllers": ["validated_impedance"],
        "backends": ["internal-mujoco"],
        "datasets": [os.environ.get("HAPLOUS_DATA_DIR", "datasets")],
        "recent runs": [
            f"{run.run_dir.parent.name}/{run.run_id}" for run in RunRegistry().recent()
        ],
    }
    for section, values in sections.items():
        print(f"{section}:")
        for value in values:
            print(f"  {value}")
        if not values:
            print("  (none)")
    return 0
