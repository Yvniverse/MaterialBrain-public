import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, require
from app.core.config import settings
from app.core.exceptions import BusinessError
from app.models import Category, IdempotencyRecord, InventoryLot, Location, Material
from app.schemas.domain import CableData, CableImportCommit, CableQuantityChange
from app.services.audit import add_audit
from app.services.cable_import import (
    analyze_cable_order_rows,
    cable_import_source_identity,
    cable_import_source_spec_identity,
    cable_import_spec_key,
)
from app.services.inventory import InventoryService

router = APIRouter(
    prefix="/cables",
    tags=["线缆"],
    dependencies=[Depends(require("material:view"))],
)

CABLE_CATEGORY_NAME = "线缆"
CABLE_CATEGORY_CODE = "CAT-13"
CABLE_KIND_LABELS = {
    "terminal": "端子线",
    "flat_flex": "FPC/FFC 软排线",
    "micro_coax": "FPC 极细同轴",
    "rf_coax": "IPEX 射频同轴",
}
END_STYLE_LABELS = {
    "double": "双头",
    "single": "单头",
    "single_tinned": "单头沾锡",
    "male_female_pair": "公母一套",
    "unspecified": "端头未注明",
}
DIRECTION_LABELS = {
    "same": "同向",
    "reverse": "反向",
    "unspecified": "方向未注明",
}


def decimal_text(value: Decimal | str | int) -> str:
    decimal = Decimal(str(value))
    text = format(decimal.normalize(), "f")
    return text if "." in text else f"{text}.0"


def optional_decimal_text(value: Decimal | str | int | None) -> str:
    if value in (None, ""):
        return ""
    return decimal_text(value)


def decimal_number(value: Decimal | str | int) -> int | float:
    """JSON-safe numeric presentation without truncating fractional quantities."""

    decimal = Decimal(str(value))
    return int(decimal) if decimal == decimal.to_integral_value() else float(decimal)


def cable_specification(
    connector_pitch_mm: Decimal | None,
    pin_count: int,
    length_cm: Decimal,
    direction: str,
    cable_kind: str = "terminal",
    end_style: str = "double",
    pin_count_b: int = 0,
    pin_layout: str = "",
) -> str:
    parts = [CABLE_KIND_LABELS.get(cable_kind, "线缆")]
    if connector_pitch_mm is not None:
        parts.append(f"{decimal_text(connector_pitch_mm)} mm")
    if pin_count_b > 0:
        parts.append(f"{pin_count}→{pin_count_b} Pin")
    elif pin_count > 0:
        parts.append(f"{pin_layout + ' · ' if pin_layout else ''}{pin_count} Pin")
    parts.append(f"{decimal_text(length_cm)} cm")
    parts.append(END_STYLE_LABELS.get(end_style, "端头未注明"))
    if direction != "unspecified":
        parts.append(DIRECTION_LABELS.get(direction, direction))
    return " · ".join(parts)


def generated_cable_name(
    connector_pitch_mm: Decimal | None,
    pin_count: int,
    length_cm: Decimal,
    direction: str,
    cable_kind: str = "terminal",
    end_style: str = "double",
    pin_count_b: int = 0,
) -> str:
    if (
        cable_kind == "terminal"
        and end_style == "double"
        and connector_pitch_mm is not None
        and pin_count > 0
        and direction in {"same", "reverse"}
    ):
        return (
            f"{decimal_text(connector_pitch_mm)}mm "
            f"{pin_count}Pin {decimal_text(length_cm)}cm "
            f"{DIRECTION_LABELS[direction]}线缆"
        )
    pitch = f"{decimal_text(connector_pitch_mm)}mm " if connector_pitch_mm else ""
    pins = (
        f"{pin_count}→{pin_count_b}Pin "
        if pin_count_b > 0
        else f"{pin_count}Pin "
        if pin_count > 0
        else ""
    )
    direction_text = f"{DIRECTION_LABELS[direction]} " if direction != "unspecified" else ""
    return (
        f"{pitch}{pins}{decimal_text(length_cm)}cm "
        f"{direction_text}{END_STYLE_LABELS.get(end_style, '')}"
        f"{CABLE_KIND_LABELS.get(cable_kind, '线缆')}"
    ).strip()


