"""Render the committed Chunk 1 CSV results into documentation figures.

The CSV files in docs/data are the source of truth. This script performs no
simulation and contains no duplicated result constants beyond plot labels.
"""
from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
FIGURE_DIR = ROOT / "docs" / "figures"
FIGURE_DIR.mkdir(parents=True, exist_ok=True)

GREEN = "#25855A"
RED = "#C84A42"
BLUE = "#3569A8"
GOLD = "#D69E2E"
GRAY = "#D8DDE5"


def read_csv(name: str) -> list[dict[str, str]]:
    with (DATA_DIR / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def save(fig: plt.Figure, stem: str) -> None:
    fig.savefig(FIGURE_DIR / f"{stem}.png", dpi=180, bbox_inches="tight")
    svg_path = FIGURE_DIR / f"{stem}.svg"
    fig.savefig(svg_path, bbox_inches="tight")
    # Matplotlib emits spaces before newlines throughout SVG path data. Strip
    # them so generated evidence passes the repository's whitespace check.
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_path.read_text().splitlines()) + "\n",
        encoding="utf-8",
    )
    plt.close(fig)


def render_l1() -> None:
    rows = read_csv("chunk1_l1_attribution.csv")
    l1a = [row for row in rows if row["experiment"] == "L1a"]
    depths = [0.05, 0.50]
    stiffnesses = [500, 2000]
    matrix = np.full((len(depths), len(stiffnesses)), np.nan)
    rejected = np.zeros_like(matrix, dtype=bool)
    for row in l1a:
        i = depths.index(float(row["depth_commit_mm"]))
        j = stiffnesses.index(int(row["k_z_search_N_per_m"]))
        if row["rejected"] == "true":
            rejected[i, j] = True
        else:
            matrix[i, j] = int(row["captures"]) / int(row["trials"])

    def find(depth: str, seed: str, continuity: str) -> dict[str, str]:
        return next(
            row for row in rows
            if row["depth_commit_mm"] == depth
            and row["z_flat_seed"] == seed
            and row["force_continuous"] == continuity
            and row["experiment"] == "interaction"
        )

    interaction_rows = [
        ("All old", find("0.05", "mid-fall", "false")),
        ("Continuity\nonly", find("0.05", "mid-fall", "true")),
        ("CAD seed\nonly", find("0.05", "CAD", "false")),
        ("Winning pair +\nold datum/handoff", find("0.50", "mid-fall", "false")),
    ]

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6), constrained_layout=True)
    cmap = ListedColormap([RED, GOLD, GREEN])
    masked = np.ma.masked_invalid(matrix)
    axes[0].imshow(masked, vmin=0, vmax=1, cmap=cmap, aspect="auto")
    axes[0].set_xticks(range(len(stiffnesses)), stiffnesses)
    axes[0].set_yticks(range(len(depths)), [f"{value:.2f}" for value in depths])
    axes[0].set_xlabel("Search stiffness $k_{z,search}$ (N/m)")
    axes[0].set_ylabel("Commit depth (mm)")
    axes[0].set_title("Requested L1a grid at 2.0 mm")
    for i in range(len(depths)):
        for j in range(len(stiffnesses)):
            label = "REJECTED" if rejected[i, j] else f"{matrix[i, j] * 16:.0f}/16"
            axes[0].text(j, i, label, ha="center", va="center", fontweight="bold")
    axes[0].text(
        1,
        1.28,
        "Gate has no physical margin",
        ha="center",
        va="top",
        fontsize=8,
        color="#555555",
    )

    labels = [label for label, _ in interaction_rows]
    rates = [int(row["captures"]) / int(row["trials"]) * 100 for _, row in interaction_rows]
    colors = [RED, RED, GREEN, GREEN]
    bars = axes[1].bar(labels, rates, color=colors, width=0.68)
    axes[1].set_ylim(0, 108)
    axes[1].set_ylabel("Capture rate (%)")
    axes[1].set_title("Interaction localization")
    axes[1].axhline(100, color="#777777", linewidth=0.8, linestyle="--")
    axes[1].bar_label(bars, labels=[f"{value:.1f}%" for value in rates], padding=3)
    axes[1].tick_params(axis="x", labelsize=9)
    fig.suptitle("L1: CAD seeding repairs the historical premature-commit path", fontsize=14)
    save(fig, "chunk1_l1_attribution")


