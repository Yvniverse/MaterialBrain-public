"""Draw the public fresh TEST120 four-arm figure from aggregate counts only."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent
COLORS = ("#637486", "#3975A1", "#087F79", "#2F9B91")
ARMS = ("S", "Anchor-ck16", "RL-seed20261008-ck16", "RL-seed20261014-ck16")


def main() -> None:
    with (RESULTS / "FRESH_TEST120_ARMS.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if tuple(row["arm"] for row in rows) != ARMS:
        raise ValueError("Expected S, Anchor and two predeclared GRPO seeds")
    summary = json.loads((RESULTS / "FRESH_TEST120_AGGREGATE.json").read_text(encoding="utf-8"))
    if summary["declared_training_seed_denominator"] != 3 or len(summary["completed_training_seeds"]) != 2:
        raise ValueError("Unexpected seed denominator")

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "axes.edgecolor": "#8FA1AE",
        "axes.labelcolor": "#304455",
        "text.color": "#203747",
        "xtick.color": "#506576",
        "ytick.color": "#506576",
        "grid.color": "#DCE5EA",
        "grid.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "svg.fonttype": "none",
        "svg.hashsalt": "materialbrain-fresh-test120-20261014",
    })
    fig, axes = plt.subplots(1, 2, figsize=(12.3, 5.3))
    fig.subplots_adjust(left=0.07, right=0.98, top=0.72, bottom=0.26, wspace=0.2)
    fig.suptitle("Fresh TEST120: two GRPO training seeds", fontsize=18, fontweight="bold", x=0.07, ha="left", y=0.96)
    fig.text(0.07, 0.885, "Same 120 frozen critical-state recovery tasks · S / Anchor controls · no inference Shield", fontsize=10.3, color="#506576")
    for ax, metric, title, subtitle in zip(
        axes,
        ("Task", "Capacity"),
        ("Task success", "Feasible capacity recovery"),
        ("All 120 fixed tasks", "Pre-fixed 75 feasible capacity tasks"),
    ):
        vals = [(int(row[metric]), int(row[f"{metric}_denominator"])) for row in rows]
        expected_denominator = 120 if metric == "Task" else 75
        if any(d != expected_denominator or not 0 <= n <= d for n, d in vals):
            raise ValueError(f"Unexpected {metric} numerator or denominator")
        bars = ax.bar(range(4), [100 * n / d for n, d in vals], width=0.63, color=COLORS, zorder=3)
        for bar, (n, d) in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2,
                    f"{n}/{d}\n{100 * n / d:.1f}%", ha="center", va="bottom", fontweight="bold", fontsize=9.5)
        ax.set_title(title, loc="left", fontweight="bold", pad=14)
        ax.text(0, 1.04, subtitle, transform=ax.transAxes, color="#607484", fontsize=9.1)
        ax.set_xticks(range(4), [row["label"] for row in rows])
        ax.set_ylim(0, 116)
        ax.set_yticks([0, 20, 40, 60, 80, 100])
        ax.set_ylabel("Success (%)")
        ax.grid(axis="y", zorder=0)
        ax.tick_params(axis="x", length=0, pad=8)
        delta = 100 * (vals[2][0] - vals[1][0]) / expected_denominator
        if vals[2] != vals[3]:
            raise ValueError(f"Expected equal raw {metric} counts for the two GRPO seeds")
        first = summary["seed20261008"]["anchor_to_grpo"][metric]
        second = summary["seed20261014"]["anchor_to_grpo"][metric]
        wlt_values = (first["wins"], first["losses"], first["ties"])
        if wlt_values != (second["wins"], second["losses"], second["ties"]):
            raise ValueError(f"Expected equal raw {metric} paired counts")
        wlt = " / ".join(str(value) for value in wlt_values)
        ax.text(0.5, -0.21, f"Anchor → each GRPO: +{delta:.2f} pp · paired W / L / T {wlt}",
                transform=ax.transAxes, ha="center", va="top", fontsize=9.2,
                bbox={"boxstyle": "round,pad=0.4", "fc": "#EDF5F3", "ec": "#C9DDD8"})
    if summary["independent_audit"]["status"] != "PASS_BOUNDED_COMPLETED_TEST_RECORD_AND_STATISTICS_AUDIT":
        raise ValueError("Expected the completed independent TEST audit")
    status_note = "Both seeds passed CPU replay and independent audit; same 120 tasks, not 240 independent samples."
    fig.text(0.07, 0.04, f"Declared training seeds: 2/3 completed; seed 20261013 infra-failed. {status_note}",
             fontsize=9.1, color="#607484")
    fig.savefig(HERE / "fresh_test120_four_arm.png", dpi=220, facecolor="white")
    fig.savefig(HERE / "fresh_test120_four_arm.svg", facecolor="white", metadata={"Date": "2026-10-14"})
    plt.close(fig)


if __name__ == "__main__":
    main()
