"""Replay of one recorded MuJoCo episode from its M0 run artifacts alone.

The recording format is defined here and written by
`benchmarks.recording`. Replay sets each recorded state with
`mj_setState` and derives geometry with `mj_forward` on the saved model. It
never steps physics and never imports a runtime, controller or benchmark:
`FORBIDDEN_MODULES` is checked at the end of every replay and reported.

Recording (an M0 run's `artifacts/`):

    model.mjb        the exact compiled MjModel (`mj_saveModel`)
    model.xml        MJCF of that model (`mj_saveLastXML`), for humans
    state.arrow      Arrow IPC file, zstd: one row per physics tick
                     tick int64, sim_time_s float64, host_monotonic_ns int64,
                     state fixed_size_list<float64>[state_size] (`mj_getState`)
    telemetry.jsonl  per tick: observations the session returned, commands
                     handed to it and their CommandOutcome
    events.jsonl     Haplous evidence events and machine fault/window events
    metadata.json    format, digests, versions, state spec, outcome

Clock domains are never mixed: `tick` and `sim_time_s` are simulation time,
`host_monotonic_ns` is the recording host's monotonic clock.
"""
from __future__ import annotations

import os

os.environ.setdefault("MUJOCO_GL", "osmesa")  # read by `import mujoco`; headless CPU default

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any

import mujoco  # noqa: E402
import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402

from evaluation.run_registry import Registry  # noqa: E402

FORMAT = "haplous-mujoco-recording-v1"
MODEL_MJB = "model.mjb"
MODEL_XML = "model.xml"
STATE = "state.arrow"
TELEMETRY = "telemetry.jsonl"
EVENTS = "events.jsonl"
METADATA = "metadata.json"
STATE_SPEC = mujoco.mjtState.mjSTATE_INTEGRATION
POSE_TOLERANCE_M = 1e-9
CAMERA_LOOKAT_M = (0.0, 0.0, 0.004)  # pegsim puts the hole mouth at the world origin
CAMERA_DISTANCE_M = 0.05
FORBIDDEN_MODULES = ("haplous.runtime", "benchmarks.heb", "pegsim.control")


