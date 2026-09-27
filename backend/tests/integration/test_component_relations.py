from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, settings
from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models import (
    AuditLog,
    Material,
    Product,
    ProductBomItem,
    ProductRevision,
    Role,
    StockMovement,
    User,
)
from app.portfolio_demo.relation_seeder import PortfolioRelationSeeder
from app.schemas.relations import ComponentRelationCreate, ProductBomAlternateCreate
from app.services.build_readiness import BuildReadinessService
from app.services.component_relations import ComponentRelationReviewService
from evals.seed_golden_db import seed_golden_database


@pytest.fixture(autouse=True)
def legacy_relation_fixtures_without_traceable_documents(monkeypatch):
    """Lifecycle tests predate evidence fixtures; production remains fail-closed."""

    monkeypatch.setattr(settings, "traceable_engineering_evidence_required", False)


def _scenario(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'relations-{suffix}.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    admin_role = Role(name=f"关系管理员-{suffix}", permissions=["*"])
    viewer_role = Role(name=f"关系只读-{suffix}", permissions=["material:view"])
    db.add_all([admin_role, viewer_role])
    db.flush()
    admin = User(
        username=f"relations-admin-{suffix}",
        full_name="Relations Admin",
        password_hash="not-a-login-secret",
        role_id=admin_role.id,
        must_change_password=False,
    )
    viewer = User(
        username=f"relations-viewer-{suffix}",
        full_name="Relations Viewer",
        password_hash="not-a-login-secret",
        role_id=viewer_role.id,
        must_change_password=False,
    )
    primary = Material(
        code=f"PRIMARY-{suffix}",
        name="Primary",
        mpn="TCAN1044",
        quantity=Decimal("0"),
        reserved_quantity=Decimal("0"),
        safety_stock=Decimal("0"),
    )
    alternate = Material(
        code=f"ALTERNATE-{suffix}",
        name="Alternate",
        mpn="MCP2562FD",
        quantity=Decimal("100"),
        reserved_quantity=Decimal("0"),
        safety_stock=Decimal("0"),
    )
    third = Material(
        code=f"THIRD-{suffix}",
        name="Third",
        quantity=Decimal("100"),
        reserved_quantity=Decimal("0"),
        safety_stock=Decimal("0"),
    )
    db.add_all([admin, viewer, primary, alternate, third])
    db.flush()
    product = Product(code=f"PROD-{suffix}", name="Scoped Product")
    other_product = Product(code=f"OTHER-{suffix}", name="Other Product")
    db.add_all([product, other_product])
    db.flush()
    revision = ProductRevision(
        product_id=product.id,
        revision="R1",
        status="released",
        is_default=True,
        bom_hash="a" * 64,
    )
    other_revision = ProductRevision(
        product_id=other_product.id,
        revision="R1",
        status="released",
        is_default=True,
        bom_hash="b" * 64,
    )
    db.add_all([revision, other_revision])
    db.flush()
    bom = ProductBomItem(
        product_revision_id=revision.id,
        material_id=primary.id,
        quantity_per_unit=Decimal("2"),
    )
    other_bom = ProductBomItem(
        product_revision_id=other_revision.id,
        material_id=primary.id,
        quantity_per_unit=Decimal("2"),
    )
    db.add_all([bom, other_bom])
    db.commit()
    return engine, db, admin, viewer, primary, alternate, third, revision, bom, other_bom


def _relation_payload(source_id: int, target_id: int, **overrides):
    values = {
        "source_material_id": source_id,
        "target_material_id": target_id,
        "relation_type": "similar_to",
        "evidence_summary": "Reviewed engineering similarity; no replacement claim.",
        "evidence_refs": [{"type": "engineering_note", "reference": "ENG-1"}],
    }
    values.update(overrides)
    return ComponentRelationCreate(**values)


def _alternate_payload(material_id: int, **overrides):
    values = {
        "alternate_material_id": material_id,
        "priority": 1,
        "usage_condition": "This exact revision and BOM position only.",
        "engineering_note": "Reviewed for this released product revision.",
        "evidence_refs": [{"type": "engineering_note", "reference": "ALT-1"}],
    }
    values.update(overrides)
    return ProductBomAlternateCreate(**values)


def test_relation_canonicalization_uniqueness_rbac_and_audit(tmp_path):
    engine, db, admin, viewer, primary, alternate, *_rest = _scenario(tmp_path, "relation")
    service = ComponentRelationReviewService(db, admin, "relation-create")
    relation = service.create_relation(_relation_payload(alternate.id, primary.id))
    assert relation.source_material_id == min(primary.id, alternate.id)
    assert relation.target_material_id == max(primary.id, alternate.id)
    with pytest.raises(BusinessError) as duplicate:
        service.create_relation(_relation_payload(primary.id, alternate.id))
    assert duplicate.value.code == "COMPONENT_RELATION_EXISTS"
    with pytest.raises(BusinessError) as same:
        service.create_relation(_relation_payload(primary.id, primary.id))
    assert same.value.code == "COMPONENT_RELATION_SAME_MATERIAL"

    with pytest.raises(BusinessError) as forbidden:
        ComponentRelationReviewService(db, viewer, "forbidden").validate_relation(relation.id)
    assert forbidden.value.status_code == 403
    db.refresh(relation)
    assert relation.status == "candidate"

    service.validate_relation(relation.id)
    db.refresh(relation)
    assert relation.status == "validated"
    data = service.relation_data(relation)
    assert data["global_replacement_approved"] is False
    assert data["pin_compatible_validated"] is False
    assert (
        db.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.resource_type == "component_relation")
        )
        == 2
    )
    engine.dispose()


