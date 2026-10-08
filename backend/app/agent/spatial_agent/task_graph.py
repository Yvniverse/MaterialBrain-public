"""Dependency scheduler and pure, idempotent mission-event reducer."""

from __future__ import annotations

import copy
import hashlib
import json

from app.schemas.spatial import ExecutionEvent, MissionPlan, TaskGraph

MAX_EVENTS = 128
MAX_OBSERVED_STEPS = 512
_TERMINAL = {"COMPLETED", "CANCELLED", "FAILED"}
_EVENT_TYPES = {
    "started",
    "feedback",
    "arrived",
    "scan_rejected",
    "scan_verified",
    "handoff_verified",
    "obstacle_added",
    "replanning",
    "recovery",
    "charging",
    "cancelled",
    "transport_paused",
    "returned_home",
    "completed",
    "failed",
}
_EVENT_SKILLS = {
    "started": "navigate_mission",
    "feedback": "navigate_mission",
    "arrived": "navigate_mission",
    "scan_rejected": "confirm_scan",
    "scan_verified": "confirm_scan",
    "handoff_verified": "verify_handoff",
    "obstacle_added": "replan_remaining",
    "replanning": "replan_remaining",
    "recovery": "replan_remaining",
    "charging": "charge_robot",
    "cancelled": "wait_or_yield",
    "transport_paused": "wait_or_yield",
    "returned_home": "return_home",
    "completed": "summarize_mission",
    "failed": "wait_or_yield",
}


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _node(graph, node_id, skill, args, dependencies, status="PENDING", result=None):
    return {
        "id": node_id,
        "skill": skill,
        "args": copy.deepcopy(args),
        "dependencies": list(dependencies),
        "status": status,
        "result": result,
        "failure_code": None,
        "attempts": 0,
        "idempotency_key": _digest([graph["mission_id"], node_id, skill])[:40],
    }


def _mission_args(graph):
    return {"mission_id": graph["mission_id"], "map_revision": graph["map_revision"]}


def _append_stops(graph, plan, previous_id):
    """Build dependencies in the actual solver order, including repeated charging."""
    revision = graph["robot_state"]["task_graph_revision"]
    stop_index = {}
    for stop in plan.get("stops", []):
        stop_index.setdefault(stop["goal_id"], []).append(stop)
    completed = set(graph["completed_goal_ids"])
    for ordinal, goal_id in enumerate(plan["ordered_goal_ids"]):
        if goal_id in completed:
            continue
        stops = stop_index.get(goal_id, [])
        stop = stops.pop(0) if stops else {"goal_id": goal_id, "kind": "task"}
        kind = stop.get("kind", "task")
        if kind == "home" or goal_id == "HOME":
            continue
        occurrence = stop.get("occurrence_id", goal_id)
        prefix = f"r{revision}:s{ordinal}:{occurrence}"
        args = {**_mission_args(graph), "goal_id": goal_id}
        navigate = _node(graph, prefix + ":navigate", "navigate_mission", args, [previous_id])
        graph["nodes"].append(navigate)
        if kind == "charge" or goal_id == "CHARGER":
            final = _node(graph, prefix + ":charge", "charge_robot", args, [navigate["id"]])
            graph["nodes"].append(final)
        else:
            scan = _node(graph, prefix + ":scan", "confirm_scan", args, [navigate["id"]])
            final = _node(graph, prefix + ":handoff", "verify_handoff", args, [scan["id"]])
            graph["nodes"].extend([scan, final])
        previous_id = final["id"]
    return_required = any(
        segment.get("to_goal_id") == "HOME" for segment in plan.get("segments", [])
    )
    graph["robot_state"]["return_home_required"] = return_required
    if return_required:
        home = _node(
            graph, f"r{revision}:return", "return_home", _mission_args(graph), [previous_id]
        )
        graph["nodes"].append(home)
        previous_id = home["id"]
    graph["nodes"].append(
        _node(
            graph, f"r{revision}:summary", "summarize_mission", _mission_args(graph), [previous_id]
        )
    )


