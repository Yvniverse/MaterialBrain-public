"""Freeze append-only runtime evidence into a stable, hashed acceptance bundle."""

import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze_jsonl(source, target):
    # Copy a complete prefix while the ROS bridge continues publishing later records.
    data = source.read_bytes()
    data = data[: data.rfind(b"\n") + 1]
    records = [json.loads(line) for line in data.splitlines()]
    target.write_bytes(data)
    return records


def bundle(evidence, output, base_url):
    source = Path(evidence)
    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    with urlopen(base_url + "/health", timeout=15) as response:
        health = json.load(response)
    topics = freeze_jsonl(source / "topics.jsonl", target / "topics.jsonl")
    events = freeze_jsonl(source / "events.jsonl", target / "events.jsonl")
    results = []
    for path in sorted((source / "results").glob("*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        results.append(result)
        (target / path.name).write_bytes(path.read_bytes())
    packages = subprocess.check_output(
        [
            "dpkg-query",
            "-W",
            "-f=${Package}=${Version}\n",
            "ros-jazzy-nav2-smac-planner",
            "ros-jazzy-nav2-mppi-controller",
            "ros-jazzy-nav2-rotation-shim-controller",
            "ros-jazzy-nav2-bt-navigator",
            "ros-jazzy-nav2-behaviors",
            "ros-jazzy-rclpy",
        ],
        text=True,
    ).splitlines()
    sources = {}
    root = Path("/opt/materialbrain")
    for path in sorted((root / "ros2").rglob("*")):
        if path.is_file():
            if not {"build", "install", "log", "__pycache__", ".runtime", ".evidence"} & set(
                path.parts
            ):
                sources[str(path.relative_to(root))] = digest(path)
    for path in [
        root / "world.v3.json",
        root / "spatial_snapshot.json",
        root / ".runtime/warehouse.yaml",
        root / ".runtime/warehouse.pgm",
        root / ".runtime/diff_drive_lattice.json",
        root / ".runtime/diff_drive_lattice.manifest.json",
    ]:
        sources[str(path.relative_to(root))] = digest(path)
    manifest = {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "execution_boundary": "ros2_nav2_simulation",
        "hardware_control": False,
        "build_sha": health["build_sha"],
        "map_revision": health["map_revision"],
        "runtime_instance": {
            "instance_id": os.environ.get("HOSTNAME", "unknown"),
            "ros_domain_id": int(os.environ.get("ROS_DOMAIN_ID", "0")),
            "sim_time_scale": float(os.environ.get("MATERIALBRAIN_SIM_TIME_SCALE", "1")),
            "physics_step_s": 0.02,
        },
        "observed_time_ratio": (
            (topics[-1]["ros_time_ns"] - topics[0]["ros_time_ns"])
            / 1e9
            / (topics[-1]["wall_time"] - topics[0]["wall_time"])
            if len(topics) > 1
            else None
        ),
        "health": health,
        "installed_packages": packages,
        "topic_record_counts": dict(Counter(item["topic"] for item in topics)),
        "event_record_count": len(events),
        "scenarios": [
            {k: r[k] for k in ("name", "status", "mission_id", "metrics", "assertions")}
            for r in results
        ],
        "source_sha256": sources,
        "artifact_sha256": {p.name: digest(p) for p in sorted(target.iterdir())},
    }
    path = target / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"manifest": str(path), "sha256": digest(path)}, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default="/opt/materialbrain/.evidence")
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    args = parser.parse_args()
    bundle(args.evidence, args.output, args.base_url)
