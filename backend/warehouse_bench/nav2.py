"""Import measured ROS2/Nav2 evidence without manufacturing missing executions."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def _number(value, field):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("INVALID_NAV2_METRIC:" + field)
    return float(value)


def import_nav2_summary(summary_path, snapshot, *, seed=17, topics_path=None, events_path=None):
    """Require exact map identity, active plugins, feedback and artifact hashes.

    A failed real Nav2 run is retained as a failed episode. Missing or stale
    artifacts raise an error, never produce a synthetic success or substituted
    V3 result. Energy remains unknown unless the measured run exported its model.
    """
    path = Path(summary_path)
    summary = json.loads(path.read_text(encoding="utf-8"))
    if (
        summary.get("map_id") != snapshot["map_id"]
        or summary.get("map_revision") != snapshot["revision"]
    ):
        raise ValueError("NAV2_MAP_REVISION_MISMATCH")
    if (
        summary.get("execution_boundary") != "ros2_nav2_simulation"
        or summary.get("hardware_control") is not False
    ):
        raise ValueError("NAV2_SIMULATION_BOUNDARY_REQUIRED")
    active = summary.get("active_plugins", {})
    planner = active.get("planner_server", {}).get("GridBased.plugin", "")
    controller = active.get("controller_server", {})
    if not planner.endswith("SmacPlannerLattice"):
        raise ValueError("NAV2_STATE_LATTICE_NOT_ACTIVE")
    selected = controller.get("FollowPath.plugin", "")
    mppi_active = selected.endswith("MPPIController") or (
        selected.endswith("RotationShimController")
        and controller.get("FollowPath.primary_controller", "").endswith("MPPIController")
    )
    if not mppi_active:
        raise ValueError("NAV2_MPPI_NOT_ACTIVE")
    if (
        controller.get("FollowPath.motion_model") != "DiffDrive"
        or controller.get("FollowPath.CostCritic.consider_footprint") is not True
    ):
        raise ValueError("NAV2_FULL_DIFFERENTIAL_FOOTPRINT_REQUIRED")
    artifacts = summary.get("artifacts", {})
    verified = {}
    for label, argument in (("topics", topics_path), ("events", events_path)):
        artifact = Path(argument) if argument else path.parent / f"{label}.jsonl"
        raw = artifact.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != artifacts.get(f"{label}_jsonl_sha256"):
            raise ValueError("NAV2_ARTIFACT_HASH_MISMATCH:" + label)
        records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        if not records:
            raise ValueError("NAV2_EMPTY_ARTIFACT:" + label)
        verified[label] = {"sha256": digest, "records": len(records)}
    episodes = []
    registered = {d["id"] for d in snapshot["docks"]}
    for scenario in summary.get("scenarios", []):
        metrics = scenario.get("metrics", {})
        feedback = _number(metrics.get("action_feedback_messages", 0), "action_feedback_messages")
        action_count = _number(metrics.get("nav2_action_count", 0), "nav2_action_count")
        if feedback == 0 or action_count == 0:
            raise ValueError("NAV2_ACTION_FEEDBACK_REQUIRED:" + scenario["name"])
        if set(scenario.get("completed_goal_ids", [])) - registered:
            raise ValueError("NAV2_UNREGISTERED_COMPLETED_GOAL")
        distance = _number(metrics.get("distance_m"), "distance_m")
        elapsed = _number(metrics.get("elapsed_s"), "elapsed_s")
        clearance = metrics.get("min_clearance_m")
        if clearance is not None:
            clearance = float(clearance)
            if not math.isfinite(clearance):
                raise ValueError("INVALID_NAV2_METRIC:min_clearance_m")
        collisions = int(_number(metrics.get("collision_count"), "collision_count"))
        latencies = [
            _number(v, "planning_latencies_ms") for v in metrics.get("planning_latencies_ms", [])
        ]
        replans = [
            _number(v, "replanning_latencies_ms")
            for v in metrics.get("replanning_latencies_ms", [])
        ]
        assertions = scenario.get("assertions", {})
        checks = list(assertions.values()) if isinstance(assertions, dict) else assertions
        valid_checks = bool(checks) and all(check is True for check in checks)
        success = (
            scenario.get("status") in {"PASS", "COMPLETED", "SUCCEEDED"}
            and valid_checks
            and collisions == 0
        )
        violation_count = sum(check is not True for check in checks)
        energy = metrics.get("energy_wh")
        if energy is not None:
            energy = _number(energy, "energy_wh")
        episodes.append(
            {
                "schema_version": 1,
                "algorithm": "nav2_state_lattice",
                "profile": scenario.get("profile", "fastest"),
                "scenario_id": scenario["name"],
                "seed": scenario.get("seed", seed),
                "repetition": 0,
                "map_id": snapshot["map_id"],
                "map_revision": snapshot["revision"],
                "world_revision": snapshot["provenance"]["source_world_revision"],
                "source_sha": summary.get("build_sha"),
                "status": scenario["status"],
                "success": success,
                "decision_correct": success,
                "path_length_m": distance,
                "path_ratio": metrics.get("path_ratio"),
                "route_cost_ratio": metrics.get("route_cost_ratio"),
                "min_clearance_m": clearance,
                "turn_count": metrics.get("turn_count"),
                "planning_ms": sum(latencies) / len(latencies) if latencies else None,
                "replan_ms": sum(replans) / len(replans) if replans else None,
                "planning_latencies_ms": latencies,
                "replanning_latencies_ms": replans,
                "collisions": collisions,
                "constraint_violations": violation_count,
                "mission_completion_time_s": elapsed,
                "mission_completion_time_source": "observed_ros2_nav2_execution",
                "energy_wh": energy,
                "energy_source": "measured_run_exported_estimate"
                if energy is not None
                else "not_exported",
                "recovery_attempted": metrics.get("replan_count", 0) > 0
                or metrics.get("bt_recovery_count", 0) > 0,
                "recovery_success": success
                if metrics.get("replan_count", 0) > 0 or metrics.get("bt_recovery_count", 0) > 0
                else None,
                "active_plugins": active,
                "verified_artifacts": verified,
                "execution_boundary": "ros2_nav2_simulation",
                "hardware_control": False,
                "inventory_written": False,
            }
        )
    if not episodes:
        raise ValueError("NAV2_SCENARIOS_REQUIRED")
    json.dumps(episodes, allow_nan=False)
    return episodes
