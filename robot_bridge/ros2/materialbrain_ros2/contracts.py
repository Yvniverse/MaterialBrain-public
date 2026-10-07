"""Dependency-free transport validation and idempotent execution event ledger."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone


class ContractError(ValueError):
    def __init__(self, code: str, status: int = 422):
        super().__init__(code)
        self.code = code
        self.status = status


def stable_hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def validate_plan(plan: dict, snapshot: dict, world: dict):
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != 1
        or plan.get("status") != "READY"
    ):
        raise ContractError("READY_MISSION_PLAN_REQUIRED")
    if plan.get("hardware_control") or plan.get("hardware"):
        raise ContractError("SIMULATION_ONLY", 403)
    if plan.get("map_id") != snapshot["map_id"] or plan.get("map_revision") != snapshot["revision"]:
        raise ContractError("STALE_OR_UNKNOWN_MAP_REVISION", 409)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", str(plan.get("mission_id", ""))):
        raise ContractError("INVALID_MISSION_ID")
    registry = {g["id"]: g for g in snapshot["docks"]}
    goals = plan.get("ordered_goal_ids", [])
    completed = plan.get("completed_goal_ids", [])
    task_goals = [g for g in goals if g != "CHARGER"] if isinstance(goals, list) else []
    if (
        not isinstance(goals, list)
        or not 0 <= len(goals) <= 34
        or (
            not goals
            and not (
                completed and any(s.get("to_goal_id") == "HOME" for s in plan.get("segments", []))
            )
        )
        or len(set(task_goals)) != len(task_goals)
    ):
        raise ContractError("UNIQUE_REGISTERED_GOALS_REQUIRED")
    if any(g not in registry for g in goals + completed):
        raise ContractError("UNKNOWN_REGISTERED_GOAL")
    if len(completed) != len(set(completed)):
        raise ContractError("DUPLICATE_COMPLETED_GOAL")
    for stop in plan.get("stops", []):
        goal = stop.get("goal_id", stop.get("id"))
        if goal not in registry:
            raise ContractError("UNKNOWN_REGISTERED_STOP")
        if "pose" in stop and any(
            abs(float(stop["pose"][k]) - registry[goal]["pose"][k]) > 1e-7
            for k in ("x", "y", "yaw")
        ):
            raise ContractError("FREE_COORDINATE_GOAL_REJECTED")
    constraints = plan.get("constraints", {})
    for key, default in (
        ("battery_pct", world["robot"]["battery_pct"]),
        ("battery_reserve_pct", 15),
    ):
        val = constraints.get(key, default)
        if not isinstance(val, (int, float)) or not math.isfinite(val) or not 0 <= val <= 100:
            raise ContractError("INVALID_" + key.upper())
    robot_class = constraints.get("robot_class", "MBR-01")
    if robot_class not in ("MBR-01", "MB-R01"):
        raise ContractError("INCOMPATIBLE_ROBOT_CLASS")
    if constraints.get("payload_capacity_kg", 18) > world["robot"]["payload_kg"]:
        raise ContractError("PAYLOAD_CAPACITY_EXCEEDS_REGISTERED_ROBOT")
    requested_payload = sum(registry[g].get("payload_kg", 0) for g in goals if g not in completed)
    if requested_payload > constraints.get("payload_capacity_kg", 18):
        raise ContractError("PAYLOAD_CAPACITY_EXCEEDED")
    nodes = {n["id"]: n for n in snapshot.get("route_graph", {}).get("nodes", [])}
    edges = {e["id"]: e for e in snapshot.get("route_graph", {}).get("edges", [])}
    for segment in plan.get("segments", []):
        node_ids = segment.get("node_ids", [])
        edge_ids = segment.get("edge_ids", [])
        if not node_ids:
            if plan.get("profile", "fastest") != "fastest":
                raise ContractError("SEMANTIC_GRAPH_ROUTE_REQUIRED")
            continue
        if len(edge_ids) != len(node_ids) - 1:
            raise ContractError("INVALID_SEGMENT_GRAPH_CHAIN")
        destination = registry.get(segment.get("to_goal_id"))
        if not destination or node_ids[-1] != destination.get("node_id", destination["id"]):
            raise ContractError("SEGMENT_DESTINATION_MUST_MATCH_REGISTERED_DOCK")
        for index, nid in enumerate(node_ids):
            if nid not in nodes and not (nid == "__current_pose__" and index == 0):
                raise ContractError("UNKNOWN_REGISTERED_ROUTE_NODE")
        for index, eid in enumerate(edge_ids):
            if (
                eid.startswith("__first_mile__:")
                and index == 0
                and node_ids[0] == "__current_pose__"
                and eid.endswith(node_ids[1])
            ):
                continue
            edge = edges.get(eid)
            if not edge or edge["from"] != node_ids[index] or edge["to"] != node_ids[index + 1]:
                raise ContractError("INVALID_DIRECTED_ROUTE_EDGE")
            if (
                edge.get("blocked")
                or edge.get("keepout")
                or edge.get("width_m", 0) < world["robot"]["width"] + 2 * world["robot"]["margin"]
            ):
                raise ContractError("FORBIDDEN_ROUTE_EDGE")
            if edge.get("allowed_robot_classes") and "MB-R01" not in edge["allowed_robot_classes"]:
                raise ContractError("INCOMPATIBLE_ROUTE_EDGE")
        # Ignore supplied poses for dispatch, but reject any invented XY data outright.
        allowed_xy = {(nodes[n]["x"], nodes[n]["y"]) for n in node_ids if n in nodes}
        if node_ids[0] == "__current_pose__" and segment.get("poses"):
            allowed_xy.add((segment["poses"][0]["x"], segment["poses"][0]["y"]))
        for pose in segment.get("poses", []):
            if not any(math.hypot(pose["x"] - x, pose["y"] - y) < 1e-7 for x, y in allowed_xy):
                raise ContractError("FREE_COORDINATE_ROUTE_REJECTED")
    return registry


def route_waypoints(snapshot: dict, segment: dict, current_pose: dict, goal_id: str, world: dict):
    """Derive constrained waypoints from registered graph IDs, preserving every turn."""
    from .geometry import point_rect_distance, static_obstacles

    nodes = {n["id"]: n for n in snapshot["route_graph"]["nodes"]}
    dock = next(d for d in snapshot["docks"] if d["id"] == goal_id)
    ids = segment.get("node_ids", [])
    if not ids:
        return [{"id": goal_id, **dock["pose"]}]
    source = nodes.get(ids[0], segment.get("poses", [current_pose])[0])
    if math.hypot(source["x"] - current_pose["x"], source["y"] - current_pose["y"]) > 0.5:
        raise ContractError("OBSERVED_START_POSE_CHANGED_REPLAN_REQUIRED", 409)
    if ids[0] == "__current_pose__" and len(ids) > 1:
        target = nodes[ids[1]]
        length = math.hypot(target["x"] - current_pose["x"], target["y"] - current_pose["y"])
        if length > snapshot["route_graph"].get("connector_max_m", 2.0) + 1e-7:
            raise ContractError("FIRST_MILE_CONNECTOR_TOO_LONG")
        radius = (
            math.hypot(world["robot"]["length"], world["robot"]["width"]) / 2
            + world["robot"]["margin"]
        )
        for i in range(max(2, int(length / 0.025) + 1)):
            t = i / max(1, int(length / 0.025))
            x = current_pose["x"] + (target["x"] - current_pose["x"]) * min(t, 1)
            y = current_pose["y"] + (target["y"] - current_pose["y"]) * min(t, 1)
            if (
                min(
                    x,
                    y,
                    world["width"] - x,
                    world["height"] - y,
                    *[point_rect_distance(x, y, r) for r in static_obstacles(world)],
                )
                <= radius
            ):
                raise ContractError("FIRST_MILE_CONNECTOR_COLLISION")
    points = [{"id": n, "x": nodes[n]["x"], "y": nodes[n]["y"]} for n in ids if n in nodes]
    selected = []
    last = current_pose
    for i, p in enumerate(points[:-1]):
        if math.hypot(p["x"] - current_pose["x"], p["y"] - current_pose["y"]) < 0.25:
            continue
        prev = points[i - 1] if i else current_pose
        nxt = points[i + 1]
        turn = (
            abs(
                (p["x"] - prev["x"]) * (nxt["y"] - p["y"])
                - (p["y"] - prev["y"]) * (nxt["x"] - p["x"])
            )
            > 1e-7
        )
        if turn or math.hypot(p["x"] - last["x"], p["y"] - last["y"]) >= 1.75:
            selected.append(p)
            last = p
    selected.append({"id": goal_id, **dock["pose"]})
    for i, p in enumerate(selected[:-1]):
        nxt = selected[i + 1]
        p["yaw"] = math.atan2(nxt["y"] - p["y"], nxt["x"] - p["x"])
    return selected


class Mission:
    def __init__(self, plan, snapshot, world):
        self.registry = validate_plan(plan, snapshot, world)
        self.plan = copy.deepcopy(plan)
        self.id = plan["mission_id"]
        self.plan_hash = stable_hash(plan)
        self.revision = snapshot["revision"]
        self.status = "QUEUED"
        self.current_goal_id = None
        self.completed = list(plan.get("completed_goal_ids", []))
        self.events = []
        self.sequence = 0
        self.queue = [
            g for g in plan["ordered_goal_ids"] if g not in self.completed or g == "CHARGER"
        ]
        self.constraints = copy.deepcopy(plan.get("constraints", {}))
        self.battery = self.constraints.get("battery_pct", world["robot"]["battery_pct"])
        self.payload = 0.0
        self.started_sim_time = None
        self.distance_start = 0.0
        self.return_home = any(s.get("to_goal_id") == "HOME" for s in plan.get("segments", []))
        self.battery_base = self.battery
        self.battery_energy_baseline_wh = 0.0
        self.charging_until = None
        self.segment_cursor = 0
        self.active_waypoint_ids = []
        self.overlay_hold = False
        self.overlay_hold_since = 0.0
        self.overlay_valid_path_ready = False
        self.return_home = (
            self.return_home or plan.get("return_home", False) or "HOME" in plan["ordered_goal_ids"]
        )
        self.metrics = {
            "action_feedback_messages": 0,
            "nav2_action_count": 0,
            "replan_count": 0,
            "bt_recovery_count": 0,
            "global_paths": 0,
            "global_path_lengths_m": [],
            "planning_latencies_ms": [],
            "completed_handoffs": 0,
        }
        self.pose = None
        self.robot_state = {}
        self.last_feedback_wall = 0.0
        self.last_recoveries = 0

    def remaining(self):
        return [g for g in self.queue if g != "HOME"]

    def event(self, event_type, goal_id=None, details=None):
        self.sequence += 1
        item = {
            "schema_version": 1,
            "mission_id": self.id,
            "event_id": f"{self.id}:{self.sequence:06d}",
            "sequence": self.sequence,
            "map_revision": self.revision,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "goal_id": goal_id,
            "pose": copy.deepcopy(self.pose),
            "battery_pct": round(self.battery, 6),
            "payload_kg": self.payload,
            "details": {
                "execution_boundary": "ros2_nav2_simulation",
                "hardware_control": False,
                **(details or {}),
            },
        }
        self.events.append(item)
        return item

    def snapshot(self, after_sequence=None):
        events = (
            self.events
            if after_sequence is None
            else [e for e in self.events if e["sequence"] > after_sequence]
        )
        return {
            "schema_version": 1,
            "mission_id": self.id,
            "map_id": self.plan["map_id"],
            "map_revision": self.revision,
            "status": self.status,
            "current_goal_id": self.current_goal_id,
            "completed_goal_ids": self.completed[:],
            "remaining_goal_ids": self.remaining(),
            "current_pose": copy.deepcopy(self.pose),
            "robot_state": copy.deepcopy(self.robot_state),
            "events": copy.deepcopy(events),
            "last_sequence": self.sequence,
            "metrics": copy.deepcopy(self.metrics),
            "planned_metrics": copy.deepcopy(self.plan.get("metrics", {})),
            "route_profile": self.plan.get("profile", "fastest"),
            "active_waypoint_ids": self.active_waypoint_ids[:],
            "execution_boundary": "ros2_nav2_simulation",
            "hardware_control": False,
        }
