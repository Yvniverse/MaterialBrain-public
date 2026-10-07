"""Canonical artifacts and deterministic dataset split identities."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def canonical(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_jsonl(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def write_jsonl(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(canonical(value) + "\n")
    os.replace(temporary, path)


def group_id(map_revision: str, scenario: dict) -> str:
    return digest(
        [
            map_revision,
            scenario["scenario_template"],
            scenario["scenario_seed"],
            scenario["inventory_snapshot_id"],
        ]
    )


def verify_dataset(directory, *, snapshot=None, frozen_receipt=None):
    """Verify artifact hashes and group membership before training or replay."""
    directory = Path(directory).resolve()
    manifest = read_json(directory / "DATASET_MANIFEST.json")
    if manifest["contract_version"] != "materialbrain-warehousebench-v1":
        raise RuntimeError("Unsupported dataset contract")
    required = {"FROZEN_SPLITS.json", "scenarios-all.jsonl"} | {
        f"{prefix}-{split}.jsonl"
        for prefix in ("scenarios", "sft", "episodes")
        for split in ("train", "validation", "test")
    }
    if not required.issubset(manifest["files"]):
        raise RuntimeError("Dataset manifest omits required artifacts")
    for relative, expected in manifest["files"].items():
        target = (directory / relative).resolve()
        if not target.is_relative_to(directory) or file_hash(target) != expected["sha256"]:
            raise RuntimeError(f"Dataset artifact hash mismatch: {relative}")
    if file_hash(directory / "SPLIT_AUDIT.json") != manifest["split_audit_sha256"]:
        raise RuntimeError("Split audit hash mismatch")
    splits = read_json(directory / "FROZEN_SPLITS.json")["groups"]
    groups = {name: set(splits[name]) for name in ("train", "validation", "test")}
    if any(len(groups[name]) != len(splits[name]) for name in groups):
        raise RuntimeError("Duplicate split group")
    if any(groups[a] & groups[b] for a, b in (
        ("train", "validation"), ("train", "test"), ("validation", "test")
    )):
        raise RuntimeError("Dataset split groups overlap")
    decision_count = 0
    for split, identities in groups.items():
        scenarios = read_jsonl(directory / f"scenarios-{split}.jsonl")
        if len(scenarios) != len(identities) or {row["group_id"] for row in scenarios} != identities:
            raise RuntimeError("Scenario split membership mismatch")
        if any(group_id(manifest["map_revision"], row) != row["group_id"] for row in scenarios):
            raise RuntimeError("Scenario group identity mismatch")
        for prefix in ("scenarios", "sft", "episodes"):
            records = read_jsonl(directory / f"{prefix}-{split}.jsonl")
            for row in records:
                if row["split"] != split or row["group_id"] not in identities:
                    raise RuntimeError("Record split membership mismatch")
            if prefix == "episodes" and (
                len(records) != len(identities)
                or {row["group_id"] for row in records} != identities
            ):
                raise RuntimeError("Episode group coverage mismatch")
            if prefix == "sft":
                if {row["group_id"] for row in records} != identities:
                    raise RuntimeError("Decision group coverage mismatch")
                if len({row["record_id"] for row in records}) != len(records):
                    raise RuntimeError("Duplicate decision record")
                decision_count += len(records)
    if sum(map(len, groups.values())) != manifest["groups"] or decision_count != manifest["decisions"]:
        raise RuntimeError("Dataset record count mismatch")
    if snapshot is not None:
        from .policy import prompt_identity

        value = read_json(snapshot)
        if file_hash(snapshot) != manifest["snapshot_sha256"]:
            raise RuntimeError("Dataset snapshot hash mismatch")
        if prompt_identity(value) != manifest["prompt_sha256"]:
            raise RuntimeError("Dataset policy prompt mismatch")
        if value["map"]["revision"] != manifest["map_revision"]:
            raise RuntimeError("Dataset map revision mismatch")
    if frozen_receipt is not None:
        receipt = read_json(frozen_receipt)
        if file_hash(directory / "DATASET_MANIFEST.json") != receipt["dataset_manifest_sha256"]:
            raise RuntimeError("Dataset input receipt mismatch")
        if snapshot is not None and file_hash(snapshot) != receipt["snapshot_sha256"]:
            raise RuntimeError("Snapshot input receipt mismatch")
    return manifest