def cable_attributes(payload: CableData) -> dict:
    return {
        "material_kind": "cable",
        "cable_custom_name": payload.name,
        "cable_kind": payload.cable_kind,
        "end_style": payload.end_style,
        "connector_a": payload.connector_a,
        "connector_b": payload.connector_b,
        "connector_pitch_mm": optional_decimal_text(payload.connector_pitch_mm),
        "direction": payload.direction,
        "length_cm": decimal_text(payload.length_cm),
        "pin_count": payload.pin_count,
        "pin_count_b": payload.pin_count_b,
        "pin_layout": payload.pin_layout,
        "storage_location": payload.storage_location,
    }


def cable_location_map(db: DB, material_ids: list[int]) -> dict[int, list[dict]]:
    if not material_ids:
        return {}
    result: dict[int, list[dict]] = {}
    rows = db.execute(
        select(InventoryLot, Location)
        .join(Location, Location.id == InventoryLot.location_id)
        .where(
            InventoryLot.material_id.in_(material_ids),
            InventoryLot.quantity > 0,
            Location.is_active.is_(True),
        )
        .order_by(InventoryLot.material_id, Location.full_path)
    ).all()
    for lot, location in rows:
        result.setdefault(lot.material_id, []).append(
            {
                "location_id": location.id,
                "code": location.code,
                "name": location.name,
                "full_path": location.full_path,
                "warehouse": location.full_path.split(" / ", 1)[0],
                "quantity": decimal_number(lot.quantity),
            }
        )
    return result


def serialize_cable(material: Material, actual_locations: list[dict] | None = None) -> dict:
    attributes = material.attributes or {}
    connector_pitch_mm = optional_decimal_text(attributes.get("connector_pitch_mm"))
    length_cm = Decimal(str(attributes.get("length_cm", "0")))
    pin_count = int(attributes.get("pin_count", 0) or 0)
    actual_locations = actual_locations or []
    actual_quantity = sum(
        (Decimal(str(item["quantity"])) for item in actual_locations), Decimal("0")
    )
    if actual_quantity > Decimal(material.quantity):
        actual_location_status = "inconsistent"
    elif actual_quantity == Decimal(material.quantity) and Decimal(material.quantity) > 0:
        actual_location_status = "complete"
    elif actual_quantity > 0:
        actual_location_status = "partial"
    else:
        actual_location_status = "unallocated"
    return {
        "id": material.id,
        "code": material.code,
        "name": material.name,
        "custom_name": str(attributes.get("cable_custom_name", "")),
        "model": material.mpn,
        "cable_kind": str(attributes.get("cable_kind") or "terminal"),
        "end_style": str(attributes.get("end_style") or "double"),
        "connector_a": str(attributes.get("connector_a") or ""),
        "connector_b": str(attributes.get("connector_b") or ""),
        "connector_pitch_mm": connector_pitch_mm,
        "direction": str(attributes.get("direction") or "same"),
        "length_cm": decimal_text(length_cm),
        "pin_count": pin_count,
        "pin_count_b": int(attributes.get("pin_count_b", 0) or 0),
        "pin_layout": str(attributes.get("pin_layout") or ""),
        # DB precision stays Decimal; JSON stays numeric and preserves fractional quantities.
        "quantity": decimal_number(material.quantity),
        "reserved_quantity": decimal_number(material.reserved_quantity),
        "available_quantity": decimal_number(material.available_quantity),
        "unit_price": decimal_text(material.unit_price),
        "storage_location": str(attributes.get("storage_location", "")),
        "actual_locations": actual_locations,
        "actual_location_status": actual_location_status,
        "actual_location_quantity": decimal_number(actual_quantity),
        "notes": material.notes,
        "updated_at": material.updated_at.isoformat() if material.updated_at else "",
    }


def serialize_cable_with_locations(db: DB, material: Material) -> dict:
    locations = cable_location_map(db, [material.id])
    return serialize_cable(material, locations.get(material.id, []))


