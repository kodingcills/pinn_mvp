"""Engineering investigation files: a new markdown file a person reviews and commits.
Studio never edits program state files; this writes only under `paths.investigations`."""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from studio.data import Paths, run_detail

EVENT_WINDOW_S = 0.5  # events within +/- this many sim seconds of t_s are listed


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "investigation"


def _head(paths: Paths) -> str | None:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.repo, capture_output=True, text=True)
    return proc.stdout.strip() or None


def _python_cmd(command: list[str]) -> str:
    """The M0-recorded command with the recording interpreter written repo-relative."""
    return " ".join([".venv/bin/python", *command[1:]] if command and "python" in Path(command[0]).name else command)


def _reproduction(paths: Paths, detail: dict[str, Any]) -> list[str]:
    m0 = detail["m0"]
    if m0 is None:
        return [f"# {detail['run']['id']} is an episode log outside M0: no recorded command exists.",
                f"PYTHONPATH=. .venv/bin/python -m haplous.cli timeline {detail['run']['id']}"]
    root = paths.rel(Path(m0["registry"]))
    meta = detail["metadata"]
    lines = [f"git checkout {m0['git_commit']}" + ("  # recorded from a dirty tree" if m0["git_dirty"] else ""),
             "# the exact command M0 recorded (it writes into $M0_ARTIFACT_DIR):",
             f"M0_ARTIFACT_DIR=$(mktemp -d) MUJOCO_GL=osmesa PYTHONPATH=. {_python_cmd(m0['command'])}"]
    if meta.get("scenario_id") and meta.get("system"):
        lines += ["# or, on a commit with Studio, as a new VERIFIED M0 run:",
                  f"MUJOCO_GL=osmesa PYTHONPATH=. .venv/bin/python -m studio.jobs record --root /tmp/repro-m0 "
                  f"--run-id {m0['run_id']}-repro --scenario {meta['scenario_id']} --system {meta['system']}"]
    lines += ["# replay the original from its archive:",
              f"PYTHONPATH=. .venv/bin/python -m evaluation.replay {m0['run_id']} --root {root} "
              f"--rrd replays/{m0['run_id']}/{m0['run_id']}.rrd"]
    return lines


def _near(detail: dict[str, Any], t_s: float) -> tuple[list[dict], list[dict]]:
    lo, hi = t_s - EVENT_WINDOW_S, t_s + EVENT_WINDOW_S
    events = [e for e in detail["events"] if lo <= e["t_s"] <= hi]
    windows = [w for w in detail["windows"] if w["start_s"] <= hi and w["end_s"] >= lo]
    return events, windows


def _run_section(label: str, detail: dict[str, Any], t_s: float) -> list[str]:
    run, meta, m0 = detail["run"], detail["metadata"], detail["m0"]
    outcome = meta.get("outcome") or {}
    events, windows = _near(detail, t_s)
    lines = [f"## {label}: `{run['id']}`", "",
             f"- Scenario: `{run['scenario_id']}`",
             f"- System: `{run['system']}`",
             f"- Backend: {run['backend']}",
             f"- Outcome: disposition `{outcome.get('disposition')}`, bounded_success `{outcome.get('bounded_success')}`,"
             f" task_success `{outcome.get('task_success')}`, reason `{outcome.get('reason')}`",
             f"- Hard violations: `{json.dumps(outcome.get('hard_violations', []), sort_keys=True)}`",
             f"- Provenance: source `{run['source']}`, path `{run['path']}`"]
    if m0 is not None:
        lines += [f"- M0: state `{m0['state']}`, manifest sha256 `{m0['manifest_sha256']}`,"
                  f" recorded at commit `{m0['git_commit']}` (dirty: {m0['git_dirty']})"]
    lines += ["", f"### Failure window (overlapping t = {t_s:.4f} s +/- {EVENT_WINDOW_S} s)", ""]
    lines += [f"- `{w['name']}`: {w['start_s']:.4f} s to {w['end_s']:.4f} s" for w in windows] or ["- none recorded"]
    lines += ["", f"### Events within +/- {EVENT_WINDOW_S} s of t = {t_s:.4f} s", "",
              "| t_s | tick | source | kind | payload |", "|---|---|---|---|---|"]
    lines += [f"| {e['t_s']:.4f} | {e['tick']} | {e['source']} | {e['kind']} | "
              f"`{json.dumps(e['payload'], sort_keys=True)[:300]}` |" for e in events] or ["| | | | none | |"]
    return [*lines, ""]


def write(paths: Paths, *, run_id: str, t_s: float, title: str, note: str,
          compare_run_id: str | None = None, today: datetime | None = None) -> Path:
    detail = run_detail(paths, run_id)
    compare = run_detail(paths, compare_run_id) if compare_run_id else None
    when = today or datetime.now(UTC)
    systems = [detail["run"]["system"]] + ([compare["run"]["system"]] if compare else [])
    lines = [f"# Investigation: {title}", "",
             f"Created {when.strftime('%Y-%m-%d %H:%M UTC')} by Haplous Studio at commit `{_head(paths)}`.",
             "Status: open. Not program state: a person reviews and commits this file, then a Claude session",
             "works it through the owner -> verifier loop.", "",
             f"- Scenario: `{detail['run']['scenario_id']}`",
             f"- Systems: {', '.join(f'`{s}`' for s in systems)}",
             f"- Focus time: t = {t_s:.4f} s (sim seconds)", "", "## Note", "", note.strip() or "(none)", ""]
    lines += _run_section("Run", detail, t_s)
    if compare is not None:
        lines += _run_section("Compared run", compare, t_s)
    lines += ["## Reproduction", "", "```bash", *_reproduction(paths, detail), "```", ""]
    paths.investigations.mkdir(parents=True, exist_ok=True)
    base = f"{when.strftime('%Y-%m-%d')}-{_slug(title)}"
    for n in range(1, 1000):
        path = paths.investigations / (f"{base}.md" if n == 1 else f"{base}-{n}.md")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return path
    raise RuntimeError(f"too many investigations named {base}")
