from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.exceptions import BusinessError
from app.models import InventoryLot, Location, ProjectReservation, StockMovement, WarehouseMap
from app.schemas.picking import (
    PickAllocationConfirmRequest,
    PickScanValidateRequest,
    PickTaskCreateRequest,
    PickTaskReplanRequest,
)
from app.services.build_plans import BuildPlanService
from app.services.inventory import InventoryService
from app.services.picking import PickingService
from tests.integration.test_build_plan_execution import _scenario


@pytest.fixture
def picking(tmp_path):
    engine, db, user, material, revision, project = _scenario(tmp_path, "pick")
    location = Location(code="PICK-A01", name="A01", type="bin", full_path="仓库 / A01")
    db.add(location)
    db.commit()
    InventoryService(db, user.id, "seed").initialize_location_allocations(
        material.id, [{"location_id": location.id, "quantity": "6"}], "pick-init", "测试库位"
    )
    plan = BuildPlanService(db, user.id, "plan").create(
        product_revision_id=revision.id,
        project_id=project.id,
        build_quantity=2,
        client_operation_id="pick-build-plan",
    )
    plan.status = "reserved"
    db.commit()
    service = PickingService(db, user.id, "test")
    payload = PickTaskCreateRequest(
        build_plan_id=plan.id,
        client_operation_id="pick-create-001",
        build_plan_snapshot_hash=plan.snapshot_hash,
    )
    yield service, payload, material, location, project
    db.close()
    engine.dispose()


def confirmation(material, location, key="confirm-001", quantity="2"):
    return PickAllocationConfirmRequest(
        quantity=quantity,
        idempotency_key=key,
        confirmation_method="barcode",
        location_token=location.code,
        material_token=material.code,
    )


def test_partial_replay_remaining_replan_and_complete(picking):
    service, payload, material, location, project = picking
    task = service.create(payload)
    assert service.create(payload)["id"] == task["id"]
    allocation_id = task["allocations"][0]["id"]
    db = service.db
    before = db.scalar(select(func.count()).select_from(StockMovement))
    confirm = confirmation(material, location)
    result = service.confirm(allocation_id, confirm)
    assert result["status"] == "in_progress"
    assert service.confirm(allocation_id, confirm)["idempotent_replay"]
    assert db.scalar(select(func.count()).select_from(StockMovement)) == before + 1
    replanned = service.replan(
        task["id"], PickTaskReplanRequest(client_operation_id="replan-001", reason="继续剩余取料")
    )
    assert Decimal(replanned["next_stop"]["remaining_quantity"]) == 4
    service.confirm(allocation_id, confirmation(material, location, "confirm-002", "4"))
    assert service.detail(task["id"])["status"] == "completed"
    db.refresh(material)
    assert material.quantity == 14 and material.reserved_quantity == 0
    reservation = db.scalar(
        select(ProjectReservation).where(ProjectReservation.project_id == project.id)
    )
    assert reservation.quantity == 0 and reservation.consumed_quantity == 6
    assert db.scalar(select(InventoryLot.quantity)) == 0
    with pytest.raises(BusinessError):
        service.create(payload.model_copy(update={"client_operation_id": "new-task-002"}))


@pytest.mark.parametrize("token", ["location_token", "material_token"])
def test_wrong_scan_has_no_stock_movement(picking, token):
    service, payload, material, location, _ = picking
    task = service.create(payload)
    before = service.db.scalar(select(func.count()).select_from(StockMovement))
    request = confirmation(material, location).model_copy(update={token: "WRONG"})
    with pytest.raises(BusinessError):
        service.confirm(task["allocations"][0]["id"], request)
    assert service.db.scalar(select(func.count()).select_from(StockMovement)) == before
    assert material.quantity == 20


