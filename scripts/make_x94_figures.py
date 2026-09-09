"""X94 figure generation: campaign figures from committed result artifacts.
Only figures with existing measured data are produced; none are fabricated.

Sources:
- results/heb_final_summary.json            (prior frozen internal HEB eval)
- results/external_eval/robosuite-1.5.2/    (dev/qual/FINAL robosuite runs)
- results/x30_governor_summary.json         (governor microbench)
- results/x31_jitter_summary.json           (timing stress)
- results/x41_solver_sensitivity.json       (contact numerics)
- results/x52_observer_summary.json         (real-data observers)
- git numstat aa46c24^..HEAD                (engineering leverage)
"""
from __future__ import annotations

import json
import pathlib
import subprocess

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIG = ROOT / "docs" / "figures"
FIG.mkdir(parents=True, exist_ok=True)


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text())


def save(fig, name: str) -> None:
    fig.savefig(FIG / name, format="svg", bbox_inches="tight")
    print("wrote", name)


def fig_bounded_success() -> None:
    heb = load("results/heb_final_summary.json")
    rob_final = load(
        "results/external_eval/robosuite-1.5.2/20260909T213850Z/summary.json"
    )
    systems = ["direct_minimal", "strong_osc", "haplous_mediated"]
    rob = {
        sysname: 100.0 * d["bounded_successes"] / d["trials"]
        for sysname, d in rob_final["systems"].items()
    }
    internal = {
        "bare_behavior": 100.0
        * heb["systems"]["bare_behavior"]["bounded_success_rate_among_accepted"],
        "haplous_mediated": 100.0
        * heb["systems"]["haplous_mediated"][
            "bounded_success_rate_among_accepted"
        ],
    }
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6))
    axes[0].bar(list(internal), list(internal.values()), color=["#999", "#2b6"])
    axes[0].set_title("Internal HEB (frozen held-out, N=38)")
    axes[0].set_ylabel("bounded success among accepted (%)")
    axes[0].set_ylim(0, 100)
    axes[1].bar(list(rob), [rob[s] for s in systems], color=["#999", "#bbb", "#2b6"])
    axes[1].set_title("robosuite NutAssemblySquare (FINAL, N=15)")
    axes[1].set_ylabel("bounded success (%)")
    axes[1].set_ylim(0, 100)
    for ax in axes:
        ax.tick_params(axis="x", labelsize=8, rotation=12)
    fig.tight_layout()
    save(fig, "01_bounded_success_by_system.svg")


def fig_hard_violations() -> None:
    heb = load("results/heb_final_summary.json")
    rob_final = load(
        "results/external_eval/robosuite-1.5.2/20260909T213850Z/summary.json"
    )
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6))
    axes[0].bar(
        ["Haplous mediated", "bare"],
        [
            heb["systems"]["haplous_mediated"]["breach_trials"],
            heb["systems"]["bare_behavior"]["breach_trials"],
        ],
        color=["#2b6", "#999"],
    )
    axes[0].set_title("Internal HEB breach trials (N=38)")
    axes[0].set_ylabel("trials breaching a hard requirement")
    axes[1].bar(
        ["direct", "strong_osc", "haplous_mediated"],
        [
            rob_final["systems"][s]["max_peak_force_n"]
            for s in ["direct_minimal", "strong_osc", "haplous_mediated"]
        ],
        color=["#999", "#bbb", "#2b6"],
    )
    axes[1].axhline(25.0, color="r", ls="--", lw=1, label="declared limit 25 N")
    axes[1].set_title("robosuite FINAL max peak force (N=15/system)")
    axes[1].set_ylabel("max peak |F| (N)")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    save(fig, "02_hard_violations_by_system.svg")


def fig_governor() -> None:
    x30 = load("results/x30_governor_summary.json")
    x31 = load("results/x31_jitter_summary.json")
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    cells = list(x30["cells"])
    g0 = [x30["cells"][c]["g0_fixed"]["bounded_success_rate"] * 100 for c in cells]
    g1 = [
        x30["cells"][c]["g1_force_governor"]["bounded_success_rate"] * 100
        for c in cells
    ]
    idx = range(len(cells))
    axes[0].bar([i - 0.2 for i in idx], g0, 0.4, label="G0 fixed impedance")
    axes[0].bar([i + 0.2 for i in idx], g1, 0.4, label="G1 governor")
    axes[0].set_xticks(list(idx))
    axes[0].set_xticklabels([c.replace("x30_", "") for c in cells], fontsize=8)
    axes[0].set_ylabel("bounded success (%)")
    axes[0].set_title("Governor microbench (X30)")
    axes[0].legend(fontsize=8)
    jcells = list(x31["cells"])
    jg0 = [x31["cells"][c]["g0_fixed"]["bounded_success_rate"] * 100 for c in jcells]
    jg1 = [
        x31["cells"][c]["g1_force_governor"]["bounded_success_rate"] * 100
        for c in jcells
    ]
    jidx = range(len(jcells))
    axes[1].bar([i - 0.2 for i in jidx], jg0, 0.4, label="G0")
    axes[1].bar([i + 0.2 for i in jidx], jg1, 0.4, label="G1")
    axes[1].set_xticks(list(jidx))
    axes[1].set_xticklabels([c.replace("x31_", "") for c in jcells], fontsize=7)
    axes[1].set_title("Timing stress (X31)")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    save(fig, "14_governor_stability_boundary.svg")


def fig_observers() -> None:
    x52 = load("results/x52_observer_summary.json")
    metrics = x52["metrics"]
    names = list(metrics)
    prec = [100.0 * metrics[n]["precision"] for n in names]
    rec = [100.0 * metrics[n]["recall"] for n in names]
    fig, ax = plt.subplots(figsize=(7, 3.6))
    y = range(len(names))
    ax.barh([i - 0.2 for i in y], prec, 0.4, label="precision")
    ax.barh([i + 0.2 for i in y], rec, 0.4, label="recall")
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=8)
    ax.set_xlabel("% (vs phase-label + force-threshold proxy GT)")
    ax.set_title("X52 observers on real telemetry (derivative subsets)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    save(fig, "15_observer_real_data_result.svg")


def fig_engineering_leverage() -> None:
    out = subprocess.run(
        ["git", "diff", "--numstat", "aa46c24^..HEAD", "--", "haplous"],
        capture_output=True, text=True, check=True, cwd=ROOT,
    ).stdout
    core_add = sum(
        int(line.split()[0]) for line in out.splitlines() if line.strip()
    )
    out_b = subprocess.run(
        ["git", "diff", "--numstat", "aa46c24^..HEAD", "--", "benchmarks", "scripts"],
        capture_output=True, text=True, check=True, cwd=ROOT,
    ).stdout
    bench_add = sum(
        int(line.split()[0]) for line in out_b.splitlines() if line.strip()
    )
    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.bar(
        ["haplous core (adapter boundary only)", "benchmarks + scripts"],
        [core_add, bench_add],
        color=["#2b6", "#999"],
    )
    ax.set_ylabel("added LOC over campaign")
    ax.set_title("Engineering leverage: core vs benchmark-layer additions")
    fig.tight_layout()
    save(fig, "16_engineering_leverage.svg")


if __name__ == "__main__":
    fig_bounded_success()
    fig_hard_violations()
    fig_governor()
    fig_observers()
    fig_engineering_leverage()
    print("done")
