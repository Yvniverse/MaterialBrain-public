from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools.common import location_dict
from app.models import InventoryLot, Location, Material


class ReadOnlyPublicResultEnricher:
    """Add live card fields after orchestration without changing tool/model state."""

    def __init__(self, db: Session):
        self.db = db

    def enrich(self, entities: dict[str, Any]) -> dict[str, Any]:
        candidates = entities.get("material_candidates") or {}
        items = candidates.get("items") or []
        for item in items:
            material_id = item.get("id") or item.get("material_id")
            if not material_id:
                continue
            material = self.db.scalar(
                select(Material).where(
                    Material.id == int(material_id),
                    Material.is_deleted.is_(False),
                    Material.is_active.is_(True),
                )
            )
            if material is None:
                continue
            item.update(
                {
                    "quantity": str(material.quantity),
                    "reserved_quantity": str(material.reserved_quantity),
                    "available_quantity": str(material.available_quantity),
                    "safety_stock": str(material.safety_stock),
                    "low_stock": material.available_quantity <= material.safety_stock,
                    "attributes": dict(material.attributes or {}),
                    "location": self._actual_location(material.id),
                    "location_truth_source": "InventoryLot",
                }
            )
        return entities

    def _actual_location(self, material_id: int) -> dict[str, Any] | None:
        row = self.db.execute(
            select(InventoryLot, Location)
            .join(Location, Location.id == InventoryLot.location_id)
            .where(
                InventoryLot.material_id == material_id,
                InventoryLot.quantity > 0,
                Location.is_active.is_(True),
            )
            .order_by(Location.full_path)
            .limit(1)
        ).first()
        if row is None:
            return None
        lot, location = row
        return location_dict(
            location,
            quantity_at_location=str(lot.quantity),
            quantity_is_exact=True,
        )