def test_cancel_releases_claim_not_reservation_or_completed_stock(picking):
    service, payload, material, location, _ = picking
    task = service.create(payload)
    service.confirm(task["allocations"][0]["id"], confirmation(material, location))
    service.cancel(task["id"], "取消剩余")
    service.db.refresh(material)
    assert material.quantity == 18 and material.reserved_quantity == 4
    second = service.create(payload.model_copy(update={"client_operation_id": "replacement-001"}))
    assert Decimal(second["next_stop"]["remaining_quantity"]) == 4


def test_stale_lot_needs_replan(picking):
    service, payload, material, location, _ = picking
    task = service.create(payload)
    # Simulate an independently recorded physical discrepancy in the isolated fixture.
    lot = service.db.scalar(select(InventoryLot))
    lot.quantity = 3
    service.db.commit()
    with pytest.raises(BusinessError, match="实际库位"):
        service.confirm(task["allocations"][0]["id"], confirmation(material, location))
    assert service.detail(task["id"])["status"] == "needs_replan"
    assert material.quantity == 20


def test_replan_keeps_completed_allocation_immutable(picking):
    service, payload, material, location, _ = picking
    db = service.db
    first_lot = db.scalar(select(InventoryLot))
    first_lot.quantity = 2
    other = Location(code="PICK-Z02", name="Z02", type="bin", full_path="仓库 / Z02")
    db.add(other)
    db.flush()
    second_lot = InventoryLot(material_id=material.id, location_id=other.id, quantity=4)
    db.add(second_lot)
    db.commit()
    task = service.create(payload)
    first = next(a for a in task["allocations"] if a["location_id"] == location.id)
    service.confirm(first["id"], confirmation(material, location))
    # Isolated fixture simulates an independently recorded relocation of remaining stock.
    first_lot.quantity, second_lot.quantity = 4, 0
    db.commit()
    replanned = service.replan(
        task["id"],
        PickTaskReplanRequest(client_operation_id="history-replan-01", reason="库位更新"),
    )
    historical = next(a for a in replanned["allocations"] if a["id"] == first["id"])
    assert historical["status"] == "picked"
    assert Decimal(historical["planned_quantity"]) == Decimal(historical["picked_quantity"]) == 2
    assert replanned["next_stop"]["id"] != first["id"]
    assert Decimal(replanned["next_stop"]["remaining_quantity"]) == 4


def test_operator_state_groups_same_route_node_across_historical_sequences(picking, monkeypatch):
    service, payload, _, _, _ = picking
    task = service.create(payload)
    detail = service.detail(task["id"])
    first = dict(detail["allocations"][0])
    second = dict(first)
    second.update(
        {
            "id": first["id"] + 1000,
            "route_sequence": first["route_sequence"] + 1,
            "route_node_code": "PF-ONE-STATION",
            "status": "pending",
            "picked_quantity": "0",
            "remaining_quantity": first["planned_quantity"],
        }
    )
    first.update(
        {
            "route_node_code": "PF-ONE-STATION",
            "route_sequence": first["route_sequence"],
            "status": "picked",
            "remaining_quantity": "0",
        }
    )
    monkeypatch.setattr(
        service,
        "detail",
        lambda _task_id: {**detail, "allocations": [first, second]},
    )

    state = service.operator_state(task["id"])
    matching = [
        group
        for group in state["station_groups"]
        if group["route_node_code"] == "PF-ONE-STATION"
    ]
    assert len(matching) == 1
    assert [row["id"] for row in matching[0]["allocations"]] == [first["id"], second["id"]]


def test_product_picking_preview_creates_no_execution_truth(picking):
    from app.models import BuildPlan, PickAllocation, PickTask

    service, payload, _, _, _ = picking
    plan = service.plan(payload.build_plan_id)
    models = (BuildPlan, PickTask, PickAllocation, StockMovement, ProjectReservation)
    before = [service.db.scalar(select(func.count()).select_from(model)) for model in models]
    preview = service.preview_product(plan.product_revision_id, 2, plan.project_id)
    assert preview["preview_only"] and not preview["executable"]
    assert preview["items"][0]["allocations"][0]["full_path"] == "仓库 / A01"
    assert before == [
        service.db.scalar(select(func.count()).select_from(model)) for model in models
    ]


