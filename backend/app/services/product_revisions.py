import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models import Material, ProductBomItem, ProductRevision


def canonical_decimal(value: Decimal) -> str:
    normalized = Decimal(value).normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def canonical_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def calculate_bom_hash(items: list[ProductBomItem]) -> str:
    return canonical_hash(
        {
            "items": [
                {
                    "material_id": item.material_id,
                    "quantity_per_unit": canonical_decimal(item.quantity_per_unit),
                }
                for item in sorted(items, key=lambda row: row.material_id)
            ]
        }
    )


def require_revision_editable(revision: ProductRevision) -> None:
    if revision.status != "draft":
        raise BusinessError(
            "PRODUCT_REVISION_IMMUTABLE",
            "已发布或已停用的产品版本不可修改；请克隆为新的草稿版本",
            409,
        )


def release_revision(
    db: Session,
    revision_id: int,
    *,
    released_by_id: int,
    make_default: bool,
) -> ProductRevision:
    revision = db.scalar(
        select(ProductRevision)
        .where(ProductRevision.id == revision_id)
        .with_for_update()
    )
    if revision is None:
        raise BusinessError("PRODUCT_REVISION_NOT_FOUND", "产品版本不存在", 404)
    require_revision_editable(revision)

    db.execute(
        select(ProductRevision.id)
        .where(ProductRevision.product_id == revision.product_id)
        .with_for_update()
    ).all()
    items = list(
        db.scalars(
            select(ProductBomItem)
            .where(ProductBomItem.product_revision_id == revision.id)
            .order_by(ProductBomItem.material_id)
        ).all()
    )
    if not items:
        raise BusinessError(
            "PRODUCT_BOM_EMPTY",
            "空 BOM 的产品版本不能发布",
            409,
        )
    if any(Decimal(item.quantity_per_unit) <= 0 for item in items):
        raise BusinessError(
            "PRODUCT_BOM_QUANTITY_INVALID",
            "产品 BOM 中存在无效单台用量",
            409,
        )
    material_ids = {item.material_id for item in items}
    materials = list(
        db.scalars(select(Material).where(Material.id.in_(material_ids))).all()
    )
    existing_ids = {material.id for material in materials}
    missing = sorted(material_ids - existing_ids)
    if missing:
        raise BusinessError(
            "PRODUCT_BOM_MATERIAL_MISSING",
            "产品 BOM 引用了不存在的物料",
            409,
            details={"material_ids": missing},
        )
    unavailable = [
        material
        for material in materials
        if material.is_deleted or not material.is_active
    ]
    if unavailable:
        raise BusinessError(
            "PRODUCT_BOM_MATERIAL_UNAVAILABLE",
            "产品 BOM 包含已停用或删除的物料，不能发布。",
            409,
            details={
                "material_ids": [material.id for material in unavailable],
                "material_codes": [material.code for material in unavailable],
            },
        )

    if make_default:
        db.execute(
            update(ProductRevision)
            .where(
                ProductRevision.product_id == revision.product_id,
                ProductRevision.id != revision.id,
            )
            .values(is_default=False)
        )
    revision.status = "released"
    revision.is_default = make_default
    revision.bom_hash = calculate_bom_hash(items)
    revision.released_at = datetime.now(UTC)
    revision.released_by_id = released_by_id
    db.flush()
    return revision


def clone_revision(
    db: Session,
    source_revision_id: int,
    *,
    new_revision: str,
    notes: str | None = None,
) -> ProductRevision:
    source = db.scalar(
        select(ProductRevision)
        .where(ProductRevision.id == source_revision_id)
        .with_for_update()
    )
    if source is None:
        raise BusinessError("PRODUCT_REVISION_NOT_FOUND", "产品版本不存在", 404)
    if source.status != "released":
        raise BusinessError(
            "PRODUCT_REVISION_CLONE_SOURCE_INVALID",
            "只能从已发布版本克隆新的草稿",
            409,
        )
    if db.scalar(
        select(ProductRevision.id).where(
            ProductRevision.product_id == source.product_id,
            ProductRevision.revision == new_revision,
        )
    ):
        raise BusinessError("PRODUCT_REVISION_EXISTS", "产品版本已存在", 409)

    clone = ProductRevision(
        product_id=source.product_id,
        revision=new_revision,
        status="draft",
        is_default=False,
        notes=source.notes if notes is None else notes,
        released_at=None,
        bom_hash=None,
        released_by_id=None,
    )
    db.add(clone)
    db.flush()
    source_items = list(
        db.scalars(
            select(ProductBomItem)
            .where(ProductBomItem.product_revision_id == source.id)
            .order_by(ProductBomItem.material_id)
        ).all()
    )
    for item in source_items:
        db.add(
            ProductBomItem(
                product_revision_id=clone.id,
                material_id=item.material_id,
                quantity_per_unit=item.quantity_per_unit,
                notes=item.notes,
            )
        )
    db.flush()
    return clone
