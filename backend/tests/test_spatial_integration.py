"""Mission identity, authorization and real engineering demand grounding."""

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agent.service import WarehouseAgentService
from app.agent.spatial_agent import ground_instruction, should_handle_instruction
from app.agent.spatial_integration import SpatialAgentIntegration, digest
from app.core.config import Settings
from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models import (
    AgentConversationContext,
    InventoryLot,
    Location,
    Material,
    Product,
    ProductBomItem,
    ProductRevision,
    Role,
    User,
)
from app.services.embodied_navigation.service import world_snapshot
from app.services.spatial_business_grounding import SpatialBusinessGrounding
from app.services.spatial_mission_store import SpatialMissionStore
from app.spatial import SpatialMapService


@pytest.fixture
def setup():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        role = Role(
            name="spatial_view", permissions=["material:view", "inventory:view", "project:view"]
        )
        db.add(role)
        db.flush()
        users = [
            User(username=name, full_name=name, password_hash="not-a-login", role_id=role.id)
            for name in ("first", "second")
        ]
        root = Location(
            code="SPATIAL-WH", name="Warehouse", type="warehouse", full_path="Warehouse"
        )
        db.add_all([*users, root])
        db.commit()
        SpatialMapService(db).register_lab()
        db.commit()
        yield db, users
    engine.dispose()


def request(db):
    snapshot = SpatialMapService(db).snapshot("MB-EMB-LAB-03")
    return {
        "map_id": snapshot["map_id"],
        "map_revision": snapshot["revision"],
        "profile": "fastest",
        "start_pose": world_snapshot()["home"],
        "goal_ids": ["P-IC"],
        "return_home": True,
        "constraints": {"battery_pct": 82},
    }


class NoMotion:
    def request(self, *args):
        raise AssertionError("A plan-only query must not invoke motion transport")


def test_plan_only_identity_retries_owner_scope_and_stock(setup):
    db, users = setup
    service = SpatialAgentIntegration(db, users[0], transport=NoMotion())
    body = request(db)
    first = service.create_mission(body, operation_id="operation-one")
    retry = service.create_mission(
        body, conversation_id=first["conversation_id"], operation_id="operation-one"
    )
    assert retry["mission_id"] == first["mission_id"]
    assert first["execution"] is None
    assert first["inventory_written"] is False
    separate = service.create_mission(body, operation_id="operation-one")
    assert separate["mission_id"] != first["mission_id"]
    with pytest.raises(BusinessError, match="任务不存在") as caught:
        SpatialAgentIntegration(db, users[1], transport=NoMotion()).poll(first["mission_id"])
    assert caught.value.code == "SPATIAL_MISSION_NOT_FOUND"
    changed = {**body, "profile": "safest"}
    with pytest.raises(BusinessError) as caught:
        service.create_mission(
            changed, conversation_id=first["conversation_id"], operation_id="operation-one"
        )
    assert caught.value.code == "SPATIAL_IDEMPOTENCY_CONFLICT"
    assert list(db.scalars(select(InventoryLot))) == []


@pytest.mark.parametrize("ttl_minutes", [30, 2880])
def test_spatial_updates_preserve_configured_ttl_and_owner_scope(setup, ttl_minutes):
    db, users = setup
    config = Settings(agent_conversation_ttl_minutes=ttl_minutes)
    service = SpatialAgentIntegration(db, users[0], config=config, transport=NoMotion())
    ttl = timedelta(minutes=ttl_minutes)
    before_create = datetime.now(UTC)
    created = service.create_mission(request(db), operation_id="configured-ttl")
    after_create = datetime.now(UTC)
    row = db.get(AgentConversationContext, created["conversation_id"])
    assert before_create + ttl <= row.expires_at.replace(tzinfo=UTC) <= after_create + ttl

    row.expires_at = datetime.now(UTC) + timedelta(minutes=1)
    db.commit()
    before_update = datetime.now(UTC)
    cancelled = service.command(created["mission_id"], "cancel")
    after_update = datetime.now(UTC)
    assert cancelled["task_graph"]["status"] == "CANCELLED"
    assert before_update + ttl <= row.expires_at.replace(tzinfo=UTC) <= after_update + ttl

    expected = (
        row.user_id,
        row.context_version,
        row.expires_at,
        copy.deepcopy(row.pending_disambiguation),
    )
    foreign = SpatialMissionStore(db, users[1], config)
    with pytest.raises(BusinessError) as caught:
        foreign.find(created["mission_id"])
    assert caught.value.code == "SPATIAL_MISSION_NOT_FOUND"
    with pytest.raises(BusinessError) as caught:
        foreign.save(row, cancelled["task_graph"])
    assert caught.value.code == "AGENT_CONVERSATION_CONFLICT"
    db.refresh(row)
    actual = (row.user_id, row.context_version, row.expires_at, row.pending_disambiguation)
    assert actual == expected