def build_task_graph(
    plan: dict,
    *,
    conversation_id: str,
    instruction: str = "",
    business_grounding: dict | None = None,
) -> dict:
    frozen = MissionPlan.model_validate(plan).model_dump(mode="json")
    business = copy.deepcopy(business_grounding or {})
    completed = list(dict.fromkeys(frozen["completed_goal_ids"]))
    charge_ids = {stop["goal_id"] for stop in frozen["stops"] if stop.get("kind") == "charge"}
    remaining = list(
        dict.fromkeys(
            goal
            for goal in frozen["ordered_goal_ids"]
            if goal not in completed and goal not in charge_ids | {"HOME", "CHARGER"}
        )
    )
    graph = {
        "schema_version": 1,
        "task_id": "ST-" + _digest([conversation_id, frozen["mission_id"]])[:24],
        "conversation_id": conversation_id,
        "map_id": frozen["map_id"],
        "map_revision": frozen["map_revision"],
        "mission_id": frozen["mission_id"],
        "nodes": [],
        "current_skill": None,
        "completed_goal_ids": completed,
        "remaining_goal_ids": remaining,
        "robot_state": {
            "instruction": instruction,
            "business_grounding": business,
            "task_graph_revision": 1,
            "last_event_sequence": -1,
            "observed_step_count": 0,
            "observed_steps": [],
            "registered_goal_ids": sorted(
                set(
                    frozen["ordered_goal_ids"]
                    + completed
                    + [s.get("to_goal_id", "") for s in frozen["segments"]]
                )
                - {""}
            ),
            "battery_pct": frozen["constraints"].get("battery_pct"),
            "payload_kg": frozen["constraints"].get("completed_payload_kg", 0),
            "execution_boundary": "ros2_nav2_simulation",
            "inventory_written": False,
            "recovery_count": 0,
            "recovery_success_count": 0,
            "transport_retry_count": 0,
        },
        "recovery": None,
        "events": [],
        "last_valid_plan": frozen if frozen["status"] == "READY" else None,
        "status": "READY" if frozen["status"] == "READY" else frozen["status"],
    }
    instruction_args = {"instruction": instruction or "registered spatial mission"}
    has_business = business.get("status") == "GROUNDED"
    graph["nodes"] = [
        _node(
            graph,
            "resolve_bom",
            "resolve_engineering_bom",
            instruction_args,
            [],
            "SUCCEEDED" if has_business and business.get("bom") else "SKIPPED",
            business.get("bom"),
        ),
        _node(
            graph,
            "inventory",
            "check_inventory",
            instruction_args,
            ["resolve_bom"],
            "SUCCEEDED" if has_business else "SKIPPED",
            business.get("inventory"),
        ),
        _node(
            graph,
            "locations",
            "resolve_pick_locations",
            instruction_args,
            ["inventory"],
            "SUCCEEDED",
            business.get("pick_locations")
            if has_business
            else {"source": "registered_mission_plan", "goal_ids": remaining},
        ),
        _node(
            graph,
            "spatial",
            "query_spatial_context",
            {
                "map_id": frozen["map_id"],
                "map_revision": frozen["map_revision"],
                "query": "affordances",
            },
            ["locations"],
            "SUCCEEDED",
            {"source": "deterministic_plan", "map_revision": frozen["map_revision"]},
        ),
        _node(
            graph,
            "plan",
            "plan_mission",
            {
                "map_id": frozen["map_id"],
                "map_revision": frozen["map_revision"],
                "goal_ids": completed + remaining,
                "profile": frozen["profile"],
            },
            ["spatial"],
            "SUCCEEDED" if frozen["status"] == "READY" else "BLOCKED",
            {
                "status": frozen["status"],
                "solver": frozen["solver"],
                "violations": frozen["violations"],
            },
        ),
    ]
    if frozen["status"] == "READY":
        _append_stops(graph, frozen, "plan")
        _select_next(graph)
    else:
        graph["recovery"] = {
            "code": "MISSION_" + frozen["status"],
            "action": "clarify",
            "violations": frozen["violations"],
            "completed_goal_ids": completed[:],
        }
    graph["robot_state"]["initial_state"] = _state(graph)
    return TaskGraph.model_validate(graph).model_dump(mode="json")


def ready_nodes(graph: dict, capabilities: set[str] | None = None) -> list[dict]:
    """Only dependency-ready nodes may be selected; paused tasks dispatch nothing."""
    if graph["status"] in _TERMINAL | {
        "BLOCKED",
        "PAUSED",
        "TRANSPORT_PAUSED",
        "CLARIFICATION",
        "INFEASIBLE",
    }:
        return []
    done = {node["id"] for node in graph["nodes"] if node["status"] in {"SUCCEEDED", "SKIPPED"}}
    nodes = [
        node
        for node in graph["nodes"]
        if node["status"] == "PENDING" and set(node["dependencies"]).issubset(done)
    ]
    if capabilities is not None:
        from .skills import skill_manifest

        required = {item["name"]: set(item["required_capabilities"]) for item in skill_manifest()}
        nodes = [node for node in nodes if required[node["skill"]].issubset(capabilities)]
    return copy.deepcopy(nodes)


