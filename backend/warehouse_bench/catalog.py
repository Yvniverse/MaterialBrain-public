"""Seeded scenarios grounded only to registered canonical warehouse docks."""

from __future__ import annotations

import copy
import random


def scenario_catalog(snapshot: dict, seed: int) -> list[dict]:
    robot = snapshot["provenance"]["robot"]
    docks = {d["id"]: d for d in snapshot["docks"]}
    goals = list(snapshot["provenance"]["default_goal_ids"])
    random.Random(seed).shuffle(goals)
    all_tasks = sorted(
        d["id"]
        for d in docks.values()
        if d.get("human_handoff_only") and d["id"] not in {"HOME", "CHARGER"}
    )
    base = {
        "map_id": snapshot["map_id"],
        "map_revision": snapshot["revision"],
        "start_pose": copy.deepcopy(robot["pose"]),
        "goal_ids": goals,
        "constraints": {
            "battery_pct": robot["battery_pct"],
            "payload_capacity_kg": robot["payload_kg"],
            "battery_reserve_pct": robot["reserve_pct"],
            "robot_class": robot["id"],
        },
        "return_home": True,
    }

    def case(identifier, tier, overrides=None, expected="READY", baseline=True):
        return {
            "id": identifier,
            "tier": tier,
            "expected_status": expected,
            "baseline_supported": baseline,
            "request": {**copy.deepcopy(base), **(overrides or {})},
        }

    priority_stops = [
        {
            "goal_id": code,
            "service_s": docks[code].get("service_s", 5),
            "demand_kg": docks[code].get("payload_kg", 0),
            "priority": 80 if code == all_tasks[-1] else 0,
            "time_window_s": [0, 3600],
        }
        for code in all_tasks
    ]
    return [
        case("easy_static_single", "easy", {"goal_ids": goals[:1]}),
        case("medium_multi_6", "medium"),
        case("semantic_safest", "semantic", {"profile": "safest"}),
        case("semantic_esd_safe", "semantic", {"profile": "esd_safe"}),
        case(
            "semantic_exposure_safest",
            "semantic",
            {"profile": "safest", "start_pose": docks["P-LAB"]["pose"], "goal_ids": ["P-MODULE"]},
        ),
        case(
            "semantic_exposure_esd_safe",
            "semantic",
            {"profile": "esd_safe", "start_pose": docks["P-LAB"]["pose"], "goal_ids": ["P-MODULE"]},
        ),
        case(
            "hard_capacity_priority",
            "hard",
            {"goal_ids": all_tasks, "stops": priority_stops},
            baseline=False,
        ),
        case("recovery_blocked_aisle", "recovery"),
        case(
            "recovery_low_battery",
            "recovery",
            {"constraints": {**base["constraints"], "battery_pct": 15.5}},
            baseline=True,
        ),
        case(
            "infeasible_capacity",
            "constraints",
            {"constraints": {**base["constraints"], "payload_capacity_kg": 1}},
            expected="INFEASIBLE",
        ),
        case(
            "infeasible_time_window",
            "constraints",
            {
                "goal_ids": goals[:1],
                "stops": [
                    {
                        "goal_id": goals[0],
                        "time_window_s": [0, 1],
                        "service_s": docks[goals[0]].get("service_s", 5),
                        "demand_kg": docks[goals[0]].get("payload_kg", 0),
                    }
                ],
            },
            expected="INFEASIBLE",
            baseline=False,
        ),
        case("long_horizon_eight", "long_horizon", {"goal_ids": all_tasks[:8]}),
    ]
