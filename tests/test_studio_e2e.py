"""Opt-in: the Studio release-1 Playwright E2E (studio/web/e2e/studio.e2e.cjs) on the real server.

Skipped unless STUDIO_E2E=1: it needs Node, a global Playwright with Chromium on NODE_PATH and
the built frontend, and records 2 episodes (~30 s). Command in docs/eval_program/REPRODUCE.md.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "studio" / "web"

pytestmark = pytest.mark.skipif(os.environ.get("STUDIO_E2E") != "1", reason="opt-in: set STUDIO_E2E=1")


def test_release_1_definition_of_done_in_a_browser() -> None:
    node = shutil.which("node") or "/opt/node22/bin/node"
    proc = subprocess.run([node, "e2e/studio.e2e.cjs"], cwd=WEB, env={**os.environ, "PYTHON": sys.executable},
                          capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    assert "PASS" in proc.stdout
