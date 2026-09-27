from app.schemas.agent import PowerDesignArgs
from app.services.power_design import PowerDesignService

from .common import ToolContext


def plan_power_design(ctx: ToolContext, args: PowerDesignArgs) -> dict:
    return PowerDesignService(ctx.db, ctx.user, ctx.request_id).plan(args.requirement)