def render_l2() -> None:
    rows = read_csv("chunk1_l2_passive_grid.csv")
    offsets = np.array([float(row["offset_mm"]) for row in rows])
    rates = np.array([float(row["capture_rate"]) * 100 for row in rows])
    angle_columns = [
        "angle_0", "angle_45", "angle_90", "angle_135",
        "angle_180", "angle_225", "angle_270", "angle_315",
    ]
    angle_labels = ["0°", "45°", "90°", "135°", "180°", "225°", "270°", "315°"]
    grid = np.array([[int(row[column]) for column in angle_columns] for row in rows]).T

    fig, axes = plt.subplots(2, 1, figsize=(10.5, 7.2), constrained_layout=True)
    axes[0].plot(offsets, rates, marker="o", linewidth=2.2, color=BLUE)
    axes[0].axhline(50, color=GOLD, linestyle="--", linewidth=1.4, label="50% threshold")
    axes[0].axvline(2.0, color=RED, linestyle=":", linewidth=1.8, label="2.0 mm demo shift")
    axes[0].fill_between(offsets, rates, alpha=0.14, color=BLUE)
    axes[0].set_ylim(-5, 105)
    axes[0].set_xlim(0.95, 3.05)
    axes[0].set_ylabel("Passive capture rate (%)")
    axes[0].set_xlabel("Initial lateral offset (mm)")
    axes[0].set_title("Passive capture collapses between 1.50 and 1.75 mm")
    axes[0].legend(loc="upper right")
    axes[0].grid(axis="y", alpha=0.25)

    axes[1].imshow(grid, vmin=0, vmax=1, cmap=ListedColormap([RED, GREEN]), aspect="auto")
    axes[1].set_xticks(range(len(offsets)), [f"{value:.2f}" for value in offsets])
    axes[1].set_yticks(range(len(angle_labels)), angle_labels)
    axes[1].set_xlabel("Initial lateral offset (mm)")
    axes[1].set_ylabel("Approach angle")
    axes[1].set_title("Full L2 grid (green = seated, red = failed)")
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            axes[1].text(j, i, str(grid[i, j]), ha="center", va="center", color="white", fontsize=8)
    fig.suptitle("L2: $R_{passive}=1.50$ mm, so the 2.0 mm baseline fails", fontsize=14)
    save(fig, "chunk1_l2_passive_capture")


def render_l3() -> None:
    rows = read_csv("chunk1_l3_control_rate.csv")
    labels = [
        f"{row['control_rate_Hz']} Hz\n{row['delay_cycles']} cycle delay"
        for row in rows
    ]
    rates = [int(row["captures"]) / int(row["trials"]) * 100 for row in rows]

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    bars = axes[0].bar(labels, rates, color=[GREEN if value == 100 else RED for value in rates])
    axes[0].set_ylim(0, 108)
    axes[0].set_ylabel("Completed capture rate (%)")
    axes[0].set_title("Only the 2000 Hz controller completes")
    axes[0].bar_label(
        bars,
        labels=["16/16" if value == 100 else "0/16\n16 asserts" for value in rates],
        padding=3,
    )
    axes[0].tick_params(axis="x", labelsize=9)

    unstable = [row for row in rows if row["assertions"] != "0"]
    x = np.arange(len(unstable))
    low = np.array([float(row["assert_tilt_min_deg"]) for row in unstable])
    high = np.array([float(row["assert_tilt_max_deg"]) for row in unstable])
    mid = (low + high) / 2
    axes[1].errorbar(
        x,
        mid,
        yerr=np.vstack((mid - low, high - mid)),
        fmt="o",
        markersize=8,
        capsize=7,
        color=RED,
        ecolor=RED,
        linewidth=2,
    )
    axes[1].axhline(10, color="#222222", linestyle="--", linewidth=1.3, label="10° safety guard")
    axes[1].set_xticks(
        x,
        [f"{row['control_rate_Hz']} Hz\n{row['delay_cycles']} cycle delay" for row in unstable],
    )
    axes[1].set_ylim(8, 24)
    axes[1].set_ylabel("Tilt reported at assertion (degrees)")
    axes[1].set_title("First failure is coupled tilt at plate contact")
    axes[1].legend(loc="upper left")
    axes[1].grid(axis="y", alpha=0.25)
    fig.suptitle("L3: sampled control is not hardware-plausible at current gains", fontsize=14)
    save(fig, "chunk1_l3_control_rate")


def main() -> None:
    plt.rcParams.update({
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    render_l1()
    render_l2()
    render_l3()


if __name__ == "__main__":
    main()