def state_schema(state_size: int) -> pa.Schema:
    return pa.schema(
        [
            ("tick", pa.int64()),
            ("sim_time_s", pa.float64()),
            ("host_monotonic_ns", pa.int64()),
            ("state", pa.list_(pa.float64(), state_size)),
        ]
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class ReplayRefused(RuntimeError):
    """The archive cannot be trusted as the recorded history."""


@dataclass(frozen=True)
class Recording:
    run_id: str
    artifacts: Path
    metadata: dict[str, Any]
    model: mujoco.MjModel


def open_recording(root: Path, run_id: str) -> Recording:
    """Every check that can refuse runs here, before anything is derived."""
    problems = Registry(root).check(run_id)
    if problems:
        raise ReplayRefused(f"M0 integrity check failed for {run_id}: {problems}")
    art = Registry(root).run_dir(run_id) / "artifacts"
    try:
        meta = json.loads((art / METADATA).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReplayRefused(f"metadata unreadable: {exc}") from exc
    if meta.get("format") != FORMAT:
        raise ReplayRefused(f"unknown recording format {meta.get('format')!r}")
    if meta["mujoco_version"] != mujoco.__version__:
        raise ReplayRefused(
            f"mujoco version mismatch: recorded {meta['mujoco_version']}, running {mujoco.__version__}"
        )
    actual = sha256_file(art / MODEL_MJB)
    if actual != meta["model_sha256"]:
        raise ReplayRefused(f"model asset digest mismatch: metadata {meta['model_sha256']}, file {actual}")
    model = mujoco.MjModel.from_binary_path(str(art / MODEL_MJB))
    if meta["state_spec"] != int(STATE_SPEC) or mujoco.mj_stateSize(model, STATE_SPEC) != meta["state_size"]:
        raise ReplayRefused("state spec or size does not match the saved model")
    rec = Recording(run_id, art, meta, model)
    _check_stream(rec)
    return rec


def _batches(rec: Recording) -> Iterator[pa.RecordBatch]:
    try:
        reader = pa.ipc.open_file(str(rec.artifacts / STATE))
        if reader.schema.remove_metadata() != state_schema(rec.metadata["state_size"]):
            raise ReplayRefused(f"state stream schema mismatch: {reader.schema}")
        for i in range(reader.num_record_batches):
            yield reader.get_batch(i)
    except (pa.ArrowInvalid, OSError) as exc:
        raise ReplayRefused(f"state stream unreadable (clipped or corrupt): {exc}") from exc


def _check_stream(rec: Recording) -> None:
    expected = 0
    for batch in _batches(rec):
        ticks = batch.column("tick").to_numpy()
        want = np.arange(expected, expected + len(ticks))
        if not np.array_equal(ticks, want):
            bad = int(want[np.argmax(ticks != want)])
            raise ReplayRefused(f"state stream is missing sample at tick {bad}")
        expected += len(ticks)
    if expected != rec.metadata["n_ticks"]:
        raise ReplayRefused(
            f"state stream clipped: {expected} ticks on disk, metadata records {rec.metadata['n_ticks']}"
        )


def iter_states(rec: Recording) -> Iterator[tuple[int, float, np.ndarray]]:
    size = rec.metadata["state_size"]
    for batch in _batches(rec):
        ticks = batch.column("tick").to_numpy()
        times = batch.column("sim_time_s").to_numpy()
        states = batch.column("state").flatten().to_numpy().reshape(-1, size)
        for i in range(len(ticks)):
            yield int(ticks[i]), float(times[i]), states[i]


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _quat_xyzw(wxyz: np.ndarray) -> list[float]:
    return [float(wxyz[1]), float(wxyz[2]), float(wxyz[3]), float(wxyz[0])]


def _log_static_geometry(rr, rec_stream, model: mujoco.MjModel) -> None:
    for g in range(model.ngeom):
        body = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.geom_bodyid[g])) or "world"
        path = f"world/{body}/geom_{g}"
        size, pos, quat = model.geom_size[g], model.geom_pos[g], _quat_xyzw(model.geom_quat[g])
        kind = int(model.geom_type[g])
        if kind == mujoco.mjtGeom.mjGEOM_BOX:
            archetype = rr.Boxes3D(half_sizes=[size], centers=[pos], quaternions=[quat])
        elif kind == mujoco.mjtGeom.mjGEOM_CYLINDER:
            archetype = rr.Cylinders3D(lengths=[2 * size[1]], radii=[size[0]], centers=[pos], quaternions=[quat])
        elif kind == mujoco.mjtGeom.mjGEOM_CAPSULE:
            archetype = rr.Capsules3D(lengths=[2 * size[1]], radii=[size[0]], translations=[pos], quaternions=[quat])
        elif kind == mujoco.mjtGeom.mjGEOM_SPHERE:
            archetype = rr.Ellipsoids3D(half_sizes=[[size[0]] * 3], centers=[pos], quaternions=[quat])
        else:
            continue  # shortcut: planes/meshes are not drawn; add them when a scene uses them
        rec_stream.log(path, archetype, static=True)


