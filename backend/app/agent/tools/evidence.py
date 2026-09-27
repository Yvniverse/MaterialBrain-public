from app.schemas.agent import ComponentEvidenceCompareArgs, DatasheetEvidenceArgs
from app.services.engineering_evidence import (
    EvidenceComparisonService,
    EvidenceRetrievalService,
)

from .common import ToolContext


def search_datasheet_evidence(ctx: ToolContext, args: DatasheetEvidenceArgs) -> dict:
    return EvidenceRetrievalService(ctx.db).search_material_evidence(
        material_ids=list(args.material_ids),
        query=args.query,
        include_superseded=args.include_superseded,
        limit=args.limit,
    )


def compare_component_evidence(
    ctx: ToolContext, args: ComponentEvidenceCompareArgs
) -> dict:
    return EvidenceComparisonService(ctx.db).compare(
        first_material_id=args.first_material_id,
        second_material_id=args.second_material_id,
        fields=list(args.fields),
    )
