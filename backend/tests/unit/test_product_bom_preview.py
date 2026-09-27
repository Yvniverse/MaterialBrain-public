from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.agent.service import WarehouseAgentService
from app.core.config import Settings
from app.core.database import Base, SessionLocal
from app.models import Material, Product, ProductBomItem, ProductRevision, Role, User
from app.portfolio_demo import PortfolioDemoV2Seeder, PortfolioProductSeeder
from app.seed.defaults import seed_defaults
from app.services.product_bom_preview import ProductBomPreviewService


def _row(material: Material, quantity: str = "1") -> dict:
    return {
        "requirement_id": f"req-{material.code}",
        "role": "bootstrap capacitor",
        "required_quantity": quantity,
        "selection_status": "selected",
        "selection_basis": "explicit_user",
        "selected_material_id": material.id,
        "candidates": [{"material_id": material.id, "match_status": "exact", "mpn": material.mpn}],
        "source_anchor": {"anchor_id": 12, "page": 3},
    }


def _setup() -> tuple[object, User, Product, ProductRevision, list[Material]]:
    db = SessionLocal()
    Base.metadata.create_all(bind=db.get_bind())
    role = db.scalar(select(Role).order_by(Role.id))
    if role is None:
        role = Role(name=f"preview-role-{uuid4().hex[:8]}", permissions=[])
        db.add(role)
        db.flush()
    user = User(
        username=f"preview-{uuid4().hex[:12]}",
        full_name="Preview Test",
        password_hash="not-used",
        role_id=role.id,
        must_change_password=False,
    )
    materials = [
        Material(
            code=f"PREVIEW-MAT-{uuid4().hex[:8]}-{index}",
            name=f"Preview material {index}",
            mpn=f"PREVIEW-MPN-{index}",
            unit="pcs",
        )
        for index in range(1, 4)
    ]
    product = Product(
        code=f"PROD-PREVIEW-{uuid4().hex[:8]}",
        name="Preview Product",
        lifecycle_status="active",
    )
    db.add_all([user, *materials, product])
    db.flush()
    revision = ProductRevision(
        product_id=product.id,
        revision="DRAFT-1",
        status="draft",
        is_default=True,
    )
    db.add(revision)
    db.commit()
    return db, user, product, revision, materials


def test_product_bom_preview_emits_add_no_change_and_update_without_writes():
    db, _user, product, revision, materials = _setup()
    try:
        db.add(
            ProductBomItem(
                product_revision_id=revision.id,
                material_id=materials[0].id,
                quantity_per_unit=Decimal("1"),
            )
        )
        db.add(
            ProductBomItem(
                product_revision_id=revision.id,
                material_id=materials[1].id,
                quantity_per_unit=Decimal("2"),
            )
        )
        db.commit()
        before = db.scalar(
            select(func.count())
            .select_from(ProductBomItem)
            .where(ProductBomItem.product_revision_id == revision.id)
        )
        result = ProductBomPreviewService(db).build(
            product_id=product.id,
            revision_id=revision.id,
            engineering_context={
                "engineering_bom_draft": {
                    "status": "complete_draft",
                    "rows": [
                        _row(materials[0], "1"),
                        _row(materials[1], "3"),
                        _row(materials[2], "1"),
                    ],
                }
            },
        )
        assert [line["action"] for line in result["lines"]] == [
            "no_change",
            "update_quantity",
            "add",
        ]
        assert {
            key: result["summary"][key]
            for key in (
                "add_count",
                "update_quantity_count",
                "no_change_count",
                "unresolved_count",
                "complete_for_apply_preview",
                "blocking_reasons",
            )
        } == {
            "add_count": 1,
            "update_quantity_count": 1,
            "no_change_count": 1,
            "unresolved_count": 0,
            "complete_for_apply_preview": True,
            "blocking_reasons": [],
        }
        assert result["summary"]["ready_for_confirmation_preview"] is True
        assert result["summary"]["selected_line_count"] == 3
        assert result["summary"]["valid_selected_line_count"] == 3
        dry_run = result["apply_readiness_dry_run"]
        assert dry_run["dry_run"] is True
        assert dry_run["apply_allowed"] is False
        assert dry_run["requires_explicit_user_confirmation"] is True
        assert [item["action"] for item in dry_run["planned_mutations"]] == [
            "UPDATE_QUANTITY",
            "ADD",
        ]
        assert dry_run["preconditions"] == []
        assert dry_run["formal_product_bom_modified"] is False
        assert result["read_only"] is True
        assert result["automatic_write"] is False
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProductBomItem)
                .where(ProductBomItem.product_revision_id == revision.id)
            )
            == before
        )
    finally:
        db.close()


