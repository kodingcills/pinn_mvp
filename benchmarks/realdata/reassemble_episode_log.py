"""Import REASSEMBLE segments into the backend-neutral episode log (design M5-L, M5.5).

Source: the LeRobot-v3 derivative `robot-lev/reassemble` (CC-BY-4.0) of
REASSEMBLE (TU Wien, DOI 10.48436/0ewrv-8cb44): a real Franka on the NIST
Assembly Task Board, every sensor nearest-neighbour resampled to 30 Hz.

Writes `telemetry.jsonl`, `events.jsonl`, `metadata.json` exactly as
`benchmarks.episode_log` names and shapes them, for a contiguous run of
annotated segments of one episode:

    observation  measured EE position, gravity/bias-compensated F/T
                 (`observation.force.base`), tilt derived from the measured
                 EE quaternion; raw quaternion and the other F/T axes in extra
    command      the dataset `action` (next absolute EE target). No stiffness,
                 and the robot reported no acceptance, so `outcome` is null
    events       source "dataset": segment_start / segment_end per annotated
                 segment. Haplous did not run, so there is no Haplous event

Nothing here is evaluator-only ground truth: `segment.success` is the
dataset's own annotation and is reported as such.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq

from benchmarks.episode_log import EVENTS, FORMAT, METADATA, TELEMETRY, _observation_row
from benchmarks.realdata.reassemble_loader import load_reassemble
from haplous.machine import MachineObservation, Stamp

SOURCE = "dataset"
CLOCK = "dataset"
TIMESTEP_S = 1.0 / 30.0
LICENCE = "CC-BY-4.0"
ATTRIBUTION = (
    "REASSEMBLE: A Multimodal Dataset for Contact-rich Robotic Assembly and Disassembly. "
    "Sliwowski, Daniel Jan; Jadav, Shail; Stanovcic, Sergej; Orbik, Jedrzej; Heidersberger, Johannes; "
    "Lee, Dongheui. TU Wien, 2025. DOI 10.48436/0ewrv-8cb44, arXiv:2502.05086. "
    "Used via the LeRobot-v3 derivative robot-lev/reassemble (reformatted and resampled), CC-BY-4.0. "
    "This episode log is a further reformatting: one episode cut to annotated segments."
)
_EXTRA_COLUMNS = ("frame_index", "action", "observation.force.base", "observation.torque.base",
                  "segment.index", "segment.success", "task_index")


def _f32(value: float) -> float:
    """Shortest decimal that round-trips the dataset's float32 value."""
    return float(str(np.float32(value)))


def tilt_deg(quat_wxyz) -> float:
    """Angle between the EE z axis and world -z (tool pointing straight down = 0)."""
    w, x, y, z = (float(v) for v in quat_wxyz)
    zz = (w * w - x * x - y * y + z * z) / (w * w + x * x + y * y + z * z)  # rotation matrix R[2][2]
    return round(math.degrees(math.acos(max(-1.0, min(1.0, -zz)))), 4)


