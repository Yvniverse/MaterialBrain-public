from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from sqlalchemy import case, func, select

from app.api.deps import DB, CurrentUser
from app.core.config import settings
from app.models import Material, StockMovement

router = APIRouter(prefix="/dashboard", tags=["仪表盘"])


def _local_day_bounds(day, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    begin_local = datetime.combine(day, datetime.min.time(), tzinfo=timezone)
    end_local = begin_local + timedelta(days=1)
    return begin_local.astimezone(UTC), end_local.astimezone(UTC)


def _flow_totals(db: DB, begin: datetime, end: datetime):
    return db.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (StockMovement.quantity_delta > 0, StockMovement.quantity_delta),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (StockMovement.quantity_delta < 0, -StockMovement.quantity_delta),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(
            StockMovement.created_at >= begin,
            StockMovement.created_at < end,
            StockMovement.operation_type.notin_(("loan", "return")),
        )
    ).one()


@router.get("/summary")
def summary(db: DB, user: CurrentUser):
    totals = db.execute(
        select(
            func.count(Material.id),
            func.coalesce(func.sum(Material.quantity), 0),
            func.coalesce(func.sum(Material.reserved_quantity), 0),
            func.coalesce(func.sum(Material.quantity - Material.reserved_quantity), 0),
            func.coalesce(func.sum(Material.quantity * Material.unit_price), 0),
        ).where(Material.is_deleted.is_(False))
    ).one()
    low = (
        db.scalar(
            select(func.count(Material.id)).where(
                Material.is_deleted.is_(False),
                Material.quantity - Material.reserved_quantity <= Material.safety_stock,
            )
        )
        or 0
    )
    out = (
        db.scalar(
            select(func.count(Material.id)).where(
                Material.is_deleted.is_(False), Material.quantity - Material.reserved_quantity <= 0
            )
        )
        or 0
    )
    timezone = ZoneInfo(settings.business_timezone)
    today_date = datetime.now(timezone).date()
    today_begin, today_end = _local_day_bounds(today_date, timezone)
    today_inbound, today_outbound = _flow_totals(db, today_begin, today_end)
    recent = db.scalars(
        select(StockMovement)
        .where(StockMovement.operation_type.notin_(("loan", "return")))
        .order_by(StockMovement.created_at.desc())
        .limit(10)
    ).all()
    trend = []
    for days in range(29, -1, -1):
        d = today_date - timedelta(days=days)
        begin, end = _local_day_bounds(d, timezone)
        vals = _flow_totals(db, begin, end)
        trend.append({"date": d.isoformat(), "inbound": vals[0], "outbound": vals[1]})
    return {
        "material_count": totals[0],
        "quantity": totals[1],
        "reserved_quantity": totals[2],
        "available_quantity": totals[3],
        "inventory_value": totals[4],
        "low_stock_count": low,
        "out_of_stock_count": out,
        "today_inbound": today_inbound,
        "today_outbound": today_outbound,
        "recent_movements": [
            {
                "id": x.id,
                "movement_no": x.movement_no,
                "material_id": x.material_id,
                "operation_type": x.operation_type,
                "quantity_delta": x.quantity_delta,
                "created_at": x.created_at,
            }
            for x in recent
        ],
        "trend": trend,
    }
