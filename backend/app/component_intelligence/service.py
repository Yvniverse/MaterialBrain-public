from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.conversation import ConversationContextService, PreparedConversationTurn
from app.agent.task_contract import TaskContract
from app.core.config import Settings, settings
from app.core.exceptions import BusinessError
from app.models import User

from .core import ComponentSearchCore
from .extractor import RequirementExtractor
from .schemas import ComponentSearchResponse


class ComponentIntelligenceService:
    """Standalone API wrapper; conversation state never enters ComponentSearchCore."""

    def __init__(
        self,
        db: Session,
        user: User,
        request_id: str,
        *,
        config: Settings = settings,
    ):
        self.db = db
        self.user = user
        self.request_id = request_id
        self.config = config

    def search(
        self,
        requirement: str,
        *,
        limit: int = 8,
        conversation_id: str | None = None,
    ) -> ComponentSearchResponse:
        if not self.config.component_intelligence_enabled:
            raise BusinessError(
                "COMPONENT_INTELLIGENCE_DISABLED",
                "Component Intelligence 当前未启用",
                503,
            )
        query = RequirementExtractor().extract(requirement)
        conversations = ConversationContextService(self.db, self.user, self.config)
        snapshot = conversations.open(conversation_id)
        guidance = ""
        selected_material_id: int | None = None
        candidate_scope_ids: list[int] | None = None

        if query.context_reference:
            prepared = conversations.prepare(snapshot, requirement)
            material_entity = prepared.entities.get("material_candidates") or {}
            selected = material_entity.get("selected_material_id")
            candidate_scope_ids = [
                int(item["id"]) for item in material_entity.get("items") or []
            ]
            if selected:
                selected_material_id = int(selected)
                candidate_scope_ids = [selected_material_id]
            guidance = prepared.direct_answer
            conversations.persist(
                prepared,
                entities=prepared.entities,
                intent=prepared.direct_intent or "component_context_followup",
            )
        else:
            prepared = PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=TaskContract(entity_kind="material"),
                explicit_material_switch=True,
            )

        result = ComponentSearchCore(self.db, self.user, self.request_id).search(
            requirement,
            limit=limit,
            candidate_scope_ids=candidate_scope_ids,
        )
        if not query.context_reference:
            selected_material_id = (
                result.candidates[0].material_id if len(result.candidates) == 1 else None
            )
            items = [
                {
                    "id": item.material_id,
                    "code": item.code,
                    "name": item.name,
                    "mpn": item.mpn,
                }
                for item in result.candidates
            ]
            entities = {
                "material_candidates": {
                    "items": items,
                    "count": len(items),
                    "exact_match_ids": (
                        [selected_material_id]
                        if selected_material_id
                        and any(
                            reason.startswith("精确")
                            for reason in result.candidates[0].match_reasons
                        )
                        else []
                    ),
                    "selected_material_id": selected_material_id,
                }
            }
            conversations.persist(prepared, entities=entities, intent="component_search")

        return ComponentSearchResponse(
            conversation_id=snapshot.id,
            selected_material_id=selected_material_id,
            guidance=guidance,
            **result.model_dump(),
        )
