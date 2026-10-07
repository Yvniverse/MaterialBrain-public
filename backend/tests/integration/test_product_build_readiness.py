import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models import (
    AgentActionProposal,
    BomItem,
    Material,
    Product,
    ProductBomItem,
    ProductRevision,
    Project,
    ProjectReservation,
    Role,
    StockMovement,
    User,
)
from app.sample_data import SampleDataV2Seeder, SampleProductSeeder
from app.seed.defaults import seed_defaults
from app.services.build_readiness import BuildReadinessService

SCENARIOS_PATH = (
    Path(__file__).resolve().parents[2]
    / "sample_data"
    / "v2_1"
    / "expected_build_readiness_scenarios.json"
)


def _operator(db: Session) -> User:
    seed_defaults(db)
    role = db.scalar(select(Role).where(Role.name == "系统管理员"))
    user = User(
        username="phase21_seed_operator",
        full_name="Phase 2.1 Seed Operator",
        password_hash="not-a-login-secret",
        role_id=role.id,
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    return user


def _write_snapshot(db: Session) -> dict:
    materials = db.execute(
        select(
            func.count(Material.id),
            func.coalesce(func.sum(Material.quantity), 0),
            func.coalesce(func.sum(Material.reserved_quantity), 0),
        )
    ).one()
    return {
        "materials": tuple(Decimal(value) for value in materials),
        "reservations": int(
            db.scalar(select(func.count()).select_from(ProjectReservation)) or 0
        ),
        "movements": int(db.scalar(select(func.count()).select_from(StockMovement)) or 0),
        "proposals": int(
            db.scalar(select(func.count()).select_from(AgentActionProposal)) or 0
        ),
    }


def test_sample_product_seed_and_expected_readiness_scenarios(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'product-seed.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _operator(db)
        SampleDataV2Seeder(
            db,
            operator,
            Settings(sample_data_seed_enabled=True),
        ).seed()
        project_bom_before = list(
            db.execute(
                select(
                    BomItem.project_id,
                    BomItem.version,
                    BomItem.material_id,
                    BomItem.required_quantity,
                ).order_by(BomItem.id)
            ).all()
        )
        sensitive_before = _write_snapshot(db)

        disabled = SampleProductSeeder(
            db,
            operator,
            Settings(sample_product_seed_enabled=False),
        )
        with pytest.raises(BusinessError, match="显式设置"):
            disabled.seed()

        enabled = SampleProductSeeder(
            db,
            operator,
            Settings(sample_product_seed_enabled=True),
        )
        first = enabled.seed()
        second = enabled.seed()

        assert first["created_products"] == 6
        assert first["created_revisions"] == 7
        assert first["created_bom_items"] == 64
        assert first["linked_projects"] == 6
        assert first["created_materials"] == 0
        assert first["inventory_changes"] == 0
        assert first["write_sensitive_unchanged"] is True
        assert second["created_products"] == 0
        assert second["created_revisions"] == 0
        assert second["created_bom_items"] == 0
        assert second["linked_projects"] == 0
        assert second["write_sensitive_unchanged"] is True
        assert db.scalar(select(func.count()).select_from(Product)) == 6
        assert db.scalar(select(func.count()).select_from(ProductRevision)) == 7
        assert db.scalar(select(func.count()).select_from(ProductBomItem)) == 64
        assert (
            db.scalar(
                select(func.count())
                .select_from(Project)
                .where(Project.product_revision_id.is_not(None))
            )
            == 6
        )
        assert _write_snapshot(db) == sensitive_before
        assert list(
            db.execute(
                select(
                    BomItem.project_id,
                    BomItem.version,
                    BomItem.material_id,
                    BomItem.required_quantity,
                ).order_by(BomItem.id)
            ).all()
        ) == project_bom_before

        expected = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        product_by_code = {row.code: row for row in db.scalars(select(Product)).all()}
        service = BuildReadinessService(db)
        for scenario in expected:
            product = product_by_code[scenario["product_code"]]
            revision = db.scalar(
                select(ProductRevision).where(
                    ProductRevision.product_id == product.id,
                    ProductRevision.revision == scenario["revision"],
                )
            )
            before = _write_snapshot(db)
            result = service.analyze(revision.id, scenario["build_quantity"])
            assert _write_snapshot(db) == before
            assert result["sufficient"] is scenario["sufficient"]
            assert result["shortage_count"] == scenario["shortage_count"]
            assert result["max_buildable_units"] == scenario["max_buildable_units"]
            assert result["safety_risk_count"] == scenario["safety_risk_count"]
            shortages = {
                item["code"]: item
                for item in result["items"]
                if Decimal(item["shortage"]) > 0
            }
            assert set(shortages) == {
                item["material_code"] for item in scenario["shortages"]
            }
            for expected_shortage in scenario["shortages"]:
                actual = shortages[expected_shortage["material_code"]]
                for key in (
                    "quantity_per_unit",
                    "required_total",
                    "available_quantity",
                    "shortage",
                    "remaining_after_build",
                    "safety_stock",
                ):
                    assert Decimal(actual[key]) == Decimal(str(expected_shortage[key]))
                assert (
                    actual["below_safety_after_build"]
                    is expected_shortage["below_safety_after_build"]
                )
    engine.dispose()


def test_build_readiness_uses_only_explicit_matching_project_reservation(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'reservation-scope.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _operator(db)
        material = Material(
            code="READINESS-MAT",
            name="Readiness Material",
            quantity=Decimal("10"),
            reserved_quantity=Decimal("4"),
            safety_stock=Decimal("2"),
        )
        product = Product(code="READINESS-PROD", name="Readiness Product")
        db.add_all([material, product])
        db.flush()
        revision = ProductRevision(
            product_id=product.id,
            revision="R1",
            status="released",
            is_default=True,
        )
        db.add(revision)
        db.flush()
        db.add(
            ProductBomItem(
                product_revision_id=revision.id,
                material_id=material.id,
                quantity_per_unit=Decimal("2"),
            )
        )
        matching = Project(
            code="READINESS-MATCH",
            name="Matching Project",
            manager_id=operator.id,
            product_revision_id=revision.id,
        )
        unlinked = Project(
            code="READINESS-UNLINKED",
            name="Unlinked Project",
            manager_id=operator.id,
        )
        db.add_all([matching, unlinked])
        db.flush()
        db.add_all(
            [
                ProjectReservation(
                    project_id=matching.id,
                    material_id=material.id,
                    quantity=Decimal("1"),
                ),
                ProjectReservation(
                    project_id=unlinked.id,
                    material_id=material.id,
                    quantity=Decimal("3"),
                ),
            ]
        )
        db.commit()

        service = BuildReadinessService(db)
        without_project = service.analyze(revision.id, 4)
        with_project = service.analyze(revision.id, 4, matching.id)
        assert Decimal(without_project["items"][0]["coverage"]) == 6
        assert Decimal(without_project["items"][0]["shortage"]) == 2
        assert Decimal(with_project["items"][0]["coverage"]) == 7
        assert Decimal(with_project["items"][0]["shortage"]) == 1
        with pytest.raises(BusinessError, match="没有明确关联"):
            service.analyze(revision.id, 4, unlinked.id)
        with pytest.raises(BusinessError, match="大于 0"):
            service.analyze(revision.id, 0)
    engine.dispose()
