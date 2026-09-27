from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agent.tools.common import ToolContext
from app.agent.tools.inventory import get_inventory_availability
from app.agent.tools.locations import find_material_locations
from app.models import ComponentRelation, Material, User
from app.schemas.agent import MaterialIdArgs

from .extractor import RequirementExtractor
from .schemas import ComponentCandidate, ComponentCoreResult
from .search import ComponentMaterialSearch, RankedMaterial


class ComponentSearchCore:
    """Conversation/provider-agnostic deterministic component search over one DB snapshot."""

    def __init__(self, db: Session, user: User, request_id: str):
        self.db = db
        self.user = user
        self.request_id = request_id

    @staticmethod
    def _caveat(ranked: list[RankedMaterial], replacement_intent: bool) -> str:
        if any(item.metadata_confidence == "low" for item in ranked):
            return (
                "低置信度条目仅按 catalog identity 返回，不形成技术参数结论；所有候选仍需工程验证。"
            )
        if replacement_intent:
            return "仅返回候选相似器件，需要工程验证；不代表 replacement_for 或 pin_compatible。"
        return "候选来自可信结构化物料主数据；参数适用性仍需工程验证。"

    def _validated_relations(self, item: RankedMaterial) -> list[dict]:
        if item.metadata_confidence == "low":
            return []
        rows = list(
            self.db.scalars(
                select(ComponentRelation).where(
                    ComponentRelation.status == "validated",
                    or_(
                        ComponentRelation.source_material_id == item.material.id,
                        ComponentRelation.target_material_id == item.material.id,
                    ),
                )
            ).all()
        )
        other_ids = {
            row.target_material_id
            if row.source_material_id == item.material.id
            else row.source_material_id
            for row in rows
        }
        other_materials = {
            material.id: material
            for material in self.db.scalars(
                select(Material).where(Material.id.in_(other_ids))
            ).all()
        }
        return [
            {
                "relation_id": row.id,
                "relation_type": row.relation_type,
                "status": "validated",
                "language": "已验证工程关系",
                "related_material": {
                    "id": other_material.id,
                    "code": other_material.code,
                    "name": other_material.name,
                    "mpn": other_material.mpn,
                },
                "evidence_summary": row.evidence_summary,
                "global_replacement_approved": False,
            }
            for row in rows
            if (
                other_material := other_materials.get(
                    row.target_material_id
                    if row.source_material_id == item.material.id
                    else row.source_material_id
                )
            )
            is not None
        ]

    def _candidate(self, item: RankedMaterial, context: ToolContext) -> ComponentCandidate:
        args = MaterialIdArgs(material_id=item.material.id)
        technical_claims_allowed = item.metadata_confidence != "low"
        return ComponentCandidate(
            material_id=item.material.id,
            code=item.material.code,
            name=item.material.name,
            mpn=item.material.mpn,
            specification=item.material.specification,
            package=item.material.package,
            manufacturer=item.material.manufacturer,
            score=item.score,
            match_reasons=list(item.reasons),
            hard_constraint_matches=list(item.hard_constraint_matches),
            soft_preference_matches=list(item.soft_preference_matches),
            metadata_confidence=item.metadata_confidence,
            technical_claims_allowed=technical_claims_allowed,
            engineering_verification_required=True,
            validated_relations=self._validated_relations(item),
            inventory=get_inventory_availability(context, args),
            locations=find_material_locations(context, args),
        )

    def search(
        self,
        requirement: str,
        *,
        limit: int = 5,
        candidate_scope_ids: list[int] | None = None,
    ) -> ComponentCoreResult:
        """Search only; candidate_scope_ids must come from verified server context."""

        query = RequirementExtractor().extract(requirement)
        search = ComponentMaterialSearch(self.db)
        ranked = (
            search.by_ids(candidate_scope_ids, reason="来自服务端已验证的候选范围")
            if candidate_scope_ids is not None
            else search.search(query, limit=limit)
        )
        if any(
            marker in requirement.casefold()
            for marker in ("库存多", "放前面", "从高到低", "available stock", "stock descending")
        ):
            ranked.sort(
                key=lambda item: (
                    -item.material.available_quantity,
                    -item.score,
                    item.material.code,
                )
            )
        ranked = ranked[:limit]
        context = ToolContext(self.db, self.user, self.request_id)
        candidates = [self._candidate(item, context) for item in ranked]
        return ComponentCoreResult(
            query=query,
            candidates=candidates,
            count=len(candidates),
            candidate_only=True,
            engineering_caveat=self._caveat(ranked, query.replacement_intent),
            read_only=True,
        )
