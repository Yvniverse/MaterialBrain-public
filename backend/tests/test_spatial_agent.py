"""Deterministic, provider-free long-horizon policy and typed-skill regressions."""

import copy
import json

import pytest

from app.agent.spatial_agent import (
    build_task_graph,
    export_episode,
    ground_instruction,
    ready_nodes,
    reduce_execution_event,
    should_handle_instruction,
    skill_manifest,
    validate_skill_selection,
)
from app.agent.spatial_agent.dataset import (
    build_grpo_records,
    build_sft_records,
    dataset_split,
    deduplicate_episodes,
    export_dataset,
    main,
    split_group,
    validate_episode,
)
from app.schemas.spatial import MissionRequest, TaskGraph


@pytest.fixture
def snapshot():
    return {
        "map_id": "MB-EMB-LAB-03",
        "revision": "map-revision-1",
        "docks": [
            {
                "id": "P-IC",
                "label": "精密芯片柜",
                "capabilities": ["navigate", "scan_code", "human_handoff"],
            },
            {
                "id": "P-SMD",
                "label": "贴片零件柜",
                "capabilities": ["navigate", "scan_code", "human_handoff"],
            },
            {
                "id": "P-WIRE",
                "label": "线缆架",
                "capabilities": ["navigate", "scan_code", "human_handoff"],
            },
            {
                "id": "HOME",
                "label": "待命点",
                "pose": {"x": 18, "y": 2.5, "yaw": 1.57},
                "capabilities": ["navigate"],
            },
            {"id": "CHARGER", "label": "充电点", "capabilities": ["navigate", "charge"]},
        ],
        "dynamic_overlays": [],
        "provenance": {
            "home_dock_id": "HOME",
            "default_goal_ids": ["P-IC", "P-SMD", "P-WIRE"],
            "robot": {"id": "MB-R01", "battery_pct": 82, "payload_kg": 18},
        },
    }


@pytest.fixture
def plan(snapshot):
    return {
        "schema_version": 1,
        "mission_id": "SM-unit-1",
        "map_id": snapshot["map_id"],
        "map_revision": snapshot["revision"],
        "profile": "fastest",
        "status": "READY",
        "stops": [
            {"goal_id": goal, "kind": "task", "occurrence_id": goal}
            for goal in ("P-IC", "P-SMD", "P-WIRE")
        ],
        "ordered_goal_ids": ["P-IC", "P-SMD", "P-WIRE"],
        "completed_goal_ids": [],
        "constraints": {"battery_pct": 82, "battery_reserve_pct": 15},
        "solver": {"name": "unit_fixture", "method": "fixture", "optimality_proven": False},
        "metrics": {"distance_m": 30, "constraint_violations": 0},
        "objective_terms": {},
        "segments": [{"from_goal_id": "P-WIRE", "to_goal_id": "HOME"}],
        "violations": [],
    }


@pytest.fixture
def graph(plan, snapshot):
    result = build_task_graph(
        plan, conversation_id="conversation-1", instruction="V4 导航三个站点并返回"
    )
    result["robot_state"].update(
        operation_id="operation-preserved",
        health={"ready": True},
        request=ground_instruction("V4规划默认六站", snapshot)["request"],
        execution={"transport_metadata": "preserved"},
    )
    return result


def event(graph, kind, goal=None, *, sequence=None, **changes):
    sequence = (
        graph["robot_state"].get("last_event_sequence", -1) + 1 if sequence is None else sequence
    )
    return {
        "schema_version": 1,
        "mission_id": graph["mission_id"],
        "map_revision": graph["map_revision"],
        "event_id": f"{graph['mission_id']}:{sequence}",
        "sequence": sequence,
        "timestamp": "2026-10-04T12:00:00Z",
        "type": kind,
        "goal_id": goal,
        "pose": {"x": 17, "y": 3, "yaw": 1.2},
        "battery_pct": 70,
        "payload_kg": 1,
        "details": {"execution_boundary": "ros2_nav2_simulation", "hardware_control": False},
        **changes,
    }


def reduce(graph, kind, goal=None, **changes):
    return reduce_execution_event(graph, event(graph, kind, goal, **changes))


def handoff(graph, goal):
    for kind in ("arrived", "scan_verified", "handoff_verified"):
        graph = reduce(graph, kind, goal, details={"scan_code": goal})
    return graph


def completed(graph):
    graph = reduce(graph, "started", "P-IC")
    for goal in list(graph["remaining_goal_ids"]):
        graph = handoff(graph, goal)
    graph = reduce(graph, "returned_home", "HOME")
    return reduce(graph, "completed")


@pytest.mark.parametrize(
    "message",
    [
        "查 PCM5102 的库存",
        "按项目 BOM 分析缺料",
        "找0.8mm 3P FPC线缆",
        "12V转3.3V，负载500mA",
        "推荐降压芯片",
        "规划实验仓控制板备料路线",
        "在机器人实验仓，按控制板备料任务规划路线",
        "查找ESD保护二极管",
        "线缆的间距是0.8mm",
        "这个产品做10台是否齐套？",
    ],
)
def test_ordinary_and_v3_queries_do_not_read_spatial_snapshot(message, graph):
    def forbidden(_):
        raise AssertionError("ordinary route must not resolve spatial business data")

    assert not should_handle_instruction(message)
    assert not ground_instruction(message, {}, business_resolver=forbidden)["handled"]
    assert not ground_instruction(message, {}, previous_graph=graph, business_resolver=forbidden)[
        "handled"
    ]