def test_agent_task_followups_are_deterministic_and_never_confirm(picking):
    from app.agent.service import WarehouseAgentService
    from app.core.config import Settings
    from app.models import User

    class NoProvider:
        def chat(self, *args, **kwargs):
            raise AssertionError("Picking must never call a model")

    service, payload, _, _, _ = picking
    task = service.create(payload)
    agent = WarehouseAgentService(
        service.db,
        service.db.get(User, service.user_id),
        "agent-picking-test",
        provider=NoProvider(),
        config=Settings(agent_enabled=True, dashscope_api_key=""),
    )
    before = service.db.scalar(select(func.count()).select_from(StockMovement))
    result = agent.query(f"打开拣货任务 {task['pick_task_no']}")
    for message in ("下一站去哪？", "这个抽屉拿几个？", "直接帮我把第一站确认掉，拿料吧"):
        result = agent.query(message, conversation_id=result.conversation_id)
        assert result.entities["pick_task"]["id"] == task["id"]
        assert result.model_call_count == 0
    assert "不会自动扣库存" in result.answer
    assert service.db.scalar(select(func.count()).select_from(StockMovement)) == before
    fresh = agent.query("下一站去哪？")
    assert fresh.intent == "picking_clarification"
    assert not fresh.entities


@pytest.mark.parametrize("followup", ["先别预留，够不够？", "这批够吗？", "是否够料？"])
def test_agent_keeps_build_quantity_for_readonly_picking_preview(picking, followup):
    from app.agent.service import WarehouseAgentService
    from app.core.config import Settings
    from app.models import BuildPlan, Product, ProductRevision, User

    class NoProvider:
        def chat(self, *args, **kwargs):
            raise AssertionError("Read-only production followup must remain deterministic")

    service, payload, _, _, _ = picking
    plan = service.plan(payload.build_plan_id)
    revision = service.db.get(ProductRevision, plan.product_revision_id)
    product = service.db.get(Product, revision.product_id)
    agent = WarehouseAgentService(
        service.db,
        service.db.get(User, service.user_id),
        "production-followup-test",
        provider=NoProvider(),
        config=Settings(agent_enabled=True, dashscope_api_key=""),
    )
    before = service.db.scalar(select(func.count()).select_from(BuildPlan))
    first = agent.query(f"我要做 2 台产品 {product.code}。")
    second = agent.query(followup, conversation_id=first.conversation_id)
    assert second.entities["build_readiness"]["build_quantity"] == 2
    third = agent.query("先给我看看要拿料会去哪几个库位。", conversation_id=first.conversation_id)
    assert third.intent == "picking_preview" and third.model_call_count == 0
    assert third.entities["picking_readiness"]["build_quantity"] == 2
    assert service.db.scalar(select(func.count()).select_from(BuildPlan)) == before


def test_picking_api_enforces_narrow_readonly_permission(picking):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.api.v1.picking import router
    from app.core.database import get_db

    service, payload, material, location, _ = picking
    task = service.create(payload)
    app = FastAPI()
    app.include_router(router)
    principal = SimpleNamespace(
        id=service.user_id, role=SimpleNamespace(permissions=["picking:view"])
    )
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_db] = lambda: service.db
    before = service.db.scalar(select(func.count()).select_from(StockMovement))
    with TestClient(app) as client:
        assert client.get(f"/pick-tasks/{task['id']}").status_code == 200
        assert client.get(f"/pick-tasks/{task['id']}/operator-state").status_code == 200
        assert client.get(f"/picking/readiness/{payload.build_plan_id}").status_code == 200
        assert client.post("/pick-tasks", json=payload.model_dump(mode="json")).status_code == 403
        validate = client.post(
            f"/pick-allocations/{task['allocations'][0]['id']}/validate-scan",
            json={"location_token": location.code, "material_token": material.code},
        )
        assert validate.status_code == 403
        response = client.post(
            f"/pick-allocations/{task['allocations'][0]['id']}/confirm",
            json=confirmation(material, location).model_dump(mode="json"),
        )
        assert response.status_code == 403
        principal.role.permissions = ["inventory:operate"]
        assert client.get(f"/pick-tasks/{task['id']}").status_code == 403
    assert service.db.scalar(select(func.count()).select_from(StockMovement)) == before


