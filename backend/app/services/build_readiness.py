from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models import (
    Material,
    Product,
    ProductBomAlternate,
    ProductBomItem,
    ProductRevision,
    Project,
    ProjectReservation,
)


def _floor_units(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


class BuildReadinessService:
    """Deterministic, read-only arithmetic over a per-unit Product BOM."""

    def __init__(self, db: Session):
        self.db = db

    def analyze(
        self,
        product_revision_id: int,
        build_quantity: int,
        project_id: int | None = None,
    ) -> dict:
        if isinstance(build_quantity, bool) or build_quantity <= 0:
            raise BusinessError(
                "BUILD_QUANTITY_INVALID",
                "构建数量必须是大于 0 的整数",
                400,
            )

        revision = self.db.get(ProductRevision, product_revision_id)
        if revision is None or revision.status == "obsolete":
            raise BusinessError(
                "PRODUCT_REVISION_NOT_FOUND",
                "产品版本不存在或已停用",
                404,
            )
        product = self.db.get(Product, revision.product_id)
        if product is None or product.lifecycle_status == "archived":
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或已归档", 404)

        project = None
        reservation_by_material: dict[int, Decimal] = {}
        if project_id is not None:
            project = self.db.get(Project, project_id)
            if project is None:
                raise BusinessError("PROJECT_NOT_FOUND", "项目不存在", 404)
            if project.product_revision_id != revision.id:
                raise BusinessError(
                    "PROJECT_PRODUCT_REVISION_MISMATCH",
                    "所选项目没有明确关联当前产品版本，不能合并项目预留",
                    409,
                )
            reservation_by_material = {
                row.material_id: row.quantity
                for row in self.db.scalars(
                    select(ProjectReservation).where(ProjectReservation.project_id == project.id)
                ).all()
            }

        rows = list(
            self.db.execute(
                select(ProductBomItem, Material)
                .join(Material, Material.id == ProductBomItem.material_id)
                .where(ProductBomItem.product_revision_id == revision.id)
                .order_by(Material.code)
            ).all()
        )
        if not rows:
            raise BusinessError("PRODUCT_BOM_EMPTY", "该产品版本还没有单台 BOM", 409)

        approved_alternates: dict[int, list[dict]] = {
            bom_item.id: [] for bom_item, _material in rows
        }
        bom_item_ids = list(approved_alternates)
        if bom_item_ids:
            for alternate, material in self.db.execute(
                select(ProductBomAlternate, Material)
                .join(
                    Material,
                    Material.id == ProductBomAlternate.alternate_material_id,
                )
                .where(
                    ProductBomAlternate.product_bom_item_id.in_(bom_item_ids),
                    ProductBomAlternate.status == "approved",
                )
                .order_by(
                    ProductBomAlternate.product_bom_item_id,
                    ProductBomAlternate.priority,
                )
            ).all():
                approved_alternates[alternate.product_bom_item_id].append(
                    {
                        "alternate_id": alternate.id,
                        "material_id": material.id,
                        "code": material.code,
                        "name": material.name,
                        "mpn": material.mpn,
                        "available_quantity": str(material.available_quantity),
                        "unit": material.unit,
                        "usage_condition": alternate.usage_condition,
                        "scope": "product_revision_bom_position",
                        "informational_only": True,
                    }
                )

        quantity = Decimal(build_quantity)
        items: list[dict] = []
        max_buildable_units: int | None = None
        for bom_item, material in rows:
            quantity_per_unit = Decimal(bom_item.quantity_per_unit)
            actual_available_quantity = Decimal(material.available_quantity)
            material_available_for_build = material.is_active and not material.is_deleted
            material_blocker = None
            if material.is_deleted:
                material_blocker = "deleted"
            elif not material.is_active:
                material_blocker = "inactive"
            available_quantity = (
                actual_available_quantity if material_available_for_build else Decimal("0")
            )
            reserved_for_project = (
                Decimal(reservation_by_material.get(material.id, Decimal("0")))
                if material_available_for_build
                else Decimal("0")
            )
            coverage = available_quantity + reserved_for_project
            required_total = quantity_per_unit * quantity
            shortage = max(Decimal("0"), required_total - coverage)
            additional_reservation_required = max(
                Decimal("0"),
                required_total - reserved_for_project,
            )
            projected_free_available_after_build = max(
                Decimal("0"),
                available_quantity - additional_reservation_required,
            )
            buildable = (
                _floor_units(coverage / quantity_per_unit) if material_available_for_build else 0
            )
            max_buildable_units = (
                buildable if max_buildable_units is None else min(max_buildable_units, buildable)
            )
            items.append(
                {
                    "material_id": material.id,
                    "code": material.code,
                    "name": material.name,
                    "mpn": material.mpn,
                    "unit": material.unit,
                    "quantity_per_unit": str(quantity_per_unit),
                    "required_total": str(required_total),
                    "available_quantity": str(available_quantity),
                    "actual_available_quantity": str(actual_available_quantity),
                    "reserved_for_project": str(reserved_for_project),
                    "coverage": str(coverage),
                    "shortage": str(shortage),
                    "additional_reservation_required": str(additional_reservation_required),
                    "projected_free_available_after_build": str(
                        projected_free_available_after_build
                    ),
                    "remaining_after_build": str(projected_free_available_after_build),
                    "safety_stock": str(material.safety_stock),
                    "below_safety_after_build": (
                        projected_free_available_after_build < Decimal(material.safety_stock)
                    ),
                    "material_available_for_build": material_available_for_build,
                    "material_blocker": material_blocker,
                    "approved_alternates": approved_alternates[bom_item.id],
                }
            )

        shortage_count = sum(Decimal(item["shortage"]) > 0 for item in items)
        return {
            "product": {
                "id": product.id,
                "code": product.code,
                "name": product.name,
            },
            "revision": {
                "id": revision.id,
                "revision": revision.revision,
                "status": revision.status,
                "is_default": revision.is_default,
            },
            "project": (
                {"id": project.id, "code": project.code, "name": project.name} if project else None
            ),
            "build_quantity": build_quantity,
            "sufficient": shortage_count == 0,
            "shortage_count": shortage_count,
            "max_buildable_units": max_buildable_units or 0,
            "safety_risk_count": sum(bool(item["below_safety_after_build"]) for item in items),
            "material_blocker_count": sum(item["material_blocker"] is not None for item in items),
            "items": items,
            "quantity_semantics": "Product BOM quantity_per_unit × build_quantity",
            "read_only": True,
        }
