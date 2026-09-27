from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.models import BomItem, InventoryLot, Location, Material, Project, User
from app.schemas.agent import AgentSuggestionItem, AgentSuggestionsResponse


class WarehouseAgentSuggestionService:
    """Build stable question-only suggestions from current database identifiers."""

    def __init__(
        self,
        db: Session,
        user: User,
        *,
        config: Settings = settings,
        now: datetime | None = None,
    ):
        self.db = db
        self.user = user
        self.config = config
        self.now = now or datetime.now(ZoneInfo(config.business_timezone))

    def _can(self, permission: str) -> bool:
        granted = set(self.user.role.permissions or [])
        return "*" in granted or permission in granted

    def _stable_pick(
        self,
        materials: list[Material],
        purpose: str,
        *,
        excluded_ids: set[int] | None = None,
    ) -> Material | None:
        excluded_ids = excluded_ids or set()
        candidates = [item for item in materials if item.id not in excluded_ids]
        if not candidates:
            return None
        day = self.now.date().isoformat()
        return min(
            candidates,
            key=lambda item: hashlib.sha256(f"{day}:{purpose}:{item.id}".encode()).digest(),
        )

    def build(self) -> AgentSuggestionsResponse:
        materials = list(
            self.db.scalars(
                select(Material)
                .where(
                    Material.is_active.is_(True),
                    Material.is_deleted.is_(False),
                )
                .order_by(Material.id)
            ).all()
        )
        mpn_counts = Counter(item.mpn.strip().casefold() for item in materials if item.mpn.strip())
        unique_mpn = [
            item
            for item in materials
            if self._natural_identity(item)
            and item.mpn.strip()
            and mpn_counts[item.mpn.strip().casefold()] == 1
        ]
        actually_located_material_ids = set(
            self.db.scalars(
                select(InventoryLot.material_id)
                .join(Location, Location.id == InventoryLot.location_id)
                .where(InventoryLot.quantity > 0, Location.is_active.is_(True))
            ).all()
        )
        located = [item for item in unique_mpn if item.id in actually_located_material_ids]

        items: list[AgentSuggestionItem] = []
        used_material_ids: set[int] = set()
        location_material = self._stable_pick(located, "location")
        if location_material:
            used_material_ids.add(location_material.id)
            items.append(
                AgentSuggestionItem(
                    type="material_location",
                    text=f"{self._natural_identity(location_material)} 在哪里？",
                    material_id=location_material.id,
                )
            )

        inventory_material = self._stable_pick(
            unique_mpn, "inventory", excluded_ids=used_material_ids
        ) or self._stable_pick(unique_mpn, "inventory")
        if inventory_material:
            used_material_ids.add(inventory_material.id)
            items.append(
                AgentSuggestionItem(
                    type="material_inventory",
                    text=f"{self._natural_identity(inventory_material)} 现在还能用多少？",
                    material_id=inventory_material.id,
                )
            )

        items.append(AgentSuggestionItem(type="low_stock", text="哪些物料低于安全库存？"))

        if self._can("project:view") or self._can("project:manage"):
            projects = list(
                self.db.scalars(
                    select(Project)
                    .where(
                        Project.code != "",
                        select(BomItem.id).where(BomItem.project_id == Project.id).exists(),
                    )
                    .order_by(Project.id)
                ).all()
            )
            if projects:
                day = self.now.date().isoformat()
                project = min(
                    projects,
                    key=lambda item: hashlib.sha256(
                        f"{day}:project-bom:{item.id}".encode()
                    ).digest(),
                )
                items.append(
                    AgentSuggestionItem(
                        type="project_bom",
                        text=f"{project.code} 的 BOM 库存够不够？",
                        project_id=project.id,
                    )
                )

        search_material = self._stable_pick(
            materials, "material-search", excluded_ids=used_material_ids
        )
        if search_material:
            identity = self._natural_identity(search_material)
            items.append(
                AgentSuggestionItem(
                    type="material_search",
                    text=f"帮我找 {identity}",
                    material_id=search_material.id,
                )
            )

        return AgentSuggestionsResponse(
            items=items[:5],
            generated_at=self.now,
            source="database",
        )

    @staticmethod
    def _natural_identity(material: Material) -> str:
        """Prefer a business identifier and avoid generated Portfolio row IDs."""

        candidates = [
            material.mpn.strip(),
            str((material.attributes or {}).get("catalog_mpn_hint") or "").strip(),
            material.name.strip(),
        ]
        for value in candidates:
            folded = value.casefold()
            if value and not folded.startswith(("portfolio-", "port-", "cbl-pf-")):
                return value
        return material.code
