"""Static truth-leak guard (M4.1b): a system's own decision code may not read evaluator-only
Scenario fields.

The dynamic detector (test_negative_truth_leak.py) sees only values that cross the machine
boundary or sit in the intent. A system that branches on `scenario.dropout_windows_s` inside its
own code crosses nothing, so it is invisible there. This guard parses each system under test.

Rule: in a system's code, every load of the name `scenario` must be one of
  1. `scenario.<attr>` with <attr> a DECLARED field (the requirements a system may be given), or
  2. a bare argument to a benchmark-owned callee in ALLOWED_CALLEES (builders and delegation).
Anything else fails: evaluator-only attributes, getattr/vars/asdict, aliasing, other calls.
Limits: syntactic and name-based. It does not follow data through other objects (for example
`realization.*`), and a renamed parameter is caught only by the `run` signature check.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_negative_truth_leak import DECLARED, EVALUATOR_ONLY

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_CALLEES = frozenset({
    "build_intent", "systems.build_intent",   # scenario requirements -> ExecutionIntent
    "realization.condition_factory",           # task success condition (task-registry truth)
    "super().run",                             # PassThroughSystem delegating to BareSystem
})
# (file, classes to check; None = whole module). Benchmark-owned code in these files (build_intent,
# system_by_name, harness and grading in passthrough.py) legitimately reads the full scenario.
TARGETS = (
    ("benchmarks/incumbent.py", None),  # G1
    ("benchmarks/heb/systems.py", ("HaplousSystem", "SupervisedSystem", "BareSystem")),  # BareSystem = direct arm too
    ("benchmarks/passthrough.py", ("PassThroughSystem",)),
)


def violations(source: str, classes: tuple[str, ...] | None) -> list[str]:
    tree = ast.parse(source)
    scopes = [tree] if classes is None else [
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name in classes]
    if classes is not None:
        assert {s.name for s in scopes} == set(classes), "system class missing from file"
    found = []
    for scope in scopes:
        parents = {c: p for p in ast.walk(scope) for c in ast.iter_child_nodes(p)}
        for fn in (n for n in ast.walk(scope) if isinstance(n, ast.FunctionDef) and n.name == "run"):
            if [a.arg for a in fn.args.args][2:3] != ["scenario"]:
                found.append(f"{fn.lineno}: run() second parameter must be named scenario")
        for node in ast.walk(scope):
            if not (isinstance(node, ast.Name) and node.id == "scenario" and isinstance(node.ctx, ast.Load)):
                continue
            parent = parents[node]
            if isinstance(parent, ast.Attribute) and parent.value is node:
                if parent.attr not in DECLARED:
                    found.append(f"{node.lineno}: scenario.{parent.attr} is not a declared field")
            elif isinstance(parent, ast.Call) and node in parent.args and ast.unparse(parent.func) in ALLOWED_CALLEES:
                continue
            else:
                found.append(f"{node.lineno}: scenario used outside the allow-list: {ast.unparse(parent)[:60]}")
    return found


@pytest.mark.parametrize("path, classes", TARGETS, ids=[t[0] for t in TARGETS])
def test_system_decision_code_reads_no_evaluator_only_scenario_field(path, classes):
    assert violations((ROOT / path).read_text(), classes) == [], path


@pytest.mark.parametrize("body", [
    "if scenario.dropout_windows_s: pass",
    "x = getattr(scenario, 'fault_at_s')",
    "alias = scenario",
    "d = vars(scenario)",
    "helper(scenario)",
    "t = scenario.tags",
])
def test_guard_flags_planted_reads(body):
    src = f"class S:\n    def run(self, realization, scenario):\n        {body}\n"
    assert violations(src, ("S",)), body


def test_guard_accepts_declared_reads_and_allowed_calls():
    src = (
        "class S:\n    def run(self, realization, scenario):\n"
        "        c = realization.condition_factory(scenario, realization.bundle)\n"
        "        i = build_intent(scenario, c)\n"
        "        return scenario.max_force_n\n"
    )
    assert violations(src, ("S",)) == []


@pytest.mark.parametrize("name", sorted(EVALUATOR_ONLY))
def test_every_evaluator_only_field_is_rejected(name):
    src = f"class S:\n    def run(self, realization, scenario):\n        scenario.{name}\n"
    assert violations(src, ("S",)), name
