from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from benchmarks.realdata.records import Episode, FloatArray, TelemetryRecord


def _array(value: list[float] | np.ndarray) -> FloatArray:
    return np.asarray(value, dtype=np.float64)


def _records(columns: dict[str, np.ndarray], timestamps: FloatArray) -> tuple[TelemetryRecord, ...]:
    labels = columns["primitive"]
    return tuple(TelemetryRecord(
        t_s=float(timestamps[index]), pose_tcp=_array(columns["tcp_pose"][index]),
        vel_tcp=_array(columns["tcp_vel"][index]), force_xyz=_array(columns["tcp_force"][index]),
        torque_xyz=_array(columns["tcp_torque"][index]), q=_array(columns["q"][index]),
        dq=_array(columns["dq"][index]), joint_effort=None, label=str(labels[index]),
    ) for index in range(len(timestamps)))


def _load_npy(path: Path) -> list[Episode]:
    raw = np.load(path, allow_pickle=True).item()
    columns = {str(key): np.asarray(value) for key, value in raw.items()}
    timestamps = np.arange(len(columns["tcp_pose"]), dtype=np.float64) / 10.0
    return [Episode("fmb_official_npy", path.stem, _records(columns, timestamps), 10.0,
                    "Source has no timestamps; derivative convention assumes 10 Hz")]


def _load_parquet(path: Path, episode_ids: set[int] | None) -> list[Episode]:
    rows = pq.read_table(path).to_pylist()
    selected = sorted({int(row["episode_index"]) for row in rows})
    if episode_ids is not None:
        selected = [episode for episode in selected if episode in episode_ids]
    episodes: list[Episode] = []
    for episode_id in selected:
        episode_rows = [row for row in rows if int(row["episode_index"]) == episode_id]
        columns = {
            "tcp_pose": np.asarray([row["observation.tcp_pose"] for row in episode_rows]),
            "tcp_vel": np.asarray([row["observation.tcp_vel"] for row in episode_rows]),
            "tcp_force": np.asarray([row["observation.tcp_force"] for row in episode_rows]),
            "tcp_torque": np.asarray([row["observation.tcp_torque"] for row in episode_rows]),
            "q": np.asarray([row["observation.joint_pos"] for row in episode_rows]),
            "dq": np.asarray([row["observation.joint_vel"] for row in episode_rows]),
            "primitive": np.asarray([f"primitive_{int(row['observation.primitive_id'])}" for row in episode_rows]),
        }
        timestamps = _array([row["timestamp"] for row in episode_rows])
        episodes.append(Episode("fmb_derivative", str(episode_id), _records(columns, timestamps), 10.0,
                                "No source timestamps; conversion assumes 10 Hz"))
    return episodes


def load_fmb(path: Path, episode_ids: set[int] | None) -> list[Episode]:
    return _load_npy(path) if path.suffix == ".npy" else _load_parquet(path, episode_ids)
