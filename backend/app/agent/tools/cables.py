from app.schemas.agent import CableSearchArgs, MaterialIdArgs
from app.services.cable_intelligence import CableSearchService

from .common import ToolContext


def search_cables(ctx: ToolContext, args: CableSearchArgs) -> dict:
    return CableSearchService(ctx.db).search(args)


def get_cable_detail(ctx: ToolContext, args: MaterialIdArgs) -> dict:
    return CableSearchService(ctx.db).detail(args.material_id)
