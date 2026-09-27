from app.agent.proposals import ProposalService
from app.schemas.agent import (
    AgentActionProposalOut,
    ProposeBuildMaterialReservationArgs,
    ProposeInventoryReservationArgs,
)
from app.services.build_plans import build_plan_data

from .common import ToolContext


def propose_inventory_reservation(
    ctx: ToolContext,
    args: ProposeInventoryReservationArgs,
) -> dict:
    proposal = ProposalService(
        ctx.db,
        ctx.user,
        ctx.request_id,
        client_operation_id=ctx.client_operation_id,
    ).create_reservation(args)
    return AgentActionProposalOut.model_validate(proposal).model_dump(mode="json")


def propose_build_material_reservation(
    ctx: ToolContext,
    args: ProposeBuildMaterialReservationArgs,
) -> dict:
    outcome = ProposalService(
        ctx.db,
        ctx.user,
        ctx.request_id,
        client_operation_id=ctx.client_operation_id,
    ).create_build_plan_reservation(args)
    plan = outcome["plan"]
    proposal = outcome["proposal"]
    result = {
        "fully_reserved": outcome["fully_reserved"],
        "build_plan": build_plan_data(ctx.db, plan),
        "proposal": None,
    }
    if proposal is not None:
        proposal_data = AgentActionProposalOut.model_validate(proposal).model_dump(
            mode="json"
        )
        result.update(proposal_data)
        result["proposal"] = proposal_data
    return result