@pytest.mark.parametrize(
    "message",
    [
        "V4规划P-IC、P-SMD并返回",
        "空间任务图先导航到P-IC",
        "ROS2 plan P-IC",
        "ESD-safe route to P-IC",
        "项目PRJ-01备料，送到交接点后返回",
        "BOM取料后运送到装配台",
    ],
)
def test_new_navigation_boundary(message):
    assert should_handle_instruction(message)


@pytest.mark.parametrize(
    "message,profile",
    [
        ("V4规划P-IC与P-SMD最快路线", "fastest"),
        ("V4用最安全的路线导航P-IC", "safest"),
        ("空间 ESD 路线导航 P-IC", "esd_safe"),
    ],
)
def test_registered_goal_grounding_profile_and_real_initial_state(snapshot, message, profile):
    grounded = ground_instruction(message, snapshot)
    assert grounded["action"] == "plan"
    request = MissionRequest.model_validate(grounded["request"])
    assert request.profile == profile
    assert request.start_pose.x == 18
    assert request.constraints.battery_pct == 82
    assert request.constraints.robot_class == "MB-R01"
    assert request.return_home


@pytest.mark.parametrize("message", ["V4导航P-FAKE", "空间导航坐标(1,2)", "ROS2导航 x=10,y=2"])
def test_never_invent_docks_or_free_coordinates(snapshot, message):
    assert ground_instruction(message, snapshot)["action"] == "clarify"


def test_query_only_and_negated_execution_do_not_dispatch(snapshot):
    assert ground_instruction("V4查询任务图和ESD规则", snapshot)["action"] == "query"
    answer = ground_instruction("V4只规划P-IC，先不执行", snapshot)
    assert answer["action"] == "plan"
    assert answer["request"]["goal_ids"] == ["P-IC"]


def test_real_business_resolver_is_authoritative_no_mass_or_stock_guess(snapshot):
    observed = {
        "status": "GROUNDED",
        "goal_ids": ["P-SMD"],
        "bom": {"product_revision_id": "revision-42", "build_quantity": 3},
        "inventory": [{"code": "MAT-001", "required_quantity": "6", "available_quantity": "9"}],
        "pick_locations": [{"location_code": "RD-01-3", "goal_id": "P-SMD", "quantity": "6"}],
        "inventory_written": False,
        "automatic_substitution": False,
    }
    grounded = ground_instruction(
        "空间按产品PROD-01生产3台，备料运送并返回", snapshot, business_resolver=lambda _: observed
    )
    assert grounded["request"]["goal_ids"] == ["P-SMD"]
    assert grounded["business_grounding"] == observed
    assert not grounded["request"]["stops"]
    assert "demand_kg" not in grounded["business_grounding"]["pick_locations"][0]
    assert observed["inventory"][0]["required_quantity"] == "6"


@pytest.mark.parametrize(
    "business",
    [
        {"status": "CLARIFICATION", "clarification": "请指定产品版本和台数", "goal_ids": []},
        {
            "status": "BLOCKED",
            "goal_ids": ["P-SMD"],
            "violations": [{"code": "INSUFFICIENT_STOCK"}],
        },
        {"status": "GROUNDED", "goal_ids": ["P-FAKE"]},
        {"status": "GROUNDED", "goal_ids": ["P-SMD"], "inventory_written": True},
    ],
)
def test_missing_ambiguous_insufficient_or_unregistered_business_is_not_dispatched(
    snapshot, business
):
    answer = ground_instruction(
        "空间按项目BOM备料运送", snapshot, business_resolver=lambda _: business
    )
    assert answer["action"] == "clarify"
    assert "request" not in answer


def test_context_commands_only_use_existing_owned_mission(graph, snapshot):
    assert ground_instruction("取消空间任务", snapshot)["action"] == "clarify"
    assert ground_instruction("取消空间任务", snapshot, graph)["action"] == "cancel"
    answer = ground_instruction("V4开始执行", snapshot, graph)
    assert answer["action"] == "execute" and answer["args"]["existing_mission"]
    assert answer["request"] == graph["robot_state"]["request"]
    assert ground_instruction("空间重新规划剩余任务", snapshot, graph)["action"] == "replan"
    assert ground_instruction("移除占道料车", snapshot, graph)["args"] == {
        "scenario_id": "blocked-crossing",
        "operation": "remove",
    }


def test_handoff_requires_actual_scan_code_and_observed_arrival(graph, snapshot):
    assert ground_instruction("V4扫码P-IC", snapshot, graph)["action"] == "clarify"
    arrived = reduce(reduce(graph, "started", "P-IC"), "arrived", "P-IC")
    assert ground_instruction("V4确认交接", snapshot, arrived)["action"] == "clarify"
    args = ground_instruction("V4扫码 WRONG-SLOT", snapshot, arrived)["args"]
    assert args == {"goal_id": "P-IC", "scan_code": "WRONG-SLOT"}


def test_manifest_is_typed_complete_and_never_a_transaction_or_physical_skill():
    manifest = skill_manifest()
    assert len(manifest) == 13 and len({item["name"] for item in manifest}) == 13
    for item in manifest:
        assert item["arguments_schema"]["additionalProperties"] is False
        assert item["preconditions"] and item["effects"] and item["failure_codes"]
        assert item["required_capabilities"] and item["idempotency"]
        assert item["mode"] in {"read_only", "simulation_execute"}
        assert not item["inventory_write"] and not item["hardware_control"]
    manifest[0]["preconditions"].append("tampered")
    assert "tampered" not in skill_manifest()[0]["preconditions"]


