"""Campaign 0A: CPU-only, policy-free LIBERO-Dynamic smoke run, recorded via the M0 registry.

Runs the upstream documented zero-policy smoke (docs/formal_dynamic_eval.md
"Small Smoke Test") with OSMesa software rendering instead of EGL/GPU, then checks
the upstream acceptance criteria against the hashed artifacts:
  total == limit; every item status ok; non-empty diagnostics.targets; diagnostics.ok.

Deviation from the upstream command: videos disabled (--no-save-videos) to keep
committed artifacts small; rendering is still exercised (policy observations).

Usage:
  python scripts/run_libero_dynamic_cpu_smoke.py --libero-src DIR --libero-python PY \
      --run-id ID [--limit 4] [--max-steps 40]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from evaluation.run_registry import Registry, probe_environment, record_run

UPSTREAM = "https://github.com/renshaojie233/LIBERO-Dynamic"
ROOT = Path(__file__).resolve().parents[1]


def acceptance(summary: dict, items: list[dict], limit: int) -> dict:
    checks = {
        "total_equals_limit": summary.get("total") == limit,
        "all_status_ok": bool(items) and all(i.get("status") == "ok" for i in items),
        "all_targets_nonempty": bool(items) and all((i.get("diagnostics") or {}).get("targets") for i in items),
        "all_diagnostics_ok": bool(items) and all((i.get("diagnostics") or {}).get("ok") is True for i in items),
    }
    return {"checks": checks, "pass": all(checks.values()), "n_items": len(items)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--libero-src", type=Path, required=True)
    ap.add_argument("--libero-python", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--registry", type=Path, default=ROOT / "results" / "m0_registry")
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--timeout-s", type=float, default=3600)
    ap.add_argument("--latency-s", type=float, default=0.0, help="fixed calibrated latency (upstream formal protocol)")
    args = ap.parse_args()

    src = args.libero_src.resolve()
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True).stdout.strip()
    sim = probe_environment(args.libero_python, ("mujoco", "robosuite", "numpy"))["dependencies"]
    command = [
        "bash", "-c",
        f'exec "{args.libero_python}" scripts/eval_dynamic_formal.py --output-dir "$M0_ARTIFACT_DIR/out" '
        f"--levels l1 --traj-indices 0 --limit {args.limit} --num-workers 1 --policy zero "
        f"--latency-source fixed --calibrated-latency-seconds {args.latency_s!r} --keep-static-support-contact "
        f"--static-support-clearance-m 0.001 --max-steps {args.max_steps} --no-save-videos",
    ]
    env = {
        "MUJOCO_GL": "osmesa",
        "PYOPENGL_PLATFORM": "osmesa",
        "PYTHONPATH": str(src),
        "LIBERO_CONFIG_PATH": str(src / "libero" / "libero" / "configs"),
    }
    reg = Registry(args.registry)
    # Re-derive the verdict from an already-recorded run instead of re-running it.
    run = reg.get(args.run_id) if reg.state(args.run_id) else record_run(
        reg,
        run_id=args.run_id,
        track="sim_policy_free",
        purpose="Campaign 0A CPU policy-free smoke of LIBERO-Dynamic formal evaluator (zero policy)",
        command=command,
        config={"limit": args.limit, "max_steps": args.max_steps, "levels": ["l1"], "traj_indices": [0],
                "latency_s": args.latency_s, "env": env, "videos": False,
                "upstream_doc": "docs/formal_dynamic_eval.md#small-smoke-test"},
        simulator={"name": "mujoco+robosuite", "version": f"mujoco {sim['mujoco']}; robosuite {sim['robosuite']}",
                   "render_backend": "osmesa (CPU llvmpipe)"},
        external_sources=[{"url": UPSTREAM, "revision": rev}],
        dependencies=("numpy", "jsonschema", "cryptography"),
        cwd=src,
        env=env,
        timeout_s=args.timeout_s,
        subject_python=args.libero_python,
        subject_dependencies=("mujoco", "robosuite", "numpy", "libero"),
    )
    out = reg.run_dir(args.run_id) / "artifacts" / "out"
    summary_path = next(out.rglob("summary.json"), None) if out.exists() else None
    summary = json.loads(summary_path.read_text()) if summary_path else {}
    items = [json.loads(p.read_text()) for p in sorted(out.glob("*/*/manifests/*.json"))] if out.exists() else []
    verdict = {
        "run_id": args.run_id,
        "state": run["state"],
        "outcome": run["outcome"],
        "manifest_sha256": run["manifest_sha256"],
        "upstream_revision": rev,
        "summary_path": str(summary_path.relative_to(ROOT)) if summary_path else None,
        "acceptance": acceptance(summary, items, args.limit),
    }
    dest = ROOT / "results" / "libero_dynamic" / f"cpu_smoke_{args.run_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(verdict, indent=2) + "\n")
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["acceptance"]["pass"] and run["outcome"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
