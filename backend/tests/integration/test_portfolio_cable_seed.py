from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models import InventoryLot, Location, Material, Role, StockMovement, User
from app.portfolio_demo import PortfolioCableSeeder, PortfolioDemoV2Seeder
from app.portfolio_demo.cable_locations import CableLocationReconciliationService
from app.seed.defaults import seed_defaults
from app.services.inventory import InventoryService


def _operator(db: Session) -> User:
    seed_defaults(db)
    role = db.scalar(select(Role).where(Role.name == "系统管理员"))
    user = User(
        username="phase25_cable_seed_operator",
        full_name="Phase 2.5 Cable Seed Operator",
        password_hash="not-a-login-secret",
        role_id=role.id,
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    return user


def test_portfolio_cable_seed_is_explicit_governed_and_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-seed.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _operator(db)
        PortfolioDemoV2Seeder(
            db,
            operator,
            Settings(portfolio_demo_seed_enabled=True),
        ).seed()

        disabled = PortfolioCableSeeder(
            db,
            operator,
            Settings(portfolio_cable_seed_enabled=False),
        )
        assert disabled.dry_run()["synthetic_inbound_quantity"] == 1186
        with pytest.raises(BusinessError, match="显式设置"):
            disabled.seed()

        enabled = PortfolioCableSeeder(
            db,
            operator,
            Settings(portfolio_cable_seed_enabled=True),
        )
        first = enabled.seed()
        movement_count = db.scalar(select(func.count()).select_from(StockMovement))
        second = enabled.seed()

        cable_ids = list(
            db.scalars(
                select(Material.id).where(
                    Material.attributes["portfolio_cable_dataset"].as_string()
                    == "portfolio_cables_v1"
                )
            ).all()
        )
        assert first["created_materials"] == 80
        assert first["inbound_operations"] == 80
        assert first["allocated_materials"] == 80
        assert first["inventory_quantity_added"] == 1186
        assert first["created_locations"] == 110
        assert second["created_materials"] == 0
        assert second["reused_materials"] == 80
        assert second["replayed_inbound_operations"] == 80
        assert second["replayed_allocations"] == 80
        assert db.scalar(select(func.count()).select_from(StockMovement)) == movement_count
        assert len(cable_ids) == 80
        assert Decimal(
            db.scalar(
                select(func.coalesce(func.sum(Material.quantity), 0)).where(
                    Material.id.in_(cable_ids)
                )
            )
        ) == Decimal("1186")
        assert Decimal(
            db.scalar(
                select(func.coalesce(func.sum(InventoryLot.quantity), 0)).where(
                    InventoryLot.material_id.in_(cable_ids)
                )
            )
        ) == Decimal("1186")
        assert all(
            Decimal(value) == Decimal("0")
            for value in db.scalars(
                select(Material.unit_price).where(Material.id.in_(cable_ids))
            ).all()
        )
        positive_location_codes = list(
            db.scalars(
                select(Location.code)
                .join(InventoryLot, InventoryLot.location_id == Location.id)
                .where(
                    InventoryLot.material_id.in_(cable_ids),
                    InventoryLot.quantity > 0,
                )
                .order_by(Location.code)
            ).all()
        )
        assert len(positive_location_codes) == 80
        assert len(set(positive_location_codes)) == 80
        assert all(code.startswith("CABLE-RACK-01-") for code in positive_location_codes)


def test_portfolio_cable_seed_never_claims_purchase_quantity_as_stock(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-safety.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _operator(db)
        PortfolioDemoV2Seeder(
            db,
            operator,
            Settings(portfolio_demo_seed_enabled=True),
        ).seed()
        PortfolioCableSeeder(
            db,
            operator,
            Settings(portfolio_cable_seed_enabled=True),
        ).seed()
        attributes = list(
            db.scalars(
                select(Material.attributes).where(
                    Material.attributes["portfolio_cable_dataset"].as_string()
                    == "portfolio_cables_v1"
                )
            ).all()
        )
        assert attributes
        assert all(item["stock_is_synthetic"] is True for item in attributes)
        assert all(item["purchase_quantity_is_inventory"] is False for item in attributes)
        assert all("order" not in " ".join(item).lower() for item in attributes)


def test_cable_location_reconciliation_moves_existing_lots_without_changing_stock(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-reconcile.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _operator(db)
        PortfolioDemoV2Seeder(
            db,
            operator,
            Settings(portfolio_demo_seed_enabled=True),
        ).seed()
        PortfolioCableSeeder(
            db,
            operator,
            Settings(portfolio_cable_seed_enabled=True),
        ).seed()
        materials = list(
            db.scalars(
                select(Material)
                .where(
                    Material.attributes["portfolio_cable_dataset"].as_string()
                    == "portfolio_cables_v1"
                )
                .order_by(Material.code)
            ).all()
        )
        legacy_bins = list(
            db.scalars(
                select(Location)
                .where(Location.code.like("PF-CABLE-__"))
                .order_by(Location.code)
            ).all()
        )
        assert len(legacy_bins) == 8
        for index, material in enumerate(materials):
            lot = db.scalar(
                select(InventoryLot).where(
                    InventoryLot.material_id == material.id,
                    InventoryLot.quantity > 0,
                )
            )
            legacy = legacy_bins[index % len(legacy_bins)]
            InventoryService(db, operator.id, "legacy-cable-layout").transfer(
                material.id,
                Decimal(lot.quantity),
                lot.location_id,
                legacy.id,
                f"test-legacy-cable-{material.code}",
                "Test-only legacy location setup",
            )
            material.location_id = legacy.id
            db.commit()

        service = CableLocationReconciliationService(db, operator)
        before = service.audit(verify_agent=True)
        movement_count = int(db.scalar(select(func.count()).select_from(StockMovement)) or 0)
        assert Decimal(before["summary"]["positive_quantity"]) == Decimal("1186")
        assert Decimal(before["summary"]["non_navigable_quantity"]) == Decimal("1186")
        assert before["summary"]["release_gate_passed"] is False

        result = service.reconcile()
        after = result["after"]
        assert result["transfers"] == 80
        assert Decimal(result["inventory_quantity_before"]) == Decimal("1186")
        assert Decimal(result["inventory_quantity_after"]) == Decimal("1186")
        assert result["layout"]["assigned_drawers"] == 80
        assert result["layout"]["spare_drawers"] == 20
        assert after["exact_located_skus"] == 80
        assert Decimal(after["unallocated_quantity"]) == 0
        assert Decimal(after["root_only_quantity"]) == 0
        assert Decimal(after["non_navigable_quantity"]) == 0
        assert after["agent_location_mismatch_count"] == 0
        assert after["release_gate_passed"] is True
        assert int(db.scalar(select(func.count()).select_from(StockMovement)) or 0) == (
            movement_count + 80
        )