def test_controlled_fake_model_may_select_only_ready_typed_registered_skill(graph, snapshot):
    class FakeProvider:
        def select(self):
            return {
                "name": "navigate_mission",
                "args": {
                    "mission_id": graph["mission_id"],
                    "map_revision": graph["map_revision"],
                    "goal_id": "P-IC",
                },
            }

    chosen = validate_skill_selection(
        FakeProvider().select(), snapshot=snapshot, graph=graph, capabilities={"navigate"}
    )
    assert chosen["args"]["goal_id"] == "P-IC"
    args = chosen["args"]
    for bad in (
        {"name": "inventory_outbound", "args": {}},
        {"name": "navigate_mission", "args": {**args, "x": 1, "y": 2}},
        {"name": "navigate_mission", "args": {**args, "goal_id": "P-FAKE"}},
        {"name": "navigate_mission", "args": {**args, "goal_id": "P-WIRE"}},
        {"name": "verify_handoff", "args": args},
        {"name": "navigate_mission", "args": {**args, "map_revision": "stale"}},
        {"name": "navigate_mission", "args": {**args, "mission_id": "another-owner"}},
    ):
        with pytest.raises(ValueError):
            validate_skill_selection(bad, snapshot=snapshot, graph=graph)
    with pytest.raises(ValueError, match="CAPABILITY"):
        validate_skill_selection(chosen, snapshot=snapshot, graph=graph, capabilities=set())


def test_dependency_scheduler_and_long_horizon_observed_completion(graph):
    assert [node["args"]["goal_id"] for node in ready_nodes(graph)] == ["P-IC"]
    assert not ready_nodes(graph, capabilities=set())
    result = completed(graph)
    assert result["status"] == "COMPLETED"
    assert result["completed_goal_ids"] == ["P-IC", "P-SMD", "P-WIRE"]
    assert result["remaining_goal_ids"] == []
    assert result["robot_state"]["health"] == graph["robot_state"]["health"]
    assert result["robot_state"]["request"] == graph["robot_state"]["request"]
    assert result["robot_state"]["execution"] == graph["robot_state"]["execution"]
    assert graph["status"] == "READY", "reducer must not mutate its input"
    TaskGraph.model_validate(result)


def test_wrong_scan_is_zero_progress_then_correct_scan_and_handoff(graph):
    graph = reduce(reduce(graph, "started", "P-IC"), "arrived", "P-IC")
    before = copy.deepcopy(graph["completed_goal_ids"])
    rejected = reduce(graph, "scan_rejected", "P-IC", details={"scan_code": "WRONG-SLOT"})
    assert rejected["completed_goal_ids"] == before
    assert rejected["remaining_goal_ids"] == graph["remaining_goal_ids"]
    assert rejected["status"] == "AWAITING_HANDOFF"
    assert rejected["current_skill"] == "confirm_scan"
    assert not rejected["robot_state"]["inventory_written"]
    verified = reduce(rejected, "scan_verified", "P-IC", details={"scan_code": "P-IC"})
    assert verified["completed_goal_ids"] == []
    assert verified["current_skill"] == "verify_handoff"
    settled = reduce(verified, "handoff_verified", "P-IC")
    assert settled["completed_goal_ids"] == ["P-IC"]


@pytest.mark.parametrize(
    "kind,goal",
    [
        ("handoff_verified", "P-IC"),
        ("arrived", "P-WIRE"),
        ("scan_verified", "P-IC"),
        ("completed", None),
        ("returned_home", "HOME"),
    ],
)
def test_cannot_skip_navigation_scan_required_stop_or_return_dependencies(graph, kind, goal):
    with pytest.raises(ValueError):
        reduce(graph, kind, goal)


def test_blockage_replan_preserves_completed_handoff_and_exact_retry_key(graph, plan):
    graph = handoff(reduce(graph, "started", "P-IC"), "P-IC")
    finished = [node for node in graph["nodes"] if node["args"].get("goal_id") == "P-IC"]
    graph = reduce(graph, "feedback", "P-SMD")
    graph = reduce(graph, "obstacle_added", details={"code": "BLOCKED_PATH"})
    assert graph["status"] == "RECOVERING"
    assert graph["recovery"]["completed_goal_ids"] == ["P-IC"]
    next_plan = copy.deepcopy(plan)
    next_plan.update(
        completed_goal_ids=["P-IC"],
        ordered_goal_ids=["P-WIRE", "P-SMD"],
        stops=[stop for stop in plan["stops"] if stop["goal_id"] != "P-IC"],
    )
    replanned = reduce(graph, "replanning", details={"plan": next_plan})
    assert replanned["remaining_goal_ids"] == ["P-WIRE", "P-SMD"]
    assert finished == [
        node for node in replanned["nodes"] if node["args"].get("goal_id") == "P-IC"
    ]
    assert [node["args"].get("goal_id") for node in ready_nodes(replanned)] == ["P-WIRE"]
    for goal in ["P-WIRE", "P-SMD"]:
        replanned = handoff(replanned, goal)
    replanned = reduce(reduce(replanned, "returned_home", "HOME"), "completed")
    assert replanned["status"] == "COMPLETED"
    assert replanned["robot_state"]["recovery_success_count"] == 1