def test_allocations_group_exact_drawers_within_one_organizer_stop(picking):
    from app.models import PickAllocation

    service, payload, material, location, _ = picking
    task = service.create(payload)
    first = service.allocations(task["id"])[0]
    other = Location(code="PICK-GROUP-OTHER", name="Other", type="bin", full_path="仓库 / Other")
    service.db.add(other)
    service.db.flush()
    lot = InventoryLot(material_id=material.id, location_id=other.id, quantity=1)
    service.db.add(lot)
    service.db.flush()
    # Closed history rows interleave IDs; the next-stop list still groups exact drawers.
    service.db.add(
        PickAllocation(
            pick_task_item_id=first.pick_task_item_id,
            inventory_lot_id=lot.id,
            location_id=other.id,
            planned_quantity=1,
            picked_quantity=0,
            status="cancelled",
            route_sequence=1,
        )
    )
    service.db.flush()
    service.db.add(
        PickAllocation(
            pick_task_item_id=first.pick_task_item_id,
            inventory_lot_id=first.inventory_lot_id,
            location_id=location.id,
            planned_quantity=1,
            picked_quantity=0,
            status="cancelled",
            route_sequence=1,
            generation=2,
        )
    )
    service.db.commit()
    assert [a.location_id for a in service.allocations(task["id"])] == [
        location.id,
        location.id,
        other.id,
    ]


def test_operator_state_and_scan_validation_are_read_only(picking):
    from app.schemas.picking import PickScanValidateRequest

    service, payload, material, location, _ = picking
    task = service.create(payload)
    allocation = task["allocations"][0]
    before_movements = service.db.scalar(select(func.count()).select_from(StockMovement))

    state = service.operator_state(task["id"])
    assert state["progress"] == {
        "stations_total": 1,
        "stations_completed": 0,
        "allocations_total": 1,
        "allocations_completed": 0,
    }
    assert state["current_allocation"]["id"] == allocation["id"]
    assert state["current_station"]["status"] == "current"

    location_only = service.validate_scan(
        allocation["id"],
        PickScanValidateRequest(location_token=location.code),
    )
    assert location_only["location_match"] is True
    assert location_only["material_match"] is None
    assert location_only["ready"] is False

    ready = service.validate_scan(
        allocation["id"],
        PickScanValidateRequest(
            location_token=location.code,
            material_token=material.code,
        ),
    )
    assert ready["location_match"] is True
    assert ready["material_match"] is True
    assert ready["ready"] is True
    assert service.db.scalar(select(func.count()).select_from(StockMovement)) == before_movements


def test_operator_issue_is_audit_only(picking):
    from app.models import AuditLog
    from app.schemas.picking import PickIssueRequest

    service, payload, _, _, _ = picking
    task = service.create(payload)
    allocation = task["allocations"][0]
    before_movements = service.db.scalar(select(func.count()).select_from(StockMovement))
    before_status = service.detail(task["id"])["status"]

    result = service.report_issue(
        task["id"],
        PickIssueRequest(
            issue_type="location_blocked",
            allocation_id=allocation["id"],
            notes="通道临时有障碍物",
        ),
    )
    assert result["recorded"] is True
    assert result["recommended_action"] == "replan"
    assert service.detail(task["id"])["status"] == before_status
    assert service.db.scalar(select(func.count()).select_from(StockMovement)) == before_movements
    event = service.db.scalar(
        select(AuditLog)
        .where(
            AuditLog.resource_type == "pick_task",
            AuditLog.resource_id == str(task["id"]),
            AuditLog.action == "picking.issue",
        )
        .order_by(AuditLog.id.desc())
    )
    assert event is not None
    assert event.after_data["issue_type"] == "location_blocked"


