from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, require
from app.models import StockMovement, Stocktake
from app.schemas.domain import AdjustOperation, InventoryOperation, MovementOut, StocktakeData
from app.services.inventory import InventoryService

router = APIRouter(
    prefix="/inventory", tags=["库存"], dependencies=[Depends(require("inventory:operate"))]
)


def service(db: DB, user: CurrentUser, request: Request) -> InventoryService:
    return InventoryService(db, user.id, request.state.request_id)


@router.post("/inbound")
def inbound(p: InventoryOperation, svc: InventoryService = Depends(service)):
    return svc.inbound(p.material_id, p.quantity, p.idempotency_key, p.reason, p.notes)


@router.post("/outbound")
def outbound(p: InventoryOperation, svc: InventoryService = Depends(service)):
    return svc.outbound(
        p.material_id, p.quantity, p.idempotency_key, p.reason, p.notes, project_id=p.project_id
    )


@router.post("/scrap")
def scrap(p: InventoryOperation, svc: InventoryService = Depends(service)):
    return svc.scrap(p.material_id, p.quantity, p.idempotency_key, p.reason, p.notes)


@router.post("/refund")
def refund(p: InventoryOperation, svc: InventoryService = Depends(service)):
    return svc.refund(p.material_id, p.quantity, p.idempotency_key, p.reason, p.notes)


@router.post("/reserve")
def reserve(p: InventoryOperation, svc: InventoryService = Depends(service)):
    if not p.project_id:
        from app.core.exceptions import BusinessError

        raise BusinessError("PROJECT_REQUIRED", "预留必须关联项目")
    return svc.reserve(
        p.material_id, p.project_id, p.quantity, p.idempotency_key, p.reason, p.notes
    )


@router.post("/cancel-reservation")
def cancel_reservation(p: InventoryOperation, svc: InventoryService = Depends(service)):
    if not p.project_id:
        from app.core.exceptions import BusinessError

        raise BusinessError("PROJECT_REQUIRED", "取消预留必须关联项目")
    return svc.cancel_reservation(
        p.material_id, p.project_id, p.quantity, p.idempotency_key, p.reason, p.notes
    )


@router.post("/reservation-to-outbound")
def reservation_to_outbound(p: InventoryOperation, svc: InventoryService = Depends(service)):
    if not p.project_id:
        from app.core.exceptions import BusinessError

        raise BusinessError("PROJECT_REQUIRED", "预留转出库必须关联项目")
    return svc.reservation_to_outbound(
        p.material_id, p.project_id, p.quantity, p.idempotency_key, p.reason, p.notes
    )


@router.post("/adjust")
def adjust(p: AdjustOperation, svc: InventoryService = Depends(service)):
    return svc.adjust(p.material_id, p.actual_quantity, p.idempotency_key, p.reason, p.notes)


@router.post("/transfer")
def transfer(p: InventoryOperation, svc: InventoryService = Depends(service)):
    if not p.source_location_id or not p.target_location_id:
        from app.core.exceptions import BusinessError

        raise BusinessError("LOCATION_REQUIRED", "库位转移必须填写来源和目标库位")
    return svc.transfer(
        p.material_id,
        p.quantity,
        p.source_location_id,
        p.target_location_id,
        p.idempotency_key,
        p.reason,
        p.notes,
    )


@router.post("/reverse")
def reverse(p: InventoryOperation, svc: InventoryService = Depends(service)):
    if not p.movement_id:
        from app.core.exceptions import BusinessError

        raise BusinessError("MOVEMENT_REQUIRED", "冲正必须关联原流水")
    return svc.reverse(p.movement_id, p.idempotency_key, p.reason, p.notes)


read_router = APIRouter(tags=["库存查询"])


@read_router.get("/stock-movements", dependencies=[Depends(require("inventory:view"))])
def movements(
    db: DB,
    user: CurrentUser,
    material_id: int | None = None,
    operation_type: str | None = None,
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    stmt = select(StockMovement).where(StockMovement.operation_type.notin_(("loan", "return")))
    if material_id:
        stmt = stmt.where(StockMovement.material_id == material_id)
    if operation_type:
        stmt = stmt.where(StockMovement.operation_type == operation_type)
    if q:
        stmt = stmt.where(StockMovement.movement_no.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(StockMovement.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [MovementOut.model_validate(x) for x in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@read_router.get("/stocktakes", dependencies=[Depends(require("inventory:view"))])
def stocktakes(db: DB, user: CurrentUser):
    return db.scalars(select(Stocktake).order_by(Stocktake.created_at.desc())).all()


@read_router.post("/stocktakes", dependencies=[Depends(require("inventory:operate"))])
def create_stocktake(p: StocktakeData, request: Request, db: DB, user: CurrentUser):
    return InventoryService(db, user.id, request.state.request_id).adjust(
        p.material_id, p.actual_quantity, p.idempotency_key, p.reason
    )
