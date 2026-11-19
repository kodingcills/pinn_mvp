"""The `haplous` distribution has no core dependencies, so importing the runtime and its CLI
must not load any extra's modules. Fresh interpreters, because this suite imports them all."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXTRA_MODULES = ("mujoco", "numpy", "rerun", "fastapi")


def _python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60)


def test_runtime_and_cli_import_closure_is_stdlib_only() -> None:
    result = _python(
        "import sys, haplous, haplous.runtime, haplous.capabilities, haplous.machine, haplous.lifecycle, haplous.cli\n"
        f"print(sorted(m for m in {EXTRA_MODULES!r} if m in sys.modules))"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_cli_names_the_missing_extra_and_doctor_survives_a_bare_install() -> None:
    result = _python(
        "import sys\n"
        "for m in ('mujoco', 'numpy', 'fastapi', 'uvicorn'): sys.modules[m] = None\n"
        "from haplous.cli import main\n"
        "print('demo rc', main(['demo']))\n"
        "print('studio rc', main(['studio', '--no-browser']))\n"
        "print('doctor rc', main(['doctor']))"
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "haplous demo needs the 'sim' extra (pip install 'haplous[sim]')" in out
    assert "demo rc 2" in out
    assert "haplous studio needs the 'studio' extra" in out and "studio rc 2" in out
    assert "extra [sim]" in out and "missing: mujoco numpy" in out
    assert "doctor rc 0" in out
