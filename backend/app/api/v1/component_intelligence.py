from fastapi import APIRouter, Depends, Request

from app.api.deps import DB, CurrentUser, require
from app.component_intelligence.schemas import ComponentSearchRequest, ComponentSearchResponse
from app.component_intelligence.service import ComponentIntelligenceService

router = APIRouter(prefix="/component-intelligence", tags=["Component Intelligence"])


@router.post(
    "/search",
    response_model=ComponentSearchResponse,
    dependencies=[Depends(require("material:view"))],
)
def search_components(
    payload: ComponentSearchRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    return ComponentIntelligenceService(db, user, request.state.request_id).search(
        payload.requirement,
        limit=payload.limit,
        conversation_id=payload.conversation_id,
    )