def test_product_bom_preview_keeps_missing_selection_and_invalid_candidate_unresolved():
    db, _user, product, revision, materials = _setup()
    try:
        result = ProductBomPreviewService(db).build(
            product_id=product.id,
            revision_id=revision.id,
            engineering_context={
                "engineering_bom_draft": {
                    "rows": [
                        {
                            "requirement_id": "missing-selection",
                            "role": "input capacitor",
                            "selection_status": "needs_selection",
                            "required_quantity": "1",
                        },
                        {
                            **_row(materials[0]),
                            "selection_conflict": "candidate changed",
                        },
                        {
                            **_row(materials[1]),
                            "required_quantity": None,
                        },
                    ]
                }
            },
        )
        assert result["summary"]["unresolved_count"] == 3
        assert all(line["action"] == "unresolved" for line in result["lines"])
        assert result["summary"]["complete_for_apply_preview"] is False
        assert any(
            "不能自动映射为 ADD" in reason for reason in result["summary"]["blocking_reasons"]
        )
        assert any(
            "candidate changed" in reason for reason in result["summary"]["blocking_reasons"]
        )
        assert result["lines"][0]["reason_code"] == "NO_EXPLICIT_SELECTION"
        assert result["lines"][1]["reason_code"] == "SELECTION_CONFLICT"
        assert result["lines"][2]["reason_code"] == "MISSING_UNIT_QUANTITY"
    finally:
        db.close()


def test_product_bom_preview_allows_released_revision_but_does_not_modify_it():
    db, _user, product, revision, materials = _setup()
    try:
        revision.status = "released"
        db.add(
            ProductBomItem(
                product_revision_id=revision.id,
                material_id=materials[0].id,
                quantity_per_unit=Decimal("1"),
            )
        )
        db.commit()
        before = db.scalar(select(func.count()).select_from(ProductBomItem))
        result = ProductBomPreviewService(db).build(
            product_id=product.id,
            revision_id=revision.id,
            engineering_context={"peripheral_requirements": [_row(materials[0], "2")]},
        )
        assert result["target_revision"]["status"] == "released"
        assert result["lines"][0]["action"] == "update_quantity"
        assert db.scalar(select(func.count()).select_from(ProductBomItem)) == before
    finally:
        db.close()


def test_product_bom_preview_revalidates_live_material_and_reports_reason_codes():
    db, _user, product, revision, materials = _setup()
    try:
        stale = _row(materials[0])
        stale["constraints"] = [
            {
                "key": "dielectric",
                "operator": "eq",
                "value": "X7R",
                "hard": True,
            }
        ]
        inactive = _row(materials[1])
        materials[1].is_active = False
        incomplete = _row(materials[2])
        incomplete["selection_status"] = "needs_selection"
        result = ProductBomPreviewService(db).build(
            product_id=product.id,
            revision_id=revision.id,
            engineering_context={
                "engineering_bom_draft": {
                    "status": "needs_selection",
                    "rows": [stale, inactive, incomplete],
                }
            },
        )
        assert [line["reason_code"] for line in result["lines"]] == [
            "SELECTED_CANDIDATE_NO_LONGER_MATCHES",
            "SELECTED_MATERIAL_INACTIVE",
            "NO_EXPLICIT_SELECTION",
        ]
        assert result["summary"]["ready_for_confirmation_preview"] is False
        assert result["summary"]["unresolved_count"] == 3
        assert result["read_only"] is True
        assert result["formal_product_bom_modified"] is False
    finally:
        db.close()


def test_product_bom_preview_query_reuses_research_context_and_stays_read_only(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'preview-query.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        seed_defaults(db)
        role = db.scalar(select(Role).where(Role.name == "系统管理员"))
        user = User(
            username=f"preview-query-{uuid4().hex[:8]}",
            full_name="Preview Query",
            password_hash="not-used",
            role_id=role.id,
            must_change_password=False,
        )
        db.add(user)
        db.commit()
        PortfolioDemoV2Seeder(db, user, Settings(portfolio_demo_seed_enabled=True)).seed()
        PortfolioProductSeeder(db, user, Settings(portfolio_product_seed_enabled=True)).seed()
        assert db.scalar(select(Product).where(Product.code == "PROD-ATLAS-AMR")) is not None
        before = db.scalar(select(func.count()).select_from(ProductBomItem))
        service = WarehouseAgentService(
            db,
            user,
            "preview-query-research",
            config=Settings(
                agent_enabled=True,
                agent_model_policy="deterministic",
                agent_engineering_research_enabled=True,
            ),
            enforce_configuration=False,
        )
        first = service.query(
            "请做 12V 转 3.3V、100mA 的工程研究，比较 Buck 和 LDO，"
            "检查库存、库位、外围和数据手册依据。"
        )
        preview = service.query(
            "把当前工程草案预览到产品 PROD-ATLAS-AMR 的 EVT-R2 ProductRevision，"
            "只做 Product BOM Preview。",
            conversation_id=first.conversation_id,
        )
        assert preview.intent == "product_bom_preview"
        entity = preview.entities["product_bom_preview"]
        assert entity["target_product"]["code"] == "PROD-ATLAS-AMR"
        assert entity["target_revision"]["revision"] == "EVT-R2"
        assert entity["read_only"] is True
        assert entity["automatic_write"] is False
        assert db.scalar(select(func.count()).select_from(ProductBomItem)) == before
