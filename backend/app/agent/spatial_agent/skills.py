"""A model can select these high-level skills; it cannot choose coordinates or writes."""

from __future__ import annotations

import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SkillArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class BusinessArguments(SkillArguments):
    instruction: str = Field(min_length=1, max_length=2000)


class ContextArguments(SkillArguments):
    map_id: str = Field(min_length=1, max_length=100)
    map_revision: str = Field(min_length=1, max_length=100)
    query: str = Field(default="affordances", min_length=1, max_length=500)


class PlanArguments(SkillArguments):
    map_id: str = Field(min_length=1, max_length=100)
    map_revision: str = Field(min_length=1, max_length=100)
    goal_ids: list[str] = Field(min_length=1, max_length=32)
    profile: Literal["fastest", "safest", "esd_safe"] = "fastest"


class MissionArguments(SkillArguments):
    mission_id: str = Field(min_length=1, max_length=160)
    map_revision: str = Field(min_length=1, max_length=100)


class GoalArguments(MissionArguments):
    goal_id: str = Field(min_length=1, max_length=100)


class ScanArguments(GoalArguments):
    scan_code: str = Field(min_length=1, max_length=200)


class ReplanArguments(MissionArguments):
    reason: str = Field(default="requested", min_length=1, max_length=200)
    profile: Literal["fastest", "safest", "esd_safe"] | None = None


class WaitArguments(MissionArguments):
    reason: str = Field(min_length=1, max_length=200)
    duration_s: int = Field(default=5, ge=0, le=300)


# Failure codes describe deterministic boundaries, never a permission for a model
# to perform an inventory transaction or actuate a physical robot.
_SKILLS = (
    (
        "resolve_engineering_bom",
        BusinessArguments,
        "read_only",
        ["business_scope"],
        ["engineering_bom_grounded"],
        ["BOM_SCOPE_REQUIRED", "BOM_VERSION_REQUIRED"],
        ["business_read"],
    ),
    (
        "check_inventory",
        BusinessArguments,
        "read_only",
        ["engineering_bom_grounded"],
        ["inventory_checked"],
        ["INSUFFICIENT_STOCK", "INVENTORY_UNKNOWN"],
        ["inventory_read"],
    ),
    (
        "resolve_pick_locations",
        BusinessArguments,
        "read_only",
        ["inventory_checked"],
        ["registered_locations_grounded"],
        ["UNMAPPED_OR_UNLOCATABLE_DEMAND"],
        ["location_read"],
    ),
    (
        "query_spatial_context",
        ContextArguments,
        "read_only",
        ["registered_map_revision"],
        ["geometry_rules_affordances_read"],
        ["STALE_MAP", "MAP_NOT_REGISTERED"],
        ["spatial_read"],
    ),
    (
        "plan_mission",
        PlanArguments,
        "read_only",
        ["registered_goals", "current_map_revision"],
        ["deterministic_feasible_plan"],
        ["UNREACHABLE", "PAYLOAD_LIMIT", "BATTERY_RESERVE"],
        ["spatial_plan"],
    ),
    (
        "navigate_mission",
        GoalArguments,
        "simulation_execute",
        ["ready_plan", "dependencies_succeeded"],
        ["observed_arrival"],
        ["BLOCKED_PATH", "STALE_MAP", "TRANSPORT_PAUSED", "UNREACHABLE"],
        ["navigate"],
    ),
    (
        "verify_handoff",
        GoalArguments,
        "simulation_execute",
        ["observed_arrival", "scan_verified"],
        ["handoff_verified", "completed_stop_remembered"],
        ["WRONG_SCAN", "HANDOFF_REQUIRED"],
        ["human_handoff"],
    ),
    (
        "confirm_scan",
        ScanArguments,
        "read_only",
        ["observed_arrival", "registered_goal"],
        ["scan_verified"],
        ["WRONG_SCAN", "WRONG_LOCATION"],
        ["scan_code"],
    ),
    (
        "replan_remaining",
        ReplanArguments,
        "read_only",
        ["current_pose", "current_map_revision"],
        ["remaining_route_replanned", "completed_stops_preserved"],
        ["UNREACHABLE", "STALE_MAP"],
        ["spatial_plan"],
    ),
    (
        "charge_robot",
        GoalArguments,
        "simulation_execute",
        ["registered_charger", "observed_arrival"],
        ["observed_simulation_charge"],
        ["CHARGER_UNREACHABLE", "TRANSPORT_PAUSED"],
        ["charge"],
    ),
    (
        "wait_or_yield",
        WaitArguments,
        "simulation_execute",
        ["mission_owned"],
        ["mission_paused_without_inventory_write"],
        ["WAIT_TIMEOUT"],
        ["wait_or_yield"],
    ),
    (
        "return_home",
        MissionArguments,
        "simulation_execute",
        ["all_required_handoffs_verified"],
        ["observed_home_arrival"],
        ["HOME_UNREACHABLE", "BATTERY_RESERVE"],
        ["navigate"],
    ),
    (
        "summarize_mission",
        MissionArguments,
        "read_only",
        ["observed_execution_memory"],
        ["structured_summary_and_replay"],
        ["TRACE_INCOMPLETE"],
        ["spatial_read"],
    ),
)

