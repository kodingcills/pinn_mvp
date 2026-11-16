"""Studio jobs: allow-listed recordings of catalog scenarios x registered systems.

The server writes the job record once (status `queued`) and starts one worker
process; from then on the worker is the record's only writer. Each episode is
an M0 run in Studio's registry, recorded the way `scripts/record_episode.py`
(haplous_mediated) and `scripts/m4_campaign.py record-one` (other arms) do.

    python -m studio.jobs run <job.json>                     # worker (started by the server)
    python -m studio.jobs record --root R --run-id X --scenario S --system Y
    python -m studio.jobs record-one --scenario S --system Y  # inside M0, writes $M0_ARTIFACT_DIR
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import secrets
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.run_registry import REPO_ROOT, Registry, record_run

# Measured once (2026-11-16, tests/test_studio.py job): one insert_nominal x haplous_mediated
# episode (3249 ticks) cost 2.99 s user+sys CPU in the recording child; the worker's own
# start-up is not counted. Other arms can cost more (results/m4/campaign/cost.json).
CPU_S_PER_EPISODE = 3.0
CPU_CAP_S = 600.0
LOG_TAIL_LINES = 40


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_record(path: Path, record: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def estimate_cpu_s(n_episodes: int) -> float:
    return n_episodes * CPU_S_PER_EPISODE


def new_job(jobs_dir: Path, registry: Path, scenario_ids: list[str], systems: list[str]) -> dict[str, Any]:
    job_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
    episodes = [{"scenario_id": s, "system": y, "run_id": f"studio-{job_id}-{s}-{y}", "state": None,
                 "outcome": None, "cpu_s": None} for s in scenario_ids for y in systems]
    record = {"job_id": job_id, "created_at": _now(), "status": "queued", "scenario_ids": scenario_ids,
              "systems": systems, "registry": str(registry), "estimate_cpu_s": estimate_cpu_s(len(episodes)),
              "episodes": episodes, "pid": None, "error": None, "finished_at": None}
    jobs_dir.mkdir(parents=True, exist_ok=True)
    write_record(jobs_dir / f"{job_id}.json", record)
    return record


def start_worker(jobs_dir: Path, job_id: str) -> subprocess.Popen:
    log = open(jobs_dir / f"{job_id}.log", "ab")  # noqa: SIM115 - handed to the child
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "MUJOCO_GL": os.environ.get("MUJOCO_GL", "osmesa")}
    proc = subprocess.Popen([sys.executable, "-m", "studio.jobs", "run", str(jobs_dir / f"{job_id}.json")],
                            cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    log.close()
    return proc


def _alive(record: dict[str, Any], proc: subprocess.Popen | None) -> bool:
    if proc is not None:
        return proc.poll() is None
    # shortcut: a worker started by an earlier server process is checked by pid only (pid reuse not handled).
    return record["pid"] is not None and Path(f"/proc/{record['pid']}").exists()


def view(jobs_dir: Path, job_id: str, workers: Mapping[str, subprocess.Popen]) -> dict[str, Any] | None:
    """The record as the API shows it: an unfinished record whose worker is gone reads as failed."""
    path = jobs_dir / f"{job_id}.json"
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record["status"] in ("queued", "running") and not _alive(record, workers.get(job_id)):
        record = {**record, "status": "failed", "error": "worker exited without finishing the job record"}
    done = [e for e in record["episodes"] if e["state"] is not None]
    log = jobs_dir / f"{job_id}.log"
    tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL_LINES:] if log.is_file() else []
    return {**record, "progress": {"done": len(done), "total": len(record["episodes"])},
            "run_ids": [e["run_id"] for e in done], "log_tail": "\n".join(tail)}


def list_ids(jobs_dir: Path) -> list[str]:
    return sorted((p.stem for p in jobs_dir.glob("*.json")), reverse=True)


# ---------------------------------------------------------------- worker side


def record(root: Path, run_id: str, scenario: str, system: str, purpose: str, extra: dict[str, Any]) -> dict:
    import mujoco

    command = [sys.executable, "-m", "studio.jobs", "record-one", "--scenario", scenario, "--system", system]
    return record_run(
        Registry(root), run_id=run_id, track="sim_policy_free",
        purpose=purpose, command=command,
        config={"scenario": scenario, "system": system, **extra},
        simulator={"name": "mujoco", "version": mujoco.__version__},
        dependencies=("numpy", "mujoco", "pyarrow", "jsonschema", "cryptography"),
        cwd=REPO_ROOT, env={"PYTHONPATH": str(REPO_ROOT), "MUJOCO_GL": os.environ.get("MUJOCO_GL", "osmesa")},
    )


def _children_cpu_s() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def run_job(path: Path) -> int:
    job = json.loads(path.read_text(encoding="utf-8"))
    job.update(status="running", pid=os.getpid())
    write_record(path, job)
    try:
        for episode in job["episodes"]:
            before = _children_cpu_s()
            result = record(Path(job["registry"]), episode["run_id"], episode["scenario_id"], episode["system"],
                            f"Studio job {job['job_id']}: HEB {episode['scenario_id']} ({episode['system']})",
                            {"studio_job": job["job_id"]})
            episode.update(state=result["state"], outcome=result["outcome"], cpu_s=_children_cpu_s() - before)
            print(json.dumps({k: episode[k] for k in ("run_id", "state", "outcome", "cpu_s")}), flush=True)
            write_record(path, job)
        ok = all(e["state"] == "VERIFIED" and e["outcome"] == "completed" for e in job["episodes"])
        job.update(status="succeeded" if ok else "failed", error=None if ok else "an episode did not verify")
    except Exception as exc:  # the record must end in a terminal state whatever went wrong
        job.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        job["finished_at"] = _now()
        write_record(path, job)
    return 0 if job["status"] == "succeeded" else 1


def record_one(scenario_id: str, system: str, out: Path) -> dict[str, Any]:
    """One episode into `out`, as `scripts/m4_campaign.py record-one` records an arm."""
    from benchmarks.heb import mujoco_backend
    from benchmarks.heb.metrics import grade_run
    from benchmarks.heb.systems import system_by_name
    from benchmarks.recording import EpisodeRecorder, run_episode, scenario_by_id

    scenario = scenario_by_id(scenario_id)
    if system == "haplous_mediated":
        run, _, metadata = run_episode(scenario, out)
    else:
        recorder = EpisodeRecorder(out)
        realization = mujoco_backend.build_realization(scenario, machine_factory=recorder.machine_factory)
        run = system_by_name(system).run(realization, scenario)
        metadata = recorder.finish(scenario, run, None, grade_run(scenario, realization.bundle, run))
    return {"scenario_id": scenario.id, "system": system, "disposition": run.disposition,
            "outcome": metadata["outcome"], "n_ticks": metadata["n_ticks"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m studio.jobs")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("job", type=Path)
    rec = sub.add_parser("record", help="record one episode as a VERIFIED M0 run")
    rec.add_argument("--root", required=True, type=Path)
    rec.add_argument("--run-id", required=True)
    one = sub.add_parser("record-one", help="one episode into $M0_ARTIFACT_DIR")
    for p in (rec, one):
        p.add_argument("--scenario", required=True)
        p.add_argument("--system", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run_job(args.job)
    if args.cmd == "record":
        result = record(args.root, args.run_id, args.scenario, args.system,
                        f"Recording of HEB scenario {args.scenario} ({args.system})", {})
        print(json.dumps({k: result[k] for k in ("run_id", "state", "outcome", "manifest_sha256")}, indent=2))
        return 0 if result["state"] == "VERIFIED" and result["outcome"] == "completed" else 1
    print(json.dumps(record_one(args.scenario, args.system, Path(os.environ["M0_ARTIFACT_DIR"])), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
