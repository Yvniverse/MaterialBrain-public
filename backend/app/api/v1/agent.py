from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.agent.proposals import ProposalService
from app.agent.service import WarehouseAgentService
from app.agent.suggestions import WarehouseAgentSuggestionService
from app.api.deps import DB, CurrentUser, require
from app.core.exceptions import BusinessError
from app.models import (
    AgentActionProposal,
    BuildPlan,
    BuildPlanItem,
    Material,
    Product,
    ProductRevision,
    Project,
)
from app.schemas.agent import (
    AgentActionProposalOut,
    AgentProposalDisplay,
    AgentProposalDisplayItem,
    AgentQueryRequest,
    AgentQueryResponse,
    AgentSuggestionsResponse,
    ProposalRejectRequest,
)

router = APIRouter(prefix="/agent", tags=["Warehouse Agent"])


def _can_operate_inventory(user: CurrentUser) -> bool:
    permissions = set(user.role.permissions or [])
    return "*" in permissions or "inventory:operate" in permissions


def _proposal_outputs(
    db: DB,
    proposals: list[AgentActionProposal],
) -> list[AgentActionProposalOut]:
    project_ids = {
        int(proposal.payload.get("project_id"))
        for proposal in proposals
        if proposal.payload.get("project_id")
    }
    material_ids = {
        int(item["material_id"])
        for proposal in proposals
        for item in proposal.payload.get("items", [])
        if item.get("material_id")
    }
    build_plan_ids = {
        int(proposal.payload["build_plan_id"])
        for proposal in proposals
        if proposal.payload.get("build_plan_id")
    }
    projects = (
        {
            project.id: project
            for project in db.scalars(select(Project).where(Project.id.in_(project_ids))).all()
        }
        if project_ids
        else {}
    )
    materials = (
        {
            material.id: material
            for material in db.scalars(select(Material).where(Material.id.in_(material_ids))).all()
        }
        if material_ids
        else {}
    )
    build_plans = (
        {
            plan.id: plan
            for plan in db.scalars(
                select(BuildPlan).where(BuildPlan.id.in_(build_plan_ids))
            ).all()
        }
        if build_plan_ids
        else {}
    )
    revision_ids = {plan.product_revision_id for plan in build_plans.values()}
    revisions = (
        {
            revision.id: revision
            for revision in db.scalars(
                select(ProductRevision).where(ProductRevision.id.in_(revision_ids))
            ).all()
        }
        if revision_ids
        else {}
    )
    product_ids = {revision.product_id for revision in revisions.values()}
    products = (
        {
            product.id: product
            for product in db.scalars(
                select(Product).where(Product.id.in_(product_ids))
            ).all()
        }
        if product_ids
        else {}
    )
    plan_items = (
        {
            (item.build_plan_id, item.material_id): item
            for item in db.scalars(
                select(BuildPlanItem).where(BuildPlanItem.build_plan_id.in_(build_plan_ids))
            ).all()
        }
        if build_plan_ids
        else {}
    )

    outputs: list[AgentActionProposalOut] = []
    for proposal in proposals:
        project = projects.get(proposal.payload.get("project_id"))
        display_items = []
        for item in proposal.payload.get("items", []):
            material = materials.get(item.get("material_id"))
            if material:
                plan_item = plan_items.get(
                    (proposal.payload.get("build_plan_id"), material.id)
                )
                display_items.append(
                    AgentProposalDisplayItem(
                        material_id=material.id,
                        code=material.code,
                        name=material.name,
                        mpn=material.mpn,
                        required_total=(plan_item.required_total if plan_item else None),
                        reserved_for_project_at_plan=(
                            plan_item.reserved_for_project_at_plan if plan_item else None
                        ),
                        additional_reservation_required=(
                            plan_item.additional_reservation_required if plan_item else None
                        ),
                    )
                )
        output = AgentActionProposalOut.model_validate(proposal)
        plan = build_plans.get(proposal.payload.get("build_plan_id"))
        revision = revisions.get(plan.product_revision_id) if plan else None
        product = products.get(revision.product_id) if revision else None
        output.display = AgentProposalDisplay(
            project_code=project.code if project else "",
            project_name=project.name if project else "",
            source=proposal.payload.get("source", "manual"),
            build_plan_id=plan.id if plan else None,
            build_plan_no=plan.plan_no if plan else "",
            product_code=product.code if product else "",
            product_name=product.name if product else "",
            product_revision=revision.revision if revision else "",
            product_bom_hash=plan.product_bom_hash if plan else "",
            build_quantity=plan.build_quantity if plan else None,
            items=display_items,
        )
        outputs.append(output)
    return outputs


@router.post(
    "/query",
    response_model=AgentQueryResponse,
    dependencies=[Depends(require("material:view"))],
)
def query_agent(payload: AgentQueryRequest, request: Request, db: DB, user: CurrentUser):
    return WarehouseAgentService(db, user, request.state.request_id).query(
        payload.message,
        conversation_id=payload.conversation_id,
        client_operation_id=payload.client_operation_id,
        **(
            {"navigation_context": payload.navigation_context} if payload.navigation_context else {}
        ),
    )


@router.get(
    "/suggestions",
    response_model=AgentSuggestionsResponse,
    dependencies=[Depends(require("material:view"))],
)
def list_agent_suggestions(db: DB, user: CurrentUser):
    return WarehouseAgentSuggestionService(db, user).build()


@router.get(
    "/proposals",
    response_model=list[AgentActionProposalOut],
    dependencies=[Depends(require("material:view"))],
)
def list_proposals(
    db: DB,
    user: CurrentUser,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    stmt = select(AgentActionProposal)
    if not _can_operate_inventory(user):
        stmt = stmt.where(AgentActionProposal.created_by_id == user.id)
    if status:
        stmt = stmt.where(AgentActionProposal.status == status)
    proposals = db.scalars(stmt.order_by(AgentActionProposal.created_at.desc()).limit(limit)).all()
    return _proposal_outputs(db, list(proposals))


@router.get(
    "/proposals/{proposal_id}",
    response_model=AgentActionProposalOut,
    dependencies=[Depends(require("material:view"))],
)
def get_proposal(proposal_id: int, db: DB, user: CurrentUser):
    proposal = db.get(AgentActionProposal, proposal_id)
    if not proposal:
        raise BusinessError("PROPOSAL_NOT_FOUND", "Proposal 不存在", 404)
    if proposal.created_by_id != user.id and not _can_operate_inventory(user):
        raise BusinessError("PROPOSAL_VIEW_FORBIDDEN", "无权查看该 Proposal", 403)
    return _proposal_outputs(db, [proposal])[0]


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=AgentActionProposalOut,
    dependencies=[Depends(require("inventory:operate"))],
)
def approve_proposal(proposal_id: int, request: Request, db: DB, user: CurrentUser):
    proposal = ProposalService(db, user, request.state.request_id).approve(proposal_id)
    return _proposal_outputs(db, [proposal])[0]


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=AgentActionProposalOut,
    dependencies=[Depends(require("material:view"))],
)
def reject_proposal(
    proposal_id: int,
    payload: ProposalRejectRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    proposal = ProposalService(db, user, request.state.request_id).reject(
        proposal_id, payload.reason
    )
    return _proposal_outputs(db, [proposal])[0]
