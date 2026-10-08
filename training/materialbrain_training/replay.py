"""Independently replay recorded decisions and verify every state transition."""
from __future__ import annotations

import argparse
import copy
from pathlib import Path

from .common import canonical, digest, read_jsonl, verify_dataset
from .environment import WarehouseTrainingEnv


def verify_episode(snapshot, scenario, episode):
    env = WarehouseTrainingEnv(snapshot)
    observation = env.reset(copy.deepcopy(scenario))
    terminated = truncated = False
    for index, recorded in enumerate(episode["transitions"]):
        if terminated or truncated or recorded["step"] != index:
            raise RuntimeError("Invalid transition order or terminal continuation")
        if digest(observation) != recorded["before"]:
            raise RuntimeError("Replay pre-state mismatch")
        observation, reward, terminated, truncated, info = env.step(recorded["action"])
        if digest(observation) != recorded["after"]:
            raise RuntimeError("Replay post-state mismatch")
        if reward != recorded["reward"] or canonical(info) != canonical(recorded["info"]):
            raise RuntimeError("Replay verifier result mismatch")
    if (terminated, truncated) != (episode["terminated"], episode["truncated"]):
        raise RuntimeError("Replay terminal-state mismatch")
    if digest(observation) != digest(episode["final_observation"]):
        raise RuntimeError("Replay final observation mismatch")
    return len(episode["transitions"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--split", choices=["train", "validation", "test"], default="test")
    args = parser.parse_args()
    verify_dataset(args.data, snapshot=args.snapshot)
    scenarios = {
        row["group_id"]: row
        for row in read_jsonl(args.data / f"scenarios-{args.split}.jsonl")
    }
    episodes = read_jsonl(args.data / f"episodes-{args.split}.jsonl")
    decisions = sum(
        verify_episode(args.snapshot, scenarios[row["group_id"]], row)
        for row in episodes
    )
    print(canonical({"episodes": len(episodes), "verified_decisions": decisions}))


if __name__ == "__main__":
    main()
