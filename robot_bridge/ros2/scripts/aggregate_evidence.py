"""Verify independent exact-image ROS acceptance bundles without merging robot state."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

REQUIRED = {"nominal", "blocked", "bt-recovery", "cancel", "low-battery", "charge", "semantic"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(bundle_paths, expected_sha, output):
    members = []
    baseline = None
    domains = set()
    instances = set()
    scenarios = {}
    for value in bundle_paths:
        path = Path(value)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["build_sha"] == expected_sha, "runtime build SHA mismatch"
        assert manifest["hardware_control"] is False, "hardware evidence cannot be aggregated"
        assert manifest["health"]["ready"], "runtime was not ready when evidence froze"
        runtime = manifest["runtime_instance"]
        assert runtime["sim_time_scale"] == 1.0, "accelerated physics is not acceptance evidence"
        assert 0.8 <= manifest["observed_time_ratio"] <= 1.2, "measured physics clock drift"
        assert runtime["ros_domain_id"] not in domains, "ROS domains must be isolated"
        assert runtime["instance_id"] not in instances, "runtime instances must be unique"
        domains.add(runtime["ros_domain_id"])
        instances.add(runtime["instance_id"])
        identity = {
            "build_sha": manifest["build_sha"],
            "map_revision": manifest["map_revision"],
            "source_sha256": manifest["source_sha256"],
            "installed_packages": manifest["installed_packages"],
            "active_plugins": manifest["health"]["active_plugins"],
        }
        if baseline is None:
            baseline = identity
        assert identity == baseline, (
            "runtime source, geometry, packages or plugin parameters differ"
        )
        for name, sha256 in manifest["artifact_sha256"].items():
            assert Path(name).name == name, "artifact path must remain inside its frozen bundle"
            assert digest(path.parent / name) == sha256, "frozen artifact hash mismatch: " + name
        counts = manifest["topic_record_counts"]
        assert all(counts.get(t, 0) > 0 for t in ("/scan", "/odom", "/tf", "/cmd_vel", "/plan")), (
            "measured ROS topics missing"
        )
        for result in manifest["scenarios"]:
            name = result["name"]
            assert name not in scenarios, "duplicate scenario evidence: " + name
            assert (
                result["status"] == "PASS"
                and result["assertions"]
                and all(result["assertions"].values())
            ), "scenario did not pass: " + name
            record = json.loads((path.parent / (name + ".json")).read_text(encoding="utf-8"))
            assert (
                record["build_sha"] == expected_sha
                and record["map_revision"] == manifest["map_revision"]
            ), "scenario runtime identity mismatch"
            assert record["active_plugins"] == manifest["health"]["active_plugins"], (
                "scenario controller parameters changed during the run"
            )
            assert record["state"]["robot_state"]["collision_count"] == 0, "scenario collision"
            scenarios[name] = {
                **result,
                "member_manifest_sha256": digest(path),
                "runtime_instance": runtime,
            }
        members.append(
            {"manifest": str(path), "manifest_sha256": digest(path), "runtime_instance": runtime}
        )
    assert REQUIRED == set(scenarios), (
        "all six scenarios and the semantic route are required exactly once"
    )
    summary = {
        "schema_version": 1,
        "status": "PASS",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "execution_boundary": "ros2_nav2_simulation",
        "hardware_control": False,
        "build_sha": expected_sha,
        "map_revision": baseline["map_revision"],
        "members": members,
        "scenarios": scenarios,
        "source_sha256": baseline["source_sha256"],
        "installed_packages": baseline["installed_packages"],
        "active_plugins": baseline["active_plugins"],
        "assertions": {
            "identical_sources_geometry_packages_plugins": True,
            "separate_domains_and_instances": True,
            "unit_scale_observed_physics": True,
            "all_member_hashes_verified": True,
            "all_scenarios_pass_exactly_once": True,
        },
    }
    target = Path(output)
    with target.open("x") as stream:
        json.dump(summary, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"aggregate": str(target), "sha256": digest(target)}, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", action="append", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    aggregate(args.bundle, args.expected_sha, args.output)