def cable_material(db: DB, material_id: int, *, lock: bool = False) -> Material:
    query = select(Material).where(
        Material.id == material_id,
        Material.is_deleted.is_(False),
        Material.attributes["material_kind"].as_string() == "cable",
    )
    if lock:
        query = query.with_for_update()
    material = db.scalar(query)
    if not material:
        raise BusinessError("CABLE_NOT_FOUND", "线缆记录不存在", 404)
    return material


def cable_category(db: DB) -> Category:
    category = db.scalar(select(Category).where(Category.code == CABLE_CATEGORY_CODE))
    if not category:
        category = db.scalar(select(Category).where(Category.name == CABLE_CATEGORY_NAME))
    if category:
        return category

    code = CABLE_CATEGORY_CODE
    if db.scalar(select(Category.id).where(Category.code == code)):
        code = f"CAT-CABLE-{uuid.uuid4().hex[:6].upper()}"
    next_order = (db.scalar(select(func.max(Category.sort_order))) or 0) + 1
    category = Category(
        name=CABLE_CATEGORY_NAME,
        code=code,
        sort_order=next_order,
        is_active=True,
    )
    db.add(category)
    db.flush()
    return category


def ensure_inventory_permission(user: CurrentUser) -> None:
    permissions = set(user.role.permissions or [])
    if "*" not in permissions and "inventory:operate" not in permissions:
        raise HTTPException(403, "修改线缆数量需要库存操作权限")


def _material_import_spec_key(material: Material) -> tuple:
    attributes = material.attributes or {}
    return cable_import_spec_key(
        {
            "model": material.mpn,
            "name": material.name,
            "cable_kind": str(attributes.get("cable_kind") or "terminal"),
            "end_style": str(attributes.get("end_style") or "double"),
            "connector_pitch_mm": optional_decimal_text(attributes.get("connector_pitch_mm")),
            "direction": str(attributes.get("direction") or "same"),
            "length_cm": decimal_text(attributes.get("length_cm", "0")),
            "pin_count": int(attributes.get("pin_count") or 0),
            "pin_count_b": int(attributes.get("pin_count_b") or 0),
            "pin_layout": str(attributes.get("pin_layout") or ""),
        }
    )


def _cable_import_materials(db: DB, *, lock: bool = False) -> list[Material]:
    query = select(Material).where(
        Material.is_deleted.is_(False),
        Material.attributes["material_kind"].as_string() == "cable",
    )
    if lock:
        query = query.with_for_update()
    return list(db.scalars(query).all())


def _stored_import_identities(materials: list[Material]) -> set[str]:
    identities: set[str] = set()
    for material in materials:
        attributes = material.attributes or {}
        fallback_variant = str(attributes.get("cable_import_raw_variant") or "")
        material_spec_key = _material_import_spec_key(material)
        for source in attributes.get("cable_import_sources", []):
            identity = str(source.get("identity") or "")
            if not identity:
                identity = cable_import_source_identity(
                    str(source.get("order_no") or ""),
                    str(source.get("product_url") or ""),
                    str(source.get("variant") or fallback_variant),
                )
            identities.add(identity)
            identities.add(
                str(source.get("spec_identity") or "")
                or cable_import_source_spec_identity(
                    str(source.get("order_no") or ""),
                    str(source.get("product_url") or ""),
                    material_spec_key,
                )
            )
    return identities


def _source_item_identity(item: dict, row: dict) -> str:
    return str(item.get("identity") or "") or cable_import_source_identity(
        str(item.get("order_no") or ""),
        str(item.get("product_url") or ""),
        str(item.get("variant") or row.get("raw_variant") or ""),
    )


def _source_item_identities(item: dict, row: dict) -> set[str]:
    return {
        _source_item_identity(item, row),
        str(item.get("spec_identity") or "")
        or cable_import_source_spec_identity(
            str(item.get("order_no") or ""),
            str(item.get("product_url") or ""),
            cable_import_spec_key(row),
        ),
    }


