"""Resolve engineering demand and real storage identity without inventory writes."""

import re
from decimal import Decimal

from sqlalchemy import select

from app.core.exceptions import BusinessError
from app.models import (
    InventoryLot,
    Location,
    Material,
    Product,
    ProductBomItem,
    ProductRevision,
    Project,
)
from app.services.embodied_navigation.service import world_snapshot


class SpatialBusinessGrounding:
    def __init__(self, db, user):
        self.db, self.user = db, user

    def resolve(
        self,
        message: str,
        snapshot: dict,
        *,
        selected_project_id=None,
        selected_product_revision_id=None,
    ) -> dict:
        permissions = set(self.user.role.permissions or [])
        if "*" not in permissions and not {"inventory:view", "project:view"}.issubset(permissions):
            raise BusinessError(
                "SPATIAL_BUSINESS_PERMISSION_REQUIRED", "备料空间查询需要库存与项目查看权限。", 403
            )
        text = message.casefold()
        world = world_snapshot()
        asset_goals = {}
        for goal in world["goals"]:
            asset_goals.setdefault(goal["asset_id"], goal["id"])
        references = {
            asset["reference_code"]: asset_goals.get(asset["id"])
            for asset in world["assets"]
            if asset.get("reference_code")
        }
        quantity_match = re.search(
            r"(?:做|生产|构建|备料|build)\s*(\d+)\s*(?:台|套|个|units)?", message, re.I
        )
        quantity = int(quantity_match[1]) if quantity_match else (1 if "单台" in text else None)
        projects = list(self.db.scalars(select(Project)))
        products = list(
            self.db.scalars(select(Product).where(Product.lifecycle_status == "active"))
        )
        matches = [
            project
            for project in projects
            if project.code.casefold() in text or project.name.casefold() in text
        ]
        product_matches = [
            product
            for product in products
            if product.code.casefold() in text or product.name.casefold() in text
        ]
        if len(matches) > 1 or len(product_matches) > 1:
            return self._clarify("项目或产品存在多个匹配，请指定完整编号。")
        project = (
            matches[0]
            if matches
            else self.db.get(Project, selected_project_id)
            if selected_project_id
            else None
        )
        revision_id = project.product_revision_id if project else selected_product_revision_id
        if product_matches:
            revisions = list(
                self.db.scalars(
                    select(ProductRevision).where(
                        ProductRevision.product_id == product_matches[0].id
                    )
                )
            )
            explicit = [revision for revision in revisions if revision.revision.casefold() in text]
            chosen = explicit or [revision for revision in revisions if revision.is_default]
            if len(chosen) != 1:
                return self._clarify("请指定产品的工程 BOM 版本。")
            revision_id = chosen[0].id
        requirements = []
        bom = None
        if revision_id:
            if not quantity or quantity > 100000:
                return self._clarify("请指定备料台数，例如“生产 3 台”；工程 BOM 按每台用量计算。")
            revision = self.db.get(ProductRevision, revision_id)
            if revision is None:
                return self._clarify("工程 BOM 版本已经变化，请重新选择。")
            rows = list(
                self.db.scalars(
                    select(ProductBomItem)
                    .where(ProductBomItem.product_revision_id == revision.id)
                    .order_by(ProductBomItem.id)
                )
            )
            requirements = [
                (self.db.get(Material, row.material_id), row.quantity_per_unit * quantity)
                for row in rows
            ]
            bom = {
                "product_revision_id": revision.id,
                "revision": revision.revision,
                "bom_hash": revision.bom_hash,
                "build_quantity": quantity,
                "source": "ProductRevision+ProductBomItem",
                "project_id": project.id if project else None,
            }
        else:
            materials = list(self.db.scalars(select(Material)))
            matched = [
                material
                for material in materials
                if any(
                    value
                    and re.search(
                        r"(?<![a-z0-9])" + re.escape(value.casefold()) + r"(?![a-z0-9])", text
                    )
                    for value in (material.code, material.mpn)
                )
            ]
            if len(matched) != 1:
                return self._clarify("请指定项目 / 产品版本与台数，或完整物料编号。")
            requirements = [(matched[0], Decimal(1))]
        if not requirements:
            return self._clarify("该版本没有工程 BOM 明细，请先维护工程设计数据。")
        locations = {location.id: location for location in self.db.scalars(select(Location))}
        inventories, stops, failures = [], [], []
        for material, required in requirements:
            available = material.available_quantity
            inventories.append(
                {
                    "material_id": material.id,
                    "code": material.code,
                    "required_quantity": str(required),
                    "available_quantity": str(available),
                    "stock_sufficient": available >= required,
                }
            )
            if available < required:
                failures.append({"code": "INSUFFICIENT_STOCK", "material_id": material.id})
            remaining = required
            lots = self.db.scalars(
                select(InventoryLot)
                .where(InventoryLot.material_id == material.id, InventoryLot.quantity > 0)
                .order_by(InventoryLot.id)
            )
            for lot in lots:
                if remaining <= 0:
                    break
                location = locations.get(lot.location_id)
                ancestor, visited, goal_id = location, set(), None
                while ancestor and ancestor.id not in visited:
                    visited.add(ancestor.id)
                    if references.get(ancestor.code):
                        goal_id = references[ancestor.code]
                        break
                    ancestor = locations.get(ancestor.parent_id)
                assigned = min(remaining, lot.quantity)
                if goal_id:
                    stops.append(
                        {
                            "goal_id": goal_id,
                            "location_id": location.id,
                            "location_code": location.code,
                            "material_id": material.id,
                            "material_code": material.code,
                            "quantity": str(assigned),
                            "mapping_source": "Location.parent_id+canonical_asset_reference",
                        }
                    )
                    remaining -= assigned
            if remaining > 0:
                failures.append(
                    {
                        "code": "UNMAPPED_OR_UNLOCATABLE_DEMAND",
                        "material_id": material.id,
                        "unlocated_quantity": str(remaining),
                    }
                )
        registered = {dock["id"] for dock in snapshot["docks"]}
        goals = list(dict.fromkeys(stop["goal_id"] for stop in stops))
        if not set(goals).issubset(registered):
            failures.append({"code": "UNREGISTERED_DOCK"})
        return {
            "status": "BLOCKED" if failures else "GROUNDED",
            "goal_ids": goals,
            "bom": bom,
            "inventory": inventories,
            "pick_locations": stops,
            "violations": failures,
            "inventory_written": False,
            "automatic_substitution": False,
            "payload_source": "canonical synthetic lab estimates; no measured material mass",
        }

    @staticmethod
    def _clarify(message):
        return {
            "status": "CLARIFICATION",
            "goal_ids": [],
            "clarification": message,
            "inventory_written": False,
        }
