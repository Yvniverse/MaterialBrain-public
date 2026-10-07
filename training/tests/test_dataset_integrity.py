"""Reject cross-split data, changed artifacts, and unverifiable episode traces."""

import copy

import pytest

from training.materialbrain_training.common import (
    file_hash,
    group_id,
    read_json,
    read_jsonl,
    verify_dataset,
    write_json,
    write_jsonl,
)
from training.materialbrain_training.dataset import _episode
from training.materialbrain_training.policy import prompt_identity
from training.materialbrain_training.replay import verify_episode
from training.tests.test_environment import scenario_fixture, snapshot_fixture


@pytest.fixture
def dataset(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    value = snapshot_fixture()
    write_json(snapshot, value)
    directory = tmp_path / "data"
    scenarios = []
    splits = {}
    decisions = 0
    for index, split in enumerate(("train", "validation", "test")):
        scenario = scenario_fixture(scenario_seed=index + 1)
        identity = group_id(value["map"]["revision"], scenario)
        scenario.update(group_id=identity, split=split)
        _, records, episode = _episode((str(snapshot), scenario))
        splits[split] = [identity]
        scenarios.append(scenario)
        decisions += len(records)
        write_jsonl(directory / f"scenarios-{split}.jsonl", [scenario])
        write_jsonl(directory / f"sft-{split}.jsonl", records)
        write_jsonl(directory / f"episodes-{split}.jsonl", [episode])
    write_json(directory / "FROZEN_SPLITS.json", {"groups": splits})
    write_jsonl(directory / "scenarios-all.jsonl", scenarios)
    write_json(directory / "SPLIT_AUDIT.json", {"status": "PASS"})
    manifest = {
        "contract_version": value["contract_version"],
        "map_revision": value["map"]["revision"],
        "snapshot_sha256": file_hash(snapshot),
        "prompt_sha256": prompt_identity(value),
        "split_audit_sha256": file_hash(directory / "SPLIT_AUDIT.json"),
        "groups": 3,
        "decisions": decisions,
        "files": {
            path.name: {"sha256": file_hash(path)}
            for path in directory.iterdir()
            if path.name != "SPLIT_AUDIT.json"
        },
    }
    write_json(directory / "DATASET_MANIFEST.json", manifest)
    return directory, snapshot


def rehash(directory, name):
    path = directory / "DATASET_MANIFEST.json"
    manifest = read_json(path)
    manifest["files"][name]["sha256"] = file_hash(directory / name)
    write_json(path, manifest)


def test_dataset_and_episode_replay_are_consistent(dataset):
    directory, snapshot = dataset
    manifest = verify_dataset(directory, snapshot=snapshot)
    scenario = read_jsonl(directory / "scenarios-test.jsonl")[0]
    episode = read_jsonl(directory / "episodes-test.jsonl")[0]
    assert manifest["groups"] == 3
    assert verify_episode(snapshot, scenario, episode) == episode["steps"]


def test_changed_decision_is_rejected_before_training(dataset):
    directory, snapshot = dataset
    path = directory / "sft-train.jsonl"
    records = read_jsonl(path)
    records[0]["reward"] += 1
    write_jsonl(path, records)
    with pytest.raises(RuntimeError, match="artifact hash mismatch"):
        verify_dataset(directory, snapshot=snapshot)


def test_omitted_artifact_hash_is_rejected(dataset):
    directory, _ = dataset
    path = directory / "DATASET_MANIFEST.json"
    manifest = read_json(path)
    del manifest["files"]["sft-train.jsonl"]
    write_json(path, manifest)
    with pytest.raises(RuntimeError, match="omits required"):
        verify_dataset(directory)


def test_overlapping_group_splits_are_rejected_even_with_valid_hashes(dataset):
    directory, _ = dataset
    path = directory / "FROZEN_SPLITS.json"
    value = read_json(path)
    value["groups"]["test"] = value["groups"]["train"]
    write_json(path, value)
    rehash(directory, path.name)
    with pytest.raises(RuntimeError, match="groups overlap"):
        verify_dataset(directory)


def test_mislabelled_decision_split_is_rejected_even_with_valid_hashes(dataset):
    directory, _ = dataset
    path = directory / "sft-train.jsonl"
    records = read_jsonl(path)
    records[0]["split"] = "test"
    write_jsonl(path, records)
    rehash(directory, path.name)
    with pytest.raises(RuntimeError, match="Record split membership"):
        verify_dataset(directory)


def test_snapshot_change_and_frozen_receipt_change_are_rejected(dataset):
    directory, snapshot = dataset
    receipt = directory.parent / "receipt.json"
    write_json(receipt, {
        "dataset_manifest_sha256": file_hash(directory / "DATASET_MANIFEST.json"),
        "snapshot_sha256": file_hash(snapshot),
    })
    verify_dataset(directory, snapshot=snapshot, frozen_receipt=receipt)
    value = read_json(receipt)
    value["dataset_manifest_sha256"] = "0" * 64
    write_json(receipt, value)
    with pytest.raises(RuntimeError, match="input receipt mismatch"):
        verify_dataset(directory, snapshot=snapshot, frozen_receipt=receipt)
    value = read_json(snapshot)
    value["map"]["revision"] = "changed-map"
    write_json(snapshot, value)
    with pytest.raises(RuntimeError, match="snapshot hash mismatch"):
        verify_dataset(directory, snapshot=snapshot)


@pytest.mark.parametrize("field", ["before", "after", "reward", "info", "step", "terminal", "final"])
def test_replay_rejects_changed_execution(dataset, field):
    directory, snapshot = dataset
    scenario = read_jsonl(directory / "scenarios-test.jsonl")[0]
    episode = copy.deepcopy(read_jsonl(directory / "episodes-test.jsonl")[0])
    transition = episode["transitions"][0]
    if field in ("before", "after"):
        transition[field] = "0" * 64
    elif field == "reward":
        transition[field] += 1
    elif field == "info":
        transition[field]["verifier_code"] = "CHANGED"
    elif field == "step":
        transition[field] += 1
    elif field == "terminal":
        episode["terminated"] = not episode["terminated"]
    else:
        episode["final_observation"]["map_revision"] = "changed-map"
    with pytest.raises(RuntimeError):
        verify_episode(snapshot, scenario, episode)
