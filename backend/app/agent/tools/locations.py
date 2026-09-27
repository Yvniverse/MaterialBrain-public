from decimal import Decimal

from sqlalchemy import func, select

from app.core.exceptions import BusinessError
from app.models import InventoryLot, Location, Material
from app.schemas.agent import MaterialIdArgs
from app.services.data_provenance import material_provenance

from .common import ToolContext, location_dict


def _organizer_for(ctx: ToolContext, location: Location) -> Location | None:
    current = location
    visited: set[int] = set()
    while current and current.id not in visited:
        visited.add(current.id)
        if current.type == "box":
            return current
        current = ctx.db.get(Location, current.parent_id) if current.parent_id else None
    return None


def find_material_locations(ctx: ToolContext, args: MaterialIdArgs) -> dict:
    material = ctx.db.scalar(
        select(Material).where(
            Material.id == args.material_id,
            Material.is_deleted.is_(False),
        )
    )
    if not material:
        raise BusinessError("MATERIAL_NOT_FOUND", "物料不存在", 404)

    lot_quantity_total, minimum_lot_quantity = ctx.db.execute(
        select(
            func.coalesce(func.sum(InventoryLot.quantity), 0),
            func.min(InventoryLot.quantity),
        ).where(
            InventoryLot.material_id == material.id
        )
    ).one()
    lot_quantity_total = Decimal(lot_quantity_total or 0)
    has_negative_lot = (
        minimum_lot_quantity is not None and Decimal(minimum_lot_quantity) < 0
    )
    if material.quantity < 0 or has_negative_lot or lot_quantity_total > material.quantity:
        distribution_status = "inconsistent"
        unallocated_quantity = Decimal("0")
    elif lot_quantity_total < material.quantity:
        distribution_status = "partial"
        unallocated_quantity = material.quantity - lot_quantity_total
    elif lot_quantity_total == material.quantity:
        distribution_status = "complete"
        unallocated_quantity = Decimal("0")

    lot_rows = list(
        ctx.db.execute(
            select(InventoryLot, Location)
            .join(Location, Location.id == InventoryLot.location_id)
            .where(
                InventoryLot.material_id == material.id,
                InventoryLot.quantity > 0,
                Location.is_active.is_(True),
            )
            .order_by(Location.full_path)
        ).all()
    )
    items = []
    seen: set[int] = set()
    for lot, location in lot_rows:
        seen.add(location.id)
        items.append(
            location_dict(
                location,
                organizer=_organizer_for(ctx, location),
                quantity_at_location=str(lot.quantity),
                quantity_is_exact=True,
            )
        )

    if material.location_id and material.location_id not in seen:
        primary = ctx.db.get(Location, material.location_id)
        if primary:
            items.append(
                location_dict(
                    primary,
                    organizer=_organizer_for(ctx, primary),
                    quantity_at_location=None,
                    quantity_is_exact=False,
                )
            )
    return {
        "material_id": material.id,
        "code": material.code,
        "name": material.name,
        "mpn": material.mpn,
        "attributes": dict(material.attributes or {}),
        "locations": items,
        "count": len(items),
        "material_quantity": str(material.quantity),
        "reserved_quantity": str(material.reserved_quantity),
        "available_quantity": str(material.available_quantity),
        "unit": material.unit,
        "lot_quantity_total": str(lot_quantity_total),
        "unallocated_quantity": str(unallocated_quantity),
        "distribution_status": distribution_status,
        "provenance": material_provenance(material, locations={}),
    }