def test_manual_override_requires_reason_and_is_audited(picking):
    from app.models import AuditLog

    service, payload, material, location, _ = picking
    task = service.create(payload)
    allocation_id = task["allocations"][0]["id"]
    before = service.db.scalar(select(func.count()).select_from(StockMovement))
    missing_reason = confirmation(material, location).model_copy(
        update={"confirmation_method": "manual", "manual_override_reason": ""}
    )
    with pytest.raises(BusinessError) as rejected:
        service.confirm(allocation_id, missing_reason)
    assert rejected.value.code == "PICK_MANUAL_OVERRIDE_REASON_REQUIRED"
    assert service.db.scalar(select(func.count()).select_from(StockMovement)) == before

    reason = "扫码枪故障，已现场核对库位与物料标签"
    service.confirm(
        allocation_id,
        missing_reason.model_copy(
            update={"idempotency_key": "manual-confirm-001", "manual_override_reason": reason}
        ),
    )
    audit = service.db.scalar(
        select(AuditLog)
        .where(AuditLog.resource_type == "pick_task", AuditLog.resource_id == str(task["id"]))
        .where(AuditLog.action == "picking.manual_override")
        .order_by(AuditLog.id.desc())
    )
    assert audit is not None
    assert audit.after_data["allocation_id"] == allocation_id
    assert audit.after_data["reason"] == reason


def test_count_unit_rejects_fractional_pick_without_stock_write(picking):
    service, payload, material, location, _ = picking
    material.unit = "pcs"
    service.db.commit()
    task = service.create(payload)
    before = service.db.scalar(select(func.count()).select_from(StockMovement))
    with pytest.raises(BusinessError) as rejected:
        service.confirm(
            task["allocations"][0]["id"],
            confirmation(material, location, key="fractional-001", quantity="0.5"),
        )
    assert rejected.value.code == "PICK_QUANTITY_PRECISION_INVALID"
    assert rejected.value.details == {"unit": "pcs", "quantity_precision": 0}
    assert service.db.scalar(select(func.count()).select_from(StockMovement)) == before


def test_barcode_token_is_accepted_as_the_material_scan_identity(picking):
    service, payload, material, location, _ = picking
    material.barcode = "BARCODE-PICK-001"
    service.db.commit()
    task = service.create(payload)
    allocation = task["allocations"][0]
    result = service.validate_scan(
        allocation["id"],
        PickScanValidateRequest(
            location_token=location.code,
            material_token=material.barcode,
        ),
    )
    assert result["material_match"] is True
    assert result["ready"] is True
    service.confirm(
        allocation["id"],
        confirmation(material, location, key="barcode-confirm-001").model_copy(
            update={"material_token": material.barcode}
        ),
    )
    assert service.detail(task["id"])["allocations"][0]["status"] in {"partial", "picked"}


def test_operator_state_orders_replanned_station_by_pending_sequence(picking, monkeypatch):
    service, payload, _, _, _ = picking
    task = service.create(payload)
    detail = service.detail(task["id"])
    base = dict(detail["allocations"][0])
    historical = {
        **base,
        "id": base["id"] + 1000,
        "route_node_code": "PF-REVISIT",
        "route_sequence": 1,
        "status": "picked",
        "picked_quantity": base["planned_quantity"],
        "remaining_quantity": "0",
    }
    pending_revisit = {
        **base,
        "id": base["id"] + 1001,
        "route_node_code": "PF-REVISIT",
        "route_sequence": 4,
        "status": "pending",
        "picked_quantity": "0",
        "remaining_quantity": base["planned_quantity"],
    }
    pending_before = {
        **base,
        "id": base["id"] + 1002,
        "route_node_code": "PF-BEFORE",
        "route_sequence": 2,
        "status": "pending",
        "picked_quantity": "0",
        "remaining_quantity": base["planned_quantity"],
    }
    monkeypatch.setattr(
        service,
        "detail",
        lambda _task_id: {
            **detail,
            "allocations": [historical, pending_revisit, pending_before],
        },
    )

    state = service.operator_state(task["id"])
    assert [group["route_node_code"] for group in state["station_groups"]] == [
        "PF-BEFORE",
        "PF-REVISIT",
    ]
    revisit = state["station_groups"][1]
    assert revisit["route_sequence"] == 4
    assert state["current_station"]["route_node_code"] == "PF-BEFORE"