def test_replan_cannot_drop_completed_or_required_goals(graph, plan):
    graph = handoff(reduce(graph, "started", "P-IC"), "P-IC")
    with pytest.raises(ValueError, match="COMPLETED_STOPS"):
        reduce(graph, "replanning", details={"plan": plan})
    bad = copy.deepcopy(plan)
    bad.update(completed_goal_ids=["P-IC"], ordered_goal_ids=["P-SMD"], stops=[plan["stops"][1]])
    with pytest.raises(ValueError, match="REMAINING_STOPS"):
        reduce(graph, "replanning", details={"plan": bad})


def test_low_battery_demands_deterministic_feasibility_before_charger(graph):
    result = reduce(reduce(graph, "started", "P-IC"), "feedback", "P-IC", battery_pct=10)
    assert result["status"] == "RECOVERING"
    assert result["recovery"]["code"] == "LOW_BATTERY"
    assert result["recovery"]["charger_feasibility_required"]
    assert result["completed_goal_ids"] == []
    assert not any(node["skill"] == "charge_robot" for node in result["nodes"])


def test_planner_inserted_charger_is_an_observed_dependency_not_an_invented_effect(plan):
    charge_plan = copy.deepcopy(plan)
    charge_plan["ordered_goal_ids"].insert(0, "CHARGER")
    charge_plan["stops"].insert(
        0, {"goal_id": "CHARGER", "kind": "charge", "occurrence_id": "CHARGER:1"}
    )
    graph = build_task_graph(charge_plan, conversation_id="charge-test")
    graph = reduce(graph, "started", "CHARGER", battery_pct=18)
    graph = reduce(graph, "arrived", "CHARGER", battery_pct=17)
    assert graph["status"] == "CHARGING" and graph["current_skill"] == "charge_robot"
    graph = reduce(graph, "charging", "CHARGER", details={"state": "charging"}, battery_pct=25)
    assert graph["completed_goal_ids"] == []
    graph = reduce(graph, "charging", "CHARGER", details={"state": "complete"}, battery_pct=100)
    assert graph["robot_state"]["battery_pct"] == 100
    assert graph["current_skill"] == "navigate_mission"
    assert ready_nodes(graph)[0]["args"]["goal_id"] == "P-IC"


def test_stale_map_blocks_motion_preserves_completed_and_requires_reground(graph):
    graph = handoff(reduce(graph, "started", "P-IC"), "P-IC")
    pose = copy.deepcopy(graph["robot_state"]["current_pose"])
    stale = reduce(
        graph,
        "feedback",
        "P-SMD",
        map_revision="different-map",
        pose={"x": 999, "y": 999, "yaw": 0},
    )
    assert stale["status"] == "BLOCKED"
    assert stale["recovery"]["action"] == "reground_map"
    assert stale["robot_state"]["current_pose"] == pose
    assert stale["completed_goal_ids"] == ["P-IC"]
    stale = reduce(stale, "arrived", "P-SMD")
    assert stale["status"] == "BLOCKED" and stale["remaining_goal_ids"] == ["P-SMD", "P-WIRE"]
    assert not ready_nodes(stale)


def test_unreachable_target_keeps_successful_stops_and_zero_inventory_write(graph):
    graph = handoff(reduce(graph, "started", "P-IC"), "P-IC")
    graph = reduce(graph, "recovery", "P-SMD", details={"code": "UNREACHABLE"})
    assert graph["status"] == "BLOCKED"
    assert graph["recovery"]["action"] == "wait_or_yield"
    assert graph["completed_goal_ids"] == ["P-IC"]
    assert not graph["robot_state"]["inventory_written"]


def test_transport_pause_uses_same_operation_and_deterministic_bounded_retry(graph):
    for retry, delay in enumerate((1, 2, 4), 1):
        graph = reduce(graph, "transport_paused")
        assert graph["recovery"]["attempt"] == retry
        assert graph["recovery"]["next_retry_delay_s"] == delay
        assert graph["recovery"]["idempotency_key"] == "operation-preserved"
        assert graph["recovery"]["safe_retry"]
        assert not ready_nodes(graph)
    graph = reduce(graph, "transport_paused")
    assert graph["status"] == "PAUSED" and graph["recovery"]["action"] == "wait_or_yield"
    assert not graph["recovery"]["safe_retry"]
    assert graph["completed_goal_ids"] == []


def test_cancellation_preserves_audit_progress_and_prevents_later_completion(graph):
    graph = handoff(reduce(graph, "started", "P-IC"), "P-IC")
    graph = reduce(graph, "cancelled")
    later = reduce(graph, "completed")
    assert later["status"] == "CANCELLED"
    assert later["completed_goal_ids"] == ["P-IC"]
    assert later["remaining_goal_ids"] == ["P-SMD", "P-WIRE"]
    assert not ready_nodes(later)


def test_event_cursor_survives_bounded_history_and_preserves_extension_fields(graph):
    first = event(graph, "started", "P-IC")
    graph = reduce_execution_event(graph, first)
    for _ in range(140):
        graph = reduce(graph, "feedback", "P-IC")
    assert len(graph["events"]) == 128
    assert graph["robot_state"]["last_event_sequence"] == 140
    assert first["event_id"] not in {item["event_id"] for item in graph["events"]}
    assert reduce_execution_event(graph, first) == graph
    assert graph["robot_state"]["operation_id"] == "operation-preserved"