_ARGUMENTS = {row[0]: row[1] for row in _SKILLS}


def skill_manifest() -> list[dict]:
    """Return an isolated JSON-schema manifest for all thirteen server-owned skills."""
    return [
        {
            "name": name,
            "version": "1.0.0",
            "mode": mode,
            "arguments_schema": args.model_json_schema(),
            "preconditions": preconditions[:],
            "effects": effects[:],
            "failure_codes": failures[:],
            "idempotency": "safe_retry" if mode == "read_only" else "key_required",
            "reversible": name not in {"verify_handoff", "charge_robot"},
            "required_capabilities": capabilities[:],
            "execution_boundary": "read_only" if mode == "read_only" else "ros2_nav2_simulation",
            "inventory_write": False,
            "hardware_control": False,
        }
        for name, args, mode, preconditions, effects, failures, capabilities in _SKILLS
    ]


def validate_skill_selection(
    selection: dict,
    *,
    snapshot: dict,
    graph: dict | None = None,
    capabilities: set[str] | None = None,
) -> dict:
    """Validate controlled/fake-model output before any service or transport dispatch.

    This function validates a choice, never executes it. A graph additionally
    confines the choice to its dependency-ready nodes; a model cannot skip scans
    or replace an engineering demand with unrelated registered stops.
    """
    if not isinstance(selection, dict) or set(selection) - {"name", "args"}:
        raise ValueError("SPATIAL_SKILL_SELECTION_SCHEMA")
    name = selection.get("name")
    if name not in _ARGUMENTS:
        raise ValueError("SPATIAL_SKILL_NOT_REGISTERED")
    try:
        args = _ARGUMENTS[name].model_validate(selection.get("args", {})).model_dump()
    except ValidationError as exc:
        raise ValueError("SPATIAL_SKILL_ARGUMENTS_INVALID") from exc
    if args.get("map_id", snapshot.get("map_id")) != snapshot.get("map_id"):
        raise ValueError("SPATIAL_MAP_NOT_REGISTERED")
    if args.get("map_revision", snapshot.get("revision")) != snapshot.get("revision"):
        raise ValueError("SPATIAL_STALE_MAP")
    docks = {dock["id"]: dock for dock in snapshot.get("docks", [])}
    goal_ids = args.get("goal_ids", []) + ([args["goal_id"]] if "goal_id" in args else [])
    if not set(goal_ids).issubset(docks) or len(goal_ids) != len(set(goal_ids)):
        raise ValueError("SPATIAL_GOAL_NOT_REGISTERED")
    if name == "charge_robot" and "charge" not in docks[args["goal_id"]].get("capabilities", []):
        raise ValueError("SPATIAL_CHARGER_REQUIRED")
    manifest = next(item for item in skill_manifest() if item["name"] == name)
    if capabilities is not None and not set(manifest["required_capabilities"]).issubset(
        capabilities
    ):
        raise ValueError("SPATIAL_CAPABILITY_REQUIRED")
    if graph is not None:
        from .task_graph import ready_nodes

        if graph["status"] in {
            "BLOCKED",
            "PAUSED",
            "TRANSPORT_PAUSED",
            "INFEASIBLE",
            "CLARIFICATION",
        }:
            raise ValueError("SPATIAL_MISSION_PAUSED")
        if (graph.get("recovery") or {}).get("action") == "replan_remaining" and name not in {
            "replan_remaining",
            "wait_or_yield",
        }:
            raise ValueError("SPATIAL_RECOVERY_REQUIRED")
        if graph["map_revision"] != snapshot["revision"]:
            raise ValueError("SPATIAL_STALE_MAP")
        if args.get("mission_id", graph["mission_id"]) != graph["mission_id"]:
            raise ValueError("SPATIAL_MISSION_MISMATCH")
        business = graph.get("robot_state", {}).get("business_grounding") or {}
        if name == "plan_mission" and business.get("status") == "GROUNDED":
            if set(args["goal_ids"]) != set(business.get("goal_ids", [])):
                raise ValueError("SPATIAL_BUSINESS_GOALS_REQUIRED")
        candidates = [node for node in ready_nodes(graph) if node["skill"] == name]
        candidates += [
            node
            for node in graph["nodes"]
            if node["skill"] == name and node["status"] in {"RUNNING", "RECOVERING"}
        ]
        if not any(
            all(
                args.get(key) == node["args"].get(key)
                for key in ("goal_id", "mission_id")
                if key in args
            )
            for node in candidates
        ):
            raise ValueError("SPATIAL_SKILL_DEPENDENCY_REQUIRED")
    return {"name": name, "args": copy.deepcopy(args)}
