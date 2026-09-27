import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.agent.conversation import ConversationContextService
from app.agent.proposals import ProposalService
from app.core.config import Settings
from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models import (
    AgentActionProposal,
    AgentConversationContext,
    BuildPlan,
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
from app.schemas.agent import (
    ProposeBuildMaterialReservationArgs,
    ProposeInventoryReservationArgs,
)
from app.services.inventory import InventoryService
from app.services.product_revisions import release_revision
from evals.production_shadow import production_read_only_session, verify_database_read_only

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


@pytest.mark.skipif(not POSTGRES_URL, reason="需要独立 PostgreSQL 测试库验证 READ ONLY")
def test_production_shadow_transaction_is_database_read_only():
    engine = create_engine(POSTGRES_URL)
    Base.metadata.create_all(engine)
    assert verify_database_read_only(engine) is True
    with production_read_only_session(engine) as db:
        before = db.scalar(select(func.count(Material.id)))
        assert db.scalar(select(func.count(Material.id))) == before
        with pytest.raises(Exception, match="read-only|read only"):
            db.execute(Material.__table__.insert().values(code="SHADOW-WRITE-FORBIDDEN", name="x"))
            db.flush()
    engine.dispose()


@pytest.mark.skipif(not POSTGRES_URL, reason="需要独立 PostgreSQL 测试库和行级锁")
def test_all_concurrent_stock_scenarios():
    """Real PostgreSQL test: outbound, reserve, mixed operations and duplicate retries."""
    engine = create_engine(POSTGRES_URL, pool_size=10)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    with sessions() as db:
        role = Role(name=f"并发测试-{suffix}", permissions=["*"])
        db.add(role)
        db.flush()
        user = User(
            username=f"concurrent-{suffix}",
            full_name="并发测试",
            password_hash="not-used",
            role_id=role.id,
        )
        db.add(user)
        db.flush()
        projects = [
            Project(code=f"CP-{suffix}-{index}", name=f"并发项目 {index}", manager_id=user.id)
            for index in range(2)
        ]
        db.add_all(projects)
        db.commit()
        user_id = user.id
        project_ids = [project.id for project in projects]

    def new_material(label: str) -> int:
        with sessions() as db:
            item = Material(
                code=f"CON-{label}-{suffix}",
                name=f"并发物料 {label}",
                quantity=Decimal("5"),
                created_by_id=user_id,
            )
            db.add(item)
            db.commit()
            return item.id

    def run_parallel(actions):
        def execute(action):
            try:
                with sessions() as db:
                    return "success", action(InventoryService(db, user_id, str(uuid.uuid4())))
            except BusinessError as exc:
                return "error", exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(execute, actions))

    outbound_id = new_material("outbound")
    results = run_parallel(
        [
            lambda service, index=index: service.outbound(
                outbound_id, Decimal("4"), f"out-{index}-{suffix}", "并发出库"
            )
            for index in range(2)
        ]
    )
    assert sorted(status for status, _ in results) == ["error", "success"]

    reserve_id = new_material("reserve")
    results = run_parallel(
        [
            lambda service, index=index: service.reserve(
                reserve_id,
                project_ids[index],
                Decimal("4"),
                f"reserve-{index}-{suffix}",
                "并发预留",
            )
            for index in range(2)
        ]
    )
    assert sorted(status for status, _ in results) == ["error", "success"]

    mixed_id = new_material("mixed")
    results = run_parallel(
        [
            lambda service: service.outbound(
                mixed_id, Decimal("4"), f"mixed-out-{suffix}", "混合出库"
            ),
            lambda service: service.reserve(
                mixed_id,
                project_ids[0],
                Decimal("4"),
                f"mixed-reserve-{suffix}",
                "混合预留",
            ),
        ]
    )
    assert sorted(status for status, _ in results) == ["error", "success"]

    retry_id = new_material("retry")
    same_key = f"same-retry-{suffix}"
    results = run_parallel(
        [
            lambda service: service.outbound(retry_id, Decimal("4"), same_key, "超时后重试")
            for _ in range(2)
        ]
    )
    assert [status for status, _ in results] == ["success", "success"]
    with sessions() as db:
        assert db.get(Material, retry_id).quantity == Decimal("1")
        assert (
            db.scalar(
                select(func.count(StockMovement.id)).where(
                    StockMovement.material_id == retry_id,
                    StockMovement.operation_type == "outbound",
                )
            )
            == 1
        )
        quantities = db.scalars(
            select(Material.quantity).where(
                Material.id.in_([outbound_id, reserve_id, mixed_id, retry_id])
            )
        ).all()
        assert all(quantity >= 0 for quantity in quantities)


