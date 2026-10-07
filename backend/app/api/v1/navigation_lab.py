"""Authenticated, read-only navigation laboratory; does not command hardware."""

import copy

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import CurrentUser, require_any
from app.services.embodied_navigation.schemas import PlanRequest
from app.services.embodied_navigation.service import plan_navigation, world_snapshot

router = APIRouter(
    prefix="/navigation-lab",
    tags=["具身导航实验仓"],
    dependencies=[Depends(require_any("material:view", "location:manage", "picking:view"))],
)


@router.get("/world")
def read_world(user: CurrentUser):
    return copy.deepcopy(world_snapshot())


@router.post("/plan")
def create_plan(body: PlanRequest, user: CurrentUser):
    try:
        return plan_navigation(body)
    except ValueError as exc:
        code = 409 if str(exc) == "WORLD_REVISION_MISMATCH" else 422
        raise HTTPException(code, detail=str(exc)) from exc
