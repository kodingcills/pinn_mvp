from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from benchmarks.realdata.fmb_loader import load_fmb
from benchmarks.realdata.observers import (
    ObserverConfig,
    ObserverMetrics,
    PandaMomentumObserver,
    direct_force_observer,
    evaluate_trace,
    tune_threshold,
)
from benchmarks.realdata.reassemble_loader import load_reassemble
from benchmarks.realdata.records import Episode, FloatArray

ROOT = Path(__file__).resolve().parents[1]
REASSEMBLE = ROOT / "datasets/reassemble/data/chunk-000/file-000.parquet"
FMB = ROOT / "datasets/fmb-insert-1MS-4cam/data/chunk-000/file-000.parquet"
RESULTS = ROOT / "results"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def downloaded_bytes() -> int:
    return sum(
        path.stat().st_size
        for path in (ROOT / "datasets").rglob("*")
        if path.is_file() and ".cache" not in path.parts
    )


def metric_row(episode: Episode, observer_name: str, metrics: ObserverMetrics) -> dict[str, str | int | float | None]:
    values = dataclasses.asdict(metrics)
    return {
        "dataset": episode.dataset,
        "episode_id": episode.episode_id,
        "observer": observer_name,
        "frames": len(episode.records),
        **values,
    }


def aggregate(rows: list[dict[str, str | int | float | None]]) -> dict[str, dict[str, float | int | None]]:
    result: dict[str, dict[str, float | int | None]] = {}
    groups = sorted({(str(row["dataset"]), str(row["observer"])) for row in rows})
    for dataset, observer in groups:
        selected = [row for row in rows if row["dataset"] == dataset and row["observer"] == observer]
        key = f"{dataset}/{observer}"
        result[key] = {"episodes": len(selected)}
        for field in ("precision", "recall", "onset_delay_ms", "residual_variance",
                      "false_contact_rate_free_space", "agreement_with_force_onset"):
            values = [float(row[field]) for row in selected if row[field] is not None]
            result[key][field] = float(np.mean(values)) if values else None
    return result


def free_space_scores(episode: Episode, scores: FloatArray) -> FloatArray:
    mask = np.asarray([record.label.lower() == "no action." for record in episode.records])
    return scores[mask] if np.any(mask) else scores[:max(3, len(scores) // 5)]


def main() -> None:
    reassemble = load_reassemble(REASSEMBLE, {0, 1, 2, 3, 4})
    fmb = load_fmb(FMB, set(range(15)))
    momentum = PandaMomentumObserver(alpha=0.2)
    reassemble_direct_threshold = tune_threshold([
        free_space_scores(episode, direct_force_observer(episode, 0.0).score)
        for episode in reassemble[:2]
    ], 3.0)
    fmb_direct_threshold = tune_threshold([
        free_space_scores(episode, direct_force_observer(episode, 0.0).score)
        for episode in fmb[:10]
    ], 3.0)
    momentum_threshold = tune_threshold(
        [free_space_scores(episode, momentum.evaluate(episode, 0.0).score) for episode in reassemble[:2]], 1.0
    )
    configs = {
        "reassemble_derivative": ObserverConfig(reassemble_direct_threshold, momentum_threshold),
        "fmb_derivative": ObserverConfig(fmb_direct_threshold, momentum_threshold),
    }
    rows: list[dict[str, str | int | float | None]] = []
    for episode in reassemble[2:] + fmb[10:]:
        config = configs[episode.dataset]
        direct = direct_force_observer(episode, config.direct_threshold_n)
        rows.append(metric_row(episode, direct.observer, evaluate_trace(episode, direct, config)))
        if episode.dataset == "reassemble_derivative":
            residual = momentum.evaluate(episode, config.momentum_threshold_nm)
            rows.append(metric_row(episode, residual.observer, evaluate_trace(episode, residual, config)))

    summary = {
        "claim_scope": "offline phase-proxy correspondence only; no closed-loop or physical contact-ground-truth claim",
        "data_label": "DERIVATIVE DATA (robot-lev/DAVIAN HF conversion) - resampled, not official archives",
        "metrics": aggregate(rows),
        "observer_parameters": {name: dataclasses.asdict(config) for name, config in configs.items()},
        "dispositions": {
            "reassemble": "DERIVATIVE OK",
            "fmb": "DERIVATIVE OK; O2 IMPOSSIBLE (no joint-effort channel)",
            "O1": "NOT APPLICABLE: pegsim StateFilter observations require peg/hole probe geometry absent here",
            "O3": "NOT RUN: no preregistered evidence of complementary O0/O2 value",
        },
        "ground_truth_limit": "Labels are task/primitive phases; positives additionally require baseline-centered force above 8 N, not independent contact truth",
        "cross_dataset": "FMB force is Franka-model-estimated wrist wrench, not a separate F/T sensor; REASSEMBLE force is wrist F/T downsampled from about 1.7 kHz to 30 Hz",
        "o2_model_error": "Residual includes payload, friction, effort semantics, inertial mismatch, numerical differentiation, and 30 Hz nearest-neighbour aliasing; it is not a calibrated external torque",
    }
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RESULTS / "external_eval/realdata" / run_id
    run_dir.mkdir(parents=True)
    trials_path = run_dir / "trials.jsonl"
    trials_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                     capture_output=True, text=True).stdout.strip(),
        "data_label": summary["data_label"],
        "datasets": {
            "reassemble": {
                "hf_repo": "robot-lev/reassemble", "license": "CC-BY-4.0",
                "hf_total_bytes": 86334690716, "selected_episodes": [0, 1, 2, 3, 4],
                "file_bytes": REASSEMBLE.stat().st_size, "sha256": file_sha256(REASSEMBLE),
                "conversion": "LeRobot v3; hand-camera 30 Hz nearest-neighbour; modality omissions/sidecars",
                "official_archive": "BLOCKED: 54.8 GiB data.zip exceeds measured 7.5 GiB free",
            },
            "fmb": {
                "hf_repo": "DAVIAN-Robotics/fmb-insert-1MS-4cam", "license": "CC-BY-4.0",
                "hf_total_bytes": 42521834, "selected_episodes": list(range(15)),
                "file_bytes": FMB.stat().st_size, "sha256": file_sha256(FMB),
                "conversion": "LeRobot v3; 10 Hz assumed; depth omitted; insert-only single-peg subset",
                "official_archive": "BLOCKED: official archives are 70-545 GB",
            },
        },
        "downloaded_bytes": downloaded_bytes(),
        "download_budget_bytes": 2500000000,
        "disk_evidence": subprocess.run(["df", "-h", str(ROOT)], check=True,
                                        capture_output=True, text=True).stdout.strip(),
        "panda_model": {
            "source": "MuJoCo Menagerie franka_emika_panda/panda.xml",
            "upstream_commit": "8161bba264d7fa7c99ca301e91e7fb44737676ad",
            "local_sha256": file_sha256(ROOT / "benchmarks/realdata/panda_inertial.xml"),
            "mapping": "dataset q[0:7]/dq[0:7]/effort[0:7] -> joint1..joint7; radians, rad/s, N m",
        },
        "artifacts": {"trials_sha256": file_sha256(trials_path), "summary_sha256": file_sha256(summary_path)},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (RESULTS / "x52_observer_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"run_dir": str(run_dir), "downloaded_bytes": manifest["downloaded_bytes"], **summary["metrics"]}, indent=2))


if __name__ == "__main__":
    main()
