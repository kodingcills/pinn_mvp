from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from argparse import Namespace
from pathlib import Path
from typing import Sequence

import haplous

from haplous.cli.benchmarks import registered_benchmarks
from haplous.cli.registry import RunRegistry


SYSTEM_NAMES = (
    "direct_minimal",
    "bare_behavior",
    "competent_supervisor",
    "haplous_mediated",
)


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [
        max([len(header), *(len(row[index]) for row in rows)])
        for index, header in enumerate(headers)
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _git_describe() -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always"], capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _gpu_check() -> tuple[str, str, str]:
    import platform
    import shutil
    import subprocess

    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0:
                return ("gpu/cuda", "OK", out.stdout.strip().splitlines()[0])
        except subprocess.TimeoutExpired:
            pass
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            chipset = next(
                (line.strip() for line in out.stdout.splitlines() if "Chipset" in line), "Metal"
            )
            return ("gpu/metal", "OK", chipset)
        except (OSError, subprocess.TimeoutExpired):
            return ("gpu/metal", "UNKNOWN", "")
    return ("gpu/cuda", "NOT INSTALLED", "")


def _full_checks(checks: list[tuple[str, str, str]]) -> None:
    for label, spec in (
        ("numpy", "numpy"),
        ("matplotlib", "matplotlib"),
        ("pytest", "pytest"),
        ("yaml", "yaml"),
    ):
        spec_found = importlib.util.find_spec(spec) is not None
        version = ""
        if spec_found:
            module = importlib.import_module(spec)
            version = getattr(module, "__version__", "")
        checks.append((label, "OK" if spec_found else "NOT INSTALLED", version))

    checks.append(("evaluation package", "OK", "evaluation/ + claims/metrics registries"))
    checks.append(("haplous git tag", "OK", _git_describe()))
    checks.append(_gpu_check())

    repo = Path(__file__).resolve().parent.parent.parent
    for venv in (".venv-maniskill", ".venv-robosuite"):
        path = repo / venv
        if path.is_dir():
            marker = "OK" if (path / "bin" / "python").exists() else "BROKEN"
            checks.append((f"venv {venv}", marker, str(path)))
        else:
            checks.append((f"venv {venv}", "NOT INSTALLED", ""))

    for label, env_var, default in (
        ("checkpoints", "HAPLOUS_CKPT_DIR", "datasets/checkpoints"),
        ("benchmark assets", "HAPLOUS_ASSETS_DIR", "datasets"),
    ):
        path = Path(os.environ.get(env_var, default))
        checks.append((label, "OK" if path.is_dir() else "MISSING", str(path)))

    try:
        import matplotlib

        importlib.import_module("matplotlib.backends.backend_agg")
        checks.append(("visualization", "OK", matplotlib.__version__))
    except (ImportError, AttributeError):
        checks.append(("visualization", "NOT INSTALLED", ""))


def doctor_command(args: Namespace) -> int:
    import platform
    import shutil

    checks = [("haplous core", "OK", haplous.__version__)]
    if importlib.util.find_spec("mujoco") is None:
        checks.append(("mujoco", "NOT INSTALLED", ""))
    else:
        mujoco = importlib.import_module("mujoco")
        checks.append(("mujoco", "OK", str(mujoco.__version__)))
    for label, modules in (
        ("robosuite", ("robosuite",)),
        ("maniskill/sapien", ("mani_skill",)),
        ("simplerenv", ("simpler_env",)),
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
    try:
        disk_free_gb = shutil.disk_usage(Path.cwd()).free / 2**30
        checks.append(("disk free", "OK" if disk_free_gb >= 4 else "LOW", f"{disk_free_gb:.1f} GiB"))
    except OSError:
        checks.append(("disk free", "UNKNOWN", ""))
    try:
        mem_bytes = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
        checks.append(("memory", "OK" if mem_bytes >= 8 * 2**30 else "LOW", f"{mem_bytes / 2**30:.1f} GiB"))
    except (ValueError, OSError):
        checks.append(("memory", "UNKNOWN", ""))
    checks.append(("python", "OK" if sys.version_info >= (3, 10) else "OLD", platform.python_version()))
    checks.append(("cmake", "OK" if shutil.which("cmake") else "NOT INSTALLED", ""))
    if getattr(args, "full", False):
        _full_checks(checks)
    _table(("check", "status", "detail"), checks)
    return 0


def list_command(_args: Namespace) -> int:
    benchmarks = registered_benchmarks()
    sections = {
        "benchmarks": [benchmark.name for benchmark in benchmarks],
        "tasks": sorted({benchmark.task_id for benchmark in benchmarks}),
        "systems": list(SYSTEM_NAMES),
        "controllers": ["validated_impedance"],
        "backends": ["internal-mujoco", "robosuite-mujoco", "maniskill-physx"],
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
