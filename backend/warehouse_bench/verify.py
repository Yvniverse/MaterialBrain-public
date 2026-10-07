"""Independent geometry/constraints audit and graph-distance small-N bound."""

from __future__ import annotations

import heapq
import math

from app.services.spatial_mission.exact import held_karp_exact
from app.services.spatial_mission.geometry import line_distance, static_clearance
from app.services.spatial_mission.semantic import RobotProfile, SemanticGraph


def graph_distance_bound(snapshot: dict, request: dict) -> dict:
    """Length-only Dijkstra + Held-Karp, independent of semantic routing cost.

    This is a graph bound, not the continuous-space optimum. A finer-grid V3
    path can legitimately have a ratio below one. Constraints/priorities do not
    weaken this unconstrained distance lower bound for the graph algorithm.
    """
    goals = [
        g
        for g in dict.fromkeys(request["goal_ids"])
        if g not in request.get("completed_goal_ids", [])
    ]
    if len(goals) > 8:
        return {
            "distance_m": None,
            "method": "held_karp_graph_distance",
            "reason": "exact comparison limit is eight remaining goals",
        }
    docks = {d["id"]: d for d in snapshot["docks"]}
    robot = RobotProfile.from_snapshot(
        snapshot, request.get("constraints", {}).get("robot_class", "MB-R01")
    )
    graph = SemanticGraph(
        snapshot,
        profile=request.get("profile", "fastest"),
        robot=robot,
        overlays=request.get("dynamic_overlays", []),
    )
    try:
        start = graph.attach_start(request["start_pose"])
    except ValueError:
        return {
            "distance_m": None,
            "method": "held_karp_graph_distance",
            "reason": "start pose is disconnected",
        }
    goal_nodes = [docks[g]["node_id"] for g in goals]
    end = docks["HOME"]["node_id"] if request.get("return_home", True) else None
    selected = [start, *goal_nodes, end]
    matrix = []
    for source in selected:
        distances, queue = {source: 0.0}, [(0.0, source)] if source is not None else []
        while queue:
            distance, node = heapq.heappop(queue)
            if distance > distances[node] + 1e-9:
                continue
            for edge in graph.adjacency[node]:
                candidate = distance + edge["length_m"]
                if candidate < distances.get(edge["to"], math.inf) - 1e-9:
                    distances[edge["to"]] = candidate
                    heapq.heappush(queue, (candidate, edge["to"]))
        matrix.append([0.0 if node is None else distances.get(node, math.inf) for node in selected])
    exact = held_karp_exact(matrix, list(range(1, len(goals) + 1)), len(selected) - 1)
    return {
        "distance_m": exact["cost"] if exact["feasible"] else None,
        "method": "length_dijkstra_plus_held_karp_exact",
        "optimality_proven": True,
        "scope": "unconstrained_order_on_filtered_directed_graph",
    }


def audit_route(snapshot: dict, request: dict, plan: dict) -> dict:
    robot = RobotProfile.from_snapshot(
        snapshot, request.get("constraints", {}).get("robot_class", "MB-R01")
    )
    graph = SemanticGraph(
        snapshot,
        profile=request.get("profile", "fastest"),
        robot=robot,
        overlays=request.get("dynamic_overlays", []),
    )
    collisions, violations, clearance_values = 0, [], []
    for segment_index, segment in enumerate(plan.get("segments", [])):
        poses = segment.get("poses", [])
        if not poses:
            violations.append({"code": "MISSING_SEGMENT_POSES", "segment": segment_index})
            continue
        coords = [[p["x"], p["y"]] for p in poses]
        clearance = static_clearance(coords, snapshot) - robot.radius_m
        if math.isfinite(clearance):
            clearance_values.append(clearance)
            if clearance <= 1e-9:
                collisions += 1
        for overlay in graph.overlays:
            if overlay.get("kind") in {
                "closure",
                "obstacle",
                "keepout",
                "blocked",
                "aisle_closure",
            }:
                if line_distance(coords, overlay) <= robot.radius_m:
                    collisions += 1
                if set(segment.get("edge_ids", [])) & set(overlay.get("edge_ids", [])):
                    violations.append(
                        {
                            "code": "CLOSED_EDGE",
                            "segment": segment_index,
                            "overlay_id": overlay["id"],
                        }
                    )
        for edge_id in segment.get("edge_ids", []):
            if edge_id.startswith("__first_mile__:"):
                continue
            if edge_id not in graph.edges:
                violations.append({"code": "INVALID_OR_EXCLUDED_EDGE", "edge_id": edge_id})
        for zone in snapshot.get("zones", []):
            if (
                zone.get("kind") in {"keepout", "restricted"}
                and line_distance(coords, zone) <= robot.radius_m
            ):
                violations.append(
                    {"code": "KEEPOUT", "segment": segment_index, "zone_id": zone["id"]}
                )
    requested = set(request["goal_ids"]) - set(request.get("completed_goal_ids", []))
    ordered = plan.get("ordered_goal_ids", [])
    if plan.get("status") == "READY":
        if not requested <= set(ordered):
            violations.append({"code": "MISSING_REQUIRED_GOAL"})
        if set(request.get("completed_goal_ids", [])) & set(ordered):
            violations.append({"code": "REPEATED_COMPLETED_GOAL"})
        constraints = request.get("constraints", {})
        docks = {dock["id"]: dock for dock in snapshot["docks"]}
        overrides = {stop["goal_id"]: stop for stop in request.get("stops", [])}
        load = sum(
            overrides.get(goal, {}).get("demand_kg", docks[goal].get("payload_kg", 0))
            for goal in requested | set(request.get("completed_goal_ids", []))
        )
        if load > constraints.get("payload_capacity_kg", 18) + 1e-8:
            violations.append({"code": "PAYLOAD_CAPACITY"})
        if abs(plan.get("metrics", {}).get("payload_kg", 0) - load) > 1e-7:
            violations.append({"code": "PAYLOAD_METRIC_MISMATCH"})
        if (
            plan.get("metrics", {}).get("battery_after_pct", 100)
            < constraints.get("battery_reserve_pct", 15) - 1e-7
        ):
            violations.append({"code": "BATTERY_RESERVE"})
        for stop in plan.get("stops", []):
            expected = overrides.get(stop["goal_id"], {})
            window = expected.get("time_window_s", stop.get("time_window_s"))
            if window and not window[0] <= stop["arrival_s"] <= window[1]:
                violations.append({"code": "TIME_WINDOW", "goal_id": stop["goal_id"]})
            if stop["kind"] != "charge":
                service_s = expected.get("service_s", docks[stop["goal_id"]].get("service_s", 5))
                if abs(service_s - stop["service_s"]) > 1e-7:
                    violations.append({"code": "SERVICE_TIME", "goal_id": stop["goal_id"]})
        if not any(stop.get("kind") == "charge" for stop in plan.get("stops", [])):
            if (
                plan.get("metrics", {}).get("battery_after_pct", 100)
                > constraints.get("battery_pct", 100) + 1e-7
            ):
                violations.append({"code": "BATTERY_INCREASE_WITHOUT_CHARGE"})
    return {
        "collisions": collisions,
        "constraint_violations": len(violations),
        "violations": violations,
        "min_clearance_m": min(clearance_values, default=None),
    }