def test_permission_and_stale_revision_fail_closed(setup):
    db, users = setup
    users[0].role.permissions = ["dashboard:view"]
    db.commit()
    with pytest.raises(BusinessError) as caught:
        SpatialAgentIntegration(db, users[0]).create_mission(request(db), operation_id="denied-op")
    assert caught.value.code == "SPATIAL_PERMISSION_REQUIRED"
    users[0].role.permissions = ["material:view"]
    db.commit()
    stale = {**request(db), "map_revision": "outdated"}
    result = SpatialAgentIntegration(db, users[0]).create_mission(stale, operation_id="stale-op")
    assert result["mission_plan"]["status"] == "CLARIFICATION"
    with pytest.raises(BusinessError) as caught:
        SpatialAgentIntegration(db, users[0]).command(result["mission_id"], "start")
    assert caught.value.code == "SPATIAL_READY_PLAN_REQUIRED"


def test_real_engineering_bom_quantity_and_location_read_only(setup):
    db, users = setup
    world = world_snapshot()
    goal = next(goal for goal in world["goals"] if goal["id"] == "P-IC")
    asset = next(asset for asset in world["assets"] if asset["id"] == goal["asset_id"])
    root = db.scalar(select(Location).where(Location.type == "warehouse"))
    device = Location(
        code=asset["reference_code"],
        name="IC storage",
        type="cabinet",
        full_path="Warehouse/IC",
        parent_id=root.id,
    )
    material = Material(
        code="SPATIAL-IC-001",
        name="grounded IC",
        mpn="GROUND123",
        unit="pcs",
        quantity=Decimal(100),
        reserved_quantity=Decimal(5),
    )
    product = Product(code="SPATIAL-PRODUCT", name="Spatial QA board")
    db.add_all([device, material, product])
    db.flush()
    slot = Location(
        code="SPATIAL-A05", name="A05", full_path="Warehouse/IC/A05", parent_id=device.id
    )
    revision = ProductRevision(
        product_id=product.id, revision="R1", status="released", is_default=True, bom_hash="a" * 64
    )
    db.add_all([slot, revision])
    db.flush()
    lot = InventoryLot(material_id=material.id, location_id=slot.id, quantity=Decimal(100))
    item = ProductBomItem(
        product_revision_id=revision.id, material_id=material.id, quantity_per_unit=Decimal(2)
    )
    db.add_all([lot, item])
    db.commit()
    before = (material.quantity, material.reserved_quantity, lot.quantity, item.quantity_per_unit)
    snapshot = SpatialMapService(db).snapshot("MB-EMB-LAB-03")
    result = SpatialBusinessGrounding(db, users[0]).resolve(
        "为空间任务备料 SPATIAL-PRODUCT R1 生产 3 台", snapshot
    )
    assert result["status"] == "GROUNDED"
    assert result["goal_ids"] == ["P-IC"]
    assert result["inventory"][0]["required_quantity"] == "6.0000"
    assert result["pick_locations"][0]["location_id"] == slot.id
    assert result["bom"]["bom_hash"] == revision.bom_hash
    assert not result["inventory_written"]
    assert before == (
        material.quantity,
        material.reserved_quantity,
        lot.quantity,
        item.quantity_per_unit,
    )
    unknown = SpatialBusinessGrounding(db, users[0]).resolve("SPATIAL-PRODUCT 生产 3 台", snapshot)
    assert unknown["status"] == "GROUNDED"
    too_many = SpatialBusinessGrounding(db, users[0]).resolve(
        "SPATIAL-PRODUCT 生产 100 台", snapshot
    )
    assert too_many["status"] == "BLOCKED"
    assert "INSUFFICIENT_STOCK" in {entry["code"] for entry in too_many["violations"]}


