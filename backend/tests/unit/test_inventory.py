from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.exceptions import BusinessError
from app.models import (
    InventoryLot,
    Location,
    Material,
    Project,
    ProjectReservation,
    StockMovement,
    User,
)
from app.services.inventory import InventoryService


def svc(db, user_id):
    return InventoryService(db, user_id, "unit-request")


def test_available_quantity_formula(material, admin):
    with SessionLocal() as db:
        item = db.get(Material, material["id"])
        item.quantity = Decimal("10")
        item.reserved_quantity = Decimal("3")
        db.commit()
        assert item.available_quantity == Decimal("7")


def test_inbound_outbound_and_insufficient(material, admin):
    with SessionLocal() as db:
        service = svc(db, admin["id"])
        result = service.inbound(material["id"], Decimal("10"), "unit-inbound", "采购到货")
        assert Decimal(result["quantity"]) == 10
        result = service.outbound(material["id"], Decimal("4"), "unit-outbound", "研发领料")
        assert Decimal(result["quantity"]) == 6
        with pytest.raises(BusinessError, match="可用库存不足"):
            service.outbound(material["id"], Decimal("7"), "unit-too-much", "超额领料")


def test_idempotency(material, admin):
    with SessionLocal() as db:
        service = svc(db, admin["id"])
        first = service.inbound(material["id"], Decimal("5"), "same-key-123", "首次")
        second = service.inbound(material["id"], Decimal("5"), "same-key-123", "重试")
        assert first["movement_id"] == second["movement_id"]
        assert second["idempotent_replay"] is True
        assert db.get(Material, material["id"]).quantity == 5