@pytest.mark.parametrize(
    "changes",
    [
        {"mission_id": "different-owner"},
        {"goal_id": "P-UNREGISTERED"},
        {"type": "direct_inventory_write"},
        {"details": {"hardware_control": True}},
        {"details": {"inventory_written": True}},
        {"details": {"execution_boundary": "physical_execute"}},
    ],
)
def test_untrusted_events_cannot_cross_identity_registration_or_write_boundary(graph, changes):
    with pytest.raises(ValueError):
        reduce_execution_event(graph, event(graph, "started", "P-IC", **changes))


def test_episode_uses_observed_steps_and_unknown_safety_is_unknown(graph):
    assert export_episode(graph)["steps"] == []
    result = completed(graph)
    episode = export_episode(result)
    assert episode["source"] == "observed_execution_events"
    assert len(episode["steps"]) == len(result["events"])
    assert episode["reward"]["components"]["task_success"]
    assert episode["reward"]["components"]["no_collision"] is None
    assert episode["reward"]["components"]["route_cost_efficiency"] is None
    assert episode["reward"]["components"]["unauthorized_action_penalty"] == 0
    assert not episode["reward"]["training_performed"]
    validate_episode(episode)


def test_dataset_dedup_ignores_incidental_identity_and_keeps_frames_together(graph):
    episode = export_episode(completed(graph))
    duplicate = copy.deepcopy(episode)
    duplicate.update(
        episode_id="different-episode", mission_id="another-id", conversation_id="another-chat"
    )
    for step in duplicate["steps"]:
        step["outcome"]["event_id"] = "another-event-" + str(step["sequence"])
        step["outcome"]["timestamp"] = "2026-10-04T13:00:00Z"
    unique = deduplicate_episodes([episode, duplicate])
    assert len(unique) == 1
    records = build_sft_records([episode, duplicate])
    assert len({record["split"] for record in records}) == 1
    assert all(record["split_group_sha256"] == split_group(episode) for record in records)
    assert all(record["split"] == dataset_split(episode) for record in records)
    changed_seed = {**episode, "scenario_seed": "different-seed"}
    assert split_group(episode) != split_group(changed_seed)
    assert len(deduplicate_episodes([episode, changed_seed])) == 2


def test_executable_dataset_cli_exports_only_observed_episode_jsonl_and_raw_rewards(
    graph, tmp_path
):
    episode = export_episode(completed(graph))
    source = tmp_path / "saved-episode.json"
    source.write_text(json.dumps(episode), encoding="utf-8")
    target = tmp_path / "dataset"
    main(["--input", str(source), "--output-dir", str(target)])
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["unique_episodes"] == 1 and manifest["grpo_records"] == 1
    assert len(manifest["files"]) == 5
    grpo = json.loads((target / "grpo-ready.jsonl").read_text(encoding="utf-8"))
    assert set(grpo["verifier_reward"]["components"]) == {
        "task_success",
        "correct_skill",
        "grounded_arguments",
        "route_valid",
        "no_collision",
        "recovery_success",
        "route_cost_efficiency",
        "unauthorized_action_penalty",
    }
    assert not grpo["training_performed"]
    assert len(build_grpo_records([episode])) == 1


def test_dataset_rejects_static_graph_and_fabricated_missing_observations(graph, tmp_path):
    with pytest.raises(ValueError, match="NO_STEPS"):
        export_dataset([export_episode(graph)], tmp_path / "empty")
    episode = export_episode(completed(graph))
    episode["steps"][0]["source"] = "fabricated"
    with pytest.raises(ValueError, match="PROVENANCE"):
        validate_episode(episode)


@pytest.mark.parametrize("status", ["INFEASIBLE", "CLARIFICATION", "BLOCKED"])
def test_followup_to_unready_plan_with_none_last_plan_clarifies(snapshot, graph, status):
    graph.update(status=status, last_valid_plan=None)
    assert ground_instruction("V4开始执行", snapshot, graph)["action"] == "clarify"
    assert ground_instruction("空间查询任务进度", snapshot, graph)["action"] == "query"
    assert ground_instruction("V4开始规划P-IC", snapshot)["action"] == "plan"


def test_registered_spatial_query_uses_observed_or_registered_pose(snapshot, graph):
    answer = ground_instruction("空间查询附近站点", snapshot)
    assert answer["action"] == "query"
    assert answer["args"]["query"] == {
        "kind": "nearby",
        "point": {"x": 18, "y": 2.5},
        "entity": "docks",
        "limit": 10,
        "radius_m": 5,
    }
    graph["robot_state"]["current_pose"] = {"x": 12, "y": 9, "yaw": 0}
    answer = ground_instruction("空间查询当前所属区域", snapshot, graph)
    assert answer["args"]["query"]["kind"] == "contains"
    assert answer["args"]["query"]["point"] == {"x": 12, "y": 9}
    assert answer["args"]["query_point_source"] == "observed_pose"
    answer = ground_instruction("空间查询P-UNKNOWN附近站点", snapshot)
    assert answer["action"] == "clarify"


