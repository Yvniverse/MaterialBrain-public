from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import BusinessError
from app.models import (
    AgentActionProposal,
    Material,
    Product,
    ProductBomItem,
    ProductRevision,
    Project,
    ProjectReservation,
    StockMovement,
    User,
)
from app.services.audit import add_audit
from app.services.product_revisions import (
    calculate_bom_hash,
    release_revision,
)

DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "portfolio_demo_data"
    / "v2_1"
    / "portfolio_products_v1.json"
)
SEED_REQUEST_ID = "portfolio-product-demo-v1"


@dataclass
class ProductSeedSummary:
    created_products: int = 0
    created_revisions: int = 0
    created_bom_items: int = 0
    linked_projects: int = 0
    resolved_materials: int = 0
    unchanged_existing_rows: int = 0
    write_sensitive_unchanged: bool = False


class PortfolioProductSeeder:
    """Add only the supplied Product domain; never create stock or Materials."""

    def __init__(self, db: Session, operator: User, config: Settings):
        self.db = db
        self.operator = operator
        self.config = config
        self.dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    def dry_run(self) -> dict[str, Any]:
        material_codes = {
            code
            for product in self.dataset["products"]
            for revision in product["revisions"]
            for code, _quantity in revision["bom"]
        }
        existing_materials = set(
            self.db.scalars(select(Material.code).where(Material.code.in_(material_codes))).all()
        )
        project_codes = {
            product["link_project"]["project_code"]
            for product in self.dataset["products"]
            if product.get("link_project")
        }
        existing_projects = set(
            self.db.scalars(select(Project.code).where(Project.code.in_(project_codes))).all()
        )
        return {
            "dataset": self.dataset["dataset"],
            "product_count": len(self.dataset["products"]),
            "revision_count": sum(
                len(product["revisions"]) for product in self.dataset["products"]
            ),
            "bom_row_count": sum(
                len(revision["bom"])
                for product in self.dataset["products"]
                for revision in product["revisions"]
            ),
            "material_identity_count": len(material_codes),
            "missing_material_codes": sorted(material_codes - existing_materials),
            "missing_project_codes": sorted(project_codes - existing_projects),
            "quantity_semantics": self.dataset["semantic_rule"],
            "will_create_materials": False,
            "will_change_inventory": False,
        }

    def seed(self) -> dict[str, Any]:
        if not self.config.portfolio_product_seed_enabled:
            raise BusinessError(
                "PORTFOLIO_PRODUCT_SEED_DISABLED",
                "必须显式设置 PORTFOLIO_PRODUCT_SEED_ENABLED=true 才能写入产品演示数据",
                409,
            )
        preview = self.dry_run()
        if preview["missing_material_codes"]:
            raise BusinessError(
                "PORTFOLIO_PRODUCT_MATERIAL_MISSING",
                "产品单台 BOM 引用了不存在的物料编码",
                409,
                details={"codes": preview["missing_material_codes"]},
            )
        if preview["missing_project_codes"]:
            raise BusinessError(
                "PORTFOLIO_PRODUCT_PROJECT_MISSING",
                "产品数据引用了不存在的项目编码",
                409,
                details={"codes": preview["missing_project_codes"]},
            )

        before = self._write_sensitive_snapshot()
        summary = ProductSeedSummary()
        materials = {
            material.code: material for material in self.db.scalars(select(Material)).all()
        }
        projects = {
            project.code: project for project in self.db.scalars(select(Project)).all()
        }

        for definition in self.dataset["products"]:
            product = self.db.scalar(
                select(Product).where(Product.code == definition["code"])
            )
            if product is None:
                product = Product(
                    code=definition["code"],
                    name=definition["name"],
                    description=definition.get("description") or "",
                    lifecycle_status="active",
                )
                self.db.add(product)
                self.db.flush()
                summary.created_products += 1
                self._audit("product.seed_create", "product", product.id, after={
                    "code": product.code,
                    "name": product.name,
                })
            else:
                expected = (
                    definition["name"],
                    definition.get("description") or "",
                    "active",
                )
                actual = (product.name, product.description, product.lifecycle_status)
                if actual != expected:
                    raise BusinessError(
                        "PORTFOLIO_PRODUCT_CONFLICT",
                        f"产品 {product.code} 已存在但定义不一致，不会自动覆盖",
                        409,
                    )
                summary.unchanged_existing_rows += 1

            revisions_by_name = {
                revision.revision: revision
                for revision in self.db.scalars(
                    select(ProductRevision).where(
                        ProductRevision.product_id == product.id
                    )
                ).all()
            }
            for revision_definition in definition["revisions"]:
                revision_name = revision_definition["revision"]
                revision = revisions_by_name.get(revision_name)
                expected_status = revision_definition["status"]
                expected_default = bool(revision_definition["is_default"])
                if revision is None:
                    revision = ProductRevision(
                        product_id=product.id,
                        revision=revision_name,
                        status="draft",
                        is_default=False,
                        notes="Portfolio Product Demo v1; quantity_per_unit is per finished unit.",
                    )
                    self.db.add(revision)
                    self.db.flush()
                    revisions_by_name[revision_name] = revision
                    summary.created_revisions += 1
                    self._audit(
                        "product_revision.seed_create",
                        "product_revision",
                        revision.id,
                        after={
                            "product_code": product.code,
                            "revision": revision.revision,
                            "status": "draft",
                            "is_default": False,
                        },
                    )
                elif revision.status not in {"draft", expected_status}:
                    raise BusinessError(
                        "PORTFOLIO_PRODUCT_REVISION_CONFLICT",
                        f"产品 {product.code} 的版本 {revision_name} 定义不一致，不会自动覆盖",
                        409,
                    )
                else:
                    summary.unchanged_existing_rows += 1

                existing_bom = {
                    item.material_id: item
                    for item in self.db.scalars(
                        select(ProductBomItem).where(
                            ProductBomItem.product_revision_id == revision.id
                        )
                    ).all()
                }
                for material_code, raw_quantity in revision_definition["bom"]:
                    material = materials[material_code]
                    quantity_per_unit = Decimal(str(raw_quantity))
                    summary.resolved_materials += 1
                    item = existing_bom.get(material.id)
                    if item is None:
                        item = ProductBomItem(
                            product_revision_id=revision.id,
                            material_id=material.id,
                            quantity_per_unit=quantity_per_unit,
                            notes="Portfolio Product Demo v1 per-unit quantity.",
                        )
                        self.db.add(item)
                        self.db.flush()
                        summary.created_bom_items += 1
                        self._audit(
                            "product_bom.seed_create",
                            "product_bom_item",
                            item.id,
                            after={
                                "product_code": product.code,
                                "revision": revision.revision,
                                "material_code": material.code,
                                "quantity_per_unit": str(quantity_per_unit),
                            },
                        )
                    elif Decimal(item.quantity_per_unit) != quantity_per_unit:
                        raise BusinessError(
                            "PORTFOLIO_PRODUCT_BOM_CONFLICT",
                            "已有产品单台 BOM 数量与供应数据不一致，不会自动覆盖",
                            409,
                            details={
                                "product_code": product.code,
                                "revision": revision.revision,
                                "material_code": material.code,
                                "existing": str(item.quantity_per_unit),
                                "expected": str(quantity_per_unit),
                            },
                        )
                    else:
                        summary.unchanged_existing_rows += 1

                revision_items = list(
                    self.db.scalars(
                        select(ProductBomItem).where(
                            ProductBomItem.product_revision_id == revision.id
                        )
                    ).all()
                )
                if expected_status == "released" and revision.status == "draft":
                    release_revision(
                        self.db,
                        revision.id,
                        released_by_id=self.operator.id,
                        make_default=expected_default,
                    )
                    self._audit(
                        "product_revision.seed_release",
                        "product_revision",
                        revision.id,
                        after={
                            "product_code": product.code,
                            "revision": revision.revision,
                            "status": revision.status,
                            "is_default": revision.is_default,
                            "bom_hash": revision.bom_hash,
                        },
                    )
                elif expected_status == "released":
                    expected_hash = calculate_bom_hash(revision_items)
                    if (
                        revision.is_default != expected_default
                        or revision.bom_hash != expected_hash
                    ):
                        raise BusinessError(
                            "PORTFOLIO_PRODUCT_REVISION_CONFLICT",
                            f"产品 {product.code} 的版本 {revision_name} 发布快照不一致",
                            409,
                        )

            link = definition.get("link_project")
            if link:
                revision = revisions_by_name[link["revision"]]
                project = projects[link["project_code"]]
                if project.product_revision_id is None:
                    project.product_revision_id = revision.id
                    summary.linked_projects += 1
                    self._audit(
                        "project.product_revision_link",
                        "project",
                        project.id,
                        after={
                            "project_code": project.code,
                            "product_code": product.code,
                            "product_revision": revision.revision,
                        },
                    )
                elif project.product_revision_id != revision.id:
                    raise BusinessError(
                        "PORTFOLIO_PROJECT_PRODUCT_LINK_CONFLICT",
                        f"项目 {project.code} 已关联其他产品版本，不会自动覆盖",
                        409,
                    )
                else:
                    summary.unchanged_existing_rows += 1

        self.db.flush()
        after = self._write_sensitive_snapshot()
        summary.write_sensitive_unchanged = before == after
        if not summary.write_sensitive_unchanged:
            self.db.rollback()
            raise RuntimeError("Product seed changed inventory-sensitive database state")
        self.db.commit()
        result = asdict(summary)
        result.update(
            {
                "dataset": self.dataset["dataset"],
                "quantity_semantics": self.dataset["semantic_rule"],
                "created_materials": 0,
                "inventory_changes": 0,
            }
        )
        return result

    def _audit(
        self,
        action: str,
        resource_type: str,
        resource_id: int,
        *,
        after: dict[str, Any],
    ) -> None:
        add_audit(
            self.db,
            self.operator.id,
            action,
            resource_type,
            str(resource_id),
            SEED_REQUEST_ID,
            after=after,
        )

    def _write_sensitive_snapshot(self) -> dict[str, Any]:
        material_totals = self.db.execute(
            select(
                func.count(Material.id),
                func.coalesce(func.sum(Material.quantity), 0),
                func.coalesce(func.sum(Material.reserved_quantity), 0),
            )
        ).one()
        return {
            "materials": tuple(str(value) for value in material_totals),
            "project_reservations": int(
                self.db.scalar(select(func.count()).select_from(ProjectReservation)) or 0
            ),
            "stock_movements": int(
                self.db.scalar(select(func.count()).select_from(StockMovement)) or 0
            ),
            "agent_action_proposals": int(
                self.db.scalar(select(func.count()).select_from(AgentActionProposal)) or 0
            ),
        }