def test_reservation_cancel_and_convert(material, admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        project = Project(code=f"P-{material['id']}", name="测试项目", manager_id=user.id)
        db.add(project)
        db.commit()
        service = svc(db, user.id)
        service.inbound(material["id"], Decimal("10"), "reserve-stock", "初始")
        reserved = service.reserve(
            material["id"], project.id, Decimal("6"), "reserve-key", "项目备料"
        )
        assert (
            Decimal(reserved["reserved_quantity"]) == 6
            and Decimal(reserved["available_quantity"]) == 4
        )
        cancelled = service.cancel_reservation(
            material["id"], project.id, Decimal("2"), "cancel-key", "需求减少"
        )
        assert Decimal(cancelled["reserved_quantity"]) == 4
        converted = service.reservation_to_outbound(
            material["id"], project.id, Decimal("4"), "convert-key", "项目领用"
        )
        assert Decimal(converted["quantity"]) == 6 and Decimal(converted["reserved_quantity"]) == 0


def test_pick_outbound_consumes_exact_location_and_project_reservation(material, admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        project = Project(code=f"PICK-{material['id']}", name="拣货项目", manager_id=user.id)
        location = Location(
            code=f"PICK-LOC-{material['id']}",
            name="拣货库位",
            type="bin",
            full_path="研发仓库 / 拣货测试 / A01",
        )
        db.add_all([project, location])
        db.commit()
        service = svc(db, user.id)
        service.inbound(material["id"], Decimal("10"), "pick-stock", "初始")
        service.initialize_location_allocations(
            material["id"],
            [{"location_id": location.id, "quantity": "8"}],
            "pick-location-init",
            "初始库位",
        )
        service.reserve(
            material["id"], project.id, Decimal("6"), "pick-reserve", "生产预留"
        )

        result = service.reservation_to_outbound_from_location(
            material["id"],
            project.id,
            Decimal("4"),
            location.id,
            "pick-confirm",
            "拣货确认",
        )

        db.expire_all()
        item = db.get(Material, material["id"])
        lot_row = db.scalar(
            select(InventoryLot).where(
                InventoryLot.material_id == material["id"],
                InventoryLot.location_id == location.id,
            )
        )
        reservation = db.scalar(
            select(ProjectReservation).where(
                ProjectReservation.project_id == project.id,
                ProjectReservation.material_id == material["id"],
            )
        )
        movement = db.get(StockMovement, result["movement_id"])
        assert item.quantity == Decimal("6")
        assert item.reserved_quantity == Decimal("2")
        assert lot_row.quantity == Decimal("4")
        assert reservation.quantity == Decimal("2")
        assert reservation.consumed_quantity == Decimal("4")
        assert movement.operation_type == "pick_outbound"
        assert movement.source_location_id == location.id
        assert Decimal(result["source_location_quantity"]) == 4


def test_pick_outbound_rejects_stale_location_quantity(material, admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        project = Project(code=f"PICK-SHORT-{material['id']}", name="拣货短缺", manager_id=user.id)
        location = Location(
            code=f"PICK-SHORT-LOC-{material['id']}",
            name="短缺库位",
            type="bin",
            full_path="研发仓库 / 拣货测试 / B01",
        )
        db.add_all([project, location])
        db.commit()
        service = svc(db, user.id)
        service.inbound(material["id"], Decimal("10"), "pick-short-stock", "初始")
        service.initialize_location_allocations(
            material["id"],
            [{"location_id": location.id, "quantity": "3"}],
            "pick-short-location",
            "初始库位",
        )
        service.reserve(
            material["id"], project.id, Decimal("6"), "pick-short-reserve", "生产预留"
        )

        with pytest.raises(BusinessError) as exc_info:
            service.reservation_to_outbound_from_location(
                material["id"],
                project.id,
                Decimal("4"),
                location.id,
                "pick-short-confirm",
                "拣货确认",
            )
        assert exc_info.value.code == "PICK_LOCATION_STOCK_CHANGED"


def test_inactive_material_allows_existing_reservation_cleanup(material, admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        project = Project(code=f"PI-{material['id']}", name="停用清理", manager_id=user.id)
        db.add(project)
        db.commit()
        service = svc(db, user.id)
        service.inbound(material["id"], Decimal("10"), "inactive-stock", "初始")
        service.reserve(
            material["id"], project.id, Decimal("6"), "inactive-reserve", "历史预留"
        )
        item = db.get(Material, material["id"])
        item.is_active = False
        db.commit()

        cancelled = service.cancel_reservation(
            material["id"], project.id, Decimal("2"), "inactive-cancel", "释放"
        )
        converted = service.reservation_to_outbound(
            material["id"], project.id, Decimal("4"), "inactive-convert", "履行"
        )
        assert Decimal(cancelled["reserved_quantity"]) == 4
        assert Decimal(converted["reserved_quantity"]) == 0
        assert Decimal(converted["quantity"]) == 6


def test_scrap_refund_adjust_and_reverse(material, admin):
    with SessionLocal() as db:
        service = svc(db, admin["id"])
        service.inbound(material["id"], Decimal("12"), "misc-stock", "初始")
        scrap = service.scrap(material["id"], Decimal("2"), "scrap-key", "损坏")
        assert Decimal(scrap["quantity"]) == 10
        refund = service.refund(material["id"], Decimal("3"), "refund-key", "退回")
        assert Decimal(refund["quantity"]) == 13
        adjusted = service.adjust(material["id"], Decimal("11"), "adjust-key", "实盘差异")
        assert Decimal(adjusted["difference"]) == -2
        reversed_result = service.reverse(scrap["movement_id"], "reverse-key", "误报废冲正")
        assert Decimal(reversed_result["quantity"]) == 13


def test_location_transfer(material, admin):
    with SessionLocal() as db:
        source = Location(
            code=f"SRC-{material['id']}", name="源库位", type="bin", full_path="源库位"
        )
        target = Location(
            code=f"DST-{material['id']}", name="目标库位", type="bin", full_path="目标库位"
        )
        db.add_all([source, target])
        db.flush()
        db.add(
            InventoryLot(material_id=material["id"], location_id=source.id, quantity=Decimal("8"))
        )
        db.commit()
        result = svc(db, admin["id"]).transfer(
            material["id"], Decimal("3"), source.id, target.id, "transfer-key", "整理库位"
        )
        assert Decimal(result["quantity"]) == 0
        lots = db.scalars(
            select(InventoryLot)
            .where(InventoryLot.material_id == material["id"])
            .order_by(InventoryLot.location_id)
        ).all()
        assert sorted(x.quantity for x in lots) == [Decimal("3"), Decimal("5")]


def test_initial_location_allocation_is_bounded_and_idempotent(material, admin):
    with SessionLocal() as db:
        first_location = Location(
            code=f"INIT-A-{material['id']}", name="初始 A", type="bin", full_path="初始 A"
        )
        second_location = Location(
            code=f"INIT-B-{material['id']}", name="初始 B", type="bin", full_path="初始 B"
        )
        db.add_all([first_location, second_location])
        db.commit()
        service = svc(db, admin["id"])
        service.inbound(material["id"], Decimal("10"), "init-stock", "初始库存")
        allocations = [
            {"location_id": first_location.id, "quantity": "6"},
            {"location_id": second_location.id, "quantity": "3"},
        ]
        first = service.initialize_location_allocations(
            material["id"], allocations, "init-lots", "初始库位"
        )
        second = service.initialize_location_allocations(
            material["id"], allocations, "init-lots", "初始库位重试"
        )

        assert Decimal(first["allocated_quantity"]) == 9
        assert second["idempotent_replay"] is True
        lots = db.scalars(
            select(InventoryLot).where(InventoryLot.material_id == material["id"])
        ).all()
        assert sorted(item.quantity for item in lots) == [Decimal("3"), Decimal("6")]
        movements = db.scalars(
            select(StockMovement).where(
                StockMovement.material_id == material["id"],
                StockMovement.operation_type == "initial_location_allocation",
            )
        ).all()
        assert len(movements) == 2


def test_initial_location_allocation_never_exceeds_or_overwrites_stock(material, admin):
    with SessionLocal() as db:
        location = Location(
            code=f"INIT-C-{material['id']}", name="初始 C", type="bin", full_path="初始 C"
        )
        other = Location(
            code=f"INIT-D-{material['id']}", name="初始 D", type="bin", full_path="初始 D"
        )
        db.add_all([location, other])
        db.commit()
        service = svc(db, admin["id"])
        service.inbound(material["id"], Decimal("5"), "bounded-stock", "初始库存")

        with pytest.raises(BusinessError, match="不能超过账面库存"):
            service.initialize_location_allocations(
                material["id"],
                [{"location_id": location.id, "quantity": "6"}],
                "bounded-lots",
                "超额分配",
            )
        assert db.scalars(
            select(InventoryLot).where(InventoryLot.material_id == material["id"])
        ).all() == []

        service.initialize_location_allocations(
            material["id"],
            [{"location_id": location.id, "quantity": "4"}],
            "valid-lots",
            "合法分配",
        )
        with pytest.raises(BusinessError, match="不会覆盖现有数据"):
            service.initialize_location_allocations(
                material["id"],
                [{"location_id": other.id, "quantity": "4"}],
                "different-lots",
                "不同分配",
            )


def test_reserve_batch_is_atomic_when_any_material_is_short(material, admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        project = Project(code=f"PB-{material['id']}", name="批量原子性", manager_id=user.id)
        second = Material(code=f"BATCH-{material['id']}", name="库存不足物料")
        db.add_all([project, second])
        db.commit()
        service = svc(db, user.id)
        service.inbound(material["id"], Decimal("10"), "batch-stock-1", "初始")
        service.inbound(second.id, Decimal("1"), "batch-stock-2", "初始")

        with pytest.raises(BusinessError, match="存在可用库存不足"):
            service.reserve_batch(
                project.id,
                [
                    {"material_id": material["id"], "quantity": "5"},
                    {"material_id": second.id, "quantity": "2"},
                ],
                "batch-reserve-fail",
                "批量预留",
            )

        db.expire_all()
        assert db.get(Material, material["id"]).reserved_quantity == 0
        assert db.get(Material, second.id).reserved_quantity == 0
        assert db.scalars(
            select(ProjectReservation).where(ProjectReservation.project_id == project.id)
        ).all() == []
        assert db.scalars(
            select(StockMovement).where(
                StockMovement.project_id == project.id,
                StockMovement.operation_type == "reserve",
            )
        ).all() == []
