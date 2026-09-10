import ast
from pathlib import Path


EXTERNAL_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "external"
EXPECTED_FILES = {
    "__init__.py",
    "robosuite_policy.py",
    "robosuite_task.py",
    "maniskill_policy.py",
    "maniskill_task.py",
}


def _trees() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(EXTERNAL_DIR.glob("*.py"))
    ]


def test_external_benchmark_file_set_is_expected():
    assert {path.name for path in EXTERNAL_DIR.glob("*.py")} == EXPECTED_FILES


def test_robosuite_imports_are_lazy():
    for path, tree in _trees():
        for node in tree.body:
            if isinstance(node, ast.Import):
                assert all(alias.name != "robosuite" for alias in node.names), path.name
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "robosuite", path.name


def test_maniskill_imports_are_lazy():
    for path, tree in _trees():
        for node in tree.body:
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".")[0] not in {"gymnasium", "mani_skill"}
                    for alias in node.names
                ), path.name
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in {
                    "gymnasium",
                    "mani_skill",
                }, path.name


def test_external_benchmarks_do_not_import_mujoco():
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name != "mujoco" for alias in node.names), path.name
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "mujoco", path.name


def test_external_benchmarks_use_only_public_haplous_api():
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root == "haplous":
                    assert node.module == "haplous", f"{path.name} imports {node.module}"
                    assert all(not alias.name.startswith("_") for alias in node.names)
