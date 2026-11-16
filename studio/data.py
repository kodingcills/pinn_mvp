"""Read side of Studio: every value comes from a committed or M0-verified artifact.
Nothing here imports FastAPI; app.py is the HTTP shell over these functions."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluation import timeline
from evaluation.run_registry import REPO_ROOT, Registry, _state_in

REPLAY_FORMAT = "haplous-mujoco-recording-v1"  # evaluation.replay.FORMAT; replay imports mujoco, so not imported here
SYSTEMS = ("haplous_mediated", "g1_incumbent", "bare_behavior", "competent_supervisor", "direct_minimal")
BENCHMARKS = [
    {"id": "heb", "name": "HEB (pegsim MuJoCo)", "executed": True, "source": "results/recordings/registry"},
    {"id": "m4-campaign", "name": "M4 confirmatory campaign (control + stress)", "executed": True,
     "source": "results/m4/campaign/official_metrics.json"},
    {"id": "robosuite", "name": "robosuite 1.5.2 Lift (episode log)", "executed": True,
     "source": "results/m5/robosuite-log"},
    {"id": "reassemble", "name": "REASSEMBLE real-robot log import", "executed": True,
     "source": "results/m5/reassemble-log"},
    {"id": "libero-dynamic", "name": "LIBERO-Dynamic / VLA", "executed": False,
     "reason": "needs a GPU and operator approval", "source": "docs/eval_program/designs/M6_haplous_studio.md"},
]


class NotFound(LookupError):
    pass


class Unreadable(RuntimeError):
    """The artifact exists but cannot be served as trusted data (integrity, format)."""


@dataclass(frozen=True)
class Paths:
    repo: Path
    studio: Path
    investigations: Path
    replays: Path

    @classmethod
    def default(cls, repo: Path = REPO_ROOT) -> Paths:
        return cls(repo, repo / "results" / "studio", repo / "docs" / "eval_program" / "investigations",
                   repo / "replays")

    @classmethod
    def under(cls, root: Path, repo: Path = REPO_ROOT) -> Paths:
        """Read committed results from `repo`; write jobs, registry, replays and investigations under `root`."""
        root = root.resolve()
        return cls(repo, root / "studio", root / "investigations", root / "replays")

    @property
    def registry(self) -> Path:
        return self.studio / "registry"

    @property
    def jobs(self) -> Path:
        return self.studio / "jobs"

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repo))
        except ValueError:
            return str(path)

    def m0_roots(self) -> list[tuple[str, Path]]:
        roots = [("recordings", self.repo / "results" / "recordings" / "registry")]
        roots += [(f"m4-{p.parent.name}", p) for p in sorted(self.repo.glob("results/m4/recordings_store/*/m0"))]
        return [*roots, ("studio", self.registry)]

    def episode_logs(self) -> list[Path]:
        return sorted(p for p in self.repo.glob("results/m5/*-log") if (p / timeline.METADATA).is_file())


@dataclass(frozen=True)
class RunRef:
    id: str
    source: str
    log_dir: Path
    root: Path | None = None  # M0 registry root; None for an episode log outside M0
    state: str | None = None  # M0 ledger state at index time


def run_index(paths: Paths) -> dict[str, RunRef]:
    index: dict[str, RunRef] = {}
    for source, root in paths.m0_roots():
        if not (root / "ledger.jsonl").is_file():
            continue
        history = Registry(root).events()
        for run_id in dict.fromkeys(e["run_id"] for e in history if e["run_id"]):
            index.setdefault(run_id, RunRef(run_id, source, root / "runs" / run_id / "artifacts", root,
                                            _state_in(history, run_id)))
    for log_dir in paths.episode_logs():
        rid = paths.rel(log_dir)
        index.setdefault(rid, RunRef(rid, "episode_log", log_dir))
    return index


def get_ref(paths: Paths, run_id: str) -> RunRef:
    ref = run_index(paths).get(run_id)
    if ref is None:
        raise NotFound(f"unknown run {run_id!r}")
    return ref


def _metadata(ref: RunRef) -> dict[str, Any]:
    try:
        return json.loads((ref.log_dir / timeline.METADATA).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def has_3d(ref: RunRef, meta: dict[str, Any]) -> bool:
    return ref.root is not None and meta.get("format") == REPLAY_FORMAT and (ref.log_dir / "state.arrow").is_file()


def run_summary(paths: Paths, ref: RunRef) -> dict[str, Any]:
    meta = _metadata(ref)
    outcome = meta.get("outcome") or {}
    backend = meta.get("backend") or (f"mujoco-{meta['mujoco_version']}" if "mujoco_version" in meta else None)
    return {
        "id": ref.id,
        "source": ref.source,
        "backend": backend,
        "scenario_id": meta.get("scenario_id") or meta.get("task_id"),
        "system": meta.get("system"),
        "disposition": outcome.get("disposition"),
        "bounded_success": outcome.get("bounded_success"),
        "has_3d": has_3d(ref, meta),
        # Ledger state; the detail endpoint re-hashes every artifact before serving.
        "verified": ref.state == "VERIFIED",
        "path": paths.rel(ref.log_dir.parent if ref.root is not None else ref.log_dir),
    }


def list_runs(paths: Paths) -> list[dict[str, Any]]:
    return [run_summary(paths, ref) for ref in run_index(paths).values()]


def m0_record(ref: RunRef) -> dict[str, Any] | None:
    if ref.root is None:
        return None
    registry = Registry(ref.root)
    problems = registry.check(ref.id)
    manifest = registry.manifest(ref.id)
    provenance = manifest.get("provenance") or manifest.get("environment") or {}
    return {"registry": str(ref.root), "run_id": ref.id, "state": registry.state(ref.id), "problems": problems,
            "manifest_sha256": registry.manifest_digest(ref.id), "command": manifest.get("command"),
            "config": manifest.get("config"), "git_commit": provenance.get("git_commit"),
            "git_dirty": provenance.get("git_dirty")}


def series(log_dir: Path, dt: float) -> dict[str, list[Any]]:
    """One entry per observation row, null observations kept as nulls. The command
    target is the last command logged before the row (the target in force then)."""
    out: dict[str, list[Any]] = {"t_s": [], "force_z_n": [], "pose_m": [], "command_target_pose_m": []}
    target = None
    for line in (log_dir / timeline.TELEMETRY).read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        if row["kind"] == "command":
            target = row["command"]["target_pose_m"]
        elif row["kind"] == "observation":
            obs = row["observation"]
            out["t_s"].append(row["tick"] * dt)
            out["force_z_n"].append(None if obs is None else obs.get("force_z_n"))
            out["pose_m"].append(None if obs is None else obs.get("pose_m"))
            out["command_target_pose_m"].append(target)
    return out


def run_detail(paths: Paths, run_id: str) -> dict[str, Any]:
    ref = get_ref(paths, run_id)
    m0 = m0_record(ref)
    if m0 is not None and m0["problems"]:
        raise Unreadable(f"M0 integrity check failed for {run_id}: {m0['problems']}")
    try:
        tl = timeline.load(ref.log_dir)
    except timeline.TimelineRefused as exc:
        raise Unreadable(str(exc)) from exc
    summary = run_summary(paths, ref)
    return {
        "metadata": tl["meta"],
        "has_3d": summary["has_3d"],
        "run": summary,
        "m0": m0,
        "series": series(ref.log_dir, tl["dt"]),
        "events": [{k: e.get(k) for k in ("source", "kind", "tick", "t_s", "payload")} for e in tl["events"]],
        "windows": tl["windows"],
    }


_rrd_lock = threading.Lock()


def ensure_rrd(paths: Paths, run_id: str) -> Path:
    """The replay .rrd, derived once by `evaluation.replay` in a fresh process (as
    `haplous replay` does) and cached under replays/<run_id>/."""
    ref = get_ref(paths, run_id)
    if not has_3d(ref, _metadata(ref)):
        raise NotFound(f"run {run_id!r} has no MuJoCo state stream (no 3D)")
    out = paths.replays / run_id / f"{run_id}.rrd"
    with _rrd_lock:
        if not out.is_file():
            env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "MUJOCO_GL": os.environ.get("MUJOCO_GL", "osmesa")}
            proc = subprocess.run(
                [sys.executable, "-m", "evaluation.replay", run_id, "--root", str(ref.root), "--rrd", str(out),
                 "--report", str(out.parent / "replay_report.json")],
                cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=600)
            if proc.returncode != 0:
                out.unlink(missing_ok=True)
                raise Unreadable(f"replay failed ({proc.returncode}): {proc.stderr.strip()[-500:]}")
    return out


# ---------------------------------------------------------------- campaigns


def campaign_dirs(paths: Paths) -> dict[str, Path]:
    return {f"{p.parent.parent.name}-{p.parent.name}": p.parent
            for p in sorted(paths.repo.glob("results/*/*/official_metrics.json"))}


def _official(paths: Paths, campaign_id: str) -> tuple[Path, dict[str, Any]]:
    directory = campaign_dirs(paths).get(campaign_id)
    if directory is None:
        raise NotFound(f"unknown campaign {campaign_id!r}")
    return directory, json.loads((directory / "official_metrics.json").read_text(encoding="utf-8"))


def list_campaigns(paths: Paths) -> list[dict[str, Any]]:
    out = []
    for cid in campaign_dirs(paths):
        directory, official = _official(paths, cid)
        out.append({
            "id": cid,
            "source": paths.rel(directory / "official_metrics.json"),
            "phase": official.get("phase"),
            "label": "confirmatory" if official.get("phase") == "campaign" else "development",
            "scoring_epoch": official.get("scoring_epoch"),
            "config_sha256": official.get("config_sha256"),
            "launchable": False,
            "populations": [
                {"id": name, "n_scenarios": pop["n_scenarios"], "arms": sorted(pop["arms"]),
                 "confirmatory": [{"id": cid_, "candidate": c["candidate"], "reference": c["reference"],
                                   "decision": c["decision"], "field": c.get("field"), "delta": c.get("delta")}
                                  for cid_, c in (pop.get("confirmatory") or {}).items()],
                 "hes_qualified": (pop.get("hes_hard_gates") or {}).get("qualified"),
                 "pairs": sorted(pop.get("paired", {}))}
                for name, pop in official["populations"].items()],
        })
    return out


def _population_rows(directory: Path, population: str) -> list[dict[str, Any]]:
    """Raw rows whose HES population id is `<prefix>_<population>@<version>`."""
    rows = [json.loads(line) for line in (directory / "raw_rows.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if r["metadata"]["population_id"].split("@")[0].endswith(f"_{population}")]


def campaign_pairs(paths: Paths, campaign_id: str, a: str, b: str, population: str | None) -> dict[str, Any]:
    """Paired outcome matrix, n, diff and CI exactly as in official_metrics.json. The
    reverse orientation swaps rescued/harmed and negates the interval; nothing is resampled."""
    directory, official = _official(paths, campaign_id)
    pops = official["populations"]
    population = population or next(iter(pops))
    if population not in pops:
        raise NotFound(f"unknown population {population!r}; available {sorted(pops)}")
    paired = pops[population]["paired"]
    if f"{a}_vs_{b}" in paired:
        pair, flip = paired[f"{a}_vs_{b}"], False
    elif f"{b}_vs_{a}" in paired:
        pair, flip = paired[f"{b}_vs_{a}"], True
    else:
        raise NotFound(f"no committed pair {a} vs {b} in {campaign_id}/{population}; available {sorted(paired)}")
    delta = pair["delta_bounded_success"]
    a_only, b_only = len(pair["rescued"]), len(pair["harmed"])
    diff, ci = delta["delta"], [delta["ci_low"], delta["ci_high"]]
    if flip:
        a_only, b_only, diff, ci = b_only, a_only, -diff, [-ci[1], -ci[0]]
    strata = set(pops[population].get("strata", {}))
    replays: dict[str, dict[str, str]] = {}
    index_path = directory / "replay_index.jsonl"
    if index_path.is_file():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if line:
                entry = json.loads(line)
                if entry["population"] == population:
                    replays.setdefault(entry["scenario_id"], {})[entry["arm"]] = entry["m0_run_id"]
    by_arm: dict[str, dict[str, dict[str, Any]]] = {}
    for row in _population_rows(directory, population):
        by_arm.setdefault(row["arm"], {})[row["scenario_id"]] = row
    rows = []
    for sid in sorted(by_arm.get(a, {})):
        ra, rb = by_arm[a][sid], by_arm.get(b, {}).get(sid)
        rec = replays.get(sid, {})
        rows.append({
            "scenario_id": sid,
            "a": bool(ra.get("bounded_success")),
            "b": None if rb is None else bool(rb.get("bounded_success")),
            "stratum": next((t for t in ra.get("tags") or () if t in strata), None),
            "replay_run_id": rec.get(a) or rec.get(b),
        })
    return {
        "campaign_id": campaign_id, "population": population, "a": a, "b": b, "field": delta["field"],
        "matrix": {"both": pair["both"], "a_only": a_only, "b_only": b_only, "neither": pair["neither"]},
        "n": delta["n_pairs"], "diff": diff, "ci95": ci, "method": delta["method"],
        "source": paths.rel(directory / "official_metrics.json"),
        "rows": rows,
    }


# ---------------------------------------------------------------- catalog, evidence

_catalog: dict[str, Any] | None = None


def scenario_catalog() -> dict[str, Any]:
    """HEB registered scenarios (imports the benchmark once, lazily)."""
    global _catalog
    if _catalog is None:
        from benchmarks.heb import tasks

        _catalog = {sid: {"task": s.task_family, "id": sid, "stratum": s.tags[1] if len(s.tags) > 1 else None,
                          "label": "DEV"}
                    for sid, s in tasks.scenarios_by_id().items()}
    return _catalog


def _markdown_tables(text: str) -> list[dict[str, Any]]:
    out, section, header = [], None, None
    for line in text.splitlines():
        if line.startswith("#"):
            section, header = line.lstrip("#").strip(), None
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = cells
            elif not all(re.fullmatch(r":?-+:?", c) for c in cells):
                out.append({"section": section, **dict(zip(header, cells, strict=False))})
        else:
            header = None
    return out


def _issue(row: dict[str, Any]) -> dict[str, Any]:
    """A KNOWN_ISSUES.md table row as {id, title, status}, the raw columns kept alongside."""
    if "Test" in row:
        return {"id": row["Test"].strip("`"), "title": row.get(next(k for k in row if k.startswith("Cause")), ""),
                "status": "known failure", **row}
    title = row.get("Finding", "")
    words = re.sub(r"[^a-z0-9 ]+", " ", title.lower()).split()[:8]
    return {"id": "-".join(words) or "finding", "title": title, "status": row.get("Status", ""), **row}


def evidence(paths: Paths) -> dict[str, Any]:
    program = paths.repo / "docs" / "eval_program"
    tasks_doc = json.loads((program / "TASKS.json").read_text(encoding="utf-8"))
    issues = program / "KNOWN_ISSUES.md"
    return {
        "integration_branch": tasks_doc.get("integration_branch"),
        "tasks": [{**{k: t.get(k) for k in ("id", "title", "owner", "status", "verdict", "commit", "deps",
                                            "exit_check", "note", "findings")}, "proof": t.get("proof") or []}
                  for t in tasks_doc["tasks"]],
        "known_issues": [_issue(r) for r in _markdown_tables(issues.read_text(encoding="utf-8"))],
        "benchmarks": BENCHMARKS,
        "source": f"{paths.rel(program / 'TASKS.json')}, {paths.rel(issues)}",
    }
