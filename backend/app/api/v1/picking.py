from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, require, require_any
from app.models import BuildPlan, PickTask, ProductRevision
from app.schemas.picking import (
    PickAllocationConfirmRequest,
    PickIssueRequest,
    PickScanValidateRequest,
    PickTaskCancelRequest,
    PickTaskCreateRequest,
    PickTaskReplanRequest,
)
from app.services.build_plans import build_plan_data
from app.services.picking import PickingService

router = APIRouter(tags=["生产拣货"])


@router.get(
    "/build-plans",
    dependencies=[Depends(require_any("project:view", "project:manage", "picking:view"))],
)
def list_build_plans(
    db: DB,
    user: CurrentUser,
    project_id: int | None = None,
    product_id: int | None = None,
    product_revision_id: int | None = None,
    status: str | None = None,
    plan_no: str = "",
):
    query = select(BuildPlan).join(ProductRevision)
    for column, value in (
        (BuildPlan.project_id, project_id),
        (ProductRevision.product_id, product_id),
        (BuildPlan.product_revision_id, product_revision_id),
    ):
        if value is not None:
            query = query.where(column == value)
    if plan_no:
        query = query.where(BuildPlan.plan_no.contains(plan_no, autoescape=True))
    rows = []
    for plan in db.scalars(
        query.order_by(BuildPlan.created_at.desc(), BuildPlan.id.desc()).limit(200)
    ):
        task = db.scalar(
            select(PickTask).where(PickTask.build_plan_id == plan.id).order_by(PickTask.id.desc())
        )
        if status and status != (task.status if task else plan.status):
            continue
        rows.append(
            {
                **build_plan_data(db, plan),
                "production_stage": task.status if task else plan.status,
                "pick_task_id": task.id if task else None,
            }
        )
    return rows


@router.get("/picking/readiness/{plan_id}", dependencies=[Depends(require("picking:view"))])
def readiness(plan_id: int, db: DB, user: CurrentUser):
    return PickingService(db).readiness(plan_id)


@router.post("/pick-tasks", status_code=201, dependencies=[Depends(require("picking:operate"))])
def create_task(payload: PickTaskCreateRequest, request: Request, db: DB, user: CurrentUser):
    return PickingService(db, user.id, request.state.request_id).create(payload)


@router.get("/pick-tasks", dependencies=[Depends(require("picking:view"))])
def list_tasks(db: DB, user: CurrentUser, project_id: int | None = None):
    query = select(PickTask)
    if project_id:
        query = query.where(PickTask.project_id == project_id)
    return [
        PickingService(db).detail(t.id)
        for t in db.scalars(query.order_by(PickTask.id.desc()).limit(100))
    ]


@router.get("/pick-tasks/{task_id}", dependencies=[Depends(require("picking:view"))])
def task_detail(task_id: int, db: DB, user: CurrentUser):
    return PickingService(db).detail(task_id)


@router.get(
    "/pick-tasks/{task_id}/operator-state",
    dependencies=[Depends(require("picking:view"))],
)
def operator_state(task_id: int, db: DB, user: CurrentUser):
    return PickingService(db).operator_state(task_id)


@router.post(
    "/pick-allocations/{allocation_id}/validate-scan",
    dependencies=[Depends(require("picking:operate"))],
)
def validate_scan(
    allocation_id: int, payload: PickScanValidateRequest, db: DB, user: CurrentUser
):
    return PickingService(db, user.id, "scan-validate").validate_scan(allocation_id, payload)


@router.post(
    "/pick-tasks/{task_id}/issues",
    dependencies=[Depends(require("picking:operate"))],
)
def report_issue(
    task_id: int, payload: PickIssueRequest, request: Request, db: DB, user: CurrentUser
):
    return PickingService(db, user.id, request.state.request_id).report_issue(task_id, payload)


@router.post("/pick-tasks/{task_id}/replan", dependencies=[Depends(require("picking:operate"))])
def replan(
    task_id: int, payload: PickTaskReplanRequest, request: Request, db: DB, user: CurrentUser
):
    return PickingService(db, user.id, request.state.request_id).replan(task_id, payload)


@router.post("/pick-tasks/{task_id}/cancel", dependencies=[Depends(require("picking:operate"))])
def cancel(
    task_id: int, payload: PickTaskCancelRequest, request: Request, db: DB, user: CurrentUser
):
    return PickingService(db, user.id, request.state.request_id).cancel(task_id, payload.reason)


@router.post(
    "/pick-allocations/{allocation_id}/confirm", dependencies=[Depends(require("picking:operate"))]
)
def confirm(
    allocation_id: int,
    payload: PickAllocationConfirmRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    return PickingService(db, user.id, request.state.request_id).confirm(allocation_id, payload)
