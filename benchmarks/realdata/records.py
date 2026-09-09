from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class TelemetryRecord:
    t_s: float
    pose_tcp: FloatArray
    vel_tcp: FloatArray
    force_xyz: FloatArray
    torque_xyz: FloatArray
    q: FloatArray
    dq: FloatArray
    joint_effort: FloatArray | None
    label: str


@dataclass(frozen=True, slots=True)
class Episode:
    dataset: str
    episode_id: str
    records: tuple[TelemetryRecord, ...]
    resample_hz: float
    timestamp_note: str