def test_relation_validation_requires_evidence_and_active_materials(tmp_path):
    engine, db, admin, _viewer, primary, alternate, *_rest = _scenario(tmp_path, "validation")
    service = ComponentRelationReviewService(db, admin, "validation")
    relation = service.create_relation(
        _relation_payload(primary.id, alternate.id, evidence_summary="")
    )
    with pytest.raises(BusinessError) as evidence:
        service.validate_relation(relation.id)
    assert evidence.value.code == "COMPONENT_RELATION_EVIDENCE_REQUIRED"
    relation.evidence_summary = "reviewed"
    alternate.is_active = False
    db.commit()
    with pytest.raises(BusinessError) as inactive:
        service.validate_relation(relation.id)
    assert inactive.value.code == "COMPONENT_RELATION_MATERIAL_UNAVAILABLE"
    engine.dispose()


def test_alternate_approval_scope_and_primary_only_readiness(tmp_path):
    (
        engine,
        db,
        admin,
        _viewer,
        primary,
        alternate,
        _third,
        revision,
        bom,
        other_bom,
    ) = _scenario(tmp_path, "alternate")
    service = ComponentRelationReviewService(db, admin, "alternate")
    original_hash = revision.bom_hash
    with pytest.raises(BusinessError) as same:
        service.create_alternate(bom.id, _alternate_payload(primary.id))
    assert same.value.code == "PRODUCT_BOM_ALTERNATE_SAME_MATERIAL"

    approved = service.create_alternate(bom.id, _alternate_payload(alternate.id))
    with pytest.raises(BusinessError) as duplicate:
        service.create_alternate(bom.id, _alternate_payload(alternate.id))
    assert duplicate.value.code == "PRODUCT_BOM_ALTERNATE_EXISTS"
    service.approve_alternate(approved.id)
    db.refresh(approved)
    db.refresh(revision)
    assert approved.status == "approved"
    assert revision.bom_hash == original_hash
    assert service.list_alternates(other_bom.id) == []

    readiness = BuildReadinessService(db).analyze(revision.id, 1)
    row = readiness["items"][0]
    assert readiness["sufficient"] is False
    assert readiness["max_buildable_units"] == 0
    assert row["coverage"] == "0.0000"
    assert row["shortage"] == "2.0000"
    assert db.scalar(select(func.count(StockMovement.id))) == 0
    assert (
        db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.resource_type == "product_bom_alternate"
            )
        )
        == 2
    )
    engine.dispose()


def test_alternate_approval_requires_released_revision_and_evidence(tmp_path):
    (
        engine,
        db,
        admin,
        _viewer,
        _primary,
        alternate,
        _third,
        revision,
        bom,
        _other_bom,
    ) = _scenario(tmp_path, "approval")
    service = ComponentRelationReviewService(db, admin, "approval")
    candidate = service.create_alternate(
        bom.id,
        _alternate_payload(alternate.id, engineering_note="", evidence_refs=[]),
    )
    with pytest.raises(BusinessError) as evidence:
        service.approve_alternate(candidate.id)
    assert evidence.value.code == "PRODUCT_BOM_ALTERNATE_EVIDENCE_REQUIRED"
    candidate.engineering_note = "reviewed"
    candidate.evidence_refs = [{"type": "note", "reference": "ALT-2"}]
    revision.status = "draft"
    revision.is_default = False
    db.commit()
    with pytest.raises(BusinessError) as release:
        service.approve_alternate(candidate.id)
    assert release.value.code == "PRODUCT_REVISION_NOT_RELEASED"
    engine.dispose()


def test_rejected_relation_is_never_returned_as_validated(tmp_path):
    engine, db, admin, _viewer, primary, alternate, *_rest = _scenario(tmp_path, "rejected")
    service = ComponentRelationReviewService(db, admin, "reject")
    relation = service.create_relation(_relation_payload(primary.id, alternate.id))
    service.reject_relation(relation.id, "Evidence did not support the claim")
    rows = service.list_relations(primary.id, status="validated")
    assert rows == []
    rejected = service.list_relations(primary.id, status="rejected")
    assert rejected[0]["pin_compatible_validated"] is False
    db.refresh(relation)
    assert relation.reviewed_by_id == admin.id
    assert relation.reviewed_at is not None
    assert relation.validated_by_id is None
    assert relation.validated_at is None
    engine.dispose()