def _decorate_cable_import_preview(db: DB, preview: dict) -> dict:
    materials = _cable_import_materials(db)
    by_spec = {_material_import_spec_key(item): item for item in materials}
    imported_keys = {
        str(source_key)
        for material in materials
        for source_key in (material.attributes or {}).get(
            "cable_import_source_keys",
            [],
        )
    }
    imported_identities = _stored_import_identities(materials)
    existing_count = 0
    already_imported_count = 0
    for row in preview["rows"]:
        source_items = row.get("source_items", [])
        source_item_count = len(source_items)
        pending_items = [
            item
            for item in source_items
            if str(item.get("key") or "") not in imported_keys
            and not (_source_item_identities(item, row) & imported_identities)
        ]
        match = by_spec.get(cable_import_spec_key(row))
        if not pending_items:
            row["selected"] = False
            row["import_action"] = "skip"
            row["warnings"].append("这些订单明细已经导入过，本次不会重复增加库存")
            already_imported_count += 1
            continue

        if len(pending_items) < source_item_count:
            row["source_items"] = pending_items
            row["source_rows"] = sorted(int(item["source_row"]) for item in pending_items)
            row["source_line_count"] = len(pending_items)
            row["quantity"] = sum(int(item["quantity"]) for item in pending_items)
            priced_items = [item for item in pending_items if item.get("unit_price")]
            if priced_items:
                priced_quantity = sum(
                    Decimal(str(item["unit_price"])) * Decimal(item["quantity"])
                    for item in priced_items
                )
                priced_units = sum(Decimal(item["quantity"]) for item in priced_items)
                row["unit_price"] = decimal_text(
                    (priced_quantity / priced_units).quantize(Decimal("0.0001"))
                )
            row["warnings"].append("部分订单明细已导入，仅保留尚未导入的数量")

        if match:
            row["import_action"] = "increase"
            row["existing_cable_id"] = match.id
            row["existing_cable_code"] = match.code
            existing_count += 1
        else:
            row["import_action"] = "create"

    preview["summary"]["existing_specs"] = existing_count
    preview["summary"]["already_imported_specs"] = already_imported_count
    return preview


def _create_import_cable(
    db: DB,
    user: CurrentUser,
    category: Category,
    row: dict,
    attributes: dict,
) -> Material:
    material = Material(
        code=f"CBL-TEMP-{uuid.uuid4().hex.upper()}",
        name=row["name"],
        category_id=category.id,
        mpn=row["model"],
        specification=cable_specification(
            row["connector_pitch_mm"],
            row["pin_count"],
            row["length_cm"],
            row["direction"],
            row["cable_kind"],
            row["end_style"],
            row["pin_count_b"],
            row["pin_layout"],
        ),
        package=(
            f"{decimal_text(row['connector_pitch_mm'])} mm"
            if row["connector_pitch_mm"] is not None
            else CABLE_KIND_LABELS.get(row["cable_kind"], "线缆")
        ),
        unit="条",
        unit_price=Decimal(str(row.get("unit_price") or 0)),
        quantity=Decimal(0),
        reserved_quantity=Decimal(0),
        attributes=attributes,
        notes=row["notes"] or f"淘宝订单导入 · {row['shop'] or '线缆订单'}",
        created_by_id=user.id,
        updated_by_id=user.id,
    )
    db.add(material)
    db.flush()
    generated_code = f"CBL-{material.id:06d}"
    if db.scalar(
        select(Material.id).where(
            Material.code == generated_code,
            Material.id != material.id,
        )
    ):
        generated_code = f"{generated_code}-{uuid.uuid4().hex[:4].upper()}"
    material.code = generated_code
    return material


