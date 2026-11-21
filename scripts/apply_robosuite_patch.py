"""Apply patches/robosuite-1.5.2-joint-velocity.patch to the robosuite installed for THIS interpreter.

Run it with the interpreter whose robosuite you want patched, e.g.
    <venv>/bin/python scripts/apply_robosuite_patch.py
Idempotent: refuses any robosuite other than 1.5.2 and any joint_vel.py that is neither the
pristine 1.5.2 file nor the patched one (sha256-checked), so it never patches unknown code.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import subprocess
import sys
from pathlib import Path

VERSION = "1.5.2"
REL = "robosuite/controllers/parts/generic/joint_vel.py"
PRISTINE = "4e8e5c15208c00a1e068efd4caa91f780369647d49e82e51bd1631d92e819fc1"
PATCHED = "53801168e9fb8e7fada70a4e144609700801e1fa09a4486e38c2b0e282f8f4da"
PATCH = Path(__file__).resolve().parents[1] / "patches" / "robosuite-1.5.2-joint-velocity.patch"


def main() -> int:
    version = importlib.metadata.version("robosuite")
    if version != VERSION:
        print(f"refused: robosuite {version} installed, patch is for {VERSION}")
        return 2
    spec = importlib.util.find_spec("robosuite")
    site = Path(spec.origin).resolve().parents[1]
    target = site / REL
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest == PATCHED:
        print(f"already patched: {target}")
        return 0
    if digest != PRISTINE:
        print(f"refused: {target} is neither pristine nor patched (sha256 {digest})")
        return 2
    subprocess.run(["patch", "-p1", "-d", str(site), "-i", str(PATCH)], check=True)
    if hashlib.sha256(target.read_bytes()).hexdigest() != PATCHED:
        print("error: patched file hash mismatch")
        return 1
    print(f"patched: {target} (sha256 {PATCHED})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