def replay(
    root: Path,
    run_id: str,
    *,
    rrd_path: Path | None,
    mp4_path: Path | None,
    report_path: Path | None,
    fps: int = 30,
    width: int = 640,
    height: int = 480,
) -> dict[str, Any]:
    rec = open_recording(root, run_id)
    meta, model = rec.metadata, rec.model
    data = mujoco.MjData(model)
    site = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, meta["tracked_site"])
    dt = meta["timestep_s"]

    observed: dict[int, list[dict[str, Any]]] = {}
    for row in _jsonl(rec.artifacts / TELEMETRY):
        if row["kind"] == "observation" and row["observation"] is not None:
            observed.setdefault(row["tick"], []).append(row["observation"])
    events_by_tick: dict[int, list[dict[str, Any]]] = {}
    for event in _jsonl(rec.artifacts / EVENTS):
        events_by_tick.setdefault(event["tick"], []).append(event)
    moving = [b for b in range(1, model.nbody) if model.body_jntnum[b] > 0]

    stream = None
    if rrd_path is not None:
        import rerun as rr

        rrd_path.parent.mkdir(parents=True, exist_ok=True)
        stream = rr.RecordingStream("haplous_replay", recording_id=run_id)
        stream.save(str(rrd_path))
        stream.set_log_time_enabled(False)  # keep host wall time out of the sim timeline
        stream.set_log_tick_enabled(False)
        stream.log("world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)
        _log_static_geometry(rr, stream, model)

    encoder = renderer = None
    frame_ticks: set[int] = set()
    if mp4_path is not None:
        mp4_path.parent.mkdir(parents=True, exist_ok=True)
        n_frames = max(1, round((meta["n_ticks"] - 1) * dt * fps))
        frame_ticks = {min(meta["n_ticks"] - 1, round(i / fps / dt)) for i in range(n_frames)}
        renderer = mujoco.Renderer(model, height, width)
        camera = mujoco.MjvCamera()
        camera.lookat[:] = CAMERA_LOOKAT_M
        camera.distance, camera.azimuth, camera.elevation = CAMERA_DISTANCE_M, 120.0, -20.0
        encoder = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-bitexact", str(mp4_path)],
            stdin=subprocess.PIPE,
        )

    max_err, compared, frames_written = 0.0, 0, 0
    for tick, sim_time, state in iter_states(rec):
        mujoco.mj_setState(model, data, state, STATE_SPEC)
        mujoco.mj_forward(model, data)
        tip = data.site_xpos[site].copy()
        for obs in observed.get(tick, ()):
            max_err = max(max_err, float(np.max(np.abs(tip - np.asarray(obs["pose_m"])))))
            compared += 1
        if stream is not None:
            stream.set_time("tick", sequence=tick)
            stream.set_time("sim_time", duration=sim_time)
            for b in moving:
                name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, b)
                stream.log(f"world/{name}", rr.Transform3D(
                    translation=data.xpos[b], quaternion=rr.Quaternion(xyzw=_quat_xyzw(data.xquat[b]))))
            stream.log(f"world/site/{meta['tracked_site']}", rr.Points3D([tip], radii=[2e-4]))
            if tick in observed:
                stream.log("plots/force_z_n", rr.Scalars(observed[tick][-1]["force_z_n"]))
            for event in events_by_tick.get(tick, ()):
                stream.log(f"events/{event['source']}", rr.TextLog(
                    f"{event['kind']} {json.dumps(event['payload'], sort_keys=True)}",
                    level="WARN" if event["source"] == "machine" else "INFO"))
        if tick in frame_ticks:
            renderer.update_scene(data, camera)
            encoder.stdin.write(renderer.render().tobytes())
            frames_written += 1

    outputs: dict[str, Any] = {}
    if stream is not None:
        stream.flush()
        stream.disconnect()
        outputs["rrd"] = {"path": str(rrd_path), "sha256": sha256_file(rrd_path), "bytes": rrd_path.stat().st_size}
    if encoder is not None:
        encoder.stdin.close()
        if encoder.wait() != 0:
            raise RuntimeError("ffmpeg failed to encode the replay video")
        renderer.close()
        outputs["mp4"] = {"path": str(mp4_path), "sha256": sha256_file(mp4_path),
                          "bytes": mp4_path.stat().st_size, "fps": fps, "frames": frames_written}

    if compared != meta["n_observations"]:
        raise ReplayRefused(f"compared {compared} observations, recording has {meta['n_observations']}")
    report = {
        "run_id": run_id,
        "format": FORMAT,
        "m0_manifest_sha256": Registry(root).manifest_digest(run_id),
        "m0_artifacts": Registry(root).get(run_id)["artifacts"],
        "n_ticks": meta["n_ticks"],
        "sim_duration_s": (meta["n_ticks"] - 1) * dt,
        "tracked_site": meta["tracked_site"],
        "pose_check": {
            "observations_compared": compared,
            "max_abs_error_m": max_err,
            "tolerance_m": POSE_TOLERANCE_M,
            "pass": max_err <= POSE_TOLERANCE_M,
        },
        "events": sum(len(v) for v in events_by_tick.values()),
        "outcome": meta["outcome"],
        "derived": outputs,
        "forbidden_modules_loaded": sorted(
            m for m in sys.modules if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_MODULES)
        ),
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.replay")
    parser.add_argument("run_id")
    parser.add_argument("--root", required=True, type=Path, help="M0 registry root holding the recording")
    parser.add_argument("--rrd", type=Path)
    parser.add_argument("--mp4", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        report = replay(args.root, args.run_id, rrd_path=args.rrd, mp4_path=args.mp4,
                        report_path=args.report, fps=args.fps)
    except ReplayRefused as exc:
        print(f"replay refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: report[k] for k in ("run_id", "n_ticks", "pose_check", "derived",
                                             "forbidden_modules_loaded")}, indent=2))
    return 0 if report["pose_check"]["pass"] and not report["forbidden_modules_loaded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