def commit_cable_import(
    payload: CableImportCommit,
    db: DB,
    user: CurrentUser,
    request_id: str,
) -> dict:
    cached = db.scalar(
        select(IdempotencyRecord.response).where(
            IdempotencyRecord.user_id == user.id,
            IdempotencyRecord.endpoint == "cable_import",
            IdempotencyRecord.key == payload.idempotency_key,
        )
    )
    if cached is not None:
        return {**cached, "idempotent_replay": True}

    materials = _cable_import_materials(db, lock=True)
    by_spec = {_material_import_spec_key(item): item for item in materials}
    imported_keys = {
        str(source_key)
        for material in materials
        for source_key in (material.attributes or {}).get(
            "cable_import_source_keys",
            [],
        )
    }
    imported_identities = _stored_import_identities(materials)
    category = cable_category(db)
    created = 0
    increased = 0
    skipped = 0
    quantity_added = 0
    cable_ids: list[int] = []
    inbound_entries: list[dict] = []

    for payload_row in payload.rows:
        row = payload_row.model_dump(mode="json")
        source_items = row["source_items"]
        new_source_items = [
            item
            for item in source_items
            if item["key"] not in imported_keys
            and not (_source_item_identities(item, row) & imported_identities)
        ]
        if not new_source_items:
            skipped += 1
            continue

        material = by_spec.get(cable_import_spec_key(row))
        all_sources_are_new = len(new_source_items) == len(source_items)
        import_quantity = (
            int(row["quantity"])
            if all_sources_are_new
            else sum(int(item["quantity"]) for item in new_source_items)
        )
        if import_quantity <= 0:
            skipped += 1
            continue

        source_records = [
            {
                "key": item["key"],
                "identity": _source_item_identity(item, row),
                "spec_identity": cable_import_source_spec_identity(
                    str(item.get("order_no") or ""),
                    str(item.get("product_url") or ""),
                    cable_import_spec_key(row),
                ),
                "item_id": item.get("item_id", ""),
                "variant": item.get("variant") or row["raw_variant"],
                "order_no": item["order_no"],
                "source_row": item["source_row"],
                "quantity": item["quantity"],
                "unit_price": item["unit_price"],
                "product_url": item["product_url"],
            }
            for item in new_source_items
        ]
        source_keys = [item["key"] for item in new_source_items]
        base_attributes = {
            "material_kind": "cable",
            "cable_custom_name": row["name"],
            "cable_kind": row["cable_kind"],
            "end_style": row["end_style"],
            "connector_a": row["connector_a"],
            "connector_b": row["connector_b"],
            "connector_pitch_mm": optional_decimal_text(row["connector_pitch_mm"]),
            "direction": row["direction"],
            "length_cm": decimal_text(row["length_cm"]),
            "pin_count": row["pin_count"],
            "pin_count_b": row["pin_count_b"],
            "pin_layout": row["pin_layout"],
            "storage_location": row["storage_location"],
            "cable_import_source_keys": source_keys,
            "cable_import_sources": source_records,
            "cable_import_shop": row["shop"],
            "cable_import_raw_product": row["raw_product_name"],
            "cable_import_raw_variant": row["raw_variant"],
        }

        if material is None:
            material = _create_import_cable(
                db,
                user,
                category,
                row,
                base_attributes,
            )
            by_spec[cable_import_spec_key(row)] = material
            created += 1
            audit_action = "cable.import_create"
        else:
            existing_attributes = material.attributes or {}
            material.attributes = {
                **existing_attributes,
                "cable_kind": existing_attributes.get("cable_kind") or row["cable_kind"],
                "end_style": existing_attributes.get("end_style") or row["end_style"],
                "connector_a": existing_attributes.get("connector_a") or row["connector_a"],
                "connector_b": existing_attributes.get("connector_b") or row["connector_b"],
                "pin_count_b": existing_attributes.get("pin_count_b") or row["pin_count_b"],
                "pin_layout": existing_attributes.get("pin_layout") or row["pin_layout"],
                "cable_import_source_keys": list(
                    dict.fromkeys(
                        [
                            *existing_attributes.get(
                                "cable_import_source_keys",
                                [],
                            ),
                            *source_keys,
                        ]
                    )
                ),
                "cable_import_sources": [
                    *existing_attributes.get("cable_import_sources", []),
                    *source_records,
                ],
            }
            if (
                not str(existing_attributes.get("storage_location") or "").strip()
                and row["storage_location"]
            ):
                material.attributes = {
                    **material.attributes,
                    "storage_location": row["storage_location"],
                }
            if Decimal(str(row.get("unit_price") or 0)) > 0:
                old_value = material.quantity * material.unit_price
                new_value = Decimal(import_quantity) * Decimal(str(row["unit_price"]))
                material.unit_price = (
                    (old_value + new_value) / (material.quantity + Decimal(import_quantity))
                ).quantize(Decimal("0.0001"))
            material.updated_by_id = user.id
            increased += 1
            audit_action = "cable.import_increase"

        for source_key in source_keys:
            imported_keys.add(source_key)
        for item in new_source_items:
            imported_identities.update(_source_item_identities(item, row))
        cable_ids.append(material.id)
        quantity_added += import_quantity
        inbound_entries.append(
            {
                "material_id": material.id,
                "quantity": import_quantity,
                "notes": (
                    f"{row['model'] or row['name']} · "
                    f"来源行 {', '.join(str(value) for value in row['source_rows'])}"
                ),
            }
        )
        add_audit(
            db,
            user.id,
            audit_action,
            "material",
            str(material.id),
            request_id,
            after={
                "code": material.code,
                "model": row["model"],
                "source_rows": row["source_rows"],
                "quantity_added": import_quantity,
            },
        )

    result = {
        "created": created,
        "increased": increased,
        "skipped": skipped,
        "imported_specs": created + increased,
        "quantity_added": quantity_added,
        "cable_ids": cable_ids,
    }
    return InventoryService(db, user.id, request_id).inbound_batch(
        inbound_entries,
        payload.idempotency_key,
        "淘宝订单线缆导入",
        result,
    )


