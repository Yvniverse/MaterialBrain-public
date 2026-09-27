from app.component_intelligence.core import ComponentSearchCore
from app.schemas.agent import ComponentRequirementArgs

from .common import ToolContext


def search_components_by_requirement(
    ctx: ToolContext, args: ComponentRequirementArgs
) -> dict:
    result = ComponentSearchCore(ctx.db, ctx.user, ctx.request_id).search(
        args.requirement,
        limit=args.limit,
    )
    data = result.model_dump(mode="json")
    candidates = data["candidates"]
    selected_id = candidates[0]["material_id"] if len(candidates) == 1 else None
    data["material_candidates"] = {
        "items": [
            {
                "id": item["material_id"],
                "code": item["code"],
                "name": item["name"],
                "mpn": item["mpn"],
                "specification": item["specification"],
                "package": item["package"],
                "manufacturer": item["manufacturer"],
            }
            for item in candidates
        ],
        "count": len(candidates),
        "exact_match_ids": [],
        "selected_material_id": selected_id,
    }
    return data