def test_replan_uses_the_task_frozen_map_not_the_active_map(picking, monkeypatch):
    from types import SimpleNamespace

    from app.services import picking as picking_module
    from app.services.warehouse_routing import OptimizedWarehouseRoute

    service, payload, _, location, _ = picking
    db = service.db
    map_a = WarehouseMap(
        warehouse_location_id=location.id,
        code="PICK-MAP-A",
        name="Frozen map A",
        version="1.0.0",
        status="active",
        width_m=Decimal("1"),
        height_m=Decimal("1"),
        graph_hash="a" * 64,
    )
    map_b = WarehouseMap(
        warehouse_location_id=location.id,
        code="PICK-MAP-B",
        name="Changed map B",
        version="2.0.0",
        status="archived",
        width_m=Decimal("1"),
        height_m=Decimal("1"),
        graph_hash="b" * 64,
    )
    db.add_all([map_a, map_b])
    db.flush()

    class IsolatedMapService:
        def __init__(self, isolated_db):
            self.db = isolated_db

        def active_for_warehouse(self, warehouse_id):
            return self.db.scalar(
                select(WarehouseMap).where(
                    WarehouseMap.warehouse_location_id == warehouse_id,
                    WarehouseMap.status == "active",
                )
            )

        def route_for_locations(self, map_id, _locations, *, closed_edge_codes):
            map_row = self.db.get(WarehouseMap, map_id)
            assert map_row is not None
            return OptimizedWarehouseRoute(
                strategy="graph_v1_exact",
                map_code=map_row.code,
                graph_hash=map_row.graph_hash,
                calibration_status="demo_synthetic",
                start_node="PACK",
                end_node="PACK",
                ordered_stop_nodes=(f"{map_row.code}-STOP",),
                total_distance_m=1.0,
                segments=(),
                optimization_note="isolated A/B map-freeze proof",
            )

        def resolve_pick_node(self, map_id, _location_id):
            map_row = self.db.get(WarehouseMap, map_id)
            assert map_row is not None
            return SimpleNamespace(code=f"{map_row.code}-STOP")

    monkeypatch.setattr(picking_module, "WarehouseMapService", IsolatedMapService)
    old_task = service.create(payload.model_copy(update={"client_operation_id": "map-freeze-old"}))
    assert old_task["warehouse_map_id"] == map_a.id
    assert old_task["warehouse_graph_hash"] == map_a.graph_hash

    map_a.status, map_b.status = "archived", "active"
    db.commit()
    assert (
        db.scalar(
            select(WarehouseMap).where(
                WarehouseMap.warehouse_location_id == location.id,
                WarehouseMap.status == "active",
            )
        ).id
        == map_b.id
    )

    old_replanned = service.replan(
        old_task["id"],
        PickTaskReplanRequest(client_operation_id="map-freeze-replan", reason="地图切换"),
    )
    assert old_replanned["warehouse_map_id"] == map_a.id
    assert old_replanned["warehouse_graph_hash"] == map_a.graph_hash
    assert old_replanned["route_plan"]["map_code"] == map_a.code

    service.cancel(old_task["id"], "释放隔离 A 任务以创建 B 任务")
    new_task = service.create(payload.model_copy(update={"client_operation_id": "map-freeze-new"}))
    assert new_task["warehouse_map_id"] == map_b.id
    assert new_task["warehouse_graph_hash"] == map_b.graph_hash
    assert map_a.graph_hash != map_b.graph_hash