def test_feedback_cannot_invent_goal_or_access_another_map(setup):
    db, users = setup
    service = SpatialAgentIntegration(db, users[0], transport=NoMotion())
    result = service.create_mission(request(db), operation_id="feedback-op")
    graph = copy.deepcopy(result["task_graph"])
    with pytest.raises(BusinessError) as caught:
        service._accept(graph, {"mission_id": "other", "map_revision": graph["map_revision"]})
    assert caught.value.code == "SPATIAL_EXECUTION_MISMATCH"
    event = {
        "schema_version": 1,
        "mission_id": graph["mission_id"],
        "event_id": "fake:1",
        "sequence": 1,
        "map_revision": graph["map_revision"],
        "timestamp": "2026-10-04T00:00:00Z",
        "type": "handoff_verified",
        "goal_id": "unregistered",
        "details": {},
    }
    with pytest.raises(BusinessError) as caught:
        service._accept(
            graph,
            {
                "mission_id": graph["mission_id"],
                "map_revision": graph["map_revision"],
                "events": [event],
            },
        )
    assert caught.value.code == "SPATIAL_UNREGISTERED_FEEDBACK"


def test_typed_spatial_request_dominates_a_controlled_provider(setup):
    db, users = setup

    class Provider:
        calls = 0

        def chat(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("Typed mission must run before any model tool choice")

    provider = Provider()
    service = WarehouseAgentService(
        db,
        users[0],
        "spatial-controlled",
        config=Settings(agent_enabled=True, agent_model_policy="normal"),
        provider=provider,
        enforce_configuration=False,
    )
    result = service.query("请只规划 V4 空间任务：P-IC、P-SENSOR、P-LAB，交接后返回 HOME，不要执行")
    assert result.intent == "spatial_mission"
    assert result.entities["spatial_mission"]["execution"] is None
    assert result.entities["spatial_mission"]["mission_plan"]["status"] == "READY"
    assert result.model_call_count == provider.calls == 0


def test_invalid_semantic_feedback_returns_contract_conflict_without_mutating_graph(setup):
    db, users = setup
    service = SpatialAgentIntegration(db, users[0], transport=NoMotion())
    planned = service.create_mission(request(db), operation_id="invalid-semantic-feedback")
    graph = copy.deepcopy(planned["task_graph"])
    before = copy.deepcopy(graph)
    foreign_plan = {**copy.deepcopy(graph["last_valid_plan"]), "mission_id": "other-mission"}
    event = {
        "schema_version": 1,
        "mission_id": graph["mission_id"],
        "map_revision": graph["map_revision"],
        "event_id": "invalid:1",
        "sequence": 1,
        "timestamp": "2026-10-04T12:00:00Z",
        "type": "replanning",
        "details": {"source": "server_semantic_replan", "plan": foreign_plan},
    }
    with pytest.raises(BusinessError) as caught:
        service._accept(
            graph,
            {
                "mission_id": graph["mission_id"],
                "map_revision": graph["map_revision"],
                "status": "NAVIGATING",
                "events": [event],
            },
        )
    assert caught.value.code == "SPATIAL_INVALID_EXECUTION_EVENT"
    assert graph == before


def test_concurrent_poll_is_rebased_without_repeating_a_motion_command(setup):
    db, users = setup
    service = SpatialAgentIntegration(db, users[0], transport=NoMotion())
    planned = service.create_mission(request(db), operation_id="concurrent-control")
    graph = copy.deepcopy(planned["task_graph"])
    row = db.get(AgentConversationContext, planned["conversation_id"])
    version = row.context_version
    pose = {"x": 17.9, "y": 2.6, "yaw": 1.5}
    execution = {
        "mission_id": graph["mission_id"],
        "map_revision": graph["map_revision"],
        "status": "NAVIGATING",
        "last_sequence": 1,
        "current_pose": pose,
        "robot_state": {"battery_pct": 81.9, "payload_kg": 0},
        "events": [
            {
                "schema_version": 1,
                "mission_id": graph["mission_id"],
                "map_revision": graph["map_revision"],
                "event_id": "poll:1",
                "sequence": 1,
                "timestamp": "2026-10-04T12:00:00Z",
                "type": "feedback",
                "goal_id": "P-IC",
                "pose": pose,
                "details": {},
            }
        ],
    }
    concurrent = copy.deepcopy(graph)
    concurrent["robot_state"]["execution"] = execution
    with Session(db.get_bind()) as other:
        latest = other.get(AgentConversationContext, row.id)
        latest.pending_disambiguation = {"spatial_task": concurrent, "retained_context": "new"}
        latest.context_version = version + 1
        other.commit()

    class ObservedTransport:
        calls = []

        def request(self, method, path, *args):
            self.calls.append((method, path))
            assert method == "GET"
            return execution

    service.transport = ObservedTransport()
    saved = service._save(row, graph)
    assert saved["execution"]["current_pose"] == pose
    assert saved["task_graph"]["status"] == "NAVIGATING"
    assert service.transport.calls == [("GET", f"/missions/{graph['mission_id']}?after_sequence=0")]
    assert row.pending_disambiguation["retained_context"] == "new"
    assert row.context_version == version + 2


REPLAN_PARAPHRASES = [
    "请重新规划剩余站点，已经交接的别重复。",
    "V4 任务重规划，保留已经完成的交接。",
    "空间任务剩余路线，请绕行。",
    "通道堵塞了，请给当前任务重新规划。",
    "路径占用了，剩余站点重新规划。",
    "机器人低电量，请重新规划剩余路线。",
    "重新规划，先保留已完成的 P-IC。",
    "V4 当前任务重规划，按最安全路线。",
    "已完成交接保留，重新规划剩余路线。",
    "空间任务重新规划：未完成站点继续。",
    "请为 V4 任务绕行并返回 HOME。",
    "机器人任务的剩余站点怎么走？请重新规划。",
    "当前位置重新规划，已完成的不要重做。",
    "当前空间任务路径封闭，重新规划剩余任务。",
    "物料已经交接一站，现在重新规划后续路线。",
    "V4 空间任务重规划，优先避开风险。",
    "请重新规划剩余任务；保留已核验交接。",
    "V4 重规划当前任务，不要建立新的任务。",
    "这条通道堵塞了，剩余路线请绕行。",
    "请重规划空间任务后续站点，交接记录保持。",
]


def _replay_event(graph, sequence, kind, goal=None, **details):
    return {
        "schema_version": 1,
        "mission_id": graph["mission_id"],
        "map_revision": graph["map_revision"],
        "event_id": f"{graph['mission_id']}:{sequence}",
        "sequence": sequence,
        "timestamp": "2026-10-05T00:00:00Z",
        "type": kind,
        "goal_id": goal,
        "battery_pct": 70,
        "payload_kg": 1,
        "details": details,
    }


def _execution(graph, events):
    return {
        "mission_id": graph["mission_id"],
        "map_revision": graph["map_revision"],
        "status": "NAVIGATING",
        "last_sequence": events[-1]["sequence"],
        "current_goal_id": "P-SENSOR",
        "current_pose": graph["robot_state"]["current_pose"],
        "robot_state": {"battery_pct": 70, "payload_kg": 1},
        "events": copy.deepcopy(events),
    }


def _ready_preparation(graph, events, body):
    # These publication/CAS tests are stationary execution fixtures. The
    # separate bridge lifecycle tests verify how actual hold/action/odometry
    # callbacks establish readiness; this fixture only speaks that protocol.
    execution = _execution(graph, events)
    execution["plan_hash"] = digest(graph["last_valid_plan"])
    execution["replan_control"] = {
        "token": body["token"],
        "state": "ready",
        "generation": 1,
        "source_plan_hash": digest(graph["last_valid_plan"]),
        "map_id": graph["map_id"],
        "map_revision": graph["map_revision"],
        "completed_goal_ids": graph["completed_goal_ids"][:],
    }
    return execution


def _started_replay(setup, instruction):
    db, users = setup
    service = SpatialAgentIntegration(db, users[0], transport=NoMotion())
    body = {**request(db), "goal_ids": ["P-IC", "P-SENSOR", "P-LAB"]}
    planned = service.create_mission(body, operation_id="p3-replay", instruction=instruction)
    graph = planned["task_graph"]
    snapshot = service.maps.snapshot(graph["map_id"])
    dock = next(item for item in snapshot["docks"] if item["id"] == "P-IC")
    graph["robot_state"]["current_pose"] = dock["pose"]
    events = [
        _replay_event(graph, 1, "started", "P-IC"),
        _replay_event(graph, 2, "arrived", "P-IC"),
        _replay_event(graph, 3, "scan_rejected", "P-IC", scan_code="WRONG-SLOT"),
        _replay_event(graph, 4, "scan_verified", "P-IC", scan_code="P-IC"),
        _replay_event(graph, 5, "handoff_verified", "P-IC"),
        _replay_event(graph, 6, "recovery", "P-SENSOR", code="BLOCKED_PATH"),
    ]
    graph = service._accept(graph, _execution(graph, events))
    row = db.get(AgentConversationContext, planned["conversation_id"])
    service._save(row, graph)
    return service, row, graph, events


@pytest.mark.parametrize("instruction", REPLAN_PARAPHRASES)
def test_p3_semantic_replan_publication_race_replays_natural_chinese(setup, instruction):
    db, users = setup
    service, row, graph, events = _started_replay(setup, instruction)
    grounded = ground_instruction(
        instruction, service.maps.snapshot(graph["map_id"]), previous_graph=graph
    )
    assert grounded["action"] == "replan"
    completed_keys = {
        node["id"]: node["idempotency_key"]
        for node in graph["nodes"]
        if node["args"].get("goal_id") == "P-IC" and node["status"] == "SUCCEEDED"
    }

    class PublicationRaceTransport:
        calls = []
        phases = []
        ledger = copy.deepcopy(events)

        def request(self, method, path, body=None):
            self.calls.append((method, path))
            if method == "POST":
                assert path.endswith("/replan")
                self.phases.append(body["phase"])
                if body["phase"] == "prepare":
                    return _ready_preparation(graph, self.ledger, body)
                assert body["phase"] == "commit"
                body = body["plan"]
                assert body["completed_goal_ids"] == ["P-IC"]
                event = _replay_event(
                    graph,
                    7,
                    "replanning",
                    source="server_semantic_replan",
                    plan=copy.deepcopy(body),
                )
                self.ledger.append(event)
                # The publication is visible before the control request's
                # context CAS commits. An independent request still has the
                # old last_valid_plan with completed_goal_ids == [].
                with Session(db.get_bind()) as polling_db:
                    owner = polling_db.get(User, users[0].id)
                    polling = SpatialAgentIntegration(polling_db, owner, transport=self)
                    polled = polling.poll(graph["mission_id"])
                    assert polled["task_graph"]["completed_goal_ids"] == ["P-IC"]
                    assert polled["mission_plan"]["profile"] == body["profile"]
                return _execution(graph, self.ledger)
            assert method == "GET"
            after = int(path.partition("after_sequence=")[2] or 0)
            value = _execution(graph, self.ledger)
            value["events"] = [item for item in value["events"] if item["sequence"] > after]
            return value

    service.transport = PublicationRaceTransport()
    result = service.command(graph["mission_id"], "replan", grounded["args"])
    final = result["task_graph"]
    assert final["completed_goal_ids"] == ["P-IC"]
    assert set(final["remaining_goal_ids"]) == {"P-SENSOR", "P-LAB"}
    assert final["robot_state"]["task_graph_revision"] == 2
    assert final["robot_state"]["scan_rejection_count"] == 1
    assert final["robot_state"]["recovery_success_count"] == 1
    assert final["robot_state"]["request"]["profile"] == result["mission_plan"]["profile"]
    assert service.transport.phases == ["prepare", "commit"]
    assert sum(method == "POST" for method, _ in service.transport.calls) == 2
    assert completed_keys == {
        node["id"]: node["idempotency_key"]
        for node in final["nodes"]
        if node["args"].get("goal_id") == "P-IC" and node["status"] == "SUCCEEDED"
    }
    assert row.pending_disambiguation["spatial_task"]["mission_id"] == final["mission_id"]
    assert not result["inventory_written"]
    assert list(db.scalars(select(InventoryLot))) == []


def test_p3_replan_without_completed_stop_proof_still_fails_closed(setup):
    service, _, graph, events = _started_replay(setup, "重新规划剩余路线")
    before = copy.deepcopy(graph)
    event = _replay_event(graph, 7, "replanning", source="server_semantic_replan")
    # This reproduces the old publication defect; the strict reducer guard is
    # intentionally unchanged instead of hiding an invalid/unsafe plan.
    with pytest.raises(BusinessError) as caught:
        service._accept(graph, _execution(graph, [*events, event]))
    assert caught.value.code == "SPATIAL_INVALID_EXECUTION_EVENT"
    assert str(caught.value.__cause__) == "SPATIAL_REPLAN_COMPLETED_STOPS_REQUIRED"
    assert graph == before


def test_p3_unprefixed_replan_requires_existing_spatial_context(setup):
    service, _, graph, _ = _started_replay(setup, "V4 重规划")
    message = "当前位置重新规划，已完成的不要重做。"
    assert should_handle_instruction(message, graph)
    assert not should_handle_instruction(message)
    assert not ground_instruction(message, {})["handled"]
    for ordinary in ("查 PCM5102 库存", "分析这个项目的 BOM", "12V 转 3.3V 负载 500mA"):
        assert not should_handle_instruction(ordinary, graph)


@pytest.mark.parametrize("expire_row", [False, True])
def test_p3_cas_preserves_newer_handoff_when_slow_poll_returns_an_old_snapshot(setup, expire_row):
    db, users = setup
    service, row, stale, events = _started_replay(setup, "V4 重规划")
    stale_version = row.context_version
    later_events = [
        *events,
        _replay_event(stale, 7, "arrived", "P-SENSOR"),
        _replay_event(stale, 8, "scan_verified", "P-SENSOR", scan_code="P-SENSOR"),
        _replay_event(stale, 9, "handoff_verified", "P-SENSOR"),
    ]
    current = service._accept(stale, _execution(stale, later_events))
    with Session(db.get_bind()) as other:
        latest = other.get(AgentConversationContext, row.id)
        latest.pending_disambiguation = {"spatial_task": current, "retained_context": "new"}
        latest.context_version = stale_version + 1
        other.commit()
    if expire_row:
        # Overlay writes commit/expire the row before a slow bridge response;
        # a fresh ORM context_version alone is insufficient to detect staleness.
        db.expire(row)

    class DelayedTransport:
        calls = []

        def request(self, method, path, *args):
            self.calls.append((method, path))
            assert method == "GET"
            return _execution(stale, events)

    service.transport = DelayedTransport()
    stale["robot_state"].update(overlay_generation=1, overlay_ids={"obstacle": "generation-1"})
    result = service._save(row, stale)
    graph = result["task_graph"]
    assert graph["completed_goal_ids"] == ["P-IC", "P-SENSOR"]
    assert graph["robot_state"]["last_event_sequence"] == 9
    assert graph["robot_state"]["execution"]["last_sequence"] == 9
    assert graph["robot_state"]["overlay_ids"] == {"obstacle": "generation-1"}
    assert row.pending_disambiguation["retained_context"] == "new"
    assert service.transport.calls == [("GET", f"/missions/{graph['mission_id']}?after_sequence=9")]
