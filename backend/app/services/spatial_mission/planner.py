"""Pure server-owned mission plan over a versioned SpatialMapSnapshot.

Plans authorize no execution or stock settlement. Poses are derived from registered
docks and directed graph edges. Only the observed current pose is attached using
a conservative, collision-checked first mile. The Motion/Nav2 adapter owns actual
closed-loop trajectories; this router's optimality scope is its pairwise matrix.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time

from app.schemas.spatial import MissionPlan, MissionRequest

from .exact import held_karp_exact
from .routing import COST_SCALE, PRIORITY_WEIGHT, objective_matrix, solve_constrained
from .semantic import RobotProfile, SemanticGraph


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _empty_path(pose):
    return {
        "node_ids": [],
        "edge_ids": [],
        "poses": [copy.deepcopy(pose)],
        "cost_terms": {
            k: 0.0
            for k in (
                "cost_s",
                "travel_time_s",
                "rotation_time_s",
                "clearance_penalty",
                "risk",
                "energy_wh",
                "semantic_penalty",
            )
        },
        "distance_m": 0.0,
        "travel_s": 0.0,
        "energy_wh": 0.0,
        "min_clearance_m": None,
        "turn_count": 0,
    }


def plan_mission(snapshot: dict, request: dict) -> dict:
    """Return the frozen MissionPlan transport shape without mutating either input.

    Bad transport arguments raise Pydantic validation errors. Semantic grounding
    failures return CLARIFICATION; known but impossible constraints return
    INFEASIBLE. Search exhaustion is explicitly distinguished from a proof.
    """
    started = time.perf_counter()
    body = MissionRequest.model_validate(request).model_dump(mode="json")
    body["goal_ids"] = list(dict.fromkeys(body["goal_ids"]))
    body["completed_goal_ids"] = list(dict.fromkeys(body["completed_goal_ids"]))
    constraints = copy.deepcopy(body["constraints"])
    plan = {
        "schema_version": 1,
        "mission_id": "SM-" + _digest({"request": body, "revision": snapshot["revision"]})[:24],
        "map_id": snapshot["map_id"],
        "map_revision": snapshot["revision"],
        "profile": body["profile"],
        "status": "CLARIFICATION",
        "stops": [],
        "ordered_goal_ids": [],
        "completed_goal_ids": body["completed_goal_ids"],
        "constraints": constraints,
        "solver": {
            "name": "ortools",
            "method": "routing_model_graph_dijkstra",
            "optimality_proven": False,
        },
        "metrics": {},
        "objective_terms": {},
        "segments": [],
        "violations": [],
    }

    def finish(status, violation=None):
        plan["status"] = status
        if violation:
            plan["violations"].append(violation)
        plan["metrics"]["planning_ms"] = (time.perf_counter() - started) * 1000
        plan["metrics"]["inventory_written"] = False
        plan["metrics"]["execution_started"] = False
        # Constructor validates only the frozen top-level shape; explicit JSON
        # serialization also rejects non-finite nested metrics/oracle values.
        result = MissionPlan.model_validate(plan).model_dump(mode="json")
        json.dumps(result, allow_nan=False)
        return result

    if body["map_id"] != snapshot["map_id"]:
        return finish("CLARIFICATION", {"code": "MAP_ID_MISMATCH"})
    if body["map_revision"] != snapshot["revision"]:
        return finish(
            "CLARIFICATION", {"code": "MAP_REVISION_STALE", "expected": snapshot["revision"]}
        )
    docks = {dock["id"]: dock for dock in snapshot["docks"]}
    if len(docks) != len(snapshot["docks"]):
        raise ValueError("DUPLICATE_REGISTERED_DOCK")
    unknown = sorted((set(body["goal_ids"]) | set(body["completed_goal_ids"])) - docks.keys())
    if unknown:
        return finish("CLARIFICATION", {"code": "UNKNOWN_REGISTERED_GOAL", "goal_ids": unknown})
    overrides = {stop["goal_id"]: stop for stop in body["stops"]}
    if len(overrides) != len(body["stops"]):
        return finish("CLARIFICATION", {"code": "DUPLICATE_STOP_CONTRACT"})
    extra = sorted(overrides.keys() - (set(body["goal_ids"]) | set(body["completed_goal_ids"])))
    if extra:
        return finish("CLARIFICATION", {"code": "STOP_NOT_REQUESTED", "goal_ids": extra})
    remaining = [g for g in body["goal_ids"] if g not in body["completed_goal_ids"]]
    robot = RobotProfile.from_snapshot(snapshot, constraints["robot_class"])
    graph = SemanticGraph(
        snapshot, profile=body["profile"], robot=robot, overlays=body["dynamic_overlays"]
    )
    plan["mission_id"] = (
        "SM-"
        + _digest(
            {"request": body, "revision": snapshot["revision"], "active_overlays": graph.overlays}
        )[:24]
    )
    plan["constraints"].update(
        {
            "required_width_m": robot.width_m + 2 * robot.margin_m,
            "connector_footprint_radius_m": robot.radius_m,
            "battery_capacity_wh": robot.battery_wh,
            "active_overlay_ids": sorted({o["id"] for o in graph.overlays}),
            "expired_overlay_ids": sorted(set(graph.expired_overlay_ids)),
        }
    )
    plan["metrics"].update(
        {
            "graph_nodes": len(graph.nodes),
            "graph_edges": len(graph.edges),
            "excluded_edges": len(graph.excluded),
        }
    )
    for goal_id in remaining:
        dock = docks[goal_id]
        if dock.get("robot_classes") and constraints["robot_class"] not in dock["robot_classes"]:
            return finish("INFEASIBLE", {"code": "DOCK_ROBOT_CLASS", "goal_id": goal_id})
        if dock["node_id"] not in graph.nodes:
            raise ValueError("REGISTERED_DOCK_NODE_MISSING:" + goal_id)
    try:
        start_node = graph.attach_start(body["start_pose"])
    except ValueError as exc:
        return finish("INFEASIBLE", {"code": str(exc)})

    def make_stop(goal_id, kind="task", mandatory=True, occurrence_id=None):
        dock = docks[goal_id]
        override = overrides.get(goal_id, {})
        is_charge = kind == "charge" or "charge" in dock.get("capabilities", [])
        # Full-from-reserve charging duration is conservative and independent of
        # route choice. Never describe it as observed hardware charge behaviour.
        charge_service = (
            robot.battery_wh
            * (100 - constraints["battery_reserve_pct"])
            / 100
            / robot.charge_w
            * 3600
        )
        return {
            "goal_id": goal_id,
            "occurrence_id": occurrence_id or goal_id,
            "node_id": dock["node_id"],
            "pose": copy.deepcopy(dock["pose"]),
            "kind": "charge" if is_charge else kind,
            "mandatory": mandatory,
            "service_s": charge_service
            if is_charge
            else float(override.get("service_s", dock.get("service_s", 5))),
            "demand_kg": 0.0
            if is_charge
            else float(override.get("demand_kg", dock.get("payload_kg", 0))),
            "priority": int(override.get("priority", dock.get("priority", 0))),
            "time_window_s": override.get("time_window_s", dock.get("time_window_s")),
        }

    completed_payload = sum(make_stop(g)["demand_kg"] for g in body["completed_goal_ids"])
    stops = [
        {
            "goal_id": "__start__",
            "node_id": start_node,
            "pose": body["start_pose"],
            "kind": "start",
            "mandatory": True,
            "service_s": 0,
            "demand_kg": 0,
            "priority": 0,
            "time_window_s": None,
        }
    ]
    stops.extend(make_stop(g) for g in remaining)
    demand = completed_payload + sum(s["demand_kg"] for s in stops)
    plan["constraints"].update(
        {
            "initial_payload_kg": completed_payload,
            "planned_payload_kg": demand,
            "goal_count": len(remaining),
        }
    )
    if demand > constraints["payload_capacity_kg"] + 1e-9:
        return finish(
            "INFEASIBLE",
            {
                "code": "PAYLOAD_CAPACITY",
                "required_kg": demand,
                "capacity_kg": constraints["payload_capacity_kg"],
            },
        )
    homes = sorted(
        (d for d in docks.values() if d.get("kind") == "home" or d["id"] == "HOME"),
        key=lambda d: d["id"],
    )
    if body["return_home"] and not homes:
        return finish("CLARIFICATION", {"code": "REGISTERED_HOME_REQUIRED"})
    terminal = (
        make_stop(homes[0]["id"], kind="home")
        if body["return_home"]
        else {
            "goal_id": "__finish__",
            "node_id": None,
            "pose": None,
            "kind": "finish",
            "mandatory": True,
            "service_s": 0,
            "demand_kg": 0,
            "priority": 0,
            "time_window_s": None,
        }
    )
    terminal.update(
        {
            "kind": "home" if body["return_home"] else "finish",
            "service_s": 0,
            "demand_kg": 0,
            "priority": 0,
            "time_window_s": None,
        }
    )

    def paths_for(selected):
        matrix = []
        for source in selected:
            row = []
            for target in selected:
                if target["kind"] == "finish":
                    row.append(_empty_path(source["pose"] or body["start_pose"]))
                elif source["kind"] == "finish":
                    row.append(None)
                else:
                    row.append(
                        graph.path(
                            source["node_id"],
                            target["node_id"],
                            start_pose=source["pose"],
                            end_pose=target["pose"],
                        )
                    )
            matrix.append(row)
        return matrix

    basic_stops = stops + [terminal]
    basic_paths = paths_for(basic_stops)
    unreachable = [s["goal_id"] for i, s in enumerate(stops[1:], 1) if basic_paths[0][i] is None]
    if unreachable:
        return finish(
            "INFEASIBLE", {"code": "UNREACHABLE_REGISTERED_GOAL", "goal_ids": unreachable}
        )
    if body["return_home"] and basic_paths[0][-1] is None:
        return finish("INFEASIBLE", {"code": "HOME_UNREACHABLE"})

    baseline = None
    if len(remaining) <= 8:
        baseline = held_karp_exact(
            objective_matrix(basic_stops, basic_paths, robot, graph.weights),
            list(range(1, len(stops))),
            len(basic_stops) - 1,
        )
        plan["solver"]["exact_baseline"] = {
            "name": "held_karp_exact",
            "scope": "unconstrained_stop_order_on_directed_pairwise_costs",
            "optimality_proven": True,
            "feasible": baseline["feasible"],
            "cost_s": baseline["cost"] if baseline["feasible"] else None,
            "ordered_goal_ids": [basic_stops[i]["goal_id"] for i in baseline["order"]],
        }

    # If every possible order's upper-bound energy fits, charging occurrences
    # cannot improve a travel/service objective and need not enlarge the model.
    max_drive_energy = max((p["energy_wh"] for row in basic_paths for p in row if p), default=0)
    service_energy = robot.idle_w * sum(s["service_s"] for s in stops) / 3600
    waiting_upper = max((s["time_window_s"][0] for s in stops if s.get("time_window_s")), default=0)
    energy_upper = (
        max_drive_energy * (len(remaining) + int(body["return_home"]))
        + service_energy
        + robot.idle_w * waiting_upper / 3600
    )
    available_wh = (
        robot.battery_wh * (constraints["battery_pct"] - constraints["battery_reserve_pct"]) / 100
    )
    chargers = sorted(
        (
            d
            for d in docks.values()
            if "charge" in d.get("capabilities", [])
            and (not d.get("robot_classes") or constraints["robot_class"] in d["robot_classes"])
        ),
        key=lambda d: d["id"],
    )
    if energy_upper > available_wh + 1e-9:
        for charger in chargers:
            for occurrence in range(len(remaining) + 1):
                stops.append(
                    make_stop(
                        charger["id"],
                        kind="charge",
                        mandatory=False,
                        occurrence_id=f"{charger['id']}:{occurrence + 1}",
                    )
                )
    stops.append(terminal)
    paths = paths_for(stops)
    for i, stop in enumerate(stops[1:-1], 1):
        window = stop.get("time_window_s")
        if (
            stop["mandatory"]
            and window
            and paths[0][i]
            and paths[0][i]["travel_s"] > window[1] + 1e-9
        ):
            return finish(
                "INFEASIBLE",
                {
                    "code": "TIME_WINDOW_UNREACHABLE",
                    "goal_id": stop["goal_id"],
                    "earliest_s": paths[0][i]["travel_s"],
                    "window": window,
                },
            )
    solved = solve_constrained(
        stops,
        paths,
        robot,
        constraints,
        graph.weights,
        initial_payload_kg=completed_payload,
        initial_order=baseline["order"] if baseline and baseline["feasible"] else None,
    )
    plan["solver"].update(
        {
            key: value
            for key, value in solved.items()
            if key not in {"order", "arrivals", "consumed_wh"}
        }
    )
    plan["solver"].update(
        {
            "priority_weight": PRIORITY_WEIGHT,
            "charging_duration_model": "full_from_reserve_at_registered_simulation_charge_w",
            "charging_power_w": robot.charge_w,
            "pairwise_route_method": "directed_semantic_dijkstra",
            "optimality_scope": (
                "integer routing objective over precomputed graph-derived pairwise paths"
            ),
        }
    )
    if solved["order"] is None:
        return finish(
            "INFEASIBLE",
            {
                "code": "NO_RESOURCE_FEASIBLE_PLAN",
                "solver_status": solved["status"],
                "infeasibility_proven": solved.get("infeasibility_proven", False),
            },
        )
    chain = [0, *solved["order"], len(stops) - 1]
    plan["ordered_goal_ids"] = [stops[i]["goal_id"] for i in solved["order"]]
    planned_stops = []
    for i in solved["order"]:
        stop = copy.deepcopy(stops[i])
        stop.update(
            {
                "arrival_s": solved["arrivals"][i],
                "departure_s": solved["arrivals"][i] + stop["service_s"],
            }
        )
        stop["battery_arrival_pct"] = 100 * (1 - solved["consumed_wh"][i] / robot.battery_wh)
        if stop["kind"] == "charge":
            stop["battery_departure_pct"] = 100.0
        planned_stops.append(stop)
    plan["stops"] = planned_stops
    waits, consumed_energy, service_energy, waiting_energy = 0.0, 0.0, 0.0, 0.0
    for i, j in zip(chain, chain[1:], strict=False):
        source, target = stops[i], stops[j]
        path = copy.deepcopy(paths[i][j])
        wait = max(
            0,
            solved["arrivals"][j] - solved["arrivals"][i] - source["service_s"] - path["travel_s"],
        )
        waits += wait
        idle_energy = (
            0 if source["kind"] == "charge" else robot.idle_w * (source["service_s"] + wait) / 3600
        )
        service_energy += idle_energy
        waiting_energy += 0 if source["kind"] == "charge" else robot.idle_w * wait / 3600
        consumed_energy += path["energy_wh"] + idle_energy
        if target["kind"] != "finish":
            path.update(
                {
                    "from_goal_id": source["goal_id"],
                    "to_goal_id": target["goal_id"],
                    "from_occurrence_id": source.get("occurrence_id", "__start__"),
                    "to_occurrence_id": target.get("occurrence_id", target["goal_id"]),
                    "arrival_s": solved["arrivals"][j],
                }
            )
            plan["segments"].append(path)
    segments = plan["segments"]
    clearance_values = [s["min_clearance_m"] for s in segments if s["min_clearance_m"] is not None]
    service_s = sum(stops[i]["service_s"] for i in solved["order"])
    charging = sum(stops[i]["kind"] == "charge" for i in solved["order"])
    total_cost = solved["objective_integer"] / COST_SCALE
    plan["metrics"].update(
        {
            "distance_m": sum(s["distance_m"] for s in segments),
            "travel_s": sum(s["travel_s"] for s in segments),
            "service_s": service_s,
            "waiting_s": waits,
            "eta_s": solved["arrivals"][len(stops) - 1],
            "mission_completion_time_s": solved["arrivals"][len(stops) - 1],
            "energy_wh": consumed_energy,
            "battery_after_pct": 100
            * (1 - solved["consumed_wh"][len(stops) - 1] / robot.battery_wh),
            "min_clearance_m": min(clearance_values, default=None),
            "turn_count": sum(s["turn_count"] for s in segments),
            "payload_kg": demand,
            "charging_stops": charging,
            "task_stops": len(remaining),
            "cost_s": total_cost,
            "constraint_violations": 0,
        }
    )
    terms = {
        key: sum(s["cost_terms"][key] for s in segments)
        for key in (
            "travel_time_s",
            "rotation_time_s",
            "clearance_penalty",
            "risk",
            "semantic_penalty",
        )
    }
    priority_cost = PRIORITY_WEIGHT * sum(
        stops[i]["priority"] * solved["arrivals"][i] for i in solved["order"]
    )
    plan["objective_terms"] = {
        **terms,
        "drive_energy_wh": sum(s["energy_wh"] for s in segments),
        "service_and_wait_energy_wh": service_energy,
        "waiting_energy_wh": waiting_energy,
        "energy_objective_wh": sum(s["energy_wh"] for s in segments)
        + service_energy
        - waiting_energy,
        "service_time_s": service_s,
        "priority_arrival_penalty_s": priority_cost,
        "weights": copy.deepcopy(graph.weights),
        "total_cost_s": total_cost,
    }
    if baseline and baseline["feasible"]:
        plan["solver"]["exact_baseline"]["gap_s"] = total_cost - baseline["cost"]
        plan["solver"]["exact_baseline"]["matches_unconstrained_bound"] = (
            abs(total_cost - baseline["cost"]) <= (len(chain) + 1) / COST_SCALE
        )
    # The integer dimension is conservative. Audit transport metrics before READY.
    if plan["metrics"]["battery_after_pct"] < constraints["battery_reserve_pct"] - 1e-7:
        return finish("INFEASIBLE", {"code": "POSTSOLVE_BATTERY_AUDIT"})
    if any(
        s["time_window_s"]
        and not s["time_window_s"][0] - 1e-7 <= s["arrival_s"] <= s["time_window_s"][1] + 1e-7
        for s in planned_stops
    ):
        return finish("INFEASIBLE", {"code": "POSTSOLVE_TIME_WINDOW_AUDIT"})
    return finish("READY")
