"""Observed mission episodes and explicit verifier components for future training."""

from __future__ import annotations

import copy

from .skills import skill_manifest


def verifier_reward(episode: dict) -> dict:
    """Keep unobserved safety/efficiency unknown; never reward invented execution.

    Components are stored separately so a future GRPO/RLVR trainer can choose
    coefficients without regenerating episode truth. No training is run here.
    """
    steps = episode.get("steps", [])
    names = {item["name"] for item in episode.get("available_skills", [])}
    observed = [step for step in steps if step.get("source") == "observed_execution_event"]
    skill_correct = bool(observed) and all(
        step.get("chosen_skill", {}).get("name") in names
        and step.get("chosen_skill", {}).get("name") == step.get("expert_skill", {}).get("name")
        for step in observed
    )
    arguments_grounded = bool(observed) and all(
        step.get("verifier", {}).get("registered_goal") is True
        and step.get("verifier", {}).get("map_revision_matches") is True
        for step in observed
    )
    unauthorized = sum(
        bool(
            step.get("verifier", {}).get("inventory_written")
            or step.get("verifier", {}).get("hardware_control")
        )
        for step in observed
    )
    metrics = episode.get("final_metrics", {})
    collision_count = metrics.get("collision_count")
    baseline = metrics.get("planned_distance_m")
    measured = metrics.get("observed_distance_m")
    efficiency = (
        min(1.0, baseline / measured)
        if baseline is not None and measured and measured > 0
        else None
    )
    final_state = episode.get("final_state", {})
    complete_event = any(step.get("outcome", {}).get("type") == "completed" for step in observed)
    components = {
        "task_success": bool(
            complete_event
            and final_state.get("status") == "COMPLETED"
            and not final_state.get("remaining_goal_ids")
        ),
        "correct_skill": skill_correct,
        "grounded_arguments": arguments_grounded,
        "route_valid": metrics.get("plan_route_valid"),
        "no_collision": collision_count == 0 if collision_count is not None else None,
        "recovery_success": (
            metrics.get("recovery_success_count", 0) >= metrics["recovery_count"]
            if metrics.get("recovery_count", 0)
            else None
        ),
        "route_cost_efficiency": efficiency,
        "unauthorized_action_penalty": unauthorized,
    }
    known_terms = [
        float(value)
        for key, value in components.items()
        if key != "unauthorized_action_penalty" and value is not None
    ]
    return {
        "components": components,
        "scalar": sum(known_terms) - unauthorized,
        "known_component_count": len(known_terms) + 1,
        "unobserved_components": [key for key, value in components.items() if value is None],
        "reward_version": "spatial_verifier_v1",
        "training_performed": False,
    }


def export_episode(graph: dict) -> dict:
    """Export only recorded state/event pairs from the existing conversation graph.

    The graph remains bounded. A long run whose decision window was truncated is
    explicitly marked, never expanded into fabricated missing trajectory steps.
    """
    robot = graph["robot_state"]
    plan = graph.get("last_valid_plan") or {}
    execution = robot.get("execution") or {}
    observed_metrics = execution.get("metrics") or {}
    planned_metrics = plan.get("metrics") or {}
    business = copy.deepcopy(robot.get("business_grounding") or {})
    final_metrics = {
        "observed_event_count": robot.get("observed_step_count", len(graph["events"])),
        "completed_stops": len(graph["completed_goal_ids"]),
        "remaining_stops": len(graph["remaining_goal_ids"]),
        "recovery_count": robot.get("recovery_count", 0),
        "recovery_success_count": robot.get("recovery_success_count", 0),
        "wrong_scan_count": robot.get("scan_rejection_count", 0),
        "planned_distance_m": planned_metrics.get("distance_m"),
        "observed_distance_m": observed_metrics.get(
            "distance_m", observed_metrics.get("distance_travelled_m")
        ),
        "collision_count": observed_metrics.get("collision_count"),
        "plan_route_valid": (
            plan.get("status") == "READY"
            and not plan.get("violations")
            and planned_metrics.get("constraint_violations") == 0
        )
        if plan
        else None,
        "inventory_written": False,
    }
    final_state = {
        "status": graph["status"],
        "completed_goal_ids": graph["completed_goal_ids"][:],
        "remaining_goal_ids": graph["remaining_goal_ids"][:],
        "battery_pct": robot.get("battery_pct"),
        "payload_kg": robot.get("payload_kg"),
        "pose": copy.deepcopy(robot.get("current_pose")),
    }
    steps = copy.deepcopy(robot.get("observed_steps", []))
    difficulty = (
        "long_horizon"
        if len(graph["completed_goal_ids"] + graph["remaining_goal_ids"]) >= 3
        else "single_goal"
    )
    if final_metrics["recovery_count"]:
        difficulty = "dynamic_recovery"
    elif plan.get("profile") != "fastest" or any(
        stop.get("time_window_s") for stop in plan.get("stops", [])
    ):
        difficulty = "constrained"
    elif len(graph["completed_goal_ids"] + graph["remaining_goal_ids"]) > 1:
        difficulty = "multi_goal" if difficulty != "long_horizon" else difficulty
    episode = {
        "schema_version": 1,
        "episode_id": graph["task_id"],
        "mission_id": graph["mission_id"],
        "conversation_id": graph.get("conversation_id"),
        "map_id": graph["map_id"],
        "map_revision": graph["map_revision"],
        "task_graph_revision": robot.get("task_graph_revision", 1),
        "scenario_id": robot.get("scenario_id", "registered_mission"),
        "scenario_seed": robot.get("scenario_seed", "default"),
        "instruction": robot.get("instruction", ""),
        "business_grounding": business,
        "initial_state": copy.deepcopy(robot.get("initial_state") or {}),
        "final_state": final_state,
        "available_skills": skill_manifest(),
        "steps": steps,
        "final_metrics": final_metrics,
        "difficulty": difficulty,
        "source": "observed_execution_events",
        "execution_boundary": "ros2_nav2_simulation",
        "inventory_written": False,
        "trace_truncated": bool(robot.get("episode_trace_truncated")),
        "trace_first_sequence": steps[0]["sequence"] if steps else None,
        "trace_last_sequence": steps[-1]["sequence"] if steps else None,
        "navigation_revision": graph["map_revision"],
    }
    episode["reward"] = verifier_reward(episode)
    return episode