@pytest.mark.skipif(not POSTGRES_URL, reason="需要独立 PostgreSQL 测试库和行级锁")
def test_concurrent_agent_proposal_creation_and_approval_are_idempotent():
    engine = create_engine(POSTGRES_URL, pool_size=10)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    with sessions() as db:
        role = Role(name=f"Proposal 并发-{suffix}", permissions=["*"])
        db.add(role)
        db.flush()
        user = User(
            username=f"proposal-concurrent-{suffix}",
            full_name="Proposal 并发测试",
            password_hash="not-used",
            role_id=role.id,
        )
        db.add(user)
        db.flush()
        project = Project(
            code=f"PCP-{suffix}",
            name="并发 Proposal 项目",
            manager_id=user.id,
        )
        material = Material(
            code=f"PCMAT-{suffix}",
            name="并发 Proposal 物料",
            quantity=Decimal("10"),
            created_by_id=user.id,
        )
        db.add_all([project, material])
        db.commit()
        user_id = user.id
        project_id = project.id
        material_id = material.id

    args = ProposeInventoryReservationArgs(
        project_id=project_id,
        items=[{"material_id": material_id, "quantity": "4"}],
        reason="同一业务操作并发重试",
    )
    operation_id = f"proposal-operation-{suffix}"

    def create_proposal(request_id: str):
        with sessions() as db:
            user = db.get(User, user_id)
            return ProposalService(
                db,
                user,
                request_id,
                client_operation_id=operation_id,
            ).create_reservation(args).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        proposal_ids = list(
            pool.map(create_proposal, [f"create-a-{suffix}", f"create-b-{suffix}"])
        )
    assert proposal_ids[0] == proposal_ids[1]
    proposal_id = proposal_ids[0]

    with sessions() as db:
        assert (
            db.scalar(
                select(func.count(AgentActionProposal.id)).where(
                    AgentActionProposal.created_by_id == user_id,
                    AgentActionProposal.client_operation_id == operation_id,
                )
            )
            == 1
        )
        assert db.get(Material, material_id).reserved_quantity == 0

    def approve_proposal(request_id: str):
        try:
            with sessions() as db:
                user = db.get(User, user_id)
                proposal = ProposalService(db, user, request_id).approve(proposal_id)
                return "success", proposal.status
        except BusinessError as exc:
            return "error", exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        approval_results = list(
            pool.map(approve_proposal, [f"approve-a-{suffix}", f"approve-b-{suffix}"])
        )
    assert sorted(status for status, _ in approval_results) == ["error", "success"]
    with sessions() as db:
        assert db.get(Material, material_id).reserved_quantity == Decimal("4")
        assert (
            db.scalar(
                select(func.count(StockMovement.id)).where(
                    StockMovement.material_id == material_id,
                    StockMovement.operation_type == "reserve",
                )
            )
            == 1
        )


@pytest.mark.skipif(not POSTGRES_URL, reason="需要独立 PostgreSQL 测试库验证会话版本锁")
def test_concurrent_conversation_updates_reject_stale_version():
    engine = create_engine(POSTGRES_URL, pool_size=4)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    with sessions() as db:
        role = Role(name=f"会话并发-{suffix}", permissions=["material:view"])
        db.add(role)
        db.flush()
        user = User(
            username=f"conversation-concurrent-{suffix}",
            full_name="会话并发测试",
            password_hash="not-used",
            role_id=role.id,
        )
        db.add(user)
        db.commit()
        user_id = user.id
        conversation_id = ConversationContextService(db, user, Settings()).open(None).id

    with sessions() as first_db, sessions() as second_db:
        first_user = first_db.get(User, user_id)
        second_user = second_db.get(User, user_id)
        first_manager = ConversationContextService(first_db, first_user, Settings())
        second_manager = ConversationContextService(second_db, second_user, Settings())
        first_turn = first_manager.prepare(first_manager.open(conversation_id), "它在哪？")
        second_turn = second_manager.prepare(second_manager.open(conversation_id), "它还有多少？")

        prepared_turns = (first_turn, second_turn)

    def persist(turn):
        with sessions() as db:
            user = db.get(User, user_id)
            manager = ConversationContextService(db, user, Settings())
            try:
                manager.persist(turn, entities={}, intent="clarify_material")
                return "success"
            except BusinessError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(persist, prepared_turns))
    assert sorted(results) == ["AGENT_CONVERSATION_CONFLICT", "success"]
    with sessions() as db:
        context = db.get(AgentConversationContext, conversation_id)
        assert context.context_version == 1
    engine.dispose()


