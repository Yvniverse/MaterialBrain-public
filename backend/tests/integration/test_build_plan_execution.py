from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agent.proposals import ProposalService
from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models import (
    AgentActionProposal,
    BuildPlan,
    Material,
    Product,
    ProductBomItem,
    ProductRevision,
    Project,
    ProjectReservation,
    Role,
    User,
)
from app.schemas.agent import (
    ProposeBuildMaterialReservationArgs,
    ProposeInventoryReservationArgs,
)
from app.seed.defaults import seed_defaults
from app.services.build_plans import BuildPlanService
from app.services.inventory import InventoryService
from app.services.product_revisions import clone_revision, release_revision


def _scenario(tmp_path, name: str, *, project_reserved: Decimal = Decimal("6")):
    engine = create_engine(f"sqlite:///{(tmp_path / f'{name}.db').as_posix()}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    seed_defaults(db)
    role = db.scalar(select(Role).where(Role.name == "系统管理员"))
    user = User(
        username=f"operator-{name}",
        full_name="Build Plan Operator",
        password_hash="not-a-login-secret",
        role_id=role.id,
        must_change_password=False,
    )
    material = Material(
        code=f"BP-MAT-{name}",
        name="Build Plan Material",
        unit="pcs",
        quantity=Decimal("20"),
        reserved_quantity=project_reserved,
        safety_stock=Decimal("5"),
    )
    product = Product(code=f"BP-PROD-{name}", name="Build Plan Product")
    db.add_all([user, material, product])
    db.flush()
    revision = ProductRevision(
        product_id=product.id,
        revision="R1",
        status="draft",
        is_default=False,
    )
    db.add(revision)
    db.flush()
    db.add(
        ProductBomItem(
            product_revision_id=revision.id,
            material_id=material.id,
            quantity_per_unit=Decimal("3"),
        )
    )
    db.flush()
    release_revision(
        db,
        revision.id,
        released_by_id=user.id,
        make_default=True,
    )
    project = Project(
        code=f"BP-PRJ-{name}",
        name="Build Plan Project",
        manager_id=user.id,
        product_revision_id=revision.id,
    )
    db.add(project)
    db.flush()
    if project_reserved:
        db.add(
            ProjectReservation(
                project_id=project.id,
                material_id=material.id,
                quantity=project_reserved,
            )
        )
    db.commit()
    return engine, db, user, material, revision, project


def test_build_plan_proposal_subtracts_existing_reservation_and_writes_only_on_approval(
    tmp_path,
):
    engine, db, user, material, revision, project = _scenario(tmp_path, "approve")
    before = (material.quantity, material.reserved_quantity)
    outcome = ProposalService(
        db,
        user,
        "request-create",
        client_operation_id="build-approve-001",
    ).create_build_plan_reservation(
        ProposeBuildMaterialReservationArgs(
            product_revision_id=revision.id,
            project_id=project.id,
            build_quantity=3,
        )
    )

    plan = outcome["plan"]
    proposal = outcome["proposal"]
    assert outcome["fully_reserved"] is False
    assert plan.status == "reservation_pending"
    assert proposal.action_type == "reserve_inventory"
    assert proposal.payload["source"] == "build_plan"
    assert proposal.payload["items"][0]["material_id"] == material.id
    assert Decimal(proposal.payload["items"][0]["quantity"]) == Decimal("3")
    assert (material.quantity, material.reserved_quantity) == before

    approved = ProposalService(db, user, "request-approve").approve(proposal.id)
    db.refresh(material)
    db.refresh(plan)
    reservation = db.scalar(
        select(ProjectReservation).where(
            ProjectReservation.project_id == project.id,
            ProjectReservation.material_id == material.id,
        )
    )
    assert approved.status == "executed"
    assert plan.status == "reserved"
    assert material.quantity == before[0]
    assert material.reserved_quantity == before[1] + Decimal("3")
    assert reservation.quantity == Decimal("9")
    with pytest.raises(BusinessError) as duplicate:
        ProposalService(db, user, "request-duplicate").approve(proposal.id)
    assert duplicate.value.code == "PROPOSAL_NOT_PENDING"
    db.close()
    engine.dispose()


def test_build_plan_idempotency_and_no_empty_proposal(tmp_path):
    engine, db, user, _material, revision, project = _scenario(
        tmp_path,
        "idempotent",
        project_reserved=Decimal("9"),
    )
    service = BuildPlanService(db, user.id, "request-plan")
    first = service.create(
        product_revision_id=revision.id,
        project_id=project.id,
        build_quantity=3,
        client_operation_id="same-plan-operation",
    )
    second = service.create(
        product_revision_id=revision.id,
        project_id=project.id,
        build_quantity=3,
        client_operation_id="same-plan-operation",
    )
    assert second.id == first.id
    with pytest.raises(BusinessError) as conflict:
        service.create(
            product_revision_id=revision.id,
            project_id=project.id,
            build_quantity=2,
            client_operation_id="same-plan-operation",
        )
    assert conflict.value.code == "BUILD_PLAN_IDEMPOTENCY_CONFLICT"

    outcome = ProposalService(
        db,
        user,
        "request-covered",
        client_operation_id="covered-operation",
    ).create_reservation_for_plan(first.id, "already covered")
    assert outcome["fully_reserved"] is True
    assert outcome["proposal"] is None
    assert outcome["plan"].status == "reserved"
    assert db.scalar(select(AgentActionProposal.id)) is None
    db.close()
    engine.dispose()


def test_changed_project_reservation_marks_plan_stale_without_silent_quantity_change(
    tmp_path,
):
    engine, db, user, material, revision, project = _scenario(tmp_path, "stale")
    outcome = ProposalService(
        db,
        user,
        "request-create",
        client_operation_id="build-stale-001",
    ).create_build_plan_reservation(
        ProposeBuildMaterialReservationArgs(
            product_revision_id=revision.id,
            project_id=project.id,
            build_quantity=3,
        )
    )
    proposal = outcome["proposal"]
    fixed_payload = proposal.payload.copy()
    InventoryService(db, user.id, "competing-request").reserve(
        material.id,
        project.id,
        Decimal("1"),
        "competing-project-reservation",
        "test baseline change",
    )
    before_approval = material.reserved_quantity

    with pytest.raises(BusinessError) as stale:
        ProposalService(db, user, "request-approve").approve(proposal.id)
    assert stale.value.code == "BUILD_PLAN_STALE"
    db.refresh(material)
    plan = db.get(BuildPlan, outcome["plan"].id)
    failed = db.get(AgentActionProposal, proposal.id)
    assert material.reserved_quantity == before_approval
    assert failed.payload == fixed_payload
    assert failed.status == "failed"
    assert plan.status == "stale"
    db.close()
    engine.dispose()


def test_unavailable_product_bom_material_is_visible_and_blocks_plan(tmp_path):
    engine, db, user, material, revision, project = _scenario(tmp_path, "inactive")
    material.is_active = False
    db.commit()
    from app.services.build_readiness import BuildReadinessService

    readiness = BuildReadinessService(db).analyze(revision.id, 1, project.id)
    assert readiness["sufficient"] is False
    assert readiness["material_blocker_count"] == 1
    assert readiness["items"][0]["material_blocker"] == "inactive"
    assert readiness["items"][0]["coverage"] == "0"
    with pytest.raises(BusinessError) as blocked:
        BuildPlanService(db, user.id, "blocked-plan").create(
            product_revision_id=revision.id,
            project_id=project.id,
            build_quantity=1,
            client_operation_id="inactive-plan-operation",
        )
    assert blocked.value.code == "BUILD_PLAN_HAS_UNAVAILABLE_MATERIAL"
    db.close()
    engine.dispose()


def test_projected_free_stock_does_not_spend_excess_project_reservation(tmp_path):
    engine, db, _user, material, revision, project = _scenario(
        tmp_path,
        "projection",
        project_reserved=Decimal("10"),
    )
    from app.services.build_readiness import BuildReadinessService

    result = BuildReadinessService(db).analyze(revision.id, 1, project.id)
    item = result["items"][0]
    assert Decimal(item["required_total"]) == Decimal("3")
    assert Decimal(item["reserved_for_project"]) == Decimal("10")
    assert Decimal(item["additional_reservation_required"]) == Decimal("0")
    assert Decimal(item["projected_free_available_after_build"]) == Decimal("10")
    db.close()
    engine.dispose()


def test_rejecting_build_proposal_cancels_plan(tmp_path):
    engine, db, user, _material, revision, project = _scenario(tmp_path, "reject")
    outcome = ProposalService(
        db, user, "request-create", client_operation_id="reject-plan"
    ).create_build_plan_reservation(
        ProposeBuildMaterialReservationArgs(
            product_revision_id=revision.id,
            project_id=project.id,
            build_quantity=3,
        )
    )
    ProposalService(db, user, "request-reject").reject(
        outcome["proposal"].id, "工程计划取消"
    )
    db.refresh(outcome["plan"])
    assert outcome["plan"].status == "cancelled"
    assert outcome["plan"].stale_reason == "工程计划取消"
    db.close()
    engine.dispose()


def test_expired_build_proposal_marks_plan_stale(tmp_path):
    engine, db, user, _material, revision, project = _scenario(tmp_path, "expire")
    outcome = ProposalService(
        db, user, "request-create", client_operation_id="expire-plan"
    ).create_build_plan_reservation(
        ProposeBuildMaterialReservationArgs(
            product_revision_id=revision.id,
            project_id=project.id,
            build_quantity=3,
        )
    )
    proposal = outcome["proposal"]
    proposal.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(BusinessError) as expired:
        ProposalService(db, user, "request-expired").approve(proposal.id)
    assert expired.value.code == "PROPOSAL_EXPIRED"
    db.refresh(proposal)
    db.refresh(outcome["plan"])
    assert proposal.status == "expired"
    assert outcome["plan"].status == "stale"
    db.close()
    engine.dispose()


def test_unauthorized_approval_does_not_mutate_proposal_or_plan(tmp_path):
    engine, db, user, _material, revision, project = _scenario(tmp_path, "forbidden")
    outcome = ProposalService(
        db, user, "request-create", client_operation_id="forbidden-plan"
    ).create_build_plan_reservation(
        ProposeBuildMaterialReservationArgs(
            product_revision_id=revision.id,
            project_id=project.id,
            build_quantity=3,
        )
    )
    read_role = Role(name="只读审批测试", permissions=["material:view"])
    db.add(read_role)
    db.flush()
    viewer = User(
        username="forbidden-viewer",
        full_name="Forbidden Viewer",
        password_hash="not-a-login-secret",
        role_id=read_role.id,
        must_change_password=False,
    )
    db.add(viewer)
    db.commit()
    with pytest.raises(BusinessError) as forbidden:
        ProposalService(db, viewer, "request-forbidden").approve(
            outcome["proposal"].id
        )
    assert forbidden.value.status_code == 403
    db.refresh(outcome["proposal"])
    db.refresh(outcome["plan"])
    assert outcome["proposal"].status == "pending"
    assert outcome["plan"].status == "reservation_pending"
    db.close()
    engine.dispose()


def test_internal_execution_failure_marks_proposal_and_plan_stale(
    tmp_path, monkeypatch
):
    engine, db, user, material, revision, project = _scenario(tmp_path, "internal")
    outcome = ProposalService(
        db, user, "request-create", client_operation_id="internal-plan"
    ).create_build_plan_reservation(
        ProposeBuildMaterialReservationArgs(
            product_revision_id=revision.id,
            project_id=project.id,
            build_quantity=3,
        )
    )
    before = (material.quantity, material.reserved_quantity)

    def fail_execute(*_args, **_kwargs):
        raise RuntimeError("synthetic execution failure")

    monkeypatch.setattr(InventoryService, "reserve_batch", fail_execute)
    with pytest.raises(RuntimeError, match="synthetic execution failure"):
        ProposalService(db, user, "request-failed").approve(outcome["proposal"].id)
    db.refresh(material)
    db.refresh(outcome["proposal"])
    db.refresh(outcome["plan"])
    assert (material.quantity, material.reserved_quantity) == before
    assert outcome["proposal"].status == "failed"
    assert outcome["plan"].status == "stale"
    db.close()
    engine.dispose()


def test_dead_terminal_proposal_is_never_returned_as_pending(tmp_path):
    engine, db, user, _material, revision, project = _scenario(tmp_path, "dead")
    outcome = ProposalService(
        db, user, "request-create", client_operation_id="dead-plan"
    ).create_build_plan_reservation(
        ProposeBuildMaterialReservationArgs(
            product_revision_id=revision.id,
            project_id=project.id,
            build_quantity=3,
        )
    )
    proposal = outcome["proposal"]
    proposal.status = "failed"
    proposal.error_message = "legacy terminal state"
    db.commit()
    with pytest.raises(BusinessError) as terminal:
        ProposalService(db, user, "request-reuse").create_reservation_for_plan(
            outcome["plan"].id, "must not reuse"
        )
    assert terminal.value.code == "BUILD_PLAN_PROPOSAL_TERMINAL"
    db.refresh(outcome["plan"])
    assert outcome["plan"].status == "stale"
    db.close()
    engine.dispose()


def test_inactive_material_rejects_new_manual_reservations(tmp_path):
    engine, db, user, material, _revision, project = _scenario(
        tmp_path, "manual-inactive", project_reserved=Decimal("0")
    )
    material.is_active = False
    db.commit()
    args = ProposeInventoryReservationArgs(
        project_id=project.id,
        items=[{"material_id": material.id, "quantity": "1"}],
        reason="inactive must reject",
    )
    with pytest.raises(BusinessError) as proposed:
        ProposalService(db, user, "inactive-proposal").create_reservation(args)
    assert proposed.value.code == "MATERIAL_INACTIVE"
    with pytest.raises(BusinessError) as reserved:
        InventoryService(db, user.id, "inactive-direct").reserve(
            material.id,
            project.id,
            Decimal("1"),
            "inactive-direct",
            "inactive must reject",
        )
    assert reserved.value.code == "MATERIAL_INACTIVE"
    db.close()
    engine.dispose()


@pytest.mark.parametrize("unavailable_kind", ["inactive", "deleted"])
def test_revision_release_rejects_unavailable_bom_material(
    tmp_path, unavailable_kind
):
    engine, db, user, material, revision, _project = _scenario(
        tmp_path, f"release-{unavailable_kind}"
    )
    draft = clone_revision(
        db,
        revision.id,
        new_revision=f"R-{unavailable_kind}",
    )
    if unavailable_kind == "inactive":
        material.is_active = False
    else:
        material.is_deleted = True
    db.commit()
    with pytest.raises(BusinessError) as unavailable:
        release_revision(
            db,
            draft.id,
            released_by_id=user.id,
            make_default=False,
        )
    assert unavailable.value.code == "PRODUCT_BOM_MATERIAL_UNAVAILABLE"
    assert material.code in unavailable.value.details["material_codes"]
    db.close()
    engine.dispose()