@router.post(
    "/import/preview",
    dependencies=[Depends(require("import:manage"))],
)
async def preview_cable_import(
    db: DB,
    user: CurrentUser,
    file: UploadFile = File(...),
):
    del user
    content = await file.read()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise BusinessError("FILE_TOO_LARGE", "导入文件超过大小限制", 413)

    # Keep spreadsheet parsing shared with the existing XLS/XLSX/CSV importer.
    from app.api.v1.files import parse_upload

    raw_rows = parse_upload(content, file.filename or "")
    preview = analyze_cable_order_rows(raw_rows, file.filename or "")
    return _decorate_cable_import_preview(db, preview)


@router.post(
    "/import/commit",
    dependencies=[
        Depends(require("import:manage")),
        Depends(require("material:manage")),
    ],
)
def import_cables(
    payload: CableImportCommit,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    ensure_inventory_permission(user)
    return commit_cable_import(payload, db, user, request.state.request_id)


@router.get("")
def list_cables(
    db: DB,
    user: CurrentUser,
    q: str = "",
    connector_pitch_mm: Annotated[Decimal | None, Query(gt=0)] = None,
    direction: Annotated[
        str | None,
        Query(pattern="^(same|reverse|unspecified)$"),
    ] = None,
    cable_kind: Annotated[
        str | None,
        Query(pattern="^(terminal|flat_flex|micro_coax|rf_coax)$"),
    ] = None,
    end_style: Annotated[
        str | None,
        Query(pattern=("^(double|single|single_tinned|male_female_pair|unspecified)$")),
    ] = None,
    length_cm: Annotated[Decimal | None, Query(gt=0)] = None,
    pin_count: Annotated[int | None, Query(ge=1)] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    del user
    materials = db.scalars(
        select(Material)
        .where(
            Material.is_deleted.is_(False),
            Material.attributes["material_kind"].as_string() == "cable",
        )
        .order_by(Material.updated_at.desc(), Material.id.desc())
    ).all()
    locations = cable_location_map(db, [item.id for item in materials])
    all_items = [serialize_cable(item, locations.get(item.id, [])) for item in materials]
    facets = {
        "connector_pitches": sorted(
            {item["connector_pitch_mm"] for item in all_items if item["connector_pitch_mm"]},
            key=Decimal,
        ),
        "lengths": sorted({item["length_cm"] for item in all_items}, key=Decimal),
        "pin_counts": sorted({item["pin_count"] for item in all_items if item["pin_count"] > 0}),
        "cable_kinds": sorted({item["cable_kind"] for item in all_items}),
        "end_styles": sorted({item["end_style"] for item in all_items}),
    }

    filtered = all_items
    if q.strip():
        keyword = q.strip().lower()
        direction_keywords = {
            "same": "同向",
            "reverse": "反向",
            "unspecified": "方向未注明",
        }

        def matches(item: dict) -> bool:
            content = " ".join(
                [
                    item["code"],
                    item["name"],
                    item["custom_name"],
                    item["model"],
                    CABLE_KIND_LABELS.get(item["cable_kind"], ""),
                    END_STYLE_LABELS.get(item["end_style"], ""),
                    item["connector_a"],
                    item["connector_b"],
                    item["connector_pitch_mm"],
                    str(item["pin_count"]),
                    str(item["pin_count_b"]),
                    item["pin_layout"],
                    item["length_cm"],
                    direction_keywords.get(item["direction"], ""),
                    item["storage_location"],
                    " ".join(location["full_path"] for location in item["actual_locations"]),
                    item["notes"],
                ]
            ).lower()
            return keyword in content

        filtered = [item for item in filtered if matches(item)]
    if connector_pitch_mm is not None:
        filtered = [
            item
            for item in filtered
            if item["connector_pitch_mm"]
            and Decimal(item["connector_pitch_mm"]) == connector_pitch_mm
        ]
    if direction:
        filtered = [item for item in filtered if item["direction"] == direction]
    if cable_kind:
        filtered = [item for item in filtered if item["cable_kind"] == cable_kind]
    if end_style:
        filtered = [item for item in filtered if item["end_style"] == end_style]
    if length_cm is not None:
        filtered = [item for item in filtered if Decimal(item["length_cm"]) == length_cm]
    if pin_count is not None:
        filtered = [item for item in filtered if item["pin_count"] == pin_count]

    total = len(filtered)
    start = (page - 1) * page_size
    page_items = filtered[start : start + page_size]
    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "summary": {
            "quantity": decimal_number(
                sum((Decimal(item["quantity"]) for item in filtered), Decimal("0"))
            ),
            "available_quantity": decimal_number(
                sum(
                    (Decimal(item["available_quantity"]) for item in filtered),
                    Decimal("0"),
                )
            ),
            "in_stock_types": sum(Decimal(item["quantity"]) > 0 for item in filtered),
            "pitch_count": len(
                {item["connector_pitch_mm"] for item in filtered if item["connector_pitch_mm"]}
            ),
        },
        "facets": facets,
    }


@router.get("/{material_id}")
def get_cable(material_id: int, db: DB, user: CurrentUser):
    del user
    material = cable_material(db, material_id)
    return serialize_cable_with_locations(db, material)


@router.post(
    "",
    status_code=201,
    dependencies=[Depends(require("material:manage"))],
)
def create_cable(
    payload: CableData,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    if payload.quantity > 0:
        ensure_inventory_permission(user)
    category = cable_category(db)
    attributes = cable_attributes(payload)
    name = payload.name or generated_cable_name(
        payload.connector_pitch_mm,
        payload.pin_count,
        payload.length_cm,
        payload.direction,
        payload.cable_kind,
        payload.end_style,
        payload.pin_count_b,
    )
    material = Material(
        code=f"CBL-TEMP-{uuid.uuid4().hex.upper()}",
        name=name,
        category_id=category.id,
        mpn=payload.model,
        specification=cable_specification(
            payload.connector_pitch_mm,
            payload.pin_count,
            payload.length_cm,
            payload.direction,
            payload.cable_kind,
            payload.end_style,
            payload.pin_count_b,
            payload.pin_layout,
        ),
        package=(
            f"{decimal_text(payload.connector_pitch_mm)} mm"
            if payload.connector_pitch_mm is not None
            else CABLE_KIND_LABELS.get(payload.cable_kind, "线缆")
        ),
        unit="条",
        quantity=Decimal(0),
        reserved_quantity=Decimal(0),
        attributes=attributes,
        notes=payload.notes,
        created_by_id=user.id,
        updated_by_id=user.id,
    )
    db.add(material)
    db.flush()
    generated_code = f"CBL-{material.id:06d}"
    if db.scalar(
        select(Material.id).where(
            Material.code == generated_code,
            Material.id != material.id,
        )
    ):
        generated_code = f"{generated_code}-{uuid.uuid4().hex[:4].upper()}"
    material.code = generated_code
    add_audit(
        db,
        user.id,
        "cable.create",
        "material",
        str(material.id),
        request.state.request_id,
        after={
            "code": material.code,
            "specification": material.specification,
            "initial_quantity": payload.quantity,
        },
    )
    if payload.quantity > 0:
        InventoryService(db, user.id, request.state.request_id).inbound(
            material.id,
            Decimal(payload.quantity),
            payload.idempotency_key,
            "线缆建档入库",
        )
    else:
        db.commit()
    db.refresh(material)
    return serialize_cable_with_locations(db, material)


@router.put(
    "/{material_id}",
    dependencies=[Depends(require("material:manage"))],
)
def update_cable(
    material_id: int,
    payload: CableData,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    material = cable_material(db, material_id, lock=True)
    target_quantity = Decimal(payload.quantity)
    if target_quantity != material.quantity:
        ensure_inventory_permission(user)
    before = serialize_cable_with_locations(db, material)
    attributes = {**(material.attributes or {}), **cable_attributes(payload)}
    material.name = payload.name or generated_cable_name(
        payload.connector_pitch_mm,
        payload.pin_count,
        payload.length_cm,
        payload.direction,
        payload.cable_kind,
        payload.end_style,
        payload.pin_count_b,
    )
    material.mpn = payload.model
    material.specification = cable_specification(
        payload.connector_pitch_mm,
        payload.pin_count,
        payload.length_cm,
        payload.direction,
        payload.cable_kind,
        payload.end_style,
        payload.pin_count_b,
        payload.pin_layout,
    )
    material.package = (
        f"{decimal_text(payload.connector_pitch_mm)} mm"
        if payload.connector_pitch_mm is not None
        else CABLE_KIND_LABELS.get(payload.cable_kind, "线缆")
    )
    material.attributes = attributes
    material.notes = payload.notes
    material.updated_by_id = user.id
    add_audit(
        db,
        user.id,
        "cable.update",
        "material",
        str(material.id),
        request.state.request_id,
        before=before,
        after={
            **payload.model_dump(mode="json"),
            "quantity_changed": target_quantity != material.quantity,
        },
    )
    if target_quantity != material.quantity:
        InventoryService(db, user.id, request.state.request_id).adjust(
            material.id,
            target_quantity,
            payload.idempotency_key,
            "线缆管理数量调整",
        )
    else:
        db.commit()
    db.refresh(material)
    return serialize_cable_with_locations(db, material)


@router.post(
    "/{material_id}/quantity",
    dependencies=[Depends(require("inventory:operate"))],
)
def change_cable_quantity(
    material_id: int,
    payload: CableQuantityChange,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    material = cable_material(db, material_id)
    service = InventoryService(db, user.id, request.state.request_id)
    if payload.delta > 0:
        service.inbound(
            material.id,
            Decimal(payload.delta),
            payload.idempotency_key,
            "线缆管理快捷入库",
        )
    else:
        service.outbound(
            material.id,
            Decimal(abs(payload.delta)),
            payload.idempotency_key,
            "线缆管理快捷出库",
        )
    db.refresh(material)
    return serialize_cable_with_locations(db, material)


@router.delete(
    "/{material_id}",
    dependencies=[Depends(require("material:manage"))],
)
def delete_cable(
    material_id: int,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    material = cable_material(db, material_id, lock=True)
    if material.quantity != 0 or material.reserved_quantity != 0:
        raise BusinessError("CABLE_HAS_STOCK", "请先将该线缆库存调整为 0 后再删除")
    material.is_deleted = True
    material.is_active = False
    material.updated_by_id = user.id
    add_audit(
        db,
        user.id,
        "cable.delete",
        "material",
        str(material.id),
        request.state.request_id,
        before=serialize_cable_with_locations(db, material),
        after={"deleted": True},
    )
    db.commit()
    return {"message": "线缆记录已删除"}
