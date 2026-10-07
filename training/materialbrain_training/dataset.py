"""Verified expert episodes with immutable group-disjoint dataset files."""

from __future__ import annotations

import argparse
import copy
import random
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .common import canonical, digest, file_hash, group_id, read_json, write_json, write_jsonl
from .policy import messages_for, prompt_identity

INSTRUCTIONS = (
    "请按{profile_label}路线完成这些站点的物料交接，逐站扫码验证，最后返回待命点。目标：{goals}。",
    "这次要送到 {goals}，路线选{profile_label}。别跳过扫码和交接，结束后回 HOME。",
    "把合成备料任务送往{goals}；遵循{profile_label}约束，完成所有交接后回家并总结。",
    "先查库存和目标库位，再执行 {goals} 的任务。"
    "优先{profile_label}；任何异常要恢复，别写真实库存。",
    "完成 {goals} 的仓库任务。规划采用{profile_label}，每到一站核验扫码和交接，全部结束返回 HOME。",
    "Please complete handoffs at {goals} using profile {profile}; "
    "verify scans and human handoffs, then return HOME.",
)


def scenarios(snapshot: dict, groups: int, seed: int):
    if groups < 800 or groups % 10:
        raise ValueError("At least 800 groups and exact 80/10/10 integer membership are required")
    docks = [dock["id"] for dock in snapshot["map"]["docks"] if dock.get("kind") == "human_handoff"]
    if len(docks) < 3:
        raise ValueError("Snapshot must contain the accepted registered handoff docks")
    rows = []
    # Covers all six tiers. Long requests retain 1–20 occurrences and original
    # dock identity; environment batches repeat docks into successive missions.
    tiers = ["T0"] * 2 + ["T1"] + ["T2"] * 3 + ["T3"] + ["T4"] * 2 + ["T5"]
    labels = {"fastest": "最快", "safest": "最安全", "esd_safe": "防静电"}
    for index in range(groups):
        rng = random.Random(seed + index)
        tier = tiers[index % len(tiers)]
        count = rng.randint(5, 20) if tier == "T3" else rng.randint(1, 3)
        goals = (
            [rng.choice(docks) for _ in range(count)] if tier == "T3" else rng.sample(docks, count)
        )
        profile = rng.choice(list(labels))
        scenario = {
            "scenario_template": tier,
            "scenario_seed": seed + index,
            "inventory_snapshot_id": "SYN-" + digest([seed + index, "synthetic-inventory-v1"])[:20],
            "instruction": rng.choice(INSTRUCTIONS).format(
                goals="、".join(goals), profile=profile, profile_label=labels[profile]
            ),
            "goal_ids": goals,
            "profile": profile,
            "battery_pct": rng.randint(55, 100),
            "payload_kg": round(rng.uniform(0.1, 4.0), 2),
            "service_s": rng.choice([0, 2, 5, 10]),
            "priority": rng.randint(0, 100),
            "time_window_s": [0, rng.choice([3600, 7200, 12000])],
            "stock_status": "full",
            "ambiguity": False,
            "events": [],
            "max_steps": 180 if tier == "T3" else 24,
            "distribution": "standard",
            "source": "deterministic_synthetic_factory",
        }
        if tier == "T0":
            scenario["ambiguity"] = True
            scenario["instruction"] = rng.choice(
                [
                    "把它送到那个地方。",
                    "把之前那批材料送给他们。",
                    "去那个柜子拿几个回来。",
                    "Move those parts to the usual place.",
                ]
            )
        elif tier == "T1":
            scenario["terminal_skill"] = "query_spatial_context"
            scenario["instruction"] = "查询当前仓库的注册停靠点与可用能力，不启动任务。"
        elif tier == "T4":
            recovery = [
                "blocked_path",
                "wrong_scan",
                "handoff_delay",
                "low_battery",
                "missing_stock",
                "overload",
            ][(index // 10) % 6]
            if recovery == "low_battery":
                scenario["battery_pct"] = 16.5
                scenario["start_goal_id"] = "CHARGER"
            elif recovery == "missing_stock":
                scenario["stock_status"] = "missing" if index % 2 else "partial"
            elif recovery == "overload":
                scenario["payload_kg"] = 23.0
            else:
                scenario["events"] = [
                    {
                        "type": recovery,
                        "at_step": 6,
                        "goal_id": goals[0],
                        "reopen_after_s": 5,
                        "duration_s": 5,
                    }
                ]
            scenario["recovery_type"] = recovery
        rows.append(scenario)
    ids = [group_id(snapshot["map"]["revision"], row) for row in rows]
    if len(set(ids)) != groups:
        raise ValueError("Duplicate group identity")
    shuffled = sorted(ids)
    random.Random(seed).shuffle(shuffled)
    memberships = {}
    for split, members in (
        ("train", shuffled[: groups * 8 // 10]),
        ("validation", shuffled[groups * 8 // 10 : groups * 9 // 10]),
        ("test", shuffled[groups * 9 // 10 :]),
    ):
        memberships.update({identity: split for identity in members})
    for row, identity in zip(rows, ids, strict=True):
        row["group_id"] = identity
        row["split"] = memberships[identity]
        if row["scenario_template"] == "T5" and row["split"] != "train":
            row["distribution"] = "heldout_composition"
            row["events"] = [
                {"type": "wrong_scan", "at_step": 6, "goal_id": row["goal_ids"][0]},
                {
                    "type": "handoff_delay",
                    "at_step": 10,
                    "goal_id": row["goal_ids"][-1],
                    "duration_s": 10,
                },
            ]
            row["max_steps"] = 32
    return sorted(rows, key=lambda row: row["group_id"])


def _episode(arguments):
    from .environment import WarehouseTrainingEnv

    snapshot_path, scenario = arguments
    snapshot = read_json(snapshot_path)
    env = WarehouseTrainingEnv(snapshot_path)
    observation = env.reset(copy.deepcopy(scenario))
    records, transitions = [], []
    for step in range(scenario["max_steps"]):
        action = env.expert_action()
        before = copy.deepcopy(observation)
        observation, reward, terminated, truncated, info = env.step(action)
        record = {
            "record_id": scenario["group_id"] + f":{step}",
            "group_id": scenario["group_id"],
            "scenario_template": scenario["scenario_template"],
            "split": scenario["split"],
            "step": step,
            "messages": messages_for(snapshot, before)
            + [{"role": "assistant", "content": canonical(action)}],
            "observation": before,
            "action": action,
            "reward": reward,
            "verifier": info,
            "next_state_hash": digest(observation),
        }
        records.append(record)
        transitions.append(
            {
                "step": step,
                "before": digest(before),
                "action": action,
                "after": digest(observation),
                "reward": reward,
                "info": info,
            }
        )
        if terminated or truncated:
            break
    outcome = {
        "group_id": scenario["group_id"],
        "scenario_template": scenario["scenario_template"],
        "split": scenario["split"],
        "steps": len(records),
        "terminated": terminated,
        "truncated": truncated,
        "reward": env.get_reward(),
        "final_observation": observation,
        "transitions": transitions,
    }
    return scenario, records, outcome


def build(snapshot_path: Path, output: Path, *, groups=1200, seed=202610050000, workers=8):
    snapshot = read_json(snapshot_path)
    rows = scenarios(snapshot, groups, seed)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "DATASET_MANIFEST.json").exists():
        raise FileExistsError("Dataset already frozen; refusing a silent replacement")
    freeze = {
        "version": 1,
        "seed": seed,
        "policy": "seeded shuffle of sorted group IDs, exactly 80/10/10",
        "snapshot_sha256": file_hash(snapshot_path),
        "groups": {
            split: [row["group_id"] for row in rows if row["split"] == split]
            for split in ("train", "validation", "test")
        },
    }
    if (output / "FROZEN_SPLITS.json").exists() and read_json(
        output / "FROZEN_SPLITS.json"
    ) != freeze:
        raise RuntimeError("Frozen group membership cannot be replaced")
    write_json(output / "FROZEN_SPLITS.json", freeze)
    generated = []
    if workers > 1:
        with ProcessPoolExecutor(max_workers=min(16, workers)) as pool:
            for index, episode in enumerate(
                pool.map(_episode, [(str(snapshot_path.resolve()), row) for row in rows])
            ):
                generated.append(episode)
                if index % 25 == 0:
                    print(canonical({"generated_groups": index + 1, "target": groups}), flush=True)
    else:
        generated = [_episode((str(snapshot_path), row)) for row in rows]
    outcomes, records = [], []
    for scenario, decisions, outcome in generated:
        final_metrics = outcome["transitions"][-1]["info"].get("metrics", {})
        if (
            not final_metrics.get("task_success")
            or final_metrics.get("safety_violations")
            or final_metrics.get("unauthorized_actions")
        ):
            raise RuntimeError(
                "Expert verifier failed for group "
                + scenario["group_id"]
                + ": "
                + canonical(final_metrics)
            )
        if any(row["verifier"].get("category") for row in decisions):
            raise RuntimeError(
                "Rejected action cannot become an expert SFT label: " + scenario["group_id"]
            )
        records.extend(decisions)
        outcomes.append(outcome)
    if len(records) < 6000:
        raise RuntimeError(f"Only {len(records)} decisions; minimum gate is 6000")
    for split in ("train", "validation", "test"):
        write_jsonl(
            output / f"scenarios-{split}.jsonl", [row for row in rows if row["split"] == split]
        )
        write_jsonl(
            output / f"sft-{split}.jsonl", [row for row in records if row["split"] == split]
        )
        write_jsonl(
            output / f"episodes-{split}.jsonl", [row for row in outcomes if row["split"] == split]
        )
    write_jsonl(output / "scenarios-all.jsonl", rows)
    files = {
        path.name: {"sha256": file_hash(path), "bytes": path.stat().st_size}
        for path in sorted(output.iterdir())
        if path.is_file() and path.name not in {"DATASET_MANIFEST.json", "SPLIT_AUDIT.json"}
    }
    audit = {
        "status": "PASS",
        "group_count": len(rows),
        "unique_groups": len(set(row["group_id"] for row in rows)),
        "decision_count": len(records),
        "splits": {key: len(value) for key, value in freeze["groups"].items()},
        "pairwise_group_intersection": 0,
        "test_used_for_training": False,
        "split_manifest_sha256": file_hash(output / "FROZEN_SPLITS.json"),
    }
    write_json(output / "SPLIT_AUDIT.json", audit)
    write_json(
        output / "DATASET_MANIFEST.json",
        {
            "contract_version": snapshot["contract_version"],
            "source_sha": snapshot["source_sha"],
            "snapshot_sha256": file_hash(snapshot_path),
            "prompt_sha256": prompt_identity(snapshot),
            "map_revision": snapshot["map"]["revision"],
            "graph_hash": snapshot["map"]["provenance"]["graph_hash"],
            "groups": len(rows),
            "decisions": len(records),
            "tier_groups": dict(Counter(row["scenario_template"] for row in rows)),
            "maximum_requested_stop_occurrences": max(len(row["goal_ids"]) for row in rows),
            "synthetic_only": True,
            "production_inventory_written": False,
            "files": files,
            "split_audit_sha256": file_hash(output / "SPLIT_AUDIT.json"),
        },
    )
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--groups", type=int, default=1200)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()
    print(
        canonical(build(
            args.snapshot, args.output, groups=args.groups, workers=args.workers, seed=args.seed
        )),
        flush=True,
    )


if __name__ == "__main__":
    main()