def _line(fh, row: dict[str, Any]) -> None:
    fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def import_segments(parquet: Path, out_dir: Path, *, episode: int, segments: list[int],
                    provenance: dict[str, Any]) -> dict[str, Any]:
    """Write the episode log of `segments` (contiguous `segment.index` values) of `episode`."""
    if not segments or segments != list(range(segments[0], segments[-1] + 1)):
        raise ValueError(f"segments must be contiguous, got {segments}")
    (record_episode,) = load_reassemble(parquet, {episode})
    table = pq.read_table(parquet, columns=["episode_index", *_EXTRA_COLUMNS])
    extra = table.filter(pc.equal(table["episode_index"], episode)).select(list(_EXTRA_COLUMNS)).to_pylist()
    if len(extra) != len(record_episode.records):
        raise ValueError(f"episode {episode}: loader returned {len(record_episode.records)} rows, parquet {len(extra)}")
    picked = [(rec, ex) for rec, ex in zip(record_episode.records, extra, strict=True)
              if ex["segment.index"] in segments]
    if not picked or {ex["segment.index"] for _, ex in picked} != set(segments):
        raise ValueError(f"episode {episode} has no rows for every segment in {segments}")
    frames = [ex["frame_index"] for _, ex in picked]
    if frames != list(range(frames[0], frames[0] + len(frames))):
        raise ValueError(f"episode {episode} segments {segments} are not one contiguous frame range")

    out_dir.mkdir(parents=True, exist_ok=True)
    spans: list[dict[str, Any]] = []
    with open(out_dir / TELEMETRY, "w", encoding="utf-8") as tel:
        for tick, (rec, ex) in enumerate(picked):
            pose, act = rec.pose_tcp, ex["action"]
            fb, tb = ex["observation.force.base"], ex["observation.torque.base"]
            obs = MachineObservation(
                stamp=Stamp(source_s=_f32(rec.t_s), received_s=_f32(rec.t_s)),
                pose_m=tuple(_f32(v) for v in pose[:3]), force_z_n=_f32(fb[2]), tilt_deg=tilt_deg(pose[3:7]),
                extra=(("force_x_n", _f32(fb[0])), ("force_y_n", _f32(fb[1])),
                       ("torque_x_nm", _f32(tb[0])), ("torque_y_nm", _f32(tb[1])), ("torque_z_nm", _f32(tb[2])),
                       *((f"quat_{k}", _f32(v)) for k, v in zip("wxyz", pose[3:7], strict=True))))
            _line(tel, {"tick": tick, "host_monotonic_ns": None, "kind": "observation",
                        "observation": _observation_row(obs)})
            _line(tel, {"tick": tick, "host_monotonic_ns": None, "kind": "command", "outcome": None,
                        "command": {"target_pose_m": [_f32(v) for v in act[:3]], "k_z_n_per_m": None,
                                    "extra": {**{f"target_quat_{k}": _f32(v) for k, v in zip("wxyz", act[3:7], strict=True)},
                                              "gripper_0": _f32(act[7]), "gripper_1": _f32(act[8])}}})
            if not spans or spans[-1]["segment_index"] != ex["segment.index"]:
                spans.append({"segment_index": ex["segment.index"], "label": rec.label,
                              "task_index": ex["task_index"], "segment_success": bool(ex["segment.success"]),
                              "start_tick": tick, "episode_frame": ex["frame_index"], "episode_t_s": _f32(rec.t_s)})
            spans[-1]["end_tick"] = tick + 1
    with open(out_dir / EVENTS, "w", encoding="utf-8") as ev:
        for span in spans:
            payload = {k: span[k] for k in ("segment_index", "label", "task_index", "segment_success")}
            for kind, tick in (("segment_start", span["start_tick"]), ("segment_end", span["end_tick"])):
                _line(ev, {"source": SOURCE, "tick": tick, "t_s": tick * TIMESTEP_S, "clock": CLOCK,
                           "kind": kind, "payload": {**payload, "episode_frame": span["episode_frame"] + tick - span["start_tick"]}})

    metadata = {
        "format": FORMAT,
        "system": "none: recorded real-robot dataset; Haplous did not run",
        "backend": "real Franka Emika Panda (REASSEMBLE, NIST Assembly Task Board), offline log",
        "timestep_s": TIMESTEP_S,
        "n_ticks": len(picked),
        "n_observations": len(picked),
        "clocks": {"tick": "frame index from the first imported frame (dataset 30 Hz frame clock)",
                   "t_s": "tick * timestep_s, seconds since the first imported frame",
                   "stamp.source_s": "dataset timestamp, seconds since episode start (resampled hand-camera clock); "
                                     "received_s repeats it because no consumer clock was recorded",
                   "host_monotonic_ns": "null: imported offline, there was no recording host"},
        "outcome": {"disposition": None,
                    "dataset_segment_success": {str(s["segment_index"]): s["segment_success"] for s in spans},
                    "note": "disposition is null because Haplous did not run; dataset_segment_success is the "
                            "dataset's own segment.success annotation, not evaluator-only ground truth"},
        "task_id": f"reassemble/episode-{episode}/segments-{segments[0]}-{segments[-1]}",
        "segments": spans,
        "signals": {"observation.pose_m": "observation.state.ee_pose[0:3], measured EE position (m)",
                    "observation.force_z_n": "observation.force.base[2], gravity/bias-compensated F/T z (N); chosen "
                                             "over observation.force because it reads ~0 out of contact",
                    "observation.tilt_deg": "derived: angle between measured EE z axis and world -z",
                    "observation.extra": "remaining force.base / torque.base axes, measured EE quaternion (w,x,y,z)",
                    "command.target_pose_m": "action[0:3], the dataset's next absolute EE target position",
                    "command.extra": "action[3:7] target quaternion (w,x,y,z), action[7:9] gripper",
                    "command.k_z_n_per_m": "null: no stiffness in the dataset",
                    "outcome": "null: the robot reported no command acceptance"},
        "timestamp_note": "LeRobot-v3 derivative: every sensor nearest-neighbour resampled onto the 30 Hz "
                          "hand-camera clock (F/T measured ~1.7 kHz, base F/T ~0.5 kHz, downsampled)",
        "provenance": provenance,
        "licence": LICENCE,
        "attribution": ATTRIBUTION,
        "ground_truth": None,
        "haplous_evidence": None,
        "claims": "no evaluator-only ground truth and no Haplous evidence exist for this log: "
                  "it is a recorded real-robot dataset that Haplous never ran on",
    }
    (out_dir / METADATA).write_text(json.dumps(metadata, indent=2, sort_keys=True, default=str) + "\n")
    return metadata


def claim_problems(log_dir: Path) -> list[str]:
    """Rows or metadata in an imported log that claim Haplous evidence or ground truth."""
    problems = []
    for name in (TELEMETRY, EVENTS):
        for n, line in enumerate((log_dir / name).read_text(encoding="utf-8").splitlines()):
            row = json.loads(line)
            if name == EVENTS and row["source"] != SOURCE:
                problems.append(f"{name}:{n} event source {row['source']!r}, not {SOURCE!r}")
            if row.get("kind") == "command" and row["outcome"] is not None:
                problems.append(f"{name}:{n} command claims an outcome {row['outcome']!r}")
            if "truth" in line:
                problems.append(f"{name}:{n} mentions truth")
    meta = json.loads((log_dir / METADATA).read_text(encoding="utf-8"))
    for key in ("ground_truth", "haplous_evidence"):
        if meta.get(key, "missing") is not None:
            problems.append(f"metadata {key} is {meta.get(key, 'missing')!r}, not null")
    if meta["outcome"].get("disposition") is not None:
        problems.append(f"metadata outcome claims disposition {meta['outcome']['disposition']!r}")
    return problems