def test_real_nav2_global_path_refresh_does_not_reset_semantic_completed_stops(graph):
    graph = handoff(reduce(graph, "started", "P-IC"), "P-IC")
    graph = reduce(graph, "feedback", "P-SMD")
    graph = reduce(
        graph, "recovery", "P-SMD", details={"source": "nav2_behavior_tree_log", "node": "Wait"}
    )
    graph = reduce(
        graph, "recovery", "P-SMD", details={"source": "nav2_behavior_tree_log", "node": "Spin"}
    )
    nodes = copy.deepcopy(
        [node for node in graph["nodes"] if node["args"].get("goal_id") == "P-IC"]
    )
    graph = reduce(
        graph, "replanning", "P-SMD", details={"source": "nav2_global_path", "path_length_m": 3.8}
    )
    assert graph["status"] == "RUNNING" and graph["recovery"] is None
    assert graph["completed_goal_ids"] == ["P-IC"]
    assert graph["robot_state"]["task_graph_revision"] == 1
    assert graph["robot_state"]["recovery_success_count"] == 2
    assert nodes == [node for node in graph["nodes"] if node["args"].get("goal_id") == "P-IC"]
    graph = handoff(graph, "P-SMD")
    assert graph["completed_goal_ids"] == ["P-IC", "P-SMD"]


def test_overlay_stop_waits_for_validated_path_after_first_handoff(graph):
    graph = handoff(reduce(graph, "started", "P-IC"), "P-IC")
    graph = reduce(graph, "feedback", "P-SMD")
    finished = copy.deepcopy(
        [node for node in graph["nodes"] if node["args"].get("goal_id") == "P-IC"]
    )
    original_plan = copy.deepcopy(graph["last_valid_plan"])
    assert original_plan["completed_goal_ids"] == []
    graph = reduce(
        graph,
        "replanning",
        "P-SMD",
        details={"source": "canonical_overlay_stop", "state": "stopped_pending_nav2_path"},
    )
    assert graph["status"] == "RECOVERING"
    assert graph["robot_state"]["overlay_path_pending"]
    assert graph["recovery"]["completed_goal_ids"] == ["P-IC"]
    graph = reduce(graph, "obstacle_added", "P-SMD")
    graph = reduce(graph, "replanning", "P-SMD", details={"source": "nav2_global_path"})
    assert graph["status"] == "RECOVERING"
    assert graph["robot_state"]["overlay_path_pending"]
    assert graph["robot_state"]["recovery_success_count"] == 0
    graph = reduce(
        graph,
        "replanning",
        "P-SMD",
        details={"source": "nav2_validated_overlay_path", "state": "resumed"},
    )
    assert graph["status"] == "RUNNING" and graph["recovery"] is None
    assert not graph["robot_state"]["overlay_path_pending"]
    assert graph["robot_state"]["recovery_success_count"] == 2
    assert graph["robot_state"]["task_graph_revision"] == 1
    assert graph["last_valid_plan"] == original_plan
    assert graph["completed_goal_ids"] == ["P-IC"]
    assert graph["remaining_goal_ids"] == ["P-SMD", "P-WIRE"]
    assert finished == [node for node in graph["nodes"] if node["args"].get("goal_id") == "P-IC"]
    graph = handoff(graph, "P-SMD")
    assert graph["completed_goal_ids"] == ["P-IC", "P-SMD"]


@pytest.mark.parametrize("blocked_event", ["charging", "failed"])
def test_overlay_motion_events_cannot_clear_low_battery_recovery(graph, blocked_event):
    graph = reduce(graph, "started", "P-IC")
    graph = reduce(
        graph,
        blocked_event,
        battery_pct=10,
        details={"state": "required", "code": "LOW_BATTERY"},
    )
    blocked_status = graph["status"]
    assert blocked_status == ("RECOVERING" if blocked_event == "charging" else "BLOCKED")
    recovery = copy.deepcopy(graph["recovery"])
    nodes = copy.deepcopy(graph["nodes"])
    for source in ("canonical_overlay_stop", "nav2_global_path", "nav2_validated_overlay_path"):
        graph = reduce(graph, "replanning", "P-IC", battery_pct=10, details={"source": source})
        assert graph["status"] == blocked_status
        assert graph["recovery"] == recovery
        assert graph["nodes"] == nodes
        assert graph["robot_state"]["recovery_success_count"] == 0
        assert graph["robot_state"]["task_graph_revision"] == 1


@pytest.mark.parametrize("source", [None, "server_semantic_replan"])
def test_overlay_stop_keeps_strict_semantic_completed_guard(graph, plan, source):
    graph = handoff(reduce(graph, "started", "P-IC"), "P-IC")
    graph = reduce(graph, "replanning", "P-SMD", details={"source": "canonical_overlay_stop"})
    with pytest.raises(ValueError, match="SPATIAL_REPLAN_COMPLETED_STOPS_REQUIRED"):
        reduce(graph, "replanning", details={"source": source, "plan": plan})
    assert graph["status"] == "RECOVERING"
    assert graph["completed_goal_ids"] == ["P-IC"]


def test_overlay_stop_can_accept_valid_server_semantic_replan(graph, plan):
    graph = handoff(reduce(graph, "started", "P-IC"), "P-IC")
    finished = copy.deepcopy(
        [node for node in graph["nodes"] if node["args"].get("goal_id") == "P-IC"]
    )
    graph = reduce(graph, "replanning", "P-SMD", details={"source": "canonical_overlay_stop"})
    next_plan = copy.deepcopy(plan)
    next_plan.update(
        completed_goal_ids=["P-IC"],
        ordered_goal_ids=["P-WIRE", "P-SMD"],
        stops=[stop for stop in plan["stops"] if stop["goal_id"] != "P-IC"],
    )
    # Integration stores a validated server plan before consuming its ROS event.
    graph["last_valid_plan"] = next_plan
    graph = reduce(graph, "replanning", details={"source": "server_semantic_replan"})
    assert graph["status"] == "RUNNING" and graph["recovery"] is None
    assert not graph["robot_state"]["overlay_path_pending"]
    assert graph["robot_state"]["task_graph_revision"] == 2
    assert graph["completed_goal_ids"] == ["P-IC"]
    assert graph["remaining_goal_ids"] == ["P-WIRE", "P-SMD"]
    assert finished == [node for node in graph["nodes"] if node["args"].get("goal_id") == "P-IC"]


