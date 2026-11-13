"""Timeline of one episode from its backend-neutral episode log alone (M5.3,
docs/eval_program/designs/M5_backend_neutral_episode_log.md).

Reads `telemetry.jsonl`, `events.jsonl` and `metadata.json` (written by
`benchmarks.episode_log`) and renders force, pose and command over time with
every Haplous evidence event and machine fault window on one time axis: a
Rerun `.rrd` (time series + text markers) and a matplotlib PNG. No simulator,
runtime, controller or benchmark is imported; `FORBIDDEN_MODULES` is checked
at the end of every render and reported.

    python -m evaluation.timeline <log-dir> --rrd out.rrd --png out.png
    python -m evaluation.timeline <run-id> --root <M0 registry root> --rrd ...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from evaluation.run_registry import Registry

TELEMETRY = "telemetry.jsonl"  # same names as benchmarks.episode_log; a test pins them
EVENTS = "events.jsonl"
METADATA = "metadata.json"
FORBIDDEN_MODULES = ("mujoco", "haplous.runtime", "benchmarks.heb", "pegsim.control")
AXES = ("x", "y", "z")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TimelineRefused(RuntimeError):
    """The log cannot be trusted or read as an episode log."""


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load(log_dir: Path) -> dict[str, Any]:
    """Episode log -> series and events, all times in machine seconds."""
    try:
        meta = json.loads((log_dir / METADATA).read_text(encoding="utf-8"))
        rows, events = _jsonl(log_dir / TELEMETRY), _jsonl(log_dir / EVENTS)
    except (OSError, ValueError) as exc:
        raise TimelineRefused(f"episode log unreadable in {log_dir}: {exc}") from exc
    dt = meta["timestep_s"]
    obs = [r for r in rows if r["kind"] == "observation" and r["observation"] is not None]
    cmd = [r for r in rows if r["kind"] == "command"]
    if len(obs) != meta["n_observations"]:
        raise TimelineRefused(f"{len(obs)} observations on disk, metadata records {meta['n_observations']}")
    end_s = meta["n_ticks"] * dt
    windows: list[dict[str, Any]] = []
    for e in events:
        if e["source"] != "machine":
            continue
        if e["kind"].endswith("_window_start"):
            windows.append({"name": e["kind"].removesuffix("_window_start"),
                            "start_s": e["payload"]["start_s"], "end_s": e["payload"]["end_s"]})
        elif e["kind"] == "machine_fault":
            open_ = [w for w in windows if w["name"] == "machine_fault" and w["end_s"] == end_s]
            if e["payload"].get("fault") is not None and not open_:
                windows.append({"name": "machine_fault", "start_s": e["t_s"], "end_s": end_s})
            elif e["payload"].get("fault") is None and open_:
                open_[0]["end_s"] = e["t_s"]
    return {
        "meta": meta,
        "dt": dt,
        "obs_ticks": [r["tick"] for r in obs],
        "force_z_n": [r["observation"]["force_z_n"] for r in obs],
        "pose_m": [r["observation"]["pose_m"] for r in obs],
        "cmd_ticks": [r["tick"] for r in cmd],
        "target_pose_m": [r["command"]["target_pose_m"] for r in cmd],
        "events": events,
        "windows": windows,
    }


def write_rrd(tl: dict[str, Any], path: Path, recording_id: str) -> None:
    import numpy as np
    import rerun as rr

    path.parent.mkdir(parents=True, exist_ok=True)
    stream = rr.RecordingStream("haplous_timeline", recording_id=recording_id)
    stream.save(str(path))
    stream.set_log_time_enabled(False)  # keep host wall time out of the sim timeline
    stream.set_log_tick_enabled(False)
    dt = tl["dt"]

    def series(entity: str, ticks: list[int], values) -> None:
        ticks_a = np.asarray(ticks, dtype=np.int64)
        stream.send_columns(entity, indexes=[rr.TimeColumn("tick", sequence=ticks_a),
                                             rr.TimeColumn("sim_time", duration=ticks_a * dt)],
                            columns=rr.Scalars.columns(scalars=np.asarray(values, dtype=np.float64)))

    series("plots/force_z_n", tl["obs_ticks"], tl["force_z_n"])
    for i, axis in enumerate(AXES):
        series(f"plots/pose_m/{axis}", tl["obs_ticks"], [p[i] for p in tl["pose_m"]])
        series(f"plots/command/target_pose_m/{axis}", tl["cmd_ticks"], [p[i] for p in tl["target_pose_m"]])
    for w in tl["windows"]:
        ticks = [round(w["start_s"] / dt), round(w["end_s"] / dt)]
        series(f"plots/faults/{w['name']}", ticks, [1.0, 0.0])
    for e in tl["events"]:
        stream.set_time("tick", sequence=e["tick"])
        stream.set_time("sim_time", duration=e["t_s"])
        stream.log(f"events/{e['source']}", rr.TextLog(
            f"{e['kind']} {json.dumps(e['payload'], sort_keys=True)}",
            level="WARN" if e["source"] == "machine" else "INFO"))
    stream.flush()
    stream.disconnect()


def write_png(tl: dict[str, Any], path: Path, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dt = tl["dt"]
    t_obs = [k * dt for k in tl["obs_ticks"]]
    t_cmd = [k * dt for k in tl["cmd_ticks"]]
    fig, axes = plt.subplots(3, 1, sharex=True, figsize=(11, 8), constrained_layout=True)
    axes[0].plot(t_obs, tl["force_z_n"], lw=1)
    axes[0].set_ylabel("force_z [N]")
    for i, axis in enumerate(AXES):
        axes[1].plot(t_obs, [p[i] for p in tl["pose_m"]], lw=1, label=axis)
        axes[2].plot(t_cmd, [p[i] for p in tl["target_pose_m"]], lw=1, label=axis)
    axes[1].set_ylabel("pose [m]")
    axes[2].set_ylabel("command target [m]")
    axes[2].set_xlabel("machine time [s]")
    for ax in axes[1:]:
        ax.legend(loc="upper right", fontsize=7)
    for ax in axes:
        for w in tl["windows"]:
            ax.axvspan(w["start_s"], w["end_s"], color="tab:red", alpha=0.15)
        for e in tl["events"]:
            if e["source"] == "haplous":
                ax.axvline(e["t_s"], color="tab:purple", lw=0.6, ls="--")
    for n, e in enumerate(x for x in tl["events"] if x["source"] == "haplous"):
        axes[0].annotate(e["kind"], (e["t_s"], 1.0), xycoords=("data", "axes fraction"),
                         xytext=(2, -10 - 9 * (n % 6)), textcoords="offset points", fontsize=6, color="tab:purple")
    for w in tl["windows"]:
        axes[0].annotate(f"{w['name']} window", (w["start_s"], 0.0), xycoords=("data", "axes fraction"),
                         xytext=(2, 3), textcoords="offset points", fontsize=7, color="tab:red")
    fig.suptitle(title)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def render(log_dir: Path, name: str, *, rrd_path: Path | None, png_path: Path | None) -> dict[str, Any]:
    tl = load(log_dir)
    outputs: dict[str, Any] = {}
    if rrd_path is not None:
        write_rrd(tl, rrd_path, name)
        outputs["rrd"] = {"path": str(rrd_path), "sha256": sha256_file(rrd_path)}
    if png_path is not None:
        write_png(tl, png_path, f"{name} ({tl['meta']['system']}, {tl['meta']['outcome'].get('disposition')})")
        outputs["png"] = {"path": str(png_path), "sha256": sha256_file(png_path)}
    return {
        "source": name,
        "log_dir": str(log_dir),
        "n_ticks": tl["meta"]["n_ticks"],
        "timestep_s": tl["dt"],
        "series": {"force_z_n": len(tl["force_z_n"]), "pose_m": len(tl["pose_m"]),
                   "command.target_pose_m": len(tl["target_pose_m"])},
        "events": [{k: e[k] for k in ("source", "kind", "tick", "t_s")} for e in tl["events"]],
        "windows": tl["windows"],
        "derived": outputs,
        "forbidden_modules_loaded": sorted(
            m for m in sys.modules if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_MODULES)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.timeline")
    parser.add_argument("source", help="episode-log directory, or an M0 run id with --root")
    parser.add_argument("--root", type=Path, help="M0 registry root; source is then a run id")
    parser.add_argument("--rrd", type=Path)
    parser.add_argument("--png", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.root is not None:
            problems = Registry(args.root).check(args.source)
            if problems:
                raise TimelineRefused(f"M0 integrity check failed for {args.source}: {problems}")
            log_dir, name = Registry(args.root).run_dir(args.source) / "artifacts", args.source
        else:
            log_dir, name = Path(args.source), Path(args.source).resolve().name
        report = render(log_dir, name, rrd_path=args.rrd, png_path=args.png)
    except TimelineRefused as exc:
        print(f"timeline refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not report["forbidden_modules_loaded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
