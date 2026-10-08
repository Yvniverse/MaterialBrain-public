"""Shared read-only service for HTTP and Agent tools. No API-layer dependency."""

import copy
from functools import lru_cache
from threading import Lock

from .planner import MetricPlanner, load_world
from .schemas import PlanRequest

_build_lock = Lock()


@lru_cache(maxsize=1)
def world_snapshot():
    return load_world()


@lru_cache(maxsize=4)
def scenario_planner(scenario_id):
    world = world_snapshot()
    scenario = next(s for s in world["scenarios"] if s["id"] == scenario_id)
    return MetricPlanner(
        world,
        profile={
            **world["robot"],
            "battery_pct": scenario.get("battery_pct", world["robot"]["battery_pct"]),
        },
        obstacles=scenario["obstacles"],
    )


def plan_navigation(body: PlanRequest):
    world = world_snapshot()
    if body.world_revision and body.world_revision != world["revision_sha256"]:
        raise ValueError("WORLD_REVISION_MISMATCH")
    with _build_lock:
        p = scenario_planner(body.scenario_id)
    with p.lock:
        result = p.plan(
            body.goal_ids,
            start=body.start.model_dump() if body.start else None,
            payload_kg=body.payload_kg,
            optimize=body.optimize,
            battery_pct=body.battery_pct,
        )
    return {
        **result,
        "world_revision": world["revision_sha256"],
        "mode": "simulation",
        "scenario_id": body.scenario_id,
        "inventory_written": False,
        "start_pose": body.start.model_dump() if body.start else copy.deepcopy(world["home"]),
        "initial_payload_kg": body.payload_kg,
        "initial_battery_pct": body.battery_pct,
    }


def navigation_manifest():
    w = world_snapshot()
    return {
        "world_id": w["id"],
        "world_revision": w["revision_sha256"],
        "provenance": "synthetic_lab",
        "units": "m",
        "robot": copy.deepcopy(w["robot"]),
        "goals": copy.deepcopy(w["goals"]),
        "default_goal_ids": list(w["default_goal_ids"]),
        "scenarios": [{"id": s["id"], "name": s["name"]} for s in w["scenarios"]],
        "ui_path": "/warehouse-twin?workspace=robot-lab",
        "inventory_written": False,
    }