@pytest.mark.parametrize("with_plan", [False, True])
def test_unknown_replanning_source_cannot_use_or_replace_valid_plan(graph, plan, with_plan):
    details = {"source": "unknown_path_or_plan"}
    if with_plan:
        details["plan"] = plan
    with pytest.raises(ValueError, match="SPATIAL_REPLAN_SOURCE"):
        reduce(graph, "replanning", details=details)


def test_real_bt_recovery_can_resolve_at_observed_arrival_without_mission_reordering(graph):
    graph = reduce(graph, "started", "P-IC")
    graph = reduce(
        graph, "recovery", "P-IC", details={"source": "nav2_behavior_tree_log", "node": "BackUp"}
    )
    graph = reduce(graph, "arrived", "P-IC")
    assert graph["status"] == "AWAITING_HANDOFF" and graph["recovery"] is None
    assert not any(
        node["skill"] == "replan_remaining" and node["status"] == "PENDING"
        for node in graph["nodes"]
    )


def test_real_low_battery_required_event_does_not_claim_charger_arrival(graph):
    graph = reduce(graph, "started", battery_pct=10)
    graph = reduce(
        graph,
        "charging",
        battery_pct=10,
        details={
            "state": "required",
            "reason": "LOW_BATTERY_RESERVE",
            "navigation_started": False,
            "charger_goal_id": "CHARGER",
        },
    )
    assert graph["recovery"]["code"] == "LOW_BATTERY"
    assert graph["robot_state"]["battery_pct"] == 10
    assert not any(node["skill"] == "charge_robot" for node in graph["nodes"])
    graph = reduce(graph, "replanning", "P-IC", details={"source": "nav2_global_path"})
    assert graph["recovery"]["code"] == "LOW_BATTERY"
    assert export_episode(graph)["steps"][-2]["expert_skill"]["name"] == "replan_remaining"


def test_real_bridge_slot_scan_and_home_events_export_typed_observed_choices(
    graph, snapshot, tmp_path
):
    graph = reduce(graph, "started")
    for goal in list(graph["remaining_goal_ids"]):
        graph = reduce(graph, "arrived", goal, details={"source": "nav2_action_result"})
        graph = reduce(graph, "scan_verified", goal, details={"slot": goal, "simulation": True})
        graph = reduce(
            graph, "handoff_verified", goal, details={"slot": goal, "inventory_mutation": False}
        )
    graph = reduce(graph, "arrived", "HOME")
    graph = reduce(graph, "returned_home", "HOME")
    graph = reduce(graph, "completed")
    episode = export_episode(graph)
    for step in episode["steps"]:
        validate_skill_selection(step["chosen_skill"], snapshot=snapshot)
    assert graph["status"] == "COMPLETED"
    manifest = export_dataset([episode], tmp_path / "real-shape")
    assert manifest["unique_episodes"] == 1
    unique = deduplicate_episodes([episode])
    assert deduplicate_episodes(unique)[0]["content_sha256"] == unique[0]["content_sha256"]


def test_controlled_model_cannot_resume_navigation_while_recovery_or_transport_paused(
    graph, snapshot
):
    selection = {"name": "navigate_mission", "args": ready_nodes(graph)[0]["args"]}
    graph = reduce(reduce(graph, "started", "P-IC"), "recovery", "P-IC")
    with pytest.raises(ValueError, match="RECOVERY_REQUIRED"):
        validate_skill_selection(selection, snapshot=snapshot, graph=graph)
    graph = reduce(graph, "transport_paused")
    with pytest.raises(ValueError, match="MISSION_PAUSED"):
        validate_skill_selection(selection, snapshot=snapshot, graph=graph)


@pytest.mark.parametrize(
    "message,battery",
    [
        ("V4 空间任务：P-IC，电量 12%，只规划，不要执行。", 12),
        ("请只规划V4 P-IC，假设电量为 12.5％，扫码交接后返回，不启动。", 12.5),
        ("空间低电量路线先规划 P-IC：电量仅剩 0%，不要执行。", 0),
        ("V4规划P-IC，电量百分比=100，只规划。", 100),
        ("Only plan V4 P-IC, battery level = 12 percent, do not execute.", 12),
        ("V4 plan P-IC; battery_pct=12; scan and handoff then return HOME; do not start.", 12),
    ],
)
def test_natural_plan_battery_is_validated_explicit_assumption_not_motion(
    snapshot, message, battery
):
    grounded = ground_instruction(message, snapshot)
    assert grounded["action"] == "plan"
    assert grounded["request"]["constraints"]["battery_pct"] == battery
    assert grounded["args"]["planning_assumptions"] == {
        "battery_pct": battery,
        "source": "instruction",
        "planning_only": True,
    }
    assert grounded["request"]["start_pose"] == snapshot["docks"][3]["pose"]
    assert snapshot["provenance"]["robot"]["battery_pct"] == 82


