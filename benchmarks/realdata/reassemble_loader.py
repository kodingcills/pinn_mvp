from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pyarrow.parquet as pq

from benchmarks.realdata.records import Episode, FloatArray, TelemetryRecord


def _array(value: list[float] | np.ndarray) -> FloatArray:
    return np.asarray(value, dtype=np.float64)


def _load_parquet(path: Path, episode_ids: set[int] | None) -> list[Episode]:
    table = pq.read_table(path)
    rows = table.to_pylist()
    task_path = path.parents[2] / "meta" / "tasks.parquet"
    tasks = {
        int(row["task_index"]): str(row["task"])
        for row in pq.read_table(task_path).to_pylist()
    }
    selected = sorted({int(row["episode_index"]) for row in rows})
    if episode_ids is not None:
        selected = [episode for episode in selected if episode in episode_ids]
    episodes: list[Episode] = []
    for episode_id in selected:
        records: list[TelemetryRecord] = []
        for row in rows:
            if int(row["episode_index"]) != episode_id:
                continue
            state = _array(row["observation.state"])
            records.append(TelemetryRecord(
                t_s=float(row["timestamp"]), pose_tcp=_array(row["observation.state.ee_pose"]),
                vel_tcp=state[30:36], force_xyz=_array(row["observation.force"]),
                torque_xyz=_array(row["observation.torque"]), q=state[:7], dq=state[7:14],
                joint_effort=state[14:21], label=tasks[int(row["task_index"])],
            ))
        episodes.append(Episode(
            dataset="reassemble_derivative", episode_id=str(episode_id), records=tuple(records),
            resample_hz=30.0,
            timestamp_note="Derivative nearest-neighbour resampling to hand-camera 30 Hz clock",
        ))
    return episodes


def _nearest(values: FloatArray, timestamps: FloatArray, grid: FloatArray) -> FloatArray:
    right = np.searchsorted(timestamps, grid, side="left")
    right = np.clip(right, 0, len(timestamps) - 1)
    left = np.clip(right - 1, 0, len(timestamps) - 1)
    choose_left = np.abs(grid - timestamps[left]) <= np.abs(timestamps[right] - grid)
    return values[np.where(choose_left, left, right)]


def _load_hdf5(path: Path, resample_hz: float) -> list[Episode]:
    names = ("joint_positions", "joint_velocities", "joint_efforts", "pose", "velocity", "measured_force", "measured_torque")
    with h5py.File(path, "r") as handle:
        starts = [float(handle[f"timestamps/{name}"][0]) for name in names]
        ends = [float(handle[f"timestamps/{name}"][-1]) for name in names]
        grid = np.arange(max(starts), min(ends) + 0.5 / resample_hz, 1.0 / resample_hz)
        streams = {
            name: _nearest(_array(handle[f"robot_state/{name}"][:]), _array(handle[f"timestamps/{name}"][:]), grid)
            for name in names
        }
        labels = ["unlabeled"] * len(grid)
        if "segments_info" in handle:
            for segment in handle["segments_info"].values():
                start, end = float(segment["start"][()]), float(segment["end"][()])
                raw = segment["text"][()]
                text = raw.decode() if isinstance(raw, bytes) else str(raw)
                for index in np.flatnonzero((grid >= start) & (grid <= end)):
                    labels[int(index)] = text
    records = tuple(TelemetryRecord(
        t_s=float(t), pose_tcp=streams["pose"][i], vel_tcp=streams["velocity"][i],
        force_xyz=streams["measured_force"][i], torque_xyz=streams["measured_torque"][i],
        q=streams["joint_positions"][i], dq=streams["joint_velocities"][i],
        joint_effort=streams["joint_efforts"][i], label=labels[i],
    ) for i, t in enumerate(grid))
    return [Episode("reassemble_official_hdf5", path.stem, records, resample_hz,
                    "Asynchronous source streams nearest-neighbour resampled on overlap grid")]


def load_reassemble(path: Path, episode_ids: set[int] | None, resample_hz: float = 30.0) -> list[Episode]:
    return _load_hdf5(path, resample_hz) if path.suffix in {".h5", ".hdf5"} else _load_parquet(path, episode_ids)
