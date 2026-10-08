import copy

import pytest

from app.agent.conversation import ConversationContextService
from app.agent.graph import WarehouseAgentGraph
from app.agent.navigation_contract import navigation_plan_arguments
from app.agent.service import WarehouseAgentService
from app.agent.task_contract import classify_task_contract
from app.agent.tools import ToolContext, ToolRegistry
from app.core.config import Settings
from app.core.database import SessionLocal
from app.models import User
from app.services.embodied_navigation.schemas import NavigationExecutionContext
from app.services.embodied_navigation.service import navigation_manifest, world_snapshot


class NoModelCalls:
    def chat(self, messages, tools=None, tool_choice="auto"):
        raise AssertionError("Navigation is a server-owned deterministic contract")


@pytest.mark.parametrize(
    "question",
    [
        "在机器人实验仓，按控制板备料任务规划路线。",
        "实验仓控制板备料，帮我规划路径",
        "robot lab 规划 P-IC P-CABLE 路线",
        "机器人能自动打开抽屉吗？",
        "机器人有机械臂可以抓取吗",
        "中央通道被占用，重新规划剩余任务。",
    ],
)
def test_explicit_navigation_contract(question):
    contract = classify_task_contract(question)
    assert contract.entity_kind == "navigation"
    assert contract.write_intent == "none"


@pytest.mark.parametrize(
    "question",
    [
        "PCM5102APWR在哪里？查看它的真实抽屉。",
        "在真的仓库查 PCM5102APWR 的库位",
        "真实拣货任务重新规划剩余路线",
        "产品控制板的 BOM 库存够不够",
        "找 0.5mm 30P 反向 15cm 排线",
        "12V 转 3.3V 100mA Buck",
    ],
)
def test_normal_business_is_not_synthetic_navigation(question):
    assert classify_task_contract(question).entity_kind != "navigation"


def execution_context():
    w = world_snapshot()
    first = next(g for g in w["goals"] if g["id"] == "P-IC")
    return NavigationExecutionContext(
        world_id=w["id"],
        world_revision=w["revision_sha256"],
        goal_ids=w["default_goal_ids"],
        completed_goal_ids=["P-IC"],
        pose=first["pose"],
        payload_kg=first["payload_kg"],
        battery_pct=78.5,
        state="PAUSED",
    )


@pytest.mark.parametrize("policy", ["deterministic", "normal"])
def test_real_registry_and_replanning_preserve_progress_without_model(admin, policy):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        service = WarehouseAgentService(
            db,
            user,
            "embodied-agent-test",
            provider=NoModelCalls(),
            config=Settings(agent_model_policy=policy),
            enforce_configuration=False,
        )
        first = service.query("在机器人实验仓，按控制板备料任务规划路线。")
        plan = first.entities["navigation_plan"]
        assert plan["status"] == "READY"
        assert len(plan["goal_ids"]) == 6
        assert [e.tool for e in first.tool_events] == ["get_navigation_lab", "plan_navigation_lab"]
        assert first.model_call_count == 0
        assert "人工扫码交接" in first.answer and "cm" in first.answer
        assert "segments" not in plan and plan["segments_count"] == 7
        snapshot = ConversationContextService(db, user, Settings()).open(first.conversation_id)
        assert snapshot.last_entity_kind == "navigation"
        assert snapshot.pending_disambiguation["world_id"] == "MB-EMB-LAB-03"
        ctx = execution_context()
        result = service.query(
            "中央通道被占用，重新规划剩余任务。",
            conversation_id=first.conversation_id,
            navigation_context=ctx,
        )
        replan = result.entities["navigation_plan"]
        assert replan["status"] == "READY"
        assert replan["resumed"] is True
        assert replan["start_pose"] == ctx.pose.model_dump()
        assert replan["initial_payload_kg"] == 0.8
        assert replan["initial_battery_pct"] == 78.5
        assert replan["completed_goal_ids"] == ["P-IC"]
        assert "P-IC" not in replan["goal_ids"]
        assert replan["scenario_id"] == "blocked-crossing"
        assert replan["inventory_written"] is False
        assert result.model_call_count == 0
        assert "当前暂停位姿" in result.answer
        assert all(f.kind == "navigation" for f in result.grounded_facts)


