"""Claim-to-experiment driver (issue #20): compile a claim spec, run its DEV plan
as an M0 run, score it with HES, and re-validate from the sealed artifacts.

    MUJOCO_GL=osmesa PYTHONPATH=. .venv/bin/python scripts/claims_run.py compile evaluation/claim_specs/X.json
    ... scripts/claims_run.py run evaluation/claim_specs/X.json       # M0 run + results/claims/X/summary.json
    ... scripts/claims_run.py validate evaluation/claim_specs/X.json  # M0 integrity + re-score == committed

An UNSUPPORTED claim writes results/claims/X/unsupported.json and runs nothing.
`execute` is the child M0 runs; it writes plan.json and rows.jsonl to $M0_ARTIFACT_DIR.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("MUJOCO_GL", "osmesa")

RESULTS = REPO / "results" / "claims"
REGISTRY = RESULTS  # M0 root; per-claim outputs sit beside runs/ so they never mark the tree dirty


def load_spec(path: str):
    from hes.claim_compiler import ClaimSpec

    return ClaimSpec.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_id(plan) -> str:
    return f"claims-{plan.claim.property_id}-{plan.sha256[:12]}"


def sealed_inputs(registry, plan) -> tuple[list[str], list[dict]]:
    """(problems, rows) from the M0 run: integrity, plan identity, then rows."""
    rid = run_id(plan)
    if registry.state(rid) is None:
        return [f"no M0 run {rid}"], []
    problems = registry.check(rid)
    art = registry.run_dir(rid) / "artifacts"
    if not problems and json.loads((art / "plan.json").read_text())["plan_sha256"] != plan.sha256:
        problems.append("sealed plan.json is not the plan compiled from this spec")
    if problems:
        return problems, []
    return [], [json.loads(line) for line in (art / "rows.jsonl").read_text().splitlines() if line.strip()]


def cmd_compile(args) -> int:
    from hes.claim_compiler import compile_claim

    print(json.dumps(compile_claim(load_spec(args.spec)).as_dict(), indent=2, sort_keys=True))
    return 0


def cmd_execute(args) -> int:
    from hes.claim_compiler import compile_claim, execute_plan

    plan = compile_claim(load_spec(args.spec))
    out = Path(os.environ["M0_ARTIFACT_DIR"])
    write_json(out / "plan.json", {**plan.as_dict(), "plan_sha256": plan.sha256})
    rows = execute_plan(plan)
    (out / "rows.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    return 0


def cmd_run(args) -> int:
    import mujoco

    from evaluation.run_registry import Registry, record_run
    from hes.claim_compiler import Unsupported, compile_claim, score

    name = Path(args.spec).stem
    result = compile_claim(load_spec(args.spec))
    if isinstance(result, Unsupported):
        write_json(RESULTS / name / "unsupported.json", result.as_dict())
        print(json.dumps(result.as_dict(), indent=2))
        return 0
    registry = Registry(REGISTRY)
    rel_spec = str(Path(args.spec).resolve().relative_to(REPO))
    record = record_run(
        registry, run_id=run_id(result), track="sim_policy_free",
        purpose=f"issue #20 DEV claim experiment: {result.claim.statement}",
        command=[sys.executable, "scripts/claims_run.py", "execute", rel_spec],
        config={"spec": rel_spec, "plan_sha256": result.sha256, "tier": result.tier},
        simulator={"name": "mujoco", "version": mujoco.__version__},
        dependencies=("numpy", "mujoco", "jsonschema", "cryptography"),
        cwd=REPO, env={"PYTHONPATH": str(REPO), "MUJOCO_GL": "osmesa"},
    )
    if record["state"] != "VERIFIED":
        print(f"M0 run {record['run_id']} ended {record['state']}", file=sys.stderr)
        return 1
    problems, rows = sealed_inputs(registry, result)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    write_json(RESULTS / name / "plan.json", result.as_dict())
    write_json(RESULTS / name / "summary.json", {**score(result, rows), "m0_run_id": run_id(result)})
    return 0


def validate(spec_path: str, registry_root: Path = REGISTRY, results: Path = RESULTS) -> list[str]:
    from evaluation.run_registry import Registry
    from hes.claim_compiler import Plan, compile_claim, score

    plan = compile_claim(load_spec(spec_path))
    if not isinstance(plan, Plan):
        return ["claim no longer compiles to a plan"]
    problems, rows = sealed_inputs(Registry(registry_root), plan)
    if problems:
        return problems
    try:
        fresh = {**score(plan, rows), "m0_run_id": run_id(plan)}
    except ValueError as exc:
        return [str(exc)]
    committed = json.loads((results / Path(spec_path).stem / "summary.json").read_text())
    return [] if json.loads(json.dumps(fresh, sort_keys=True)) == committed else [
        "committed summary differs from the re-score of the sealed rows"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claims_run.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("compile", "execute", "run", "validate"):
        sub.add_parser(name).add_argument("spec")
    args = parser.parse_args(argv)
    if args.cmd == "validate":
        problems = validate(args.spec)
        print("\n".join(problems) or "OK")
        return 1 if problems else 0
    return {"compile": cmd_compile, "execute": cmd_execute, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