def test_validated_relation_can_only_be_revoked_with_audited_reason(tmp_path):
    engine, db, admin, _viewer, primary, alternate, *_rest = _scenario(
        tmp_path, "revoke-relation"
    )
    service = ComponentRelationReviewService(db, admin, "revoke-relation")
    relation = service.create_relation(_relation_payload(primary.id, alternate.id))
    service.validate_relation(relation.id)
    service.revoke_relation(relation.id, "Current engineering review withdrew the claim")
    db.refresh(relation)
    assert relation.status == "revoked"
    assert relation.validated_by_id == admin.id
    assert relation.revoked_by_id == admin.id
    assert relation.revoked_reason == "Current engineering review withdrew the claim"
    data = service.relation_data(relation)
    assert data["historically_validated"] is True
    assert data["currently_usable"] is False
    assert "工程关系已撤销" in data["unavailable_reasons"]
    with pytest.raises(BusinessError) as invalid:
        service.validate_relation(relation.id)
    assert invalid.value.code == "COMPONENT_RELATION_NOT_CANDIDATE"
    engine.dispose()


def test_rejected_alternate_uses_generic_review_metadata(tmp_path):
    (
        engine,
        db,
        admin,
        _viewer,
        _primary,
        alternate_material,
        _third,
        _revision,
        bom,
        _other_bom,
    ) = _scenario(tmp_path, "reject-alternate")
    service = ComponentRelationReviewService(db, admin, "reject-alternate")
    alternate = service.create_alternate(bom.id, _alternate_payload(alternate_material.id))
    service.reject_alternate(alternate.id, "Not suitable for this BOM position")
    db.refresh(alternate)
    assert alternate.status == "rejected"
    assert alternate.reviewed_by_id == admin.id
    assert alternate.reviewed_at is not None
    assert alternate.approved_by_id is None
    assert alternate.approved_at is None
    engine.dispose()


def test_approved_alternate_history_is_separate_from_current_usability(tmp_path):
    (
        engine,
        db,
        admin,
        _viewer,
        _primary,
        alternate_material,
        _third,
        _revision,
        bom,
        _other_bom,
    ) = _scenario(tmp_path, "alternate-usability")
    service = ComponentRelationReviewService(db, admin, "alternate-usability")
    alternate = service.create_alternate(bom.id, _alternate_payload(alternate_material.id))
    service.approve_alternate(alternate.id)
    alternate_material.is_active = False
    db.commit()
    data = service.alternate_data(alternate)
    assert data["status"] == "approved"
    assert data["historically_approved"] is True
    assert data["currently_usable"] is False
    assert "备选料已停用" in data["unavailable_reasons"]
    alternate_material.is_active = True
    db.commit()
    service.revoke_alternate(alternate.id, "Approval intentionally withdrawn")
    data = service.alternate_data(alternate)
    assert data["status"] == "revoked"
    assert data["historically_approved"] is True
    assert data["currently_usable"] is False
    assert data["revoked_reason"] == "Approval intentionally withdrawn"
    engine.dispose()


def test_alternate_approval_rejects_inactive_material(tmp_path):
    (
        engine,
        db,
        admin,
        _viewer,
        _primary,
        alternate,
        _third,
        _revision,
        bom,
        _other_bom,
    ) = _scenario(tmp_path, "inactive-alternate")
    service = ComponentRelationReviewService(db, admin, "inactive-alternate")
    candidate = service.create_alternate(bom.id, _alternate_payload(alternate.id))
    alternate.is_active = False
    db.commit()
    with pytest.raises(BusinessError) as unavailable:
        service.approve_alternate(candidate.id)
    assert unavailable.value.code == "PRODUCT_BOM_ALTERNATE_MATERIAL_UNAVAILABLE"
    engine.dispose()


def test_portfolio_relation_seed_is_explicit_idempotent_and_write_safe(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'relations-seed-eval.db'}"
    mapping = seed_golden_database(
        database_url,
        reset=True,
        fixture_refs=["PROD_ATLAS_R2", "PROD_DEXGRIP", "PROD_SCOUT"],
        fixture_profile="component-intelligence",
    )
    engine = create_engine(database_url)
    with sessionmaker(bind=engine)() as db:
        operator = db.get(User, mapping["user_id"])
        with pytest.raises(BusinessError) as disabled:
            PortfolioRelationSeeder(
                db,
                operator,
                Settings(portfolio_relation_seed_enabled=False),
            ).seed()
        assert disabled.value.code == "PORTFOLIO_RELATION_SEED_DISABLED"

        seeder = PortfolioRelationSeeder(
            db,
            operator,
            Settings(portfolio_relation_seed_enabled=True),
        )
        first = seeder.seed()
        second = seeder.seed()
        assert first["created_relations"] == 4
        assert first["validated_relations"] == 3
        assert first["created_alternates"] == 3
        assert first["approved_alternates"] == 1
        assert first["inventory_writes"] == 0
        assert first["write_sensitive_unchanged"] is True
        assert second["created_relations"] == 0
        assert second["created_alternates"] == 0
        assert second["unchanged_existing_rows"] == 7
        assert second["write_sensitive_unchanged"] is True
    engine.dispose()
