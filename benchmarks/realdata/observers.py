from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np
from numpy.typing import NDArray

from benchmarks.realdata.records import Episode, FloatArray


class MissingJointEffortError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ObserverConfig:
    direct_threshold_n: float
    momentum_threshold_nm: float
    momentum_alpha: float = 0.2
    reference_force_threshold_n: float = 8.0


@dataclass(frozen=True, slots=True)
class ObserverTrace:
    observer: str
    score: FloatArray
    contact: NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class ObserverMetrics:
    precision: float
    recall: float
    onset_delay_ms: float | None
    residual_variance: float
    false_contact_rate_free_space: float
    agreement_with_force_onset: float


def _baseline_center(values: FloatArray) -> FloatArray:
    count = max(3, len(values) // 10)
    return values - np.median(values[:count], axis=0)


def direct_force_observer(episode: Episode, threshold_n: float) -> ObserverTrace:
    force = np.asarray([record.force_xyz for record in episode.records])
    score = np.linalg.norm(_baseline_center(force), axis=1)
    return ObserverTrace("O0_direct_wrench", score, score >= threshold_n)


class PandaMomentumObserver:
    def __init__(self, alpha: float) -> None:
        self._model = mujoco.MjModel.from_xml_path(
            str(Path(__file__).with_name("panda_inertial.xml"))
        )
        self._data = mujoco.MjData(self._model)
        self._alpha = alpha

    def evaluate(self, episode: Episode, threshold_nm: float) -> ObserverTrace:
        if any(record.joint_effort is None for record in episode.records):
            raise MissingJointEffortError
        momentum: list[FloatArray] = []
        modeled_rate: list[FloatArray] = []
        for record in episode.records:
            self._data.qpos[:] = record.q
            self._data.qvel[:] = record.dq
            mujoco.mj_forward(self._model, self._data)
            mass = np.zeros((7, 7), dtype=np.float64)
            mujoco.mj_fullM(self._model, self._data, mass)
            momentum.append(mass @ record.dq)
            modeled_rate.append(record.joint_effort - self._data.qfrc_bias)
        times = np.asarray([record.t_s for record in episode.records])
        measured_rate = np.gradient(np.asarray(momentum), times, axis=0)
        raw = measured_rate - np.asarray(modeled_rate)
        raw = _baseline_center(raw)
        filtered = np.empty_like(raw)
        filtered[0] = raw[0]
        for index in range(1, len(raw)):
            filtered[index] = self._alpha * raw[index] + (1.0 - self._alpha) * filtered[index - 1]
        score = np.linalg.norm(filtered, axis=1)
        return ObserverTrace("O2_momentum_residual", score, score >= threshold_nm)


def tune_threshold(scores: list[FloatArray], minimum: float) -> float:
    pooled = np.concatenate([score[:max(3, len(score) // 5)] for score in scores])
    return max(minimum, float(np.quantile(pooled, 0.99) * 1.25))


def evaluate_trace(episode: Episode, trace: ObserverTrace, config: ObserverConfig) -> ObserverMetrics:
    force = np.asarray([record.force_xyz for record in episode.records])
    force_score = np.linalg.norm(_baseline_center(force), axis=1)
    insertion_label = np.asarray([
        any(token in record.label.lower() for token in ("insert", "place", "contact", "primitive_"))
        for record in episode.records
    ])
    phase_proxy = insertion_label & (force_score >= config.reference_force_threshold_n)
    free_space = (~insertion_label) | np.asarray([
        record.pose_tcp[2] >= np.quantile([item.pose_tcp[2] for item in episode.records], 0.8)
        for record in episode.records
    ])
    true_positive = int(np.sum(trace.contact & phase_proxy))
    predicted = int(np.sum(trace.contact))
    positives = int(np.sum(phase_proxy))
    precision = true_positive / predicted if predicted else 0.0
    recall = true_positive / positives if positives else 0.0
    reference_indices = np.flatnonzero(phase_proxy)
    observer_indices = np.flatnonzero(trace.contact)
    delay = None
    if len(reference_indices) and len(observer_indices):
        delay = (episode.records[int(observer_indices[0])].t_s - episode.records[int(reference_indices[0])].t_s) * 1000.0
    false_rate = float(np.mean(trace.contact[free_space])) if np.any(free_space) else 0.0
    reference_contact = force_score >= config.direct_threshold_n
    return ObserverMetrics(precision, recall, delay, float(np.var(trace.score)), false_rate,
                           float(np.mean(trace.contact == reference_contact)))
