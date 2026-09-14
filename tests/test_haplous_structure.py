"""Structural guards: haplous core is simulator-free and behavior-free."""
import ast
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[1] / "haplous"
CORE_FILES = sorted(CORE_DIR.glob("*.py"))


def test_core_file_set_is_expected():
    names = [p.name for p in CORE_FILES]
    assert set(names) == {
        "__init__.py",
        "behavior.py",
        "capabilities.py",
        "constraints.py",
        "evidence.py",
        "evidence_store.py",
        "intent.py",
        "machine.py",
        "runtime.py",
        "state.py",
    }


def test_core_imports_no_simulator_or_substrate():
    for path in CORE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                assert root not in ("mujoco", "pegsim"), (
                    f"{path.name} imports {module}"
                )


def test_core_has_no_behavior_vocabulary():
    for path in CORE_FILES:
        text = path.read_text(encoding="utf-8").lower()
        assert "peg" not in text, f"{path.name} mentions peg"
        assert "insert" not in text, f"{path.name} mentions insert"