@pytest.mark.skipif(not POSTGRES_URL, reason="需要独立 PostgreSQL 测试库和行级锁")
def test_concurrent_project_reservation_change_makes_build_plan_stale():
    """A locked competing reservation must invalidate, never rewrite, an approved payload."""
    engine = create_engine(POSTGRES_URL, pool_size=6)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    with sessions() as db:
        role = Role(name=f"BuildPlan 并发-{suffix}", permissions=["*"])
        db.add(role)
        db.flush()
        user = User(
            username=f"build-plan-concurrent-{suffix}",
            full_name="BuildPlan 并发测试",
            password_hash="not-used",
            role_id=role.id,
        )
        material = Material(
            code=f"BPC-MAT-{suffix}",
            name="BuildPlan 并发物料",
            quantity=Decimal("20"),
            reserved_quantity=Decimal("6"),
            created_by_id=None,
        )
        product = Product(code=f"BPC-PROD-{suffix}", name="BuildPlan 并发产品")
        db.add_all([user, material, product])
        db.flush()
        material.created_by_id = user.id
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
            code=f"BPC-PRJ-{suffix}",
            name="BuildPlan 并发项目",
            manager_id=user.id,
            product_revision_id=revision.id,
        )
        db.add(project)
        db.flush()
        reservation = ProjectReservation(
            project_id=project.id,
            material_id=material.id,
            quantity=Decimal("6"),
        )
        db.add(reservation)
        db.commit()
        user_id = user.id
        material_id = material.id
        project_id = project.id
        revision_id = revision.id

    with sessions() as db:
        user = db.get(User, user_id)
        outcome = ProposalService(
            db,
            user,
            f"build-create-{suffix}",
            client_operation_id=f"build-operation-{suffix}",
        ).create_build_plan_reservation(
            ProposeBuildMaterialReservationArgs(
                product_revision_id=revision_id,
                project_id=project_id,
                build_quantity=3,
            )
        )
        proposal_id = outcome["proposal"].id
        plan_id = outcome["plan"].id
        fixed_payload = outcome["proposal"].payload.copy()

    locked = threading.Event()
    release_competitor = threading.Event()

    def competing_reservation() -> None:
        with sessions() as db:
            reservation = db.scalar(
                select(ProjectReservation)
                .where(
                    ProjectReservation.project_id == project_id,
                    ProjectReservation.material_id == material_id,
                )
                .with_for_update()
            )
            material = db.scalar(
                select(Material).where(Material.id == material_id).with_for_update()
            )
            locked.set()
            assert release_competitor.wait(timeout=10)
            reservation.quantity += Decimal("1")
            material.reserved_quantity += Decimal("1")
            db.commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        competing = pool.submit(competing_reservation)
        assert locked.wait(timeout=10)
        release_competitor.set()
        with sessions() as db:
            user = db.get(User, user_id)
            with pytest.raises(BusinessError) as stale:
                ProposalService(db, user, f"build-approve-{suffix}").approve(
                    proposal_id
                )
            assert stale.value.code == "BUILD_PLAN_STALE"
        competing.result(timeout=10)

    with sessions() as db:
        proposal = db.get(AgentActionProposal, proposal_id)
        plan = db.get(BuildPlan, plan_id)
        material = db.get(Material, material_id)
        reservation = db.scalar(
            select(ProjectReservation).where(
                ProjectReservation.project_id == project_id,
                ProjectReservation.material_id == material_id,
            )
        )
        assert proposal.payload == fixed_payload
        assert proposal.status == "failed"
        assert plan.status == "stale"
        assert material.reserved_quantity == Decimal("7")
        assert reservation.quantity == Decimal("7")
    engine.dispose()
