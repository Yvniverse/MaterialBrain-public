from sqlalchemy import select

from app.core.exceptions import BusinessError
from app.models import Material
from app.schemas.agent import LowStockArgs, MaterialIdArgs
from app.services.data_provenance import material_provenance

from .common import ToolContext


def get_inventory_availability(ctx: ToolContext, args: MaterialIdArgs) -> dict:
    material = ctx.db.scalar(
        select(Material).where(
            Material.id == args.material_id,
            Material.is_deleted.is_(False),
        )
    )
    if not material:
        raise BusinessError("MATERIAL_NOT_FOUND", "物料不存在", 404)
    return {
        "material_id": material.id,
        "code": material.code,
        "name": material.name,
        "mpn": material.mpn,
        "manufacturer": material.manufacturer,
        "unit": material.unit,
        "attributes": dict(material.attributes or {}),
        "quantity": str(material.quantity),
        "reserved_quantity": str(material.reserved_quantity),
        "available_quantity": str(material.available_quantity),
        "safety_stock": str(material.safety_stock),
        "target_stock": str(material.target_stock),
        "low_stock": material.available_quantity <= material.safety_stock,
        "provenance": material_provenance(material, inventory={}),
    }


def get_low_stock_materials(ctx: ToolContext, args: LowStockArgs) -> dict:
    materials = list(
        ctx.db.scalars(
            select(Material)
            .where(
                Material.is_deleted.is_(False),
                Material.is_active.is_(True),
                Material.safety_stock > 0,
                Material.quantity - Material.reserved_quantity < Material.safety_stock,
            )
            .order_by(
                (
                    Material.safety_stock
                    - (Material.quantity - Material.reserved_quantity)
                ).desc(),
                (Material.quantity - Material.reserved_quantity).asc(),
                Material.code,
            )
            .limit(args.limit)
        ).all()
    )
    return {
        "items": [
            {
                "material_id": material.id,
                "code": material.code,
                "name": material.name,
                "mpn": material.mpn,
                "manufacturer": material.manufacturer,
                "unit": material.unit,
                "quantity": str(material.quantity),
                "reserved_quantity": str(material.reserved_quantity),
                "available_quantity": str(material.available_quantity),
                "safety_stock": str(material.safety_stock),
                "target_stock": str(material.target_stock),
                "attributes": dict(material.attributes or {}),
                "provenance": material_provenance(material, inventory={}),
                "suggested_purchase": str(
                    max(0, material.target_stock - material.available_quantity)
                ),
            }
            for material in materials
        ],
        "count": len(materials),
        "threshold_semantics": "available_quantity < safety_stock; safety_stock > 0",
    }
