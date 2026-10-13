"""Reproduce the public frozen comparison and original formal-16 TRAIN plots.

Run from any directory: python path/to/figures/make_figures.py
Optional source extraction: --extract-summary path/to/TRAINING_CURVE_SUMMARY.json
The extracted public input contains selected numeric fields, not local paths or IDs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np


HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent
CURVE_INPUT = HERE / "training_curve_inputs.json"
SOURCE_SUMMARY_SHA256 = "6581acdb94052a24b2c4a1bb95f00704e730048887eef6ae03702bde66a11d2e"
TABLE_SHA256 = "a898a703a6b30f9f9c6acf6453afc78dbc5e3da0e4f2897f2aa225397e8f8660"
PAIRED_SHA256 = "ce7451d01574b796a084ee57b81c37771a343d96306f7c52cfde075b1750fa16"
COLORS = {"S": "#637486", "Anchor": "#3975A1", "GRPO": "#087F79"}
ZERO_COLOR = "#B57640"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_sha(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"Unexpected SHA256 for {path.name}: {actual}")


def save_plot(fig: plt.Figure, stem: str) -> None:
    fig.savefig(HERE / f"{stem}.png", dpi=220, facecolor="white")
    fig.savefig(HERE / f"{stem}.svg", facecolor="white", metadata={"Date": "2026-10-13"})
    plt.close(fig)


def extract_summary(path: Path) -> None:
    assert_sha(path, SOURCE_SUMMARY_SHA256)
    source = json.loads(path.read_text(encoding="utf-8"))
    selected = []
    for row in source["per_update"]:
        selected.append({
            "update": row["update"],
            "template": row["template_id"],
            "train_success": row["TRAIN_task_success_numerator"],
            "train_n": row["TRAIN_denominator"],
            "terminal_reward_sd": row["terminal_reward_std"],
            "reward_gradient_norm": row["reward_gradient_norm"],
            "anchor_gradient_norm": row["anchor_gradient_norm"],
            "kl_gradient_norm": row["KL_gradient_norm"],
            "accepted_reward_update": row["accepted_reward_update"],
            "cumulative_accepted_reward_updates": row["cumulative_accepted_reward_updates"],
        })
    if len(selected) != 16 or sum(int(r["accepted_reward_update"]) for r in selected) != 12:
        raise ValueError("Unexpected formal-run counts")
    if [r["update"] for r in selected if r["reward_gradient_norm"] == 0] != [12, 14, 15, 16]:
        raise ValueError("Unexpected zero-gradient updates")
    output = {
        "kind": "FORMAL16_PUBLIC_PLOT_INPUTS",
        "source_summary_sha256": SOURCE_SUMMARY_SHA256,
        "interpretation": "Each update uses a different pre-fixed TRAIN group; these are not common-set validation scores.",
        "updates": selected,
    }
    CURVE_INPUT.write_text(json.dumps(output, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def style() -> None:
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
        "svg.hashsalt": "materialbrain-original-frozen-figures-20261013",
    })


def frozen_comparison() -> None:
    table_path = RESULTS / "THREE_ARM_FROZEN_TABLE_B.csv"
    paired_path = RESULTS / "FROZEN_COMPARISON_B.json"
    assert_sha(table_path, TABLE_SHA256)
    assert_sha(paired_path, PAIRED_SHA256)
    with table_path.open(encoding="utf-8", newline="") as handle:
        rows = {row["metric"]: row for row in csv.DictReader(handle)}
    paired = json.loads(paired_path.read_text(encoding="utf-8"))["metrics"]
    arms = [("S", "S"), ("S+Anchor-only-ck16", "Anchor-only"), ("S+Anchor+GRPO-ck16", "Anchor+GRPO")]
    metrics = [
        ("task_success", "Task success", "All frozen critical-state tasks"),
        ("recovery_success", "Capacity recovery", "Pre-fixed feasible capacity subset"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(11.7, 5.0), constrained_layout=False)
    fig.subplots_adjust(left=0.075, right=0.98, top=0.72, bottom=0.265, wspace=0.20)
    fig.suptitle("Frozen three-arm comparison", fontsize=18, fontweight="bold", x=0.075, ha="left", y=0.955)
    fig.text(0.075, 0.885, "Original TEST120 · verifier-defined outcomes · no inference Shield", fontsize=10.5, color="#506576")
    for ax, (key, title, subtitle) in zip(axes, metrics):
        vals = []
        for arm, _ in arms:
            numerator, denominator = map(int, rows[key][arm].split("/"))
            vals.append((numerator, denominator))
        bars = ax.bar(range(3), [100 * n / d for n, d in vals], width=0.62,
                      color=[COLORS["S"], COLORS["Anchor"], COLORS["GRPO"]], zorder=3)
        for bar, (n, d) in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2.2,
                    f"{n}/{d}\n{100*n/d:.0f}%", ha="center", va="bottom", fontweight="bold", fontsize=10)
        ax.set_title(title, loc="left", fontweight="bold", pad=14)
        ax.text(0, 1.04, subtitle, transform=ax.transAxes, color="#607484", fontsize=9.1)
        ax.set_xticks(range(3), [name for _, name in arms])
        ax.set_ylim(0, 116)
        ax.set_yticks([0, 20, 40, 60, 80, 100])
        ax.set_ylabel("Success (%)")
        ax.grid(axis="y", zorder=0)
        ax.tick_params(axis="x", length=0, pad=8)
        comparison = paired[key]
        delta = comparison["delta_pp"]
        lower, upper = comparison["paired_bootstrap_95ci_pp"]
        ax.text(0.5, -0.19, f"Anchor-only → GRPO: +{delta:g} pp\nPaired bootstrap 95% CI [{lower:.2f}, {upper:.2f}] pp",
                transform=ax.transAxes, ha="center", va="top", fontsize=9.7, color="#203747",
                bbox={"boxstyle": "round,pad=0.45", "fc": "#EDF5F3", "ec": "#C9DDD8"})
    save_plot(fig, "frozen_three_arm")


def formal16_training() -> None:
    curve = json.loads(CURVE_INPUT.read_text(encoding="utf-8"))
    if curve["source_summary_sha256"] != SOURCE_SUMMARY_SHA256:
        raise ValueError("Unrecognized TRAIN plot input")
    rows = curve["updates"]
    x = np.array([r["update"] for r in rows], dtype=int)
    if list(x) != list(range(1, 17)):
        raise ValueError("Expected original formal 16 updates")
    zero_updates = [int(r["update"]) for r in rows if float(r["reward_gradient_norm"]) == 0]
    if zero_updates != [12, 14, 15, 16]:
        raise ValueError("Unexpected zero-gradient steps")
    success = np.array([r["train_success"] for r in rows], dtype=float)
    n = np.array([r["train_n"] for r in rows], dtype=float)
    std = np.array([r["terminal_reward_sd"] for r in rows], dtype=float)
    reward = np.array([r["reward_gradient_norm"] for r in rows], dtype=float)
    anchor = np.array([r["anchor_gradient_norm"] for r in rows], dtype=float)
    kl = np.array([r["kl_gradient_norm"] for r in rows], dtype=float)
    accepted = np.array([r["cumulative_accepted_reward_updates"] for r in rows], dtype=float)
    if np.any(n != 8) or accepted[-1] != 12:
        raise ValueError("Unexpected TRAIN denominator or accepted-update count")
    family = {"batch_reload": ("B", "#667C8B"), "priority_batch": ("P", "#699BC0"), "depot_reload": ("D", "#2F9B91")}

    fig, axes = plt.subplots(4, 1, figsize=(11.7, 10.5), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1, 1.55, 0.9]})
    fig.subplots_adjust(left=0.092, right=0.975, top=0.84, bottom=0.13, hspace=0.38)
    fig.suptitle("Formal 16-step run: original TRAIN diagnostics", x=0.092, y=0.969,
                 ha="left", fontsize=18, fontweight="bold")
    fig.text(0.092, 0.925, "One distinct, pre-fixed TRAIN group per update (G8); repeated template families B / P / D.",
             fontsize=10.5, color="#506576")
    fig.text(0.092, 0.897, "Per-update TRAIN success is not a common-set validation curve; no new seed is plotted.",
             fontsize=10.5, color="#506576")

    for ax in axes:
        for update in zero_updates:
            ax.axvspan(update - 0.43, update + 0.43, color="#FAF0E5", zorder=0)
        ax.set_xlim(0.5, 16.5)
        ax.grid(axis="y", zorder=0)
        ax.set_axisbelow(True)

    ax = axes[0]
    ax.bar(x, success, color=[family[r["template"]][1] for r in rows], width=0.62, zorder=3)
    ax.set_ylim(0, 8.7)
    ax.set_yticks([0, 2, 4, 6, 8])
    ax.set_ylabel("Success / 8")
    ax.set_title("A  TRAIN task success by distinct group", loc="left", fontweight="bold", pad=8)
    for xi, yi in zip(x, success):
        ax.text(xi, yi + 0.15, f"{int(yi)}", ha="center", va="bottom", fontsize=7.7, color="#304455")

    ax = axes[1]
    ax.vlines(x, 0, std, color="#8295A3", linewidth=1.35, zorder=2)
    ax.scatter(x, std, color=COLORS["GRPO"], s=30, zorder=3)
    ax.set_ylim(0, 53)
    ax.set_yticks([0, 15, 30, 45])
    ax.set_ylabel("Reward SD")
    ax.set_title("B  Terminal reward spread within each G8", loc="left", fontweight="bold", pad=8)

    ax = axes[2]
    reward_plot = np.where(reward > 0, reward, np.nan)
    ax.plot(x, reward_plot, "o-", color=COLORS["GRPO"], linewidth=1.7, markersize=4.5, label="Reward", zorder=3)
    ax.plot(x, anchor, "s-", color=COLORS["Anchor"], linewidth=1.4, markersize=4, label="Anchor", zorder=2)
    ax.plot(x, kl, "^-", color=COLORS["S"], linewidth=1.3, markersize=4, label="KL", zorder=2)
    ax.scatter(zero_updates, [1.8e-6] * len(zero_updates), facecolors="white", edgecolors=ZERO_COLOR,
               linewidths=1.7, s=58, marker="o", zorder=5, label="Reward = 0")
    ax.set_yscale("log")
    ax.set_ylim(1e-6, 10)
    ax.set_yticks([1e-6, 1e-4, 1e-2, 1, 10])
    ax.yaxis.set_major_formatter(mticker.LogFormatterMathtext())
    ax.set_ylabel("Gradient norm (log)")
    ax.set_title("C  Measured reward / Anchor / KL gradient norms", loc="left", fontweight="bold", pad=8)
    ax.legend(loc="upper right", ncol=4, fontsize=8.7, frameon=True, facecolor="white", edgecolor="#DCE5EA")
    ax.text(0.015, 0.065, "Open circles at axis floor mark exact zero; floor is not the measured value.",
            transform=ax.transAxes, fontsize=8.2, color=ZERO_COLOR,
            bbox={"fc": "white", "ec": "none", "alpha": 0.92})

    ax = axes[3]
    ax.step(x, accepted, where="mid", color=COLORS["GRPO"], linewidth=2.1)
    ax.scatter(x, accepted, color=COLORS["GRPO"], s=22, zorder=4)
    ax.scatter(zero_updates, [accepted[i - 1] for i in zero_updates], facecolors="white",
               edgecolors=ZERO_COLOR, linewidths=1.6, s=58, zorder=5)
    ax.set_ylim(0, 13.1)
    ax.set_yticks([0, 4, 8, 12])
    ax.set_ylabel("Accepted (cum.)")
    ax.set_title("D  Accepted nonzero reward-gradient updates: 12 / 16", loc="left", fontweight="bold", pad=8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{r['update']}\n{family[r['template']][0]}" for r in rows], fontsize=8)
    ax.set_xlabel("Optimizer update / TRAIN template (B=batch reload, P=priority batch, D=depot reload)", labelpad=9)
    fig.text(0.092, 0.038, "Ochre columns: exact zero reward gradient at updates 12, 14, 15, 16. Anchor and KL gradients remain measured.",
             fontsize=9, color="#6C5B4D")
    save_plot(fig, "formal16_train_diagnostics")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract-summary", type=Path, help="Optional original formal-16 summary to extract")
    args = parser.parse_args()
    if args.extract_summary:
        extract_summary(args.extract_summary)
    if not CURVE_INPUT.exists():
        raise FileNotFoundError("Run once with --extract-summary, or use the supplied public training_curve_inputs.json")
    style()
    frozen_comparison()
    formal16_training()
    output_hashes = {p.name: sha256(p) for p in sorted(HERE.glob("*.png")) + sorted(HERE.glob("*.svg"))}
    manifest = {
        "kind": "MATERIALBRAIN_ORIGINAL_RESULT_FIGURES",
        "source_frozen_table_sha256": TABLE_SHA256,
        "source_paired_comparison_sha256": PAIRED_SHA256,
        "source_formal16_summary_sha256": SOURCE_SUMMARY_SHA256,
        "public_training_curve_inputs_sha256": sha256(CURVE_INPUT),
        "reproduction_script_sha256": sha256(Path(__file__)),
        "matplotlib_version": matplotlib.__version__,
        "numpy_version": np.__version__,
        "outputs_sha256": output_hashes,
        "scope": "Original frozen TEST and original formal16 TRAIN only; no added seeds or Nav2 comparison.",
    }
    (HERE / "FIGURE_MANIFEST.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
