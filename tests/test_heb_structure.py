"""HEB independence guards: the benchmark evaluates Haplous through its
public interfaces only. It must not import Haplous private symbols or
reach into haplous core internals."""
import ast
from pathlib import Path

HEB_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "heb"
HEB_FILES = sorted(HEB_DIR.glob("*.py"))


def test_heb_file_set_is_expected():
    names = [p.name for p in HEB_DIR.glob("*.py")]
    assert set(names) == {
        "__init__.py",
        "counterfactual.py",
        "insertion.py",
        "metrics.py",
        "mujoco_backend.py",
        "protocol.py",
        "runner.py",
        "sliding.py",
        "systems.py",
        "tasks.py",
    }


def test_heb_imports_no_private_haplous_symbols():
    for path in sorted(HEB_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] == "haplous":
                    for alias in node.names:
                        assert not alias.name.startswith("_"), (
                            f"{path.name} imports private "
                            f"{node.module}.{alias.name}"
                        )
                if node.module == "haplous" or node.module.startswith("haplous."):
                    continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                if isinstance(node.value, ast.Name) and node.value.id in (
                    "haplous",
                    "runtime",
                    "machine",
                ):
                    raise AssertionError(
                        f"{path.name} accesses private haplous member "
                        f"{node.attr}"
                    )


def test_heb_core_modules_import_no_haplous_logic():
    """Scenario representation, grading, and metrics must not depend on
    the system under test's logic; haplous.machine data types (the
    observation contract) are the only allowed dependency."""
    allowed = {"haplous.machine"}
    for name in ("protocol.py", "metrics.py"):
        tree = ast.parse((HEB_DIR / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root == "haplous":
                    assert node.module in allowed, f"{name} imports {node.module}"