def _select_next(graph):
    running = next(
        (node for node in graph["nodes"] if node["status"] in {"RUNNING", "RECOVERING"}), None
    )
    ready = ready_nodes(graph)
    graph["current_skill"] = (running or (ready[0] if ready else {})).get("skill")


def _find(graph, skill, goal_id=None, *, done=False):
    return next(
        (
            node
            for node in graph["nodes"]
            if node["skill"] == skill
            and (goal_id is None or node["args"].get("goal_id") == goal_id)
            and (done or node["status"] not in {"SUCCEEDED", "SKIPPED"})
        ),
        None,
    )


def _dependencies_done(graph, node):
    done = {item["id"] for item in graph["nodes"] if item["status"] in {"SUCCEEDED", "SKIPPED"}}
    return bool(node) and set(node["dependencies"]).issubset(done)


def _run(graph, node):
    if node is None or not _dependencies_done(graph, node):
        raise ValueError("SPATIAL_EVENT_DEPENDENCY_REQUIRED")
    if node["status"] not in {"RUNNING", "RECOVERING"}:
        node["attempts"] += 1
    node["status"] = "RUNNING"


def _succeed(graph, node, event):
    _run(graph, node)
    node.update(
        status="SUCCEEDED",
        failure_code=None,
        result={
            "event_id": event["event_id"],
            "sequence": event["sequence"],
            "source": "observed_execution_event",
            "details": event["details"],
        },
    )


def _recover(graph, code, action="replan_remaining", **details):
    graph["robot_state"]["recovery_count"] += 1
    graph["status"] = "BLOCKED" if action in {"reground_map", "wait_or_yield"} else "RECOVERING"
    graph["recovery"] = {
        "code": code,
        "action": action,
        "completed_goal_ids": graph["completed_goal_ids"][:],
        **details,
    }
    for node in graph["nodes"]:
        if node["skill"] in {"navigate_mission", "return_home"} and node["status"] == "RUNNING":
            node.update(
                status="RECOVERING" if action == "replan_remaining" else "BLOCKED",
                failure_code=code,
            )
    if action == "replan_remaining":
        node_id = "recovery:" + str(graph["robot_state"]["recovery_count"])
        graph["nodes"].append(
            _node(
                graph,
                node_id,
                "replan_remaining",
                {**_mission_args(graph), "reason": code},
                ["plan"],
            )
        )
    graph["current_skill"] = "query_spatial_context" if action == "reground_map" else action


def _apply_replan(graph, event):
    source = event["details"].get("source")
    if source in {"canonical_overlay_stop", "nav2_global_path", "nav2_validated_overlay_path"}:
        # Nav2 refreshes a local/global motion path without changing semantic
        # mission ordering. It is not a new server mission-plan contract.
        if source == "canonical_overlay_stop":
            graph["robot_state"]["overlay_path_pending"] = True
        elif source == "nav2_validated_overlay_path":
            graph["robot_state"]["overlay_path_pending"] = False
        if (graph.get("recovery") or {}).get("code") == "LOW_BATTERY":
            return  # A motion-path update cannot prove charger/resource feasibility.
        if source == "canonical_overlay_stop":
            _recover(graph, "BLOCKED_PATH", goal_id=event["goal_id"], source=source)
            return
        if source == "nav2_global_path" and graph["robot_state"].get("overlay_path_pending"):
            return  # The bridge has not yet validated an obstacle-safe path.
        _resolve_recovery(graph, event)
        for node in graph["nodes"]:
            if node["status"] == "RECOVERING" and node["skill"] in {
                "navigate_mission",
                "return_home",
            }:
                node.update(status="RUNNING", failure_code=None)
        graph["status"] = "RUNNING"
        _select_next(graph)
        return
    if source not in {None, "server_semantic_replan"}:
        raise ValueError("SPATIAL_REPLAN_SOURCE")
    raw_plan = event["details"].get("plan") or graph.get("last_valid_plan")
    if not raw_plan:
        raise ValueError("SPATIAL_REPLAN_REQUIRED")
    plan = MissionPlan.model_validate(raw_plan).model_dump(mode="json")
    if plan["mission_id"] != graph["mission_id"] or plan["map_revision"] != graph["map_revision"]:
        raise ValueError("SPATIAL_REPLAN_IDENTITY")
    if plan["status"] != "READY":
        _recover(graph, "UNREACHABLE", "wait_or_yield", violations=plan["violations"])
        return
    if not set(graph["completed_goal_ids"]).issubset(plan["completed_goal_ids"]):
        raise ValueError("SPATIAL_REPLAN_COMPLETED_STOPS_REQUIRED")
    expected = set(graph["remaining_goal_ids"])
    planned = {stop["goal_id"] for stop in plan["stops"] if stop.get("kind") == "task"}
    if planned != expected:
        raise ValueError("SPATIAL_REPLAN_REMAINING_STOPS_REQUIRED")
    if graph["recovery"]:
        graph["robot_state"]["recovery_success_count"] = graph["robot_state"]["recovery_count"]
    # Keep the exact succeeded scan/handoff nodes and their stable retry keys.
    graph["nodes"] = [
        node
        for node in graph["nodes"]
        if node["status"] in {"SUCCEEDED", "SKIPPED"}
        or node["id"] in {"resolve_bom", "inventory", "locations", "spatial", "plan"}
    ]
    graph["robot_state"]["task_graph_revision"] += 1
    graph["robot_state"]["overlay_path_pending"] = False
    graph["last_valid_plan"] = plan
    graph["remaining_goal_ids"] = [goal for goal in plan["ordered_goal_ids"] if goal in expected]
    _append_stops(graph, plan, "plan")
    graph.update(status="RUNNING", recovery=None)
    _select_next(graph)


