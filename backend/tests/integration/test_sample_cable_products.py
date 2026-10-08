from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.agent.service import WarehouseAgentService
from app.core.config import Settings
from app.core.database import Base
from app.models import Material, Product, ProductBomItem, ProductRevision, Role, User
from app.sample_data import (
    SampleCableProductSeeder,
    SampleCableSeeder,
    SampleDataV2Seeder,
    SampleProductSeeder,
)
from app.seed.defaults import seed_defaults
from app.services.build_readiness import BuildReadinessService


class NeverProvider:
    def chat(self, messages, tools=None, tool_choice="auto"):
        raise AssertionError("deterministic cable Product turns must not call an LLM")


def _operator(db: Session) -> User:
    seed_defaults(db)
    role = db.scalar(select(Role).where(Role.name == "系统管理员"))
    operator = User(
        username="phase25_product_operator",
        full_name="Phase 2.5 Product Operator",
        password_hash="not-a-login-secret",
        role_id=role.id,
        must_change_password=False,
    )
    db.add(operator)
    db.commit()
    return operator


def test_cable_products_are_anonymous_released_and_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-products.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _operator(db)
        SampleDataV2Seeder(db, operator, Settings(sample_data_seed_enabled=True)).seed()
        SampleProductSeeder(db, operator, Settings(sample_product_seed_enabled=True)).seed()
        SampleCableSeeder(db, operator, Settings(sample_cable_seed_enabled=True)).seed()

        source_before = list(
            db.execute(
                select(
                    Product.code,
                    ProductRevision.revision,
                    ProductRevision.status,
                    ProductRevision.is_default,
                    ProductRevision.bom_hash,
                )
                .join(ProductRevision, ProductRevision.product_id == Product.id)
                .where(
                    Product.code.in_(["PROD-ATLAS-AMR", "PROD-NOVA-ARM", "PROD-SCOUT-TOF"]),
                    ProductRevision.revision.in_(["EVT-R2", "DVT-R1", "EVT-R1"]),
                )
                .order_by(Product.code, ProductRevision.revision)
            ).all()
        )
        seeder = SampleCableProductSeeder(
            db, operator, Settings(sample_cable_seed_enabled=True)
        )
        first = seeder.seed()
        second = seeder.seed()

        assert first["created_products"] == 2
        assert first["created_revisions"] == 5
        assert first["released_revisions"] == 5
        assert first["source_revisions_unchanged"] is True
        assert first["inventory_unchanged"] is True
        assert second["created_products"] == 0
        assert second["created_revisions"] == 0
        assert second["unchanged_revisions"] == 5
        assert (
            list(
                db.execute(
                    select(
                        Product.code,
                        ProductRevision.revision,
                        ProductRevision.status,
                        ProductRevision.is_default,
                        ProductRevision.bom_hash,
                    )
                    .join(ProductRevision, ProductRevision.product_id == Product.id)
                    .where(
                        Product.code.in_(["PROD-ATLAS-AMR", "PROD-NOVA-ARM", "PROD-SCOUT-TOF"]),
                        ProductRevision.revision.in_(["EVT-R2", "DVT-R1", "EVT-R1"]),
                    )
                    .order_by(Product.code, ProductRevision.revision)
                ).all()
            )
            == source_before
        )
        assert db.scalar(select(func.count()).select_from(Product)) == 8

        cable_revisions = list(
            db.scalars(
                select(ProductRevision).where(ProductRevision.revision == "DVT-CABLE-R1")
            ).all()
        )
        assert len(cable_revisions) == 3
        assert all(item.status == "released" and not item.is_default for item in cable_revisions)
        for revision in cable_revisions:
            cable_rows = db.scalar(
                select(func.count())
                .select_from(ProductBomItem)
                .join(Material, Material.id == ProductBomItem.material_id)
                .where(
                    ProductBomItem.product_revision_id == revision.id,
                    Material.attributes["material_kind"].as_string() == "cable",
                )
            )
            assert int(cable_rows or 0) >= 3


def test_build_readiness_includes_cables_from_per_unit_product_bom(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-readiness.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _operator(db)
        SampleDataV2Seeder(db, operator, Settings(sample_data_seed_enabled=True)).seed()
        SampleProductSeeder(db, operator, Settings(sample_product_seed_enabled=True)).seed()
        SampleCableSeeder(db, operator, Settings(sample_cable_seed_enabled=True)).seed()
        SampleCableProductSeeder(
            db, operator, Settings(sample_cable_seed_enabled=True)
        ).seed()
        product = db.scalar(select(Product).where(Product.code == "PROD-ATLAS-AMR"))
        revision = db.scalar(
            select(ProductRevision).where(
                ProductRevision.product_id == product.id,
                ProductRevision.revision == "DVT-CABLE-R1",
            )
        )

        result = BuildReadinessService(db).analyze(revision.id, 2)
        cable_items = [item for item in result["items"] if item["code"].startswith("CBL-PF-")]
        assert len(cable_items) == 4
        assert all(
            Decimal(item["required_total"]) == Decimal(item["quantity_per_unit"]) * 2
            for item in cable_items
        )
        assert "quantity_per_unit × build_quantity" in result["quantity_semantics"]

        agent_result = WarehouseAgentService(
            db,
            operator,
            "atlas-cable-readiness",
            provider=NeverProvider(),
            config=Settings(agent_enabled=True),
            enforce_configuration=False,
        ).query("Atlas 做 3 台，包含线缆后料够吗？")
        agent_readiness = agent_result.entities["build_readiness"]
        assert agent_readiness["revision"]["revision"] == "DVT-CABLE-R1"
        assert any(item["code"].startswith("CBL-PF-") for item in agent_readiness["items"])
