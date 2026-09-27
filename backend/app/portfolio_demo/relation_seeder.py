from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import BusinessError
from app.models import (
    ComponentRelation,
    Material,
    Product,
    ProductBomAlternate,
    ProductBomItem,
    ProductRevision,
    StockMovement,
    User,
)
from app.schemas.relations import ComponentRelationCreate, ProductBomAlternateCreate
from app.services.component_relations import (
    ComponentRelationReviewService,
    canonical_material_pair,
)

DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "portfolio_demo_data"
    / "v2_1"
    / "portfolio_component_relations_v1.json"
)
SEED_REQUEST_ID = "portfolio-component-relations-v1"


@dataclass
class RelationSeedSummary:
    created_relations: int = 0
    validated_relations: int = 0
    created_alternates: int = 0
    approved_alternates: int = 0
    unchanged_existing_rows: int = 0
    inventory_writes: int = 0
    write_sensitive_unchanged: bool = False


class PortfolioRelationSeeder:
    """Seed synthetic review knowledge without touching inventory or primary BOM rows."""

    def __init__(self, db: Session, operator: User, config: Settings):
        self.db = db
        self.operator = operator
        self.config = config
        self.dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
        self.service = ComponentRelationReviewService(db, operator, SEED_REQUEST_ID)

    def _material_by_code(self, code: str) -> Material:
        material = self.db.scalar(select(Material).where(Material.code == code))
        if material is None:
            raise BusinessError(
                "PORTFOLIO_RELATION_MATERIAL_MISSING",
                "器件关系数据引用了不存在的物料。",
                409,
                details={"code": code},
            )
        return material

    def _bom_item(self, definition: dict[str, Any]) -> ProductBomItem:
        row = self.db.execute(
            select(ProductBomItem)
            .join(ProductRevision, ProductRevision.id == ProductBomItem.product_revision_id)
            .join(Product, Product.id == ProductRevision.product_id)
            .join(Material, Material.id == ProductBomItem.material_id)
            .where(
                Product.code == definition["product_code"],
                ProductRevision.revision == definition["revision"],
                Material.code == definition["primary_material_code"],
            )
        ).scalar_one_or_none()
        if row is None:
            raise BusinessError(
                "PORTFOLIO_RELATION_BOM_POSITION_MISSING",
                "产品备选数据引用了不存在的产品 BOM 位。",
                409,
                details={
                    "product_code": definition["product_code"],
                    "revision": definition["revision"],
                    "primary_material_code": definition["primary_material_code"],
                },
            )
        return row

    def _write_sensitive_snapshot(self) -> dict[str, Any]:
        return {
            "materials": list(
                self.db.execute(
                    select(
                        Material.id,
                        Material.quantity,
                        Material.reserved_quantity,
                    ).order_by(Material.id)
                ).all()
            ),
            "stock_movements": int(self.db.scalar(select(func.count(StockMovement.id))) or 0),
            "bom_rows": list(
                self.db.execute(
                    select(
                        ProductBomItem.id,
                        ProductBomItem.material_id,
                        ProductBomItem.quantity_per_unit,
                    ).order_by(ProductBomItem.id)
                ).all()
            ),
            "revision_hashes": list(
                self.db.execute(
                    select(ProductRevision.id, ProductRevision.bom_hash).order_by(
                        ProductRevision.id
                    )
                ).all()
            ),
        }

    def dry_run(self) -> dict[str, Any]:
        material_codes = {
            item[key]
            for section in ("component_relations", "product_bom_alternates")
            for item in self.dataset[section]
            for key in (
                ("source_code", "target_code")
                if section == "component_relations"
                else ("primary_material_code", "alternate_material_code")
            )
        }
        existing = set(
            self.db.scalars(select(Material.code).where(Material.code.in_(material_codes))).all()
        )
        return {
            "dataset": self.dataset["dataset"],
            "disclaimer": self.dataset["disclaimer"],
            "relation_count": len(self.dataset["component_relations"]),
            "alternate_count": len(self.dataset["product_bom_alternates"]),
            "missing_material_codes": sorted(material_codes - existing),
            "will_change_inventory": False,
            "will_mutate_primary_bom": False,
        }

    def seed(self) -> dict[str, Any]:
        if not self.config.portfolio_relation_seed_enabled:
            raise BusinessError(
                "PORTFOLIO_RELATION_SEED_DISABLED",
                "必须显式设置 PORTFOLIO_RELATION_SEED_ENABLED=true。",
                409,
            )
        preview = self.dry_run()
        if preview["missing_material_codes"]:
            raise BusinessError(
                "PORTFOLIO_RELATION_MATERIAL_MISSING",
                "器件关系数据引用了不存在的物料。",
                409,
                details={"codes": preview["missing_material_codes"]},
            )
        before = self._write_sensitive_snapshot()
        summary = RelationSeedSummary()

        for definition in self.dataset["component_relations"]:
            source = self._material_by_code(definition["source_code"])
            target = self._material_by_code(definition["target_code"])
            source_id, target_id = canonical_material_pair(source.id, target.id)
            relation = self.db.scalar(
                select(ComponentRelation).where(
                    ComponentRelation.source_material_id == source_id,
                    ComponentRelation.target_material_id == target_id,
                    ComponentRelation.relation_type == definition["relation_type"],
                )
            )
            if relation is None:
                relation = self.service.create_relation(
                    ComponentRelationCreate(
                        source_material_id=source.id,
                        target_material_id=target.id,
                        relation_type=definition["relation_type"],
                        confidence_note="工程评审记录。",
                        evidence_summary=definition["evidence_summary"],
                        evidence_refs=definition["evidence_refs"],
                    )
                )
                summary.created_relations += 1
            else:
                summary.unchanged_existing_rows += 1
            expected_status = definition["status"]
            if relation.status == "candidate" and expected_status == "validated":
                relation = self.service.validate_relation(relation.id)
                summary.validated_relations += 1
            elif relation.status != expected_status:
                raise BusinessError(
                    "PORTFOLIO_RELATION_STATUS_CONFLICT",
                    "已有器件关系状态与供应数据不一致，不会自动覆盖。",
                    409,
                    details={"relation_id": relation.id},
                )

        for definition in self.dataset["product_bom_alternates"]:
            bom_item = self._bom_item(definition)
            alternate_material = self._material_by_code(definition["alternate_material_code"])
            alternate = self.db.scalar(
                select(ProductBomAlternate).where(
                    ProductBomAlternate.product_bom_item_id == bom_item.id,
                    ProductBomAlternate.alternate_material_id == alternate_material.id,
                )
            )
            if alternate is None:
                primary_id, alternate_id = canonical_material_pair(
                    bom_item.material_id,
                    alternate_material.id,
                )
                source_relation_id = self.db.scalar(
                    select(ComponentRelation.id).where(
                        ComponentRelation.source_material_id == primary_id,
                        ComponentRelation.target_material_id == alternate_id,
                        ComponentRelation.status == "validated",
                    )
                )
                alternate = self.service.create_alternate(
                    bom_item.id,
                    ProductBomAlternateCreate(
                        alternate_material_id=alternate_material.id,
                        priority=definition["priority"],
                        usage_condition=definition["usage_condition"],
                        engineering_note=definition["engineering_note"],
                        evidence_refs=definition["evidence_refs"],
                        source_component_relation_id=source_relation_id,
                    ),
                )
                summary.created_alternates += 1
            else:
                summary.unchanged_existing_rows += 1
            expected_status = definition["status"]
            if alternate.status == "candidate" and expected_status == "approved":
                alternate = self.service.approve_alternate(alternate.id)
                summary.approved_alternates += 1
            elif alternate.status != expected_status:
                raise BusinessError(
                    "PORTFOLIO_ALTERNATE_STATUS_CONFLICT",
                    "已有产品备选状态与供应数据不一致，不会自动覆盖。",
                    409,
                    details={"alternate_id": alternate.id},
                )

        after = self._write_sensitive_snapshot()
        if before != after:
            raise RuntimeError(
                "Relation seed changed inventory, primary BOM, or ProductRevision hash"
            )
        summary.write_sensitive_unchanged = True
        summary.inventory_writes = after["stock_movements"] - before["stock_movements"]
        return {
            "dataset": self.dataset["dataset"],
            "disclaimer": self.dataset["disclaimer"],
            **asdict(summary),
        }