def _resolve_recovery(graph, event):
    if (graph.get("recovery") or {}).get("action") == "replan_remaining":
        graph["robot_state"]["recovery_success_count"] = graph["robot_state"]["recovery_count"]
        for node in graph["nodes"]:
            if node["skill"] == "replan_remaining" and node["status"] in {"PENDING", "RUNNING"}:
                _succeed(graph, node, event)
        graph["recovery"] = None


def _state(graph):
    robot = graph["robot_state"]
    return {
        "map_id": graph["map_id"],
        "map_revision": graph["map_revision"],
        "mission_id": graph["mission_id"],
        "status": graph["status"],
        "current_skill": graph["current_skill"],
        "completed_goal_ids": graph["completed_goal_ids"][:],
        "remaining_goal_ids": graph["remaining_goal_ids"][:],
        "pose": copy.deepcopy(robot.get("current_pose")),
        "battery_pct": robot.get("battery_pct"),
        "payload_kg": robot.get("payload_kg"),
        "task_graph_revision": robot["task_graph_revision"],
    }


def _observe(graph, event, state, chosen):
    robot = graph["robot_state"]
    robot["observed_step_count"] += 1
    # Frequent feedback stays in the bounded ledger; dataset decisions capture
    # actual transitions instead of fabricating independent frames as episodes.
    if event["type"] == "feedback" and not graph["recovery"]:
        return
    skill = _event_skill(event)
    args = copy.deepcopy(chosen["args"] if chosen else _mission_args(graph))
    if event["goal_id"] and skill not in {
        "return_home",
        "summarize_mission",
        "replan_remaining",
        "wait_or_yield",
    }:
        args["goal_id"] = event["goal_id"]
    if skill == "confirm_scan":
        args["scan_code"] = event["details"].get("scan_code", event["details"].get("slot", ""))
    if skill == "replan_remaining":
        args = {
            **_mission_args(graph),
            "reason": event["details"].get("reason", "observed_replanning"),
        }
    robot["observed_steps"].append(
        {
            "sequence": event["sequence"],
            "source": "observed_execution_event",
            "state": state,
            "chosen_skill": {"name": skill, "args": args},
            "expert_skill": {
                "name": skill,
                "args": copy.deepcopy(args),
                "source": "deterministic_event_policy",
            },
            "outcome": copy.deepcopy(event),
            "verifier": {
                "registered_goal": not event["goal_id"]
                or event["goal_id"] in robot["registered_goal_ids"],
                "map_revision_matches": event["map_revision"] == graph["map_revision"],
                "inventory_written": event["details"].get("inventory_written", False),
                "hardware_control": event["details"].get("hardware_control", False),
            },
        }
    )
    if len(robot["observed_steps"]) > MAX_OBSERVED_STEPS:
        robot["observed_steps"] = robot["observed_steps"][-MAX_OBSERVED_STEPS:]
        robot["episode_trace_truncated"] = True


