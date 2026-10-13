"""Offline checks for the public aggregate Route B evidence copies."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
EXPECTED_SHA256 = {
    "FROZEN_COMPARISON_B.json": "ce7451d01574b796a084ee57b81c37771a343d96306f7c52cfde075b1750fa16",
    "THREE_ARM_FROZEN_TABLE_B.csv": "a898a703a6b30f9f9c6acf6453afc78dbc5e3da0e4f2897f2aa225397e8f8660",
    "THREE_ARM_FROZEN_TABLE_B.json": "ac63f178b11b05a9d1e228e730f9f6a6ee4624ed445bdce237deaccda5b328d1",
}


def fraction(raw: str) -> tuple[int, int]:
    numerator, denominator = raw.split("/", 1)
    return int(numerator), int(denominator)


def main() -> None:
    for name, expected in EXPECTED_SHA256.items():
        actual = hashlib.sha256((HERE / name).read_bytes()).hexdigest()
        assert actual == expected, f"SHA256 mismatch: {name}"

    with (HERE / "THREE_ARM_FROZEN_TABLE_B.csv").open(newline="", encoding="utf-8") as handle:
        rows = {row["metric"]: row for row in csv.DictReader(handle)}
    control = "S+Anchor-only-ck16"
    rl = "S+Anchor+GRPO-ck16"
    assert fraction(rows["task_success"][control]) == (78, 120)
    assert fraction(rows["task_success"][rl]) == (120, 120)
    assert fraction(rows["recovery_success"][control]) == (33, 75)
    assert fraction(rows["recovery_success"][rl]) == (75, 75)
    assert fraction(rows["CAP_correct_refusal"][rl]) == (15, 15)
    assert fraction(rows["DEAD_correct_refusal"][rl]) == (15, 15)
    assert fraction(rows["safety_episode"][rl]) == (0, 120)
    assert fraction(rows["unauthorized_episode"][rl]) == (0, 120)

    comparison = json.loads((HERE / "FROZEN_COMPARISON_B.json").read_text(encoding="utf-8"))
    assert comparison["baseline"] == control and comparison["candidate"] == rl
    task = comparison["metrics"]["task_success"]
    capacity = comparison["metrics"]["recovery_success"]
    for metric, before, after, n, ties, pp in (
        (task, 78, 120, 120, 78, 35.0),
        (capacity, 33, 75, 75, 33, 56.0),
    ):
        assert (metric["baseline_success"], metric["candidate_success"], metric["n"]) == (
            before, after, n
        )
        assert (metric["win"], metric["loss"], metric["tie"]) == (42, 0, ties)
        assert metric["delta_pp"] == pp == 100 * (after - before) / n
        assert metric["paired_bootstrap_95ci_pp"][0] < pp < metric["paired_bootstrap_95ci_pp"][1]

    mechanism = json.loads((HERE / "MECHANISM_SUMMARY.json").read_text(encoding="utf-8"))
    assert mechanism["capacity_tasks"] == 39 + 36 == 75
    assert mechanism["Anchor_only_vs_RL"]["paired_wins"] == 39 + 3 == 42
    assert sum(mechanism["RL_success_composition"][key] for key in (
        "SUCCESS", "CORRECT_REFUSAL", "CLARIFICATION"
    )) == 120
    print("PASS: three byte-identical aggregate copies and headline arithmetic")


if __name__ == "__main__":
    main()