def test_missing_execution_is_clarification_and_not_home_reset(admin):
    with SessionLocal() as db:
        service = WarehouseAgentService(
            db,
            db.get(User, admin["id"]),
            "embodied-missing",
            provider=NoModelCalls(),
            config=Settings(agent_model_policy="deterministic"),
            enforce_configuration=False,
        )
        result = service.query("中央通道被占用，重新规划剩余任务。")
        assert result.entities["navigation_plan"]["status"] == "CLARIFICATION"
        assert [event.tool for event in result.tool_events] == ["get_navigation_lab"]
        assert result.model_call_count == 0
        assert "当前位姿" in result.answer


def test_capabilities_then_business_switch_clears_lab_context(admin, material):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        service = WarehouseAgentService(
            db,
            user,
            "embodied-switch",
            provider=NoModelCalls(),
            config=Settings(agent_model_policy="deterministic"),
            enforce_configuration=False,
        )
        result = service.query("机器人能自动打开抽屉吗？")
        assert result.entities["navigation_capabilities"]["automatic_drawer_open"] is False
        assert "没有机械臂" in result.answer
        material_result = service.query(
            f"{material['code']}在哪里？查看它的真实抽屉。", conversation_id=result.conversation_id
        )
        assert (
            "navigation_plan" not in material_result.entities
            and "navigation_lab" not in material_result.entities
        )
        assert "find_material_locations" in [event.tool for event in material_result.tool_events]
        snapshot = ConversationContextService(db, user, Settings()).open(
            material_result.conversation_id
        )
        assert snapshot.last_entity_kind == "material"
        assert snapshot.pending_disambiguation.get("kind") != "navigation"


@pytest.mark.parametrize(
    "field,value",
    [
        ("world_revision", "0" * 64),
        ("goal_ids", ["P-FAKE"]),
        ("completed_goal_ids", ["P-IC", "P-IC"]),
        ("payload_kg", 9),
        ("state", "WAITING_HANDOFF"),
    ],
)
def test_invalid_execution_blocks_only_this_plan(field, value):
    context = execution_context().model_dump(mode="json")
    context[field] = value
    args, result = navigation_plan_arguments(
        navigation_manifest(),
        classify_task_contract("中央通道被占用，重新规划剩余任务。"),
        "中央通道被占用，重新规划剩余任务。",
        context,
    )
    assert args is None and result["status"] == "CLARIFICATION"


def test_manifest_copy_and_mcp_readonly_metadata():
    registry = ToolRegistry()
    manifest = copy.deepcopy(navigation_manifest())
    manifest["goals"][0]["pose"]["x"] = 999
    assert navigation_manifest()["goals"][0]["pose"]["x"] != 999
    assert {"get_navigation_lab", "plan_navigation_lab"}.issubset(registry.mcp_exposed_names)
    metadata_by_name = {item["name"]: item for item in registry.capability_matrix()}
    for name in ("get_navigation_lab", "plan_navigation_lab"):
        metadata = metadata_by_name[name]
        assert metadata["risk_level"] == "read"
        assert metadata["side_effect"] == "none"
        assert metadata["mcp_exposed"] is True


@pytest.mark.parametrize("name", ["get_navigation_lab", "plan_navigation_lab"])
def test_model_cannot_invoke_synthetic_tools_for_an_ordinary_business_turn(admin, name):
    with SessionLocal() as db:
        graph = WarehouseAgentGraph(
            provider=NoModelCalls(),
            tool_context=ToolContext(db, db.get(User, admin["id"]), "navigation-scope-guard"),
            registry=ToolRegistry(),
            max_tool_rounds=3,
            total_deadline_seconds=90,
            force_fact_tool_calls=True,
            deterministic_material_resolution_enabled=True,
        )
        state = {
            "task_contract": classify_task_contract("查询库存").model_dump(mode="json"),
            "entities": {},
            "messages": [],
            "tool_events": [],
            "ui_actions": [],
            "proposal_ids": [],
            "tool_round": 0,
            "pending_tool_calls": [
                {
                    "id": "unexpected-lab-tool",
                    "type": "function",
                    "function": {"name": name, "arguments": "{}"},
                }
            ],
        }
        assert not {"get_navigation_lab", "plan_navigation_lab"}.intersection(
            graph._allowed_schema_names(state)
        )
        result = graph._execute_tools(state)
        assert result["tool_events"][0]["error_code"] == "TOOL_SCOPE_MISMATCH"
        assert (
            "navigation_lab" not in result["entities"]
            and "navigation_plan" not in result["entities"]
        )