def _event_skill(event):
    if event["type"] == "charging" and event["details"].get("state") == "required":
        return "replan_remaining"
    if event["goal_id"] == "HOME" and event["type"] in {"feedback", "arrived"}:
        return "return_home"
    return _EVENT_SKILLS[event["type"]]


def reduce_execution_event(graph: dict, event: dict) -> dict:
    """Project validated events without inventory access, wall-clock progress or LLMs."""
    result = copy.deepcopy(graph)
    frozen = ExecutionEvent.model_validate(event).model_dump(mode="json")
    if frozen["mission_id"] != result["mission_id"]:
        raise ValueError("SPATIAL_MISSION_MISMATCH")
    if frozen["type"] not in _EVENT_TYPES:
        raise ValueError("SPATIAL_EVENT_NOT_REGISTERED")
    robot = result["robot_state"]
    # This cursor is persisted separately from the 128-event UI history, so an
    # old delivery cannot become new after the ledger has rolled over.
    cursor = robot.get(
        "last_event_sequence", max((e["sequence"] for e in result["events"]), default=-1)
    )
    if frozen["sequence"] <= cursor:
        return result
    if any(item["event_id"] == frozen["event_id"] for item in result["events"]):
        raise ValueError("SPATIAL_EVENT_ID_REUSED")
    goal = frozen["goal_id"]
    if goal and goal not in robot["registered_goal_ids"]:
        raise ValueError("SPATIAL_UNREGISTERED_FEEDBACK")
    details = frozen["details"]
    if details.get("inventory_written") or details.get("hardware_control"):
        raise ValueError("SPATIAL_EXECUTION_BOUNDARY")
    boundary = details.get("execution_boundary", "ros2_nav2_simulation")
    if boundary != "ros2_nav2_simulation":
        raise ValueError("SPATIAL_EXECUTION_BOUNDARY")
    state = _state(result)
    chosen = _find(result, _event_skill(frozen), goal if goal != "HOME" else None)
    stale = frozen["map_revision"] != result["map_revision"]
    if stale:
        _recover(result, "STALE_MAP", "reground_map", observed_map_revision=frozen["map_revision"])
    elif result.get("recovery", {}) and result["recovery"].get("code") == "STALE_MAP":
        pass  # Re-grounding must create a reviewed graph; old-route events cannot resume it.
    elif result["status"] in _TERMINAL:
        pass
    else:
        if frozen["pose"] is not None:
            robot["current_pose"] = frozen["pose"]
        for key in ("battery_pct", "payload_kg"):
            if frozen[key] is not None:
                robot[key] = frozen[key]
        if goal:
            robot["current_goal_id"] = goal
        kind = frozen["type"]
        if kind in {"started", "feedback"}:
            if result["status"] not in {"AWAITING_HANDOFF", "CHARGING", "BLOCKED", "RECOVERING"}:
                nav = (
                    _find(result, "navigate_mission", goal)
                    if goal
                    else _find(result, "navigate_mission")
                )
                if nav:
                    _run(result, nav)
                else:
                    home = _find(result, "return_home")
                    if home and _dependencies_done(result, home):
                        _run(result, home)
                result.update(status="RUNNING", recovery=None)
                robot["transport_retry_count"] = 0
            reserve = (
                (result.get("last_valid_plan") or {})
                .get("constraints", {})
                .get("battery_reserve_pct", 15)
            )
            if (
                frozen["battery_pct"] is not None
                and frozen["battery_pct"] < reserve
                and result["status"] not in {"CHARGING", "BLOCKED"}
            ):
                _recover(result, "LOW_BATTERY", charger_feasibility_required=True)
        elif kind == "arrived":
            _resolve_recovery(result, frozen)
            if goal == "HOME":
                _succeed(result, _find(result, "return_home"), frozen)
                result["status"] = "RETURNED_HOME"
            else:
                _succeed(result, _find(result, "navigate_mission", goal), frozen)
                charge = _find(result, "charge_robot", goal)
                next_node = charge or _find(result, "confirm_scan", goal)
                _run(result, next_node)
                result["status"] = "CHARGING" if charge else "AWAITING_HANDOFF"
        elif kind == "scan_rejected":
            node = _find(result, "confirm_scan", goal)
            _run(result, node)
            node.update(
                failure_code="WRONG_SCAN",
                result={"accepted": False, "event_id": frozen["event_id"]},
            )
            robot["scan_rejection_count"] = robot.get("scan_rejection_count", 0) + 1
            result.update(
                status="AWAITING_HANDOFF",
                recovery={
                    "code": "WRONG_SCAN",
                    "action": "confirm_scan",
                    "goal_id": goal,
                    "inventory_written": False,
                },
            )
        elif kind == "scan_verified":
            _succeed(result, _find(result, "confirm_scan", goal), frozen)
            _run(result, _find(result, "verify_handoff", goal))
            result.update(status="AWAITING_HANDOFF", recovery=None)
        elif kind == "handoff_verified":
            if goal not in result["completed_goal_ids"]:
                _succeed(result, _find(result, "verify_handoff", goal), frozen)
                result["completed_goal_ids"].append(goal)
                result["remaining_goal_ids"] = [
                    item for item in result["remaining_goal_ids"] if item != goal
                ]
            result.update(status="RUNNING", recovery=None)
        elif kind == "charging":
            node = _find(result, "charge_robot", goal)
            if details.get("state") == "required":
                _recover(result, "LOW_BATTERY", charger_feasibility_required=True)
            elif details.get("state") in {"complete", "completed"}:
                _succeed(result, node, frozen)
                result.update(status="RUNNING", recovery=None)
            else:
                _run(result, node)
                result.update(
                    status="CHARGING", recovery={"code": "CHARGING", "action": "charge_robot"}
                )
        elif kind in {"obstacle_added", "recovery"}:
            code = details.get("code", details.get("reason", "BLOCKED_PATH"))
            unreachable = "UNREACHABLE" in code.upper() or details.get("unreachable")
            _recover(
                result,
                code,
                "wait_or_yield" if unreachable else "replan_remaining",
                goal_id=goal,
                charger_feasibility_required="BATTERY" in code.upper(),
            )
        elif kind == "replanning":
            _apply_replan(result, frozen)
        elif kind == "transport_paused":
            retries = robot.get("transport_retry_count", 0) + 1
            robot["transport_retry_count"] = retries
            result.update(
                status="TRANSPORT_PAUSED" if retries <= 3 else "PAUSED",
                recovery={
                    "code": "TRANSPORT_PAUSED",
                    "action": "retry_transport" if retries <= 3 else "wait_or_yield",
                    "attempt": retries,
                    "max_attempts": 3,
                    "safe_retry": retries <= 3,
                    "next_retry_delay_s": [1, 2, 4][min(retries - 1, 2)] if retries <= 3 else None,
                    "idempotency_key": robot.get("operation_id", result["mission_id"]),
                    "completed_goal_ids": result["completed_goal_ids"][:],
                },
            )
        elif kind == "returned_home":
            if result["remaining_goal_ids"]:
                raise ValueError("SPATIAL_REQUIRED_STOPS_INCOMPLETE")
            home = _find(result, "return_home")
            if home:
                _succeed(result, home, frozen)
            result["status"] = "RETURNED_HOME"
        elif kind == "completed":
            if result["remaining_goal_ids"] or (
                robot["return_home_required"] and _find(result, "return_home")
            ):
                raise ValueError("SPATIAL_REQUIRED_STOPS_INCOMPLETE")
            _succeed(result, _find(result, "summarize_mission"), frozen)
            result.update(status="COMPLETED", recovery=None)
        elif kind == "cancelled":
            for node in result["nodes"]:
                if node["status"] in {"PENDING", "RUNNING", "RECOVERING", "BLOCKED"}:
                    node.update(status="SKIPPED", failure_code="CANCELLED")
            result.update(status="CANCELLED", recovery=None)
        elif kind == "failed":
            code = str(details.get("code", details.get("reason", "NAVIGATION_FAILED")))
            recoverable = any(
                marker in code.upper() for marker in ("UNREACHABLE", "BATTERY", "STALE", "BLOCKED")
            )
            _recover(result, code, "reground_map" if "STALE" in code.upper() else "wait_or_yield")
            if not recoverable:
                result["status"] = "FAILED"
        if (
            kind not in {"obstacle_added", "recovery", "transport_paused", "failed"}
            and not (result.get("recovery") or {}).get("action") == "replan_remaining"
        ):
            _select_next(result)
    robot["last_event_sequence"] = frozen["sequence"]
    result["events"].append(frozen)
    result["events"] = result["events"][-MAX_EVENTS:]
    _observe(result, frozen, state, chosen)
    return TaskGraph.model_validate(result).model_dump(mode="json")
