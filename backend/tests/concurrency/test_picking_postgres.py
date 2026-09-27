import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models import (
    BuildPlan,
    BuildPlanItem,
    InventoryLot,
    Location,
    Material,
    PickAllocation,
    Product,
    ProductRevision,
    Project,
    ProjectReservation,
    Role,
    StockMovement,
    User,
)
from app.schemas.picking import PickAllocationConfirmRequest, PickTaskCreateRequest
from app.services.picking import PickingService


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="requires isolated PostgreSQL")
def test_competing_plans_and_duplicate_confirmation_are_serialized():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    with sessions() as db:
        role = Role(name=f"picker-{suffix}", permissions=["picking:view", "picking:operate"])
        db.add(role)
        db.flush()
        user = User(
            username=f"picker-{suffix}", full_name="picker", password_hash="unused", role_id=role.id
        )
        material = Material(code=f"PK-{suffix}", name="test", quantity=10, reserved_quantity=10)
        location = Location(code=f"PL-{suffix}", name="test", type="bin", full_path="Test / A01")
        product = Product(code=f"PP-{suffix}", name="test")
        db.add_all([user, material, location, product])
        db.flush()
        revision = ProductRevision(
            product_id=product.id, revision="R1", status="released", bom_hash="a" * 64
        )
        db.add(revision)
        db.flush()
        lot = InventoryLot(material_id=material.id, location_id=location.id, quantity=5)
        db.add(lot)
        plan_ids = []
        for index in range(2):
            project = Project(
                code=f"PR-{suffix}-{index}",
                name="test",
                manager_id=user.id,
                product_revision_id=revision.id,
            )
            db.add(project)
            db.flush()
            db.add(ProjectReservation(project_id=project.id, material_id=material.id, quantity=5))
            plan = BuildPlan(
                plan_no=f"BP-{suffix}-{index}",
                product_revision_id=revision.id,
                project_id=project.id,
                build_quantity=1,
                product_bom_hash="a" * 64,
                snapshot_hash="b" * 64,
                status="reserved",
                created_by_id=user.id,
                client_operation_id=f"create-{suffix}-{index}",
            )
            db.add(plan)
            db.flush()
            db.add(
                BuildPlanItem(
                    build_plan_id=plan.id,
                    material_id=material.id,
                    quantity_per_unit=5,
                    required_total=5,
                    available_quantity_at_plan=5,
                    reserved_for_project_at_plan=5,
                    additional_reservation_required=0,
                    projected_free_available_after_build=0,
                    safety_stock_at_plan=0,
                    below_safety_after_build=False,
                )
            )
            plan_ids.append(plan.id)
        db.commit()
        user_id, lot_id = user.id, lot.id
        material_code, location_code = material.code, location.code
    barrier = Barrier(2)

    def create(plan_id):
        with sessions() as db:
            barrier.wait()
            try:
                return PickingService(db, user_id, "race").create(
                    PickTaskCreateRequest(
                        build_plan_id=plan_id,
                        build_plan_snapshot_hash="b" * 64,
                        client_operation_id=f"pick-{suffix}-{plan_id}",
                    )
                )
            except BusinessError as error:
                db.rollback()
                return {"error": error.code}

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(create, plan_ids))
    assert sum("error" not in r for r in results) == 1
    task = next(r for r in results if "error" not in r)
    allocation = task["allocations"][0]
    with sessions() as db:
        assert db.scalar(
            select(func.sum(PickAllocation.planned_quantity)).where(
                PickAllocation.inventory_lot_id == lot_id
            )
        ) == Decimal("5")
    barrier = Barrier(2)

    def confirm(_):
        with sessions() as db:
            barrier.wait()
            return PickingService(db, user_id, "confirm-race").confirm(
                allocation["id"],
                PickAllocationConfirmRequest(
                    quantity=5,
                    idempotency_key=f"same-{suffix}",
                    confirmation_method="barcode",
                    location_token=location_code,
                    material_token=material_code,
                ),
            )

    with ThreadPoolExecutor(2) as pool:
        confirmations = list(pool.map(confirm, range(2)))
    assert sum(r.get("idempotent_replay", False) for r in confirmations) == 1
    with sessions() as db:
        assert db.get(InventoryLot, lot_id).quantity == 0
        assert (
            db.scalar(
                select(func.count(StockMovement.id)).where(
                    StockMovement.material_id == material.id,
                    StockMovement.operation_type == "pick_outbound",
                )
            )
            == 1
        )
    engine.dispose()
