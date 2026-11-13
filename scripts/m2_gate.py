"""Run the M2 gate (docs/eval_program/PROGRAM_STATE.md) end to end and print
one JSON verdict per item.

    .venv/bin/python scripts/m2_gate.py                       # fresh temp workdir
    .venv/bin/python scripts/m2_gate.py --workdir W --keep    # keep outputs
    .venv/bin/python scripts/m2_gate.py --root results/recordings/registry \
        --run-id m2-heb-insert_dropout_large --existing      # gate an existing recording
    .venv/bin/python scripts/m2_gate.py --root results/recordings/registry \
        --run-id m3-heb-slide_disturbance_recoverable_ceiling --existing \
        --rss-scenarios slide_machine_fault slide_stale_mid_slide --tests tests/test_slide_recording.py

Every step shells out to the committed command a user would run, so the
recording process exits before replay starts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import mujoco
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from evaluation import replay as rp  # noqa: E402
from evaluation.run_registry import Registry  # noqa: E402

PY = sys.executable
RRD_POSE_TOLERANCE_M = 1e-6  # rerun stores Transform3D translations as float32
RSS_GROWTH_BUDGET_KB = 2048  # allocator slack on top of the fixed chunk buffers
RSS_HORIZONS = ("insert_machine_fault", "insert_nominal:0.0035")  # short, long; name[:offset_m]


def sh(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)


def gate5_event_sync(rrd: Path, root: Path, run_id: str) -> dict:
    import rerun.chunk as rc

    rec = rp.open_recording(root, run_id)
    events = [json.loads(line) for line in (rec.artifacts / rp.EVENTS).read_text().splitlines()]
    seek = [e for e in events if e["tick"] > 0]
    texts: dict[int, list[str]] = {}
    peg: dict[int, np.ndarray] = {}
    for chunk in rc.RrdReader(str(rrd)).stream():
        batch = chunk.to_record_batch()
        if chunk.entity_path.startswith("/events/"):
            for tick, text in zip(batch.column("tick").to_pylist(), batch.column("TextLog:text").to_pylist(), strict=True):
                texts.setdefault(tick, []).extend(text)
        elif chunk.entity_path == "/world/peg":
            for tick, tr in zip(batch.column("tick").to_pylist(),
                                batch.column("Transform3D:translation").to_pylist(), strict=True):
                peg[tick] = np.asarray(tr[0])
    model, data = rec.model, mujoco.MjData(rec.model)
    body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "peg")
    want = {e["tick"] for e in seek}
    archived: dict[int, np.ndarray] = {}
    for tick, _, state in rp.iter_states(rec):
        if tick in want:
            mujoco.mj_setState(model, data, state, rp.STATE_SPEC)
            mujoco.mj_forward(model, data)
            archived[tick] = data.xpos[body].copy()
    rows = []
    for e in seek:
        marker = any(t.startswith(e["kind"] + " ") for t in texts.get(e["tick"], []))
        err = float(np.max(np.abs(peg[e["tick"]] - archived[e["tick"]]))) if e["tick"] in peg else None
        rows.append({"tick": e["tick"], "source": e["source"], "kind": e["kind"], "marker": marker, "pose_err_m": err})
    ok = bool(rows) and all(r["marker"] and r["pose_err_m"] is not None and r["pose_err_m"] <= RRD_POSE_TOLERANCE_M
                            for r in rows)
    return {"pass": ok, "events_seeked": rows, "tolerance_m": RRD_POSE_TOLERANCE_M}


def gate6_video(mp4: Path, report: dict) -> dict:
    probe = sh("ffprobe", "-v", "error", "-show_entries", "format=duration:stream=nb_frames,r_frame_rate",
               "-of", "json", str(mp4))
    info = json.loads(probe.stdout)
    duration = float(info["format"]["duration"])
    fps = report["derived"]["mp4"]["fps"]
    md5 = [line.rsplit(",", 1)[-1].strip() for line in sh("ffmpeg", "-v", "error", "-i", str(mp4), "-f", "framemd5", "-").stdout.splitlines()
           if line and not line.startswith("#")]
    sim = report["sim_duration_s"]
    return {"pass": abs(duration - sim) <= 1.0 / fps and len(set(md5)) > 1 and len(md5) == report["derived"]["mp4"]["frames"],
            "ffprobe_duration_s": duration, "sim_duration_s": sim, "fps": fps,
            "frames": len(md5), "distinct_frames": len(set(md5))}


def gate9_rss(workdir: Path, horizons: tuple[str, str]) -> dict:
    rows = []
    for spec in horizons:
        scenario, _, offset = spec.partition(":")
        offset = offset or None
        row = {"scenario": scenario, "offset_m": offset}
        for recorded in (True, False):
            out = workdir / f"rss-{scenario}"
            shutil.rmtree(out, ignore_errors=True)
            cmd = [PY, "-m", "benchmarks.recording", "--scenario", scenario, "--out", str(out)]
            cmd += ["--offset-m", offset] if offset is not None else []
            cmd += [] if recorded else ["--no-record"]
            res = json.loads(sh(*cmd).stdout)
            row["sim_s"] = res["sim_s"]
            row["peak_rss_kb_recorded" if recorded else "peak_rss_kb_plain"] = res["peak_rss_kb"]
            if recorded:
                row["artifact_bytes"] = res["artifact_bytes"]
                row["bytes_per_sim_s"] = round(res["artifact_bytes"] / res["sim_s"])
        row["recorder_overhead_kb"] = row["peak_rss_kb_recorded"] - row["peak_rss_kb_plain"]
        rows.append(row)
    growth = rows[1]["recorder_overhead_kb"] - rows[0]["recorder_overhead_kb"]
    return {"pass": growth <= RSS_GROWTH_BUDGET_KB, "horizons": rows,
            "overhead_growth_kb": growth, "budget_kb": RSS_GROWTH_BUDGET_KB}


def run_gates(workdir: Path, root: Path, run_id: str, existing: bool, scenario: str,
              rss_horizons: tuple[str, str] = RSS_HORIZONS, tests: str = "tests/test_recording_replay.py") -> dict:
    g: dict[str, dict] = {}
    if existing:
        g["1_record"] = {"pass": Registry(root).state(run_id) is not None, "detail": "existing recording"}
    else:
        res = sh(PY, "scripts/record_episode.py", "--root", str(root), "--run-id", run_id, "--scenario", scenario)
        g["1_record"] = {"pass": res.returncode == 0, "stdout": json.loads(res.stdout or "{}")}
    res = sh(PY, "-m", "evaluation.run_registry", "verify", "--root", str(root), run_id)
    g["2_m0_verified"] = {"pass": res.returncode == 0 and json.loads(res.stdout)["problems"] == []
                          and Registry(root).state(run_id) == "VERIFIED",
                          "state": Registry(root).state(run_id), "verify": json.loads(res.stdout)}
    rrd, mp4, rep = workdir / f"{run_id}.rrd", workdir / f"{run_id}.mp4", workdir / "replay_report.json"
    res = sh(PY, "-m", "haplous.cli", "replay", run_id, "--recordings-root", str(root), "--ui",
             "--rrd", str(rrd), "--mp4", str(mp4), "--report", str(rep))
    if res.returncode != 0:
        g["3_replay"] = {"pass": False, "stderr": res.stderr[-2000:]}
        return g
    report = json.loads(rep.read_text())
    g["3_no_runtime_imports"] = {"pass": report["forbidden_modules_loaded"] == [],
                                 "forbidden_modules_loaded": report["forbidden_modules_loaded"],
                                 "checked": list(rp.FORBIDDEN_MODULES)}
    g["4_pose_matches"] = {"pass": report["pose_check"]["pass"], **report["pose_check"]}
    g["5_event_sync"] = gate5_event_sync(rrd, root, run_id)
    g["6_mp4"] = gate6_video(mp4, report)
    res = sh(str(Path(PY).with_name("rerun")), "rrd", "verify", str(rrd))
    g["7_rrd_verify"] = {"pass": res.returncode == 0, "output": (res.stdout + res.stderr).strip()[-300:]}
    problems = Registry(root).check(run_id)
    g["8_hashes_match_m0"] = {"pass": problems == [] and report["m0_artifacts"] == Registry(root).get(run_id)["artifacts"],
                              "problems": problems, "artifacts": len(report["m0_artifacts"])}
    g["9_bounded_rss"] = gate9_rss(workdir, rss_horizons)
    res = sh(PY, "-m", "pytest", "-q", tests, "-k", "perturb")
    g["10_aa_noninterference"] = {"pass": res.returncode == 0, "pytest": res.stdout.strip().splitlines()[-1]}
    res = sh(PY, "-m", "pytest", "-q", tests, "-k", "defect or tampered or no_runtime")
    g["11_planted_defects"] = {"pass": res.returncode == 0, "pytest": res.stdout.strip().splitlines()[-1]}
    g["_report"] = report
    return g


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--root", type=Path, help="M0 registry root (default: <workdir>/registry)")
    parser.add_argument("--run-id", default="m2-heb-insert_dropout_large")
    parser.add_argument("--scenario", default="insert_dropout_large")
    parser.add_argument("--existing", action="store_true", help="gate an already recorded run")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--rss-scenarios", nargs=2, default=RSS_HORIZONS, metavar=("SHORT", "LONG"),
                        help="gate 9 horizons of the same task pack, each name[:offset_m]")
    parser.add_argument("--tests", default="tests/test_recording_replay.py",
                        help="pytest file holding this pack's A/A (gate 10) and planted-defect (gate 11) tests")
    parser.add_argument("--report-out", type=Path, help="also write the replay report here")
    args = parser.parse_args(argv)
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="m2-gate-"))
    workdir.mkdir(parents=True, exist_ok=True)
    root = args.root or workdir / "registry"
    gates = run_gates(workdir, root, args.run_id, args.existing, args.scenario, tuple(args.rss_scenarios), args.tests)
    report = gates.pop("_report", None)
    if args.report_out and report is not None:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(json.dumps({**report, "gates": {k: v["pass"] for k, v in gates.items()}},
                                              indent=2, sort_keys=True) + "\n")
    print(json.dumps({"workdir": str(workdir), "verdicts": {k: v["pass"] for k, v in gates.items()},
                      "gates": gates}, indent=2, default=str))
    if not args.keep and args.workdir is None:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0 if all(v["pass"] for v in gates.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
