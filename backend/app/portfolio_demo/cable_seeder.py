from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import BusinessError
from app.models import (
    Category,
    IdempotencyRecord,
    InventoryLot,
    Location,
    Material,
    StockMovement,
    User,
)
from app.portfolio_demo.cable_locations import (
    DRAWER_RACK_CODE,
    ensure_cable_location_layout,
)
from app.services.audit import add_audit
from app.services.inventory import InventoryService

DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "portfolio_demo_data"
    / "v2_3"
    / "portfolio_cables_v1.json"
)
DATASET_NAME = "portfolio_cables_v1"
SEED_REQUEST_ID = "portfolio-cables-v1"
CATEGORY_CODE = "CAT-13"


@dataclass
class CableSeedSummary:
    created_materials: int = 0
    reused_materials: int = 0
    created_locations: int = 0
    inbound_operations: int = 0
    replayed_inbound_operations: int = 0
    allocated_materials: int = 0
    replayed_allocations: int = 0
    inventory_quantity_added: int = 0


class PortfolioCableSeeder:
    """Seed sanitized Portfolio cables through the governed inventory path."""

    def __init__(self, db: Session, operator: User, config: Settings):
        self.db = db
        self.operator = operator
        self.config = config
        self.dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
        self._validate_dataset()
        self._mpn_counts = Counter(item["mpn"] for item in self.dataset["items"])

    def _validate_dataset(self) -> None:
        items = self.dataset.get("items") or []
        if self.dataset.get("dataset") != DATASET_NAME or len(items) != 80:
            raise RuntimeError("Portfolio Cable dataset identity/count mismatch")
        if sum(int(item["demo_inbound_quantity"]) for item in items) != 1186:
            raise RuntimeError("Portfolio Cable synthetic inbound total mismatch")
        if any(not item.get("portfolio_only") for item in items):
            raise RuntimeError("Portfolio Cable dataset contains a non-Portfolio item")

    def dry_run(self) -> dict[str, Any]:
        items = self.dataset["items"]
        return {
            "dataset": DATASET_NAME,
            "spec_count": len(items),
            "synthetic_inbound_quantity": sum(int(item["demo_inbound_quantity"]) for item in items),
            "high_confidence_count": sum(item["parse_confidence"] == "high" for item in items),
            "medium_confidence_count": sum(item["parse_confidence"] == "medium" for item in items),
            "will_use_inventory_service": True,
            "purchase_quantity_is_current_inventory": False,
            "private_source_metadata_persisted": False,
        }

    def seed(self) -> dict[str, Any]:
        if not self.config.portfolio_cable_seed_enabled:
            raise BusinessError(
                "PORTFOLIO_CABLE_SEED_DISABLED",
                "必须显式设置 PORTFOLIO_CABLE_SEED_ENABLED=true 才能写入线缆演示数据",
                409,
            )
        summary = CableSeedSummary()
        category = self._ensure_category()
        bins = self._ensure_locations(summary)

        definitions = sorted(self.dataset["items"], key=lambda item: item["suggested_code"])
        for index, definition in enumerate(definitions):
            location = bins[index]
            material, created = self._ensure_material(
                definition,
                category=category,
                location=location,
            )
            if created:
                summary.created_materials += 1
            else:
                summary.reused_materials += 1

            quantity = Decimal(str(definition["demo_inbound_quantity"]))
            inbound_key = f"portfolio-cables-v1-inbound-{definition['seed_key'].split(':')[-1]}"
            inbound_record = self.db.scalar(
                select(IdempotencyRecord.id).where(
                    IdempotencyRecord.user_id == self.operator.id,
                    IdempotencyRecord.endpoint == "inbound",
                    IdempotencyRecord.key == inbound_key,
                )
            )
            if not created and inbound_record is None:
                raise BusinessError(
                    "PORTFOLIO_CABLE_INBOUND_IDENTITY_MISSING",
                    "已有线缆缺少受控入库幂等记录，不会猜测或重写库存。",
                    409,
                    details={"code": material.code},
                )
            inbound = InventoryService(
                self.db,
                self.operator.id,
                SEED_REQUEST_ID,
            ).inbound(
                material.id,
                quantity,
                inbound_key,
                "线缆入库",
                "仓库入库；历史采购数量不作为当前库存依据。",
            )
            if inbound.get("idempotent_replay"):
                summary.replayed_inbound_operations += 1
            else:
                summary.inbound_operations += 1
                summary.inventory_quantity_added += int(quantity)
            self.db.refresh(material)
            if Decimal(material.quantity) != quantity:
                raise BusinessError(
                    "PORTFOLIO_CABLE_STOCK_CONFLICT",
                    "线缆账面库存与固定合成入库数量不一致。",
                    409,
                    details={"code": material.code},
                )

            allocation_key = f"portfolio-cables-v1-location-{definition['seed_key'].split(':')[-1]}"
            allocation = InventoryService(
                self.db,
                self.operator.id,
                SEED_REQUEST_ID,
            ).initialize_location_allocations(
                material.id,
                [{"location_id": location.id, "quantity": quantity}],
                allocation_key,
                "线缆库位分配",
            )
            if allocation.get("idempotent_replay") or allocation.get("already_initialized"):
                summary.replayed_allocations += 1
            else:
                summary.allocated_materials += 1

        self._verify_postconditions()
        result = asdict(summary)
        result.update(self.dry_run())
        result["actual_cable_quantity"] = str(
            self.db.scalar(
                select(func.coalesce(func.sum(Material.quantity), 0)).where(
                    Material.is_deleted.is_(False),
                    Material.attributes["portfolio_cable_dataset"].as_string() == DATASET_NAME,
                )
            )
        )
        return result

    def _ensure_category(self) -> Category:
        category = self.db.scalar(select(Category).where(Category.code == CATEGORY_CODE))
        if category is None:
            category = Category(name="线缆", code=CATEGORY_CODE, sort_order=13, is_active=True)
            self.db.add(category)
            self.db.commit()
            self.db.refresh(category)
        elif category.name != "线缆" or not category.is_active:
            raise BusinessError(
                "PORTFOLIO_CABLE_CATEGORY_CONFLICT",
                "线缆分类与 Portfolio v2.5 定义冲突。",
                409,
            )
        return category

    def _ensure_locations(self, summary: CableSeedSummary) -> list[Location]:
        layout = ensure_cable_location_layout(self.db)
        summary.created_locations += layout.created_locations
        if len(layout.drawers) != 100:
            raise RuntimeError("Portfolio Cable 100-drawer layout is incomplete")
        return layout.drawers

    def _ensure_material(
        self,
        definition: dict[str, Any],
        *,
        category: Category,
        location: Location,
    ) -> tuple[Material, bool]:
        seed_key = definition["seed_key"]
        material_mpn = self._material_mpn(definition)
        material = self.db.scalar(
            select(Material).where(
                Material.attributes["portfolio_cable_seed_key"].as_string() == seed_key
            )
        )
        if material is None:
            matches = list(
                self.db.scalars(
                    select(Material).where(
                        Material.is_deleted.is_(False),
                        (
                            (Material.code == definition["suggested_code"])
                            | (Material.mpn == material_mpn)
                        ),
                    )
                ).all()
            )
            if matches:
                raise BusinessError(
                    "PORTFOLIO_CABLE_IDENTITY_CONFLICT",
                    "现有物料与线缆编码或 MPN 重合；不会创建重复或自动覆盖。",
                    409,
                    details={"suggested_code": definition["suggested_code"]},
                )
            attributes = self._attributes(definition, location)
            material = Material(
                code=definition["suggested_code"],
                name=definition["name"],
                category_id=category.id,
                location_id=location.id,
                mpn=material_mpn,
                specification=self._specification(definition),
                package="线缆",
                unit="条",
                unit_price=Decimal("0"),
                safety_stock=Decimal("5"),
                target_stock=Decimal("20"),
                quantity=Decimal("0"),
                reserved_quantity=Decimal("0"),
                tags=["cable", definition["cable_kind"]],
                attributes=attributes,
                notes="",
                created_by_id=self.operator.id,
                updated_by_id=self.operator.id,
            )
            self.db.add(material)
            self.db.flush()
            add_audit(
                self.db,
                self.operator.id,
                "portfolio_cable.seed_create",
                "material",
                str(material.id),
                SEED_REQUEST_ID,
                after={
                    "code": material.code,
                    "seed_key": seed_key,
                    "synthetic_stock": True,
                },
            )
            self.db.commit()
            self.db.refresh(material)
            return material, True

        expected = self._attributes(definition, location)
        attributes = material.attributes or {}
        definition_keys = set(expected) - {"storage_location"}
        if (
            material.is_deleted
            or material.code != definition["suggested_code"]
            or material.mpn != material_mpn
            or any(attributes.get(key) != expected[key] for key in definition_keys)
        ):
            raise BusinessError(
                "PORTFOLIO_CABLE_DEFINITION_CONFLICT",
                "已有 Portfolio 线缆定义与固定数据集不一致，不会自动覆盖。",
                409,
                details={"code": material.code},
            )
        return material, False

    @staticmethod
    def _attributes(definition: dict[str, Any], location: Location) -> dict[str, Any]:
        return {
            "material_kind": "cable",
            "cable_custom_name": definition["name"],
            "cable_kind": definition["cable_kind"],
            "end_style": definition["end_style"],
            "connector_a": definition["connector_a"],
            "connector_b": definition["connector_b"],
            "connector_pitch_mm": definition["connector_pitch_mm"] or "",
            "direction": definition["direction"],
            "length_cm": definition["length_cm"],
            "pin_count": int(definition["pin_count"]),
            "pin_count_b": int(definition["pin_count_b"]),
            "pin_layout": definition["pin_layout"],
            # Descriptive fallback only. Operational truth always comes from InventoryLot.
            "storage_location": location.full_path.split(" / ", 1)[0],
            "portfolio_cable_dataset": DATASET_NAME,
            "portfolio_cable_seed_key": definition["seed_key"],
            "portfolio_only": True,
            "stock_is_synthetic": True,
            "purchase_quantity_is_inventory": False,
            "reference_unit_price": definition["reference_unit_price"],
            "metadata_confidence": definition["parse_confidence"],
            "technical_claims_allowed": definition["parse_confidence"] == "high",
            "catalog_mpn_hint": definition["mpn"],
            "normalization_warnings": definition["normalization_warnings"],
        }

    def _material_mpn(self, definition: dict[str, Any]) -> str:
        """Use exact MPN only when it identifies one normalized cable specification."""
        source_mpn = definition["mpn"]
        if (
            source_mpn
            and self._mpn_counts[source_mpn] == 1
            and definition["parse_confidence"] == "high"
        ):
            return source_mpn
        return f"PORTFOLIO-{definition['suggested_code']}"

    @staticmethod
    def _specification(definition: dict[str, Any]) -> str:
        parts = [definition["cable_kind"]]
        if definition["connector_pitch_mm"]:
            parts.append(f"{definition['connector_pitch_mm']} mm")
        if definition["pin_count_b"]:
            parts.append(f"{definition['pin_count']}→{definition['pin_count_b']} Pin")
        elif definition["pin_count"]:
            parts.append(f"{definition['pin_count']} Pin")
        parts.extend(
            [
                f"{definition['length_cm']} cm",
                definition["end_style"],
                definition["direction"],
            ]
        )
        return " · ".join(parts)

    def _verify_postconditions(self) -> None:
        materials = list(
            self.db.scalars(
                select(Material).where(
                    Material.is_deleted.is_(False),
                    Material.attributes["portfolio_cable_dataset"].as_string() == DATASET_NAME,
                )
            ).all()
        )
        if len(materials) != 80 or sum(
            (Decimal(item.quantity) for item in materials), Decimal("0")
        ) != Decimal("1186"):
            raise RuntimeError("Portfolio Cable material count/stock invariant failed")
        lot_total = self.db.scalar(
            select(func.coalesce(func.sum(InventoryLot.quantity), 0)).where(
                InventoryLot.material_id.in_([item.id for item in materials])
            )
        )
        if Decimal(lot_total or 0) != Decimal("1186"):
            raise RuntimeError("Portfolio Cable location allocation invariant failed")
        positive_locations = list(
            self.db.execute(
                select(InventoryLot, Location)
                .join(Location, Location.id == InventoryLot.location_id)
                .where(
                    InventoryLot.material_id.in_([item.id for item in materials]),
                    InventoryLot.quantity > 0,
                )
            ).all()
        )
        if len(positive_locations) != 80 or any(
            not location.code.startswith(f"{DRAWER_RACK_CODE}-")
            for _lot, location in positive_locations
        ):
            raise RuntimeError("Portfolio Cable stock is not mapped one-SKU-per-visible-drawer")
        movement_total = self.db.scalar(
            select(func.coalesce(func.sum(StockMovement.quantity_delta), 0)).where(
                StockMovement.material_id.in_([item.id for item in materials]),
                StockMovement.operation_type == "inbound",
            )
        )
        if Decimal(movement_total or 0) != Decimal("1186"):
            raise RuntimeError("Portfolio Cable InventoryService movement invariant failed")