@pytest.mark.parametrize(
    "battery_clause",
    [
        "电量-1%",
        "电量101%",
        "电量NaN%",
        "电量inf%",
        "电量1e999%",
        "电量十二%",
        "电量12瓦时",
        "电量12%%",
        "电量12%或者20%",
        "电量12%，电量20%",
        "battery_pct=12; battery=12%",
    ],
)
def test_invalid_or_multiple_planning_battery_values_clarify(snapshot, battery_clause):
    grounded = ground_instruction(f"V4只规划P-IC，{battery_clause}，不要执行。", snapshot)
    assert grounded["action"] == "clarify"
    assert grounded["args"]["code"] in {
        "BATTERY_PERCENTAGE_INVALID",
        "BATTERY_PERCENTAGE_AMBIGUOUS",
    }
    assert "request" not in grounded


@pytest.mark.parametrize("status", ["INFEASIBLE", "BLOCKED", "CLARIFICATION"])
@pytest.mark.parametrize(
    "message",
    [
        "请只规划 V4 空间任务：P-IC、P-SENSOR、P-LAB，扫码交接后返回 HOME，不要执行。",
        "V4先规划P-IC、P-SENSOR和P-LAB的路线，扫描核验并人工交接后回到HOME，别执行。",
        "Only plan V4 P-IC, P-SENSOR, P-LAB; scan and handoff then return HOME; do not execute.",
    ],
)
def test_new_plan_with_future_scan_after_low_infeasible_task_is_not_handoff(
    snapshot, graph, status, message
):
    snapshot["docks"].extend(
        [
            {"id": "P-SENSOR", "label": "传感器柜", "capabilities": ["navigate", "scan_code"]},
            {"id": "P-LAB", "label": "实验台", "capabilities": ["navigate", "human_handoff"]},
        ]
    )
    graph.update(status=status, last_valid_plan=None)
    graph["robot_state"].update(battery_pct=12, execution=None, last_event_sequence=-1)
    previous = copy.deepcopy(graph)
    grounded = ground_instruction(message, snapshot, graph)
    assert grounded["action"] == "plan"
    assert grounded["request"]["goal_ids"] == ["P-IC", "P-SENSOR", "P-LAB"]
    assert grounded["request"]["constraints"]["battery_pct"] == 82
    assert grounded["request"]["return_home"]
    assert grounded["args"] == {}
    assert graph == previous


def test_sequential_hypothetical_low_plan_then_normal_plan_does_not_leak_battery(snapshot, plan):
    low = ground_instruction("V4 空间任务：P-IC，电量 12%，只规划，不要执行。", snapshot)
    low_plan = copy.deepcopy(plan)
    low_plan.update(
        status="INFEASIBLE",
        ordered_goal_ids=[],
        stops=[],
        segments=[],
        constraints=low["request"]["constraints"],
        violations=[{"code": "BATTERY_RESERVE"}],
    )
    low_graph = build_task_graph(low_plan, conversation_id="hypothetical-low-battery")
    low_graph["robot_state"]["request"] = low["request"]
    assert low_graph["robot_state"]["battery_pct"] == 12
    followup = ground_instruction(
        "请只规划V4 P-IC、P-SMD、P-WIRE，扫码交接后返回，不要执行。", snapshot, low_graph
    )
    assert followup["action"] == "plan"
    assert followup["request"]["constraints"]["battery_pct"] == 82
    assert low_graph["robot_state"]["request"]["constraints"]["battery_pct"] == 12


def test_genuine_observed_execution_battery_and_pose_ground_new_plan_and_replan(snapshot, graph):
    pose = {"x": 11.5, "y": 8, "yaw": 0.4}
    graph["robot_state"].update(
        battery_pct=12, execution={"robot_state": {"battery_pct": 47}, "current_pose": pose}
    )
    previous = copy.deepcopy(graph)
    planned = ground_instruction(
        "只规划V4 P-IC和P-SMD，扫码交接后返回，不要执行。", snapshot, graph
    )
    assert planned["action"] == "plan"
    assert planned["request"]["constraints"]["battery_pct"] == 47
    assert planned["request"]["start_pose"] == pose
    replanned = ground_instruction("V4重新规划剩余任务，电量95%，不要执行。", snapshot, graph)
    assert replanned["action"] == "replan"
    assert replanned["args"] == {"profile": "fastest"}
    assert "request" not in replanned
    assert graph == previous


def test_explicit_planning_does_not_weaken_actual_scan_handoff_or_execution_guards(snapshot, graph):
    arrived = reduce(reduce(graph, "started", "P-IC"), "arrived", "P-IC")
    previous = copy.deepcopy(arrived)
    assert ground_instruction("V4扫码 P-IC，确认交接", snapshot, arrived)["action"] == "handoff"
    assert ground_instruction("V4确认交接", snapshot, arrived)["action"] == "clarify"
    planned = ground_instruction(
        "V4只规划P-SMD，电量12%，之后扫码交接，不要执行。", snapshot, arrived
    )
    assert planned["action"] == "plan"
    assert planned["request"]["constraints"]["battery_pct"] == 12
    assert arrived == previous
    assert ground_instruction("V4扫码 P-IC", snapshot, graph)["action"] == "clarify"
    started = ground_instruction("V4开始执行，电量12%", snapshot, graph)
    assert started["action"] == "execute"
    assert started["request"]["constraints"]["battery_pct"] == 82
    assert "planning_assumptions" not in started["args"]
