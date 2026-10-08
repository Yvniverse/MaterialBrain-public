"""Executable observed-episode JSONL/SFT/GRPO preparation (no model training).

Usage: python -m app.agent.spatial_agent.dataset --input saved-episodes.jsonl
       --output-dir dataset
Only saved observed event episodes are accepted; this module creates no runs.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from .episodes import verifier_reward

_INCIDENTAL = {
    "episode_id",
    "mission_id",
    "conversation_id",
    "event_id",
    "timestamp",
    "idempotency_key",
    "content_sha256",
    "split_group_sha256",
    "split",
}


def _semantic(value):
    if isinstance(value, dict):
        return {key: _semantic(item) for key, item in value.items() if key not in _INCIDENTAL}
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    return value


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def split_group(episode: dict) -> str:
    """All frames from one map/scenario seed stay in one deterministic split."""
    return _hash(
        [
            episode["map_revision"],
            episode.get("scenario_id", "registered_mission"),
            str(episode.get("scenario_seed", "default")),
        ]
    )


def dataset_split(episode: dict) -> str:
    bucket = int(split_group(episode)[:12], 16) % 100
    return "train" if bucket < 80 else "validation" if bucket < 90 else "test"


def validate_episode(episode: dict) -> None:
    if episode.get("source") != "observed_execution_events" or not episode.get("map_revision"):
        raise ValueError("OBSERVED_EPISODE_REQUIRED")
    if not episode.get("steps"):
        raise ValueError("OBSERVED_EPISODE_HAS_NO_STEPS")
    if episode.get("execution_boundary") != "ros2_nav2_simulation" or episode.get(
        "inventory_written"
    ):
        raise ValueError("EPISODE_EXECUTION_BOUNDARY")
    previous = -1
    for step in episode["steps"]:
        outcome = step.get("outcome") or {}
        sequence = step.get("sequence", -1)
        if (
            step.get("source") != "observed_execution_event"
            or not outcome.get("event_id")
            or not outcome.get("timestamp")
            or not outcome.get("type")
            or sequence <= previous
            or outcome.get("sequence") != sequence
        ):
            raise ValueError("OBSERVED_STEP_PROVENANCE_REQUIRED")
        previous = sequence


def deduplicate_episodes(episodes: list[dict]) -> list[dict]:
    seen, result = set(), []
    for episode in episodes:
        validate_episode(episode)
        digest = _hash(_semantic(episode))
        if digest in seen:
            continue
        seen.add(digest)
        record = copy.deepcopy(episode)
        record["content_sha256"] = digest
        record["split_group_sha256"] = split_group(record)
        record["split"] = dataset_split(record)
        record["reward"] = verifier_reward(record)
        result.append(record)
    return result


def build_sft_records(episodes: list[dict]) -> list[dict]:
    result = []
    for episode in deduplicate_episodes(episodes):
        for step in episode["steps"]:
            expert = step["expert_skill"]
            outcome_type = step["outcome"]["type"]
            # A rejected scan, failed navigation or pause remains verifier data,
            # rather than teaching a future model that a failed action succeeded.
            if outcome_type in {"scan_rejected", "failed", "transport_paused", "cancelled"}:
                continue
            result.append(
                {
                    "schema_version": 1,
                    "episode_id": episode["episode_id"],
                    "sequence": step["sequence"],
                    "instruction": episode["instruction"],
                    "state": copy.deepcopy(step["state"]),
                    "available_skills": copy.deepcopy(episode["available_skills"]),
                    "chosen_skill": copy.deepcopy(step["chosen_skill"]),
                    "expert_skill": copy.deepcopy(expert),
                    "outcome": copy.deepcopy(step["outcome"]),
                    "verifier_fields": copy.deepcopy(step["verifier"]),
                    "difficulty": episode["difficulty"],
                    "map_revision": episode["map_revision"],
                    "scenario_id": episode["scenario_id"],
                    "scenario_seed": episode["scenario_seed"],
                    "split": episode["split"],
                    "split_group_sha256": episode["split_group_sha256"],
                    "episode_content_sha256": episode["content_sha256"],
                    "source": "observed_execution_event",
                }
            )
    return result


def build_grpo_records(episodes: list[dict]) -> list[dict]:
    return [
        {
            "schema_version": 1,
            "episode_id": episode["episode_id"],
            "instruction": episode["instruction"],
            "initial_state": episode["initial_state"],
            "available_skills": episode["available_skills"],
            "observed_trajectory": episode["steps"],
            "verifier_reward": episode["reward"],
            "map_revision": episode["map_revision"],
            "scenario_id": episode["scenario_id"],
            "scenario_seed": episode["scenario_seed"],
            "difficulty": episode["difficulty"],
            "split": episode["split"],
            "split_group_sha256": episode["split_group_sha256"],
            "content_sha256": episode["content_sha256"],
            "training_performed": False,
        }
        for episode in deduplicate_episodes(episodes)
    ]


def _read(path):
    content = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in content.splitlines() if line.strip()]
    value = json.loads(content)
    return value if isinstance(value, list) else [value]


def _write_jsonl(path, records):
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def export_dataset(episodes: list[dict], output_dir: Path) -> dict:
    unique = deduplicate_episodes(episodes)
    records = build_sft_records(unique)
    grpo = build_grpo_records(unique)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "episodes.jsonl", unique)
    for split in ("train", "validation", "test"):
        _write_jsonl(
            output_dir / f"sft-{split}.jsonl",
            [record for record in records if record["split"] == split],
        )
    _write_jsonl(output_dir / "grpo-ready.jsonl", grpo)
    manifest = {
        "schema_version": 1,
        "input_episodes": len(episodes),
        "unique_episodes": len(unique),
        "duplicates_removed": len(episodes) - len(unique),
        "sft_records": len(records),
        "grpo_records": len(grpo),
        "split_policy": "sha256(map_revision,scenario_id,scenario_seed)",
        "reward_version": "spatial_verifier_v1",
        "training_performed": False,
        "files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(output_dir.glob("*.jsonl"))
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = export_dataset(_read(args.input), args.output_dir)
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
