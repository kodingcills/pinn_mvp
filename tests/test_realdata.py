from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from benchmarks.realdata.fmb_loader import load_fmb
from benchmarks.realdata.observers import (
    MissingJointEffortError,
    PandaMomentumObserver,
    direct_force_observer,
)
from benchmarks.realdata.reassemble_loader import load_reassemble
from benchmarks.realdata.records import Episode, TelemetryRecord


def test_reassemble_hdf5_resamples_asynchronous_streams(tmp_path: Path) -> None:
    path = tmp_path / "recording.h5"
    with h5py.File(path, "w") as handle:
        state = handle.create_group("robot_state")
        stamps = handle.create_group("timestamps")
        for name, width in (
            ("joint_positions", 7), ("joint_velocities", 7),
            ("joint_efforts", 7), ("pose", 7), ("velocity", 6),
            ("measured_force", 3), ("measured_torque", 3),
        ):
            state.create_dataset(name, data=np.arange(3 * width).reshape(3, width))
            stamps.create_dataset(name, data=np.array([0.0, 0.11, 0.21]))
        segment = handle.create_group("segments_info/insert")
        segment.create_dataset("start", data=0.05)
        segment.create_dataset("end", data=0.2)
        segment.create_dataset("text", data=np.bytes_("Insert peg"))

    episode = load_reassemble(path, episode_ids=None, resample_hz=10.0)[0]

    assert episode.resample_hz == 10.0
    assert len(episode.records) == 3
    assert episode.records[1].label == "Insert peg"
    np.testing.assert_array_equal(episode.records[1].q, np.arange(7, 14))


def test_fmb_npy_preserves_normalized_fields(tmp_path: Path) -> None:
    path = tmp_path / "episode.npy"
    fixture = {
        "tcp_pose": np.ones((3, 7)), "tcp_vel": np.ones((3, 6)),
        "tcp_force": np.ones((3, 3)), "tcp_torque": np.ones((3, 3)),
        "q": np.ones((3, 7)), "dq": np.ones((3, 7)),
        "primitive": np.array(["approach", "insert", "insert"]),
    }
    np.save(path, fixture, allow_pickle=True)

    episode = load_fmb(path, episode_ids=None)[0]

    assert episode.resample_hz == 10.0
    assert episode.records[1].label == "insert"
    assert episode.records[2].t_s == 0.2
    assert episode.records[0].joint_effort is None


def test_fmb_parquet_splits_episodes(tmp_path: Path) -> None:
    path = tmp_path / "data.parquet"
    vector = lambda width: [[float(i)] * width for i in range(4)]
    table = pa.table({
        "episode_index": [0, 0, 1, 1], "timestamp": [0.0, 0.1, 0.0, 0.1],
        "observation.tcp_pose": vector(7), "observation.tcp_vel": vector(6),
        "observation.tcp_force": vector(3), "observation.tcp_torque": vector(3),
        "observation.joint_pos": vector(7), "observation.joint_vel": vector(7),
        "observation.primitive_id": [0.0, 1.0, 0.0, 1.0],
    })
    pq.write_table(table, path)

    episodes = load_fmb(path, episode_ids={1})

    assert len(episodes) == 1
    assert episodes[0].episode_id == "1"
    assert episodes[0].records[1].label == "primitive_1"


def test_direct_observer_detects_force_change_after_baseline() -> None:
    records = tuple(TelemetryRecord(
        t_s=index * 0.1, pose_tcp=np.zeros(7), vel_tcp=np.zeros(6),
        force_xyz=np.array([0.0, 0.0, float(index >= 3) * 5.0]),
        torque_xyz=np.zeros(3), q=np.zeros(7), dq=np.zeros(7),
        joint_effort=np.zeros(7), label="Insert peg",
    ) for index in range(6))
    episode = Episode("synthetic", "0", records, 10.0, "synthetic")

    trace = direct_force_observer(episode, 3.0)

    np.testing.assert_array_equal(trace.contact, [False, False, False, True, True, True])


def test_momentum_observer_rejects_episode_without_efforts() -> None:
    record = TelemetryRecord(
        t_s=0.0, pose_tcp=np.zeros(7), vel_tcp=np.zeros(6), force_xyz=np.zeros(3),
        torque_xyz=np.zeros(3), q=np.zeros(7), dq=np.zeros(7), joint_effort=None,
        label="insert",
    )
    episode = Episode("fmb_derivative", "0", (record,), 10.0, "assumed")

    with np.testing.assert_raises(MissingJointEffortError):
        PandaMomentumObserver(0.2).evaluate(episode, 1.0)
