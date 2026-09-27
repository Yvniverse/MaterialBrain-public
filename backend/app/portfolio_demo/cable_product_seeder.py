from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import BusinessError
from app.models import Material, Product, ProductBomItem, ProductRevision, StockMovement, User
from app.services.audit import add_audit
from app.services.product_revisions import calculate_bom_hash, clone_revision, release_revision

DATA_DIR = Path(__file__).resolve().parents[2] / "portfolio_demo_data" / "v2_3"
PRODUCTS_PATH = DATA_DIR / "portfolio_cable_products_v1.json"
ADAPTER_PATH = DATA_DIR / "portfolio_product_io_adapter_v1.json"
SEED_REQUEST_ID = "portfolio-cable-products-v1"


@dataclass
class CableProductSeedSummary:
    created_materials: int = 0
    reused_materials: int = 0
    created_products: int = 0
    created_revisions: int = 0
    created_bom_items: int = 0
    released_revisions: int = 0
    unchanged_revisions: int = 0
    source_revisions_unchanged: bool = False
    inventory_unchanged: bool = False


class PortfolioCableProductSeeder:
    """Create anonymous cable-aware Products without mutating released source revisions."""

    def __init__(self, db: Session, operator: User, config: Settings):
        self.db = db
        self.operator = operator
        self.config = config
        self.dataset = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
        self.adapter = json.loads(ADAPTER_PATH.read_text(encoding="utf-8"))
        self._validate()

    def _validate(self) -> None:
        privacy = self.dataset.get("privacy") or {}
        if (
            self.dataset.get("dataset") != "portfolio_cable_products_v1"
            or privacy.get("source_spreadsheets_parsed") != 10
            or privacy.get("source_values_committed") is not False
            or self.adapter.get("dataset") != "portfolio_product_io_adapter_v1"
            or len(self.adapter.get("bom_items") or []) != 18
        ):
            raise RuntimeError("Portfolio cable Product dataset identity/privacy mismatch")

    def dry_run(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset["dataset"],
            "new_product_count": len(self.dataset["new_products"]),
            "released_revision_clone_count": len(self.dataset["released_revision_clones"]),
            "adapter_source_rows": len(self.adapter["bom_items"]),
            "private_source_values_committed": False,
            "will_change_inventory": False,
            "will_mutate_released_source_revisions": False,
            "automatic_cable_substitution": False,
            "quantity_semantics": self.dataset["quantity_semantics"],
        }

    def seed(self) -> dict[str, Any]:
        if not self.config.portfolio_cable_seed_enabled:
            raise BusinessError(
                "PORTFOLIO_CABLE_PRODUCT_SEED_DISABLED",
                "必须显式设置 PORTFOLIO_CABLE_SEED_ENABLED=true 才能写入线缆产品演示数据",
                409,
            )
        summary = CableProductSeedSummary()
        self._require_cables()
        inventory_before = self._inventory_snapshot()
        source_before = self._source_revision_snapshot()

        adapter_materials = self._ensure_adapter_materials(summary)
        for definition in self.dataset["new_products"]:
            bom = self._new_product_bom(definition, adapter_materials)
            self._ensure_product_revision(definition, bom, summary)
        for definition in self.dataset["released_revision_clones"]:
            self._ensure_clone(definition, summary)

        self.db.commit()
        summary.inventory_unchanged = inventory_before == self._inventory_snapshot()
        summary.source_revisions_unchanged = source_before == self._source_revision_snapshot()
        if not summary.inventory_unchanged or not summary.source_revisions_unchanged:
            raise RuntimeError("Cable Product seed changed inventory or a released source revision")
        result = asdict(summary)
        result.update(self.dry_run())
        return result

    def _require_cables(self) -> None:
        count = self.db.scalar(
            select(func.count(Material.id)).where(
                Material.is_deleted.is_(False),
                Material.attributes["portfolio_cable_dataset"].as_string() == "portfolio_cables_v1",
            )
        )
        if int(count or 0) != 80:
            raise BusinessError(
                "PORTFOLIO_CABLES_REQUIRED",
                "必须先完成 80 条 Portfolio 线缆的受控种子写入",
                409,
            )

    def _ensure_adapter_materials(
        self, summary: CableProductSeedSummary
    ) -> list[tuple[Material, Decimal]]:
        resolved: list[tuple[Material, Decimal]] = []
        for index, row in enumerate(self.adapter["bom_items"], 1):
            candidate = str(row.get("source_libref_candidate") or "").strip()
            exact_identity = (
                row.get("identity_policy") == "catalog_candidate"
                and not row.get("identity_conflict")
                and candidate
                and "," not in candidate
            )
            identity_matches = (
                list(
                    self.db.scalars(
                        select(Material)
                        .where(
                            Material.is_deleted.is_(False),
                            func.lower(Material.mpn) == candidate.casefold(),
                        )
                        .limit(2)
                    ).all()
                )
                if exact_identity
                else []
            )
            material = identity_matches[0] if len(identity_matches) == 1 else None
            if material is not None:
                summary.reused_materials += 1
            else:
                code = f"PORT-IO-BOM-{index:03d}"
                material = self.db.scalar(select(Material).where(Material.code == code))
                expected_mpn = candidate if exact_identity else f"PORTFOLIO-GENERIC-{index:03d}"
                if material is None:
                    material = Material(
                        code=code,
                        name=(candidate if exact_identity else f"合成规格物料 {index:02d}"),
                        mpn=expected_mpn,
                        specification=self._adapter_spec(row, exact_identity),
                        package=str(row.get("footprint") or ""),
                        unit="pcs",
                        unit_price=Decimal("0"),
                        safety_stock=Decimal("0"),
                        target_stock=Decimal("0"),
                        quantity=Decimal("0"),
                        reserved_quantity=Decimal("0"),
                        tags=["portfolio", "anonymized-product-bom"],
                        attributes={
                            "portfolio_dataset": "portfolio_product_io_adapter_v1",
                            "identity_policy": (
                                "catalog_candidate" if exact_identity else "generic_by_spec"
                            ),
                            "metadata_confidence": "high" if exact_identity else "medium",
                            "technical_claims_allowed": exact_identity,
                            "private_transaction_identity_retained": False,
                            "portfolio_demo": {
                                "dataset_version": "v2.3",
                                "source_ref": code,
                                "identity_source": "anonymized_structural_derivative",
                                "catalog_confidence": ("high" if exact_identity else "medium"),
                                "stock_is_synthetic": True,
                            },
                        },
                        notes="匿名化工程 BOM 派生项，不包含外部产品身份。",
                        created_by_id=self.operator.id,
                        updated_by_id=self.operator.id,
                    )
                    self.db.add(material)
                    self.db.flush()
                    summary.created_materials += 1
                    self._audit(
                        "portfolio_cable_product.material_create", material.id, {"code": code}
                    )
                elif material.mpn != expected_mpn or Decimal(material.quantity) != 0:
                    raise BusinessError(
                        "PORTFOLIO_ADAPTER_MATERIAL_CONFLICT",
                        "匿名适配板物料定义冲突，不会自动覆盖",
                        409,
                        details={"code": code},
                    )
                else:
                    summary.reused_materials += 1
            attributes = dict(material.attributes or {})
            if attributes.get(
                "portfolio_dataset"
            ) == "portfolio_product_io_adapter_v1" and not attributes.get("portfolio_demo"):
                attributes["portfolio_demo"] = {
                    "dataset_version": "v2.3",
                    "source_ref": material.code,
                    "identity_source": "anonymized_structural_derivative",
                    "catalog_confidence": "high" if exact_identity else "medium",
                    "stock_is_synthetic": True,
                }
                material.attributes = attributes
                material.updated_by_id = self.operator.id
            resolved.append((material, Decimal(str(row["quantity_per_unit"]))))
        return resolved

    @staticmethod
    def _adapter_spec(row: dict[str, Any], exact_identity: bool) -> str:
        if exact_identity:
            return str(row.get("description") or row.get("value_or_comment") or "")[:300]
        value = str(row.get("value_or_comment") or "合成规格")
        footprint = str(row.get("footprint") or "")
        return " · ".join(item for item in (value, footprint) if item)[:300]

    def _new_product_bom(
        self,
        definition: dict[str, Any],
        adapter_materials: list[tuple[Material, Decimal]],
    ) -> list[tuple[Material, Decimal]]:
        if definition.get("bom_source") == "portfolio_product_io_adapter_v1":
            rows = list(adapter_materials)
            rows.extend(self._resolve_code_bom(definition.get("additional_bom") or []))
            return self._aggregate(rows)
        return self._resolve_code_bom(definition.get("bom") or [])

    def _resolve_code_bom(self, rows: list[list[Any]]) -> list[tuple[Material, Decimal]]:
        result = []
        for code, quantity in rows:
            material = self.db.scalar(
                select(Material).where(
                    Material.code == code,
                    Material.is_deleted.is_(False),
                    Material.is_active.is_(True),
                )
            )
            if material is None:
                raise BusinessError(
                    "PORTFOLIO_CABLE_PRODUCT_MATERIAL_MISSING",
                    "线缆产品 BOM 引用了不存在或已停用的物料",
                    409,
                    details={"code": code},
                )
            result.append((material, Decimal(str(quantity))))
        return result

    @staticmethod
    def _aggregate(rows: list[tuple[Material, Decimal]]) -> list[tuple[Material, Decimal]]:
        quantities: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
        materials: dict[int, Material] = {}
        for material, quantity in rows:
            materials[material.id] = material
            quantities[material.id] += quantity
        return [(materials[key], quantities[key]) for key in sorted(materials)]

    def _ensure_product_revision(
        self,
        definition: dict[str, Any],
        bom: list[tuple[Material, Decimal]],
        summary: CableProductSeedSummary,
    ) -> None:
        product = self.db.scalar(select(Product).where(Product.code == definition["code"]))
        if product is None:
            product = Product(
                code=definition["code"],
                name=definition["name"],
                description=definition["description"],
                lifecycle_status="active",
            )
            self.db.add(product)
            self.db.flush()
            summary.created_products += 1
            self._audit(
                "portfolio_cable_product.product_create", product.id, {"code": product.code}
            )
        elif (product.name, product.description, product.lifecycle_status) != (
            definition["name"],
            definition["description"],
            "active",
        ):
            raise BusinessError(
                "PORTFOLIO_CABLE_PRODUCT_CONFLICT",
                "匿名线缆产品定义冲突，不会自动覆盖",
                409,
            )
        revision = self.db.scalar(
            select(ProductRevision).where(
                ProductRevision.product_id == product.id,
                ProductRevision.revision == definition["revision"],
            )
        )
        if revision is None:
            revision = ProductRevision(
                product_id=product.id,
                revision=definition["revision"],
                status="draft",
                is_default=False,
                notes="匿名化单台 BOM。",
            )
            self.db.add(revision)
            self.db.flush()
            summary.created_revisions += 1
            self._add_bom(revision, bom, summary)
            release_revision(
                self.db,
                revision.id,
                released_by_id=self.operator.id,
                make_default=True,
            )
            summary.released_revisions += 1
            return
        self._verify_revision(revision, bom)
        summary.unchanged_revisions += 1

    def _ensure_clone(
        self,
        definition: dict[str, Any],
        summary: CableProductSeedSummary,
    ) -> None:
        product = self.db.scalar(select(Product).where(Product.code == definition["product_code"]))
        if product is None:
            raise BusinessError("PORTFOLIO_PRODUCT_MISSING", "待扩展的 Portfolio 产品不存在", 409)
        source = self.db.scalar(
            select(ProductRevision).where(
                ProductRevision.product_id == product.id,
                ProductRevision.revision == definition["source_revision"],
                ProductRevision.status == "released",
            )
        )
        if source is None:
            raise BusinessError("PORTFOLIO_PRODUCT_REVISION_MISSING", "克隆源版本不存在", 409)
        expected = [
            (material, Decimal(item.quantity_per_unit))
            for item, material in self.db.execute(
                select(ProductBomItem, Material)
                .join(Material, Material.id == ProductBomItem.material_id)
                .where(ProductBomItem.product_revision_id == source.id)
            ).all()
        ]
        expected.extend(self._resolve_code_bom(definition["cables"]))
        expected = self._aggregate(expected)
        revision = self.db.scalar(
            select(ProductRevision).where(
                ProductRevision.product_id == product.id,
                ProductRevision.revision == definition["new_revision"],
            )
        )
        if revision is None:
            revision = clone_revision(
                self.db,
                source.id,
                new_revision=definition["new_revision"],
                notes="包含线缆项的新修订；原发布修订保持不变。",
            )
            summary.created_revisions += 1
            existing = {
                item.material_id: item
                for item in self.db.scalars(
                    select(ProductBomItem).where(ProductBomItem.product_revision_id == revision.id)
                ).all()
            }
            for material, quantity in expected:
                if material.id in existing:
                    existing[material.id].quantity_per_unit = quantity
            additions = [(item, quantity) for item, quantity in expected if item.id not in existing]
            self._add_bom(revision, additions, summary)
            release_revision(
                self.db,
                revision.id,
                released_by_id=self.operator.id,
                make_default=False,
            )
            summary.released_revisions += 1
            return
        self._verify_revision(revision, expected)
        summary.unchanged_revisions += 1

    def _add_bom(
        self,
        revision: ProductRevision,
        bom: list[tuple[Material, Decimal]],
        summary: CableProductSeedSummary,
    ) -> None:
        for material, quantity in bom:
            self.db.add(
                ProductBomItem(
                    product_revision_id=revision.id,
                    material_id=material.id,
                    quantity_per_unit=quantity,
                    notes="单台用量。",
                )
            )
            summary.created_bom_items += 1
        self.db.flush()

    def _verify_revision(
        self,
        revision: ProductRevision,
        expected: list[tuple[Material, Decimal]],
    ) -> None:
        items = list(
            self.db.scalars(
                select(ProductBomItem).where(ProductBomItem.product_revision_id == revision.id)
            ).all()
        )
        actual = {item.material_id: Decimal(item.quantity_per_unit) for item in items}
        wanted = {material.id: quantity for material, quantity in expected}
        if (
            revision.status != "released"
            or actual != wanted
            or revision.bom_hash != calculate_bom_hash(items)
        ):
            raise BusinessError(
                "PORTFOLIO_CABLE_PRODUCT_REVISION_CONFLICT",
                "已有线缆产品版本与固定发布快照不一致，不会自动覆盖",
                409,
            )

    def _source_revision_snapshot(self) -> list[tuple[Any, ...]]:
        scopes = {
            (item["product_code"], item["source_revision"])
            for item in self.dataset["released_revision_clones"]
        }
        rows = self.db.execute(
            select(
                Product.code,
                ProductRevision.revision,
                ProductRevision.status,
                ProductRevision.is_default,
                ProductRevision.bom_hash,
                ProductRevision.released_at,
                ProductRevision.released_by_id,
            )
            .join(ProductRevision, ProductRevision.product_id == Product.id)
            .where(ProductRevision.status == "released")
            .order_by(Product.code, ProductRevision.revision)
        ).all()
        return [tuple(row) for row in rows if (row[0], row[1]) in scopes]

    def _inventory_snapshot(self) -> tuple[str, str, int]:
        totals = self.db.execute(
            select(
                func.coalesce(func.sum(Material.quantity), 0),
                func.coalesce(func.sum(Material.reserved_quantity), 0),
            )
        ).one()
        movements = int(self.db.scalar(select(func.count()).select_from(StockMovement)) or 0)
        return str(totals[0]), str(totals[1]), movements

    def _audit(self, action: str, resource_id: int, after: dict[str, Any]) -> None:
        add_audit(
            self.db,
            self.operator.id,
            action,
            "portfolio_cable_product",
            str(resource_id),
            SEED_REQUEST_ID,
            after=after,
        )
