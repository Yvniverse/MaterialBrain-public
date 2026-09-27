from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, func, or_, select

from app.api.deps import DB, CurrentUser, require
from app.core.exceptions import BusinessError
from app.models import Category, InventoryLot, Location, Material, StockMovement, Supplier
from app.schemas.domain import (
    BinContentUpdate,
    CategoryData,
    LocationData,
    OrganizerCreate,
    OrganizerLayoutUpdate,
    ShelfStorageBoxCreate,
    SupplierData,
)
from app.services.audit import add_audit
from app.services.location_organizers import (
    LocationOrganizerService,
    organizer_bin_note,
    organizer_module_slots,
    organizer_slot_names,
)

router = APIRouter(tags=["基础资料"])


def row_dict(obj, fields):
    return {field: getattr(obj, field) for field in fields}


LOCATION_FIELDS = [
    "id",
    "parent_id",
    "code",
    "name",
    "type",
    "full_path",
    "manager",
    "notes",
    "is_active",
    "organizer_style",
    "organizer_left_module",
    "organizer_right_module",
    "bin_material_name",
    "bin_quantity",
    "bin_content_notes",
]


def serialize_organizer(organizer: Location, bins: list[Location]) -> dict:
    return {
        "organizer": row_dict(organizer, LOCATION_FIELDS),
        "bins": [row_dict(item, LOCATION_FIELDS) for item in bins],
    }


def validate_category_parent(db: DB, item_id: int | None, parent_id: int | None) -> None:
    if parent_id is None:
        return
    if parent_id == item_id:
        raise BusinessError("CATEGORY_CYCLE", "分类不能以自身为上级")
    parent = db.get(Category, parent_id)
    if not parent:
        raise BusinessError("PARENT_CATEGORY_NOT_FOUND", "上级分类不存在")
    visited = set()
    while parent:
        if parent.id in visited or parent.id == item_id:
            raise BusinessError("CATEGORY_CYCLE", "上级分类不能选择当前分类的下级")
        visited.add(parent.id)
        parent = db.get(Category, parent.parent_id) if parent.parent_id else None


@router.get("/categories")
def categories(db: DB, user: CurrentUser):
    fields = [
        "id",
        "parent_id",
        "name",
        "code",
        "sort_order",
        "is_active",
        "created_at",
        "updated_at",
    ]
    return [
        row_dict(x, fields)
        for x in db.scalars(select(Category).order_by(Category.sort_order, Category.id)).all()
    ]


@router.post("/categories", status_code=201, dependencies=[Depends(require("category:manage"))])
def create_category(p: CategoryData, request: Request, db: DB, user: CurrentUser):
    if db.scalar(select(Category.id).where(Category.code == p.code)):
        raise BusinessError("CATEGORY_CODE_EXISTS", "分类编码已存在", 409)
    validate_category_parent(db, None, p.parent_id)
    item = Category(**p.model_dump())
    db.add(item)
    db.flush()
    add_audit(
        db,
        user.id,
        "category.create",
        "category",
        str(item.id),
        request.state.request_id,
        after=p.model_dump(mode="json"),
    )
    db.commit()
    return row_dict(item, ["id", *type(p).model_fields])


@router.put("/categories/{item_id}", dependencies=[Depends(require("category:manage"))])
def update_category(item_id: int, p: CategoryData, request: Request, db: DB, user: CurrentUser):
    item = db.get(Category, item_id)
    if not item:
        raise BusinessError("CATEGORY_NOT_FOUND", "分类不存在", 404)
    if db.scalar(select(Category.id).where(Category.code == p.code, Category.id != item_id)):
        raise BusinessError("CATEGORY_CODE_EXISTS", "分类编码已存在", 409)
    validate_category_parent(db, item_id, p.parent_id)
    for k, v in p.model_dump().items():
        setattr(item, k, v)
    add_audit(
        db,
        user.id,
        "category.update",
        "category",
        str(item.id),
        request.state.request_id,
        after=p.model_dump(mode="json"),
    )
    db.commit()
    return row_dict(item, ["id", *type(p).model_fields])


@router.delete("/categories/{item_id}", dependencies=[Depends(require("category:manage"))])
def delete_category(item_id: int, db: DB, user: CurrentUser):
    if db.scalar(select(Category.id).where(Category.parent_id == item_id)):
        raise BusinessError("CATEGORY_HAS_CHILDREN", "存在下级分类，不能删除")
    if db.scalar(select(Material.id).where(Material.category_id == item_id)):
        raise BusinessError("CATEGORY_IN_USE", "已被物料引用的分类不能删除")
    db.execute(delete(Category).where(Category.id == item_id))
    db.commit()
    return {"message": "已删除"}


@router.get("/locations")
def locations(db: DB, user: CurrentUser):
    material_stats = {
        row.location_id: row
        for row in db.execute(
            select(
                Material.location_id,
                func.count(Material.id).label("material_count"),
                func.coalesce(func.sum(Material.quantity), 0).label("quantity"),
                func.coalesce(func.sum(Material.reserved_quantity), 0).label("reserved_quantity"),
            )
            .where(Material.is_deleted.is_(False), Material.location_id.is_not(None))
            .group_by(Material.location_id)
        )
    }
    result = []
    for item in db.scalars(select(Location).order_by(Location.full_path)).all():
        stats = material_stats.get(item.id)
        result.append(
            {
                **row_dict(item, LOCATION_FIELDS),
                "material_count": stats.material_count if stats else 0,
                "quantity": str(stats.quantity if stats else Decimal("0")),
                "reserved_quantity": str(
                    stats.reserved_quantity if stats else Decimal("0")
                ),
            }
        )
    return result


def calculate_path(db: DB, p: LocationData) -> str:
    if not p.parent_id:
        return p.name
    parent = db.get(Location, p.parent_id)
    if not parent:
        raise BusinessError("PARENT_LOCATION_NOT_FOUND", "父库位不存在")
    return f"{parent.full_path} / {p.name}"


def ensure_location_code_available(db: DB, code: str, item_id: int | None = None) -> None:
    query = select(Location.id).where(Location.code == code)
    if item_id is not None:
        query = query.where(Location.id != item_id)
    if db.scalar(query):
        raise BusinessError("LOCATION_CODE_EXISTS", "库位编码已存在", 409)


def validate_location_parent(db: DB, item_id: int | None, parent_id: int | None) -> None:
    if parent_id is None:
        return
    if parent_id == item_id:
        raise BusinessError("LOCATION_CYCLE", "库位不能以自身为上级")
    parent = db.get(Location, parent_id)
    if not parent:
        raise BusinessError("PARENT_LOCATION_NOT_FOUND", "上级库位不存在", 404)
    visited = set()
    while parent:
        if parent.id in visited or parent.id == item_id:
            raise BusinessError("LOCATION_CYCLE", "上级库位不能选择当前库位的下级")
        visited.add(parent.id)
        parent = db.get(Location, parent.parent_id) if parent.parent_id else None


@router.post(
    "/locations/organizers",
    status_code=201,
    dependencies=[Depends(require("location:manage"))],
)
def create_organizer(p: OrganizerCreate, request: Request, db: DB, user: CurrentUser):
    """Create a fixed or configurable physical storage organizer."""
    validate_location_parent(db, None, p.parent_id)
    organizer, bins = LocationOrganizerService(db).create(
        parent_id=p.parent_id,
        code=p.code,
        name=p.name,
        organizer_style=p.organizer_style,
        organizer_left_module=p.organizer_left_module,
        organizer_right_module=p.organizer_right_module,
        manager=p.manager,
        notes=p.notes,
        is_active=p.is_active,
    )
    add_audit(
        db,
        user.id,
        "location.organizer.create",
        "location",
        str(organizer.id),
        request.state.request_id,
        after={
            "code": organizer.code,
            "name": organizer.name,
            "layout": p.organizer_style,
            "left_module": p.organizer_left_module,
            "right_module": p.organizer_right_module,
            "bin_count": len(bins),
        },
    )
    db.commit()
    return serialize_organizer(organizer, bins)


def get_shelf_rack_and_level(
    db: DB,
    rack_id: int,
    shelf_id: int,
) -> tuple[Location, Location]:
    rack = db.get(Location, rack_id)
    if not rack or rack.type != "box" or rack.organizer_style != "shelf_rack_6":
        raise BusinessError("SHELF_RACK_NOT_FOUND", "六层货架不存在", 404)
    shelf = db.get(Location, shelf_id)
    if not shelf or shelf.type != "shelf" or shelf.parent_id != rack.id:
        raise BusinessError("SHELF_LEVEL_NOT_FOUND", "货架层不存在", 404)
    return rack, shelf


def get_shelf_storage_box(db: DB, box_id: int) -> Location:
    box = db.get(Location, box_id)
    if not box or box.type != "container" or box.organizer_style != "shelf_storage_box":
        raise BusinessError("SHELF_STORAGE_BOX_NOT_FOUND", "层架箱子不存在", 404)
    return box


@router.post(
    "/locations/shelf-racks/{rack_id}/shelves/{shelf_id}/boxes",
    status_code=201,
    dependencies=[Depends(require("location:manage"))],
)
def create_shelf_storage_box(
    rack_id: int,
    shelf_id: int,
    p: ShelfStorageBoxCreate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    rack, shelf = get_shelf_rack_and_level(db, rack_id, shelf_id)
    existing_codes = set(
        db.scalars(select(Location.code).where(Location.parent_id == shelf.id)).all()
    )
    shelf_slot = shelf.code.rsplit("-", 1)[-1]
    code = ""
    for index in range(1, 100):
        candidate = f"{rack.code[:48]}-{shelf_slot}-B{index:02}"
        if candidate not in existing_codes:
            code = candidate
            break
    if not code:
        raise BusinessError("SHELF_BOX_LIMIT", "该层可创建的箱子数量已达到上限")
    ensure_location_code_available(db, code)
    box = Location(
        code=code,
        name=p.name,
        parent_id=shelf.id,
        type="container",
        full_path=f"{shelf.full_path} / {p.name}",
        manager=rack.manager,
        notes=p.notes,
        is_active=rack.is_active,
        organizer_style="shelf_storage_box",
    )
    db.add(box)
    db.flush()
    add_audit(
        db,
        user.id,
        "location.shelf_box.create",
        "location",
        str(box.id),
        request.state.request_id,
        after={
            "rack_id": rack.id,
            "shelf_id": shelf.id,
            "code": box.code,
            **p.model_dump(),
        },
    )
    db.commit()
    return row_dict(box, LOCATION_FIELDS)


@router.delete(
    "/locations/shelf-racks/{rack_id}/boxes/{box_id}",
    dependencies=[Depends(require("location:manage"))],
)
def delete_shelf_storage_box(
    rack_id: int,
    box_id: int,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    rack = db.get(Location, rack_id)
    box = get_shelf_storage_box(db, box_id)
    shelf = db.get(Location, box.parent_id) if box.parent_id else None
    if (
        not rack
        or rack.organizer_style != "shelf_rack_6"
        or not shelf
        or shelf.parent_id != rack.id
    ):
        raise BusinessError("SHELF_STORAGE_BOX_NOT_FOUND", "该箱子不属于当前货架", 404)
    has_child = db.scalar(select(Location.id).where(Location.parent_id == box.id).limit(1))
    has_material = db.scalar(
        select(Material.id).where(Material.location_id == box.id).limit(1)
    )
    has_lot = db.scalar(
        select(InventoryLot.id).where(InventoryLot.location_id == box.id).limit(1)
    )
    if has_child or has_material or has_lot:
        raise BusinessError(
            "SHELF_STORAGE_BOX_IN_USE",
            "箱内仍有物料，请先清空所有物料后再移除箱子",
        )
    movements = list(
        db.scalars(
            select(StockMovement).where(
                or_(
                    StockMovement.source_location_id == box.id,
                    StockMovement.target_location_id == box.id,
                )
            )
        ).all()
    )
    for movement in movements:
        if movement.source_location_id == box.id:
            movement.source_location_id = None
        if movement.target_location_id == box.id:
            movement.target_location_id = None
    add_audit(
        db,
        user.id,
        "location.shelf_box.delete",
        "location",
        str(box.id),
        request.state.request_id,
        before={"rack_id": rack.id, "shelf_id": shelf.id, "code": box.code, "name": box.name},
        after={"deleted": True},
    )
    db.delete(box)
    db.commit()
    return {"message": "箱子已从货架移除"}


@router.post(
    "/locations/shelf-boxes/{box_id}/items",
    status_code=201,
    dependencies=[Depends(require("location:manage"))],
)
def create_shelf_box_item(
    box_id: int,
    p: BinContentUpdate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    box = get_shelf_storage_box(db, box_id)
    existing_codes = set(
        db.scalars(select(Location.code).where(Location.parent_id == box.id)).all()
    )
    code = ""
    for index in range(1, 1000):
        candidate = f"SBX-{box.id}-I{index:03}"
        if candidate not in existing_codes:
            code = candidate
            break
    if not code:
        raise BusinessError("SHELF_BOX_ITEM_LIMIT", "该箱子的物料条目已达到上限")
    ensure_location_code_available(db, code)
    item = Location(
        code=code,
        name=p.material_name,
        parent_id=box.id,
        type="bin",
        full_path=f"{box.full_path} / {p.material_name}",
        manager=box.manager,
        notes="箱内物料条目",
        is_active=box.is_active,
        bin_material_name=p.material_name,
        bin_quantity=p.quantity,
        bin_content_notes=p.notes,
    )
    db.add(item)
    db.flush()
    add_audit(
        db,
        user.id,
        "location.shelf_box.item.create",
        "location",
        str(item.id),
        request.state.request_id,
        after={"box_id": box.id, **p.model_dump()},
    )
    db.commit()
    return row_dict(item, LOCATION_FIELDS)


@router.delete(
    "/locations/shelf-boxes/{box_id}/items/{item_id}",
    dependencies=[Depends(require("location:manage"))],
)
def delete_shelf_box_item(
    box_id: int,
    item_id: int,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    box = get_shelf_storage_box(db, box_id)
    item = db.get(Location, item_id)
    if not item or item.type != "bin" or item.parent_id != box.id:
        raise BusinessError("SHELF_BOX_ITEM_NOT_FOUND", "箱内物料条目不存在", 404)
    if db.scalar(select(Material.id).where(Material.location_id == item.id).limit(1)) or db.scalar(
        select(InventoryLot.id).where(InventoryLot.location_id == item.id).limit(1)
    ):
        raise BusinessError(
            "SHELF_BOX_ITEM_IN_USE",
            "该条目已关联正式物料库存，不能直接删除",
        )
    movements = list(
        db.scalars(
            select(StockMovement).where(
                or_(
                    StockMovement.source_location_id == item.id,
                    StockMovement.target_location_id == item.id,
                )
            )
        ).all()
    )
    for movement in movements:
        if movement.source_location_id == item.id:
            movement.source_location_id = None
        if movement.target_location_id == item.id:
            movement.target_location_id = None
    add_audit(
        db,
        user.id,
        "location.shelf_box.item.delete",
        "location",
        str(item.id),
        request.state.request_id,
        before={
            "box_id": box.id,
            "material_name": item.bin_material_name,
            "quantity": item.bin_quantity,
            "notes": item.bin_content_notes,
        },
        after={"deleted": True},
    )
    db.delete(item)
    db.commit()
    return {"message": "箱内物料已删除"}


@router.put(
    "/locations/organizers/{item_id}/layout",
    dependencies=[Depends(require("location:manage"))],
)
def update_organizer_layout(
    item_id: int,
    p: OrganizerLayoutUpdate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    organizer = db.get(Location, item_id)
    if not organizer or organizer.type != "box":
        raise BusinessError("ORGANIZER_NOT_FOUND", "元件盒不存在", 404)
    if organizer.organizer_style != "split_configurable":
        raise BusinessError("ORGANIZER_LAYOUT_FIXED", "该库位为固定布局，不能切换半区类型")

    old_left = organizer.organizer_left_module or "small"
    old_right = organizer.organizer_right_module or "small"
    changes = [
        ("left", old_left, p.organizer_left_module),
        ("right", old_right, p.organizer_right_module),
    ]
    for side, old_module, new_module in changes:
        if old_module == new_module:
            continue
        old_names = organizer_module_slots(side, old_module)
        old_bins = list(
            db.scalars(
                select(Location).where(
                    Location.parent_id == organizer.id,
                    Location.name.in_(old_names),
                )
            ).all()
        )
        old_ids = [item.id for item in old_bins]
        if not old_ids:
            continue
        material_count = db.scalar(
            select(func.count(Material.id)).where(
                Material.location_id.in_(old_ids),
                Material.is_deleted.is_(False),
            )
        )
        has_lot_stock = db.scalar(
            select(InventoryLot.id)
            .where(InventoryLot.location_id.in_(old_ids), InventoryLot.quantity > 0)
            .limit(1)
        )
        direct_content_count = db.scalar(
            select(func.count(Location.id)).where(
                Location.id.in_(old_ids),
                Location.bin_material_name != "",
            )
        )
        if material_count or has_lot_stock or direct_content_count:
            side_label = "左半区" if side == "left" else "右半区"
            raise BusinessError(
                "ORGANIZER_HALF_IN_USE",
                f"{side_label}已有物料，请先将物料移到其他库位后再切换格型",
                details={
                    "side": side,
                    "material_count": (material_count or 0) + (direct_content_count or 0),
                },
            )

    all_module_names = {
        name
        for side in ("left", "right")
        for module in ("small", "large")
        for name in organizer_module_slots(side, module)
    }
    controlled_bins = list(
        db.scalars(
            select(Location).where(
                Location.parent_id == organizer.id,
                Location.name.in_(all_module_names),
            )
        ).all()
    )
    existing_by_name = {item.name: item for item in controlled_bins}
    for item in controlled_bins:
        item.is_active = False

    desired_names = organizer_slot_names(
        "split_configurable",
        p.organizer_left_module,
        p.organizer_right_module,
    )
    desired_bins = []
    for slot in desired_names:
        item = existing_by_name.get(slot)
        if item is None:
            code = f"{organizer.code}-{slot}"
            ensure_location_code_available(db, code)
            item = Location(
                code=code,
                name=slot,
                parent_id=organizer.id,
                type="bin",
                full_path=f"{organizer.full_path} / {slot}",
                manager=organizer.manager,
                notes=organizer_bin_note("split_configurable", slot),
                is_active=organizer.is_active,
            )
            db.add(item)
        else:
            item.is_active = organizer.is_active
        desired_bins.append(item)

    before = {
        "organizer_left_module": old_left,
        "organizer_right_module": old_right,
    }
    organizer.organizer_left_module = p.organizer_left_module
    organizer.organizer_right_module = p.organizer_right_module
    db.flush()
    add_audit(
        db,
        user.id,
        "location.organizer.layout.update",
        "location",
        str(organizer.id),
        request.state.request_id,
        before=before,
        after={
            **p.model_dump(),
            "bin_count": len(desired_bins),
        },
    )
    db.commit()
    return serialize_organizer(organizer, desired_bins)


@router.put(
    "/locations/{item_id}/content",
    dependencies=[Depends(require("location:manage"))],
)
def update_bin_content(
    item_id: int,
    p: BinContentUpdate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    item = db.get(Location, item_id)
    if not item or item.type != "bin":
        raise BusinessError("BIN_NOT_FOUND", "小盒库位不存在", 404)
    before = {
        "material_name": item.bin_material_name,
        "quantity": item.bin_quantity,
        "notes": item.bin_content_notes,
    }
    item.bin_material_name = p.material_name
    item.bin_quantity = p.quantity
    item.bin_content_notes = p.notes
    parent = db.get(Location, item.parent_id) if item.parent_id else None
    if parent and parent.type == "container":
        item.name = p.material_name
        item.full_path = f"{parent.full_path} / {p.material_name}"
    add_audit(
        db,
        user.id,
        "location.bin.content.update",
        "location",
        str(item.id),
        request.state.request_id,
        before=before,
        after=p.model_dump(),
    )
    db.commit()
    return row_dict(item, LOCATION_FIELDS)


@router.delete(
    "/locations/{item_id}/content",
    dependencies=[Depends(require("location:manage"))],
)
def clear_bin_content(
    item_id: int,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    item = db.get(Location, item_id)
    if not item or item.type != "bin":
        raise BusinessError("BIN_NOT_FOUND", "小盒库位不存在", 404)
    linked_materials = list(
        db.scalars(select(Material).where(Material.location_id == item.id)).all()
    )
    linked_lots = list(
        db.scalars(select(InventoryLot).where(InventoryLot.location_id == item.id)).all()
    )
    before = {
        "material_name": item.bin_material_name,
        "quantity": item.bin_quantity,
        "notes": item.bin_content_notes,
        "linked_materials": [
            {
                "id": material.id,
                "code": material.code,
                "quantity": str(material.quantity),
            }
            for material in linked_materials
        ],
        "location_lots": [
            {
                "id": lot.id,
                "material_id": lot.material_id,
                "quantity": str(lot.quantity),
            }
            for lot in linked_lots
        ],
    }
    item.bin_material_name = ""
    item.bin_quantity = None
    item.bin_content_notes = ""
    for material in linked_materials:
        material.location_id = None
        material.updated_by_id = user.id
    for lot in linked_lots:
        db.delete(lot)
    add_audit(
        db,
        user.id,
        "location.bin.content.clear",
        "location",
        str(item.id),
        request.state.request_id,
        before=before,
        after={
            "material_name": "",
            "quantity": None,
            "notes": "",
            "linked_materials": [],
            "location_lots": [],
        },
    )
    db.commit()
    return row_dict(item, LOCATION_FIELDS)


@router.post("/locations", status_code=201, dependencies=[Depends(require("location:manage"))])
def create_location(p: LocationData, request: Request, db: DB, user: CurrentUser):
    ensure_location_code_available(db, p.code)
    validate_location_parent(db, None, p.parent_id)
    item = Location(**p.model_dump(), full_path=calculate_path(db, p))
    db.add(item)
    db.flush()
    add_audit(db, user.id, "location.create", "location", str(item.id), request.state.request_id)
    db.commit()
    return row_dict(
        item,
        ["id", "parent_id", "code", "name", "type", "full_path", "manager", "notes", "is_active"],
    )


@router.put("/locations/{item_id}", dependencies=[Depends(require("location:manage"))])
def update_location(item_id: int, p: LocationData, request: Request, db: DB, user: CurrentUser):
    item = db.get(Location, item_id)
    if not item:
        raise BusinessError("LOCATION_NOT_FOUND", "库位不存在", 404)
    ensure_location_code_available(db, p.code, item_id)
    validate_location_parent(db, item_id, p.parent_id)
    for k, v in p.model_dump().items():
        setattr(item, k, v)
    item.full_path = calculate_path(db, p)
    pending = [item]
    while pending:
        parent = pending.pop()
        children = list(
            db.scalars(select(Location).where(Location.parent_id == parent.id)).all()
        )
        for child in children:
            child.full_path = f"{parent.full_path} / {child.name}"
        pending.extend(children)
    add_audit(db, user.id, "location.update", "location", str(item.id), request.state.request_id)
    db.commit()
    return row_dict(
        item,
        ["id", "parent_id", "code", "name", "type", "full_path", "manager", "notes", "is_active"],
    )


@router.delete(
    "/locations/organizers/{item_id}",
    dependencies=[Depends(require("location:manage"))],
)
def delete_organizer(
    item_id: int,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    organizer = db.get(Location, item_id)
    if not organizer or organizer.type != "box":
        raise BusinessError("ORGANIZER_NOT_FOUND", "大物料盒不存在", 404)

    descendants: list[Location] = []
    descendant_depths: dict[int, int] = {}
    pending = [(organizer.id, 0)]
    while pending:
        parent_id, parent_depth = pending.pop()
        children = list(
            db.scalars(select(Location).where(Location.parent_id == parent_id)).all()
        )
        descendants.extend(children)
        for child in children:
            child_depth = parent_depth + 1
            descendant_depths[child.id] = child_depth
            pending.append((child.id, child_depth))

    scoped_ids = [organizer.id, *(item.id for item in descendants)]
    linked_materials = list(
        db.scalars(select(Material).where(Material.location_id.in_(scoped_ids))).all()
    )
    direct_content = [
        item for item in descendants if item.type == "bin" and item.bin_material_name.strip()
    ]
    positive_lots = list(
        db.scalars(
            select(InventoryLot).where(
                InventoryLot.location_id.in_(scoped_ids),
                InventoryLot.quantity > 0,
            )
        ).all()
    )
    active_materials = [item for item in linked_materials if not item.is_deleted]
    if direct_content or active_materials or positive_lots:
        raise BusinessError(
            "ORGANIZER_IN_USE",
            "大物料盒内仍有物料，请先清空所有小格并移出只分配到大盒的物料后再删除",
            details={
                "direct_content_count": len(direct_content),
                "material_count": len(active_materials),
                "location_lot_count": len(positive_lots),
            },
        )

    for material in linked_materials:
        material.location_id = None
        material.updated_by_id = user.id
    db.execute(delete(InventoryLot).where(InventoryLot.location_id.in_(scoped_ids)))

    movements = list(
        db.scalars(
            select(StockMovement).where(
                or_(
                    StockMovement.source_location_id.in_(scoped_ids),
                    StockMovement.target_location_id.in_(scoped_ids),
                )
            )
        ).all()
    )
    for movement in movements:
        if movement.source_location_id in scoped_ids:
            movement.source_location_id = None
        if movement.target_location_id in scoped_ids:
            movement.target_location_id = None

    add_audit(
        db,
        user.id,
        "location.organizer.delete",
        "location",
        str(organizer.id),
        request.state.request_id,
        before={
            "code": organizer.code,
            "name": organizer.name,
            "bin_count": len(descendants),
        },
        after={"deleted": True},
    )

    # Flush detached material/movement references before deleting locations. Explicit
    # depth-ordered DELETE statements are required here: ORM delete ordering is not
    # guaranteed for a self-referencing table and PostgreSQL otherwise may delete the
    # organizer before its child bins, violating locations_parent_id_fkey.
    db.flush()
    for depth in sorted(set(descendant_depths.values()), reverse=True):
        location_ids = [
            location_id
            for location_id, location_depth in descendant_depths.items()
            if location_depth == depth
        ]
        db.execute(delete(Location).where(Location.id.in_(location_ids)))
    db.execute(delete(Location).where(Location.id == organizer.id))
    db.commit()
    return {"message": "大物料盒已删除", "deleted_bin_count": len(descendants)}


@router.delete("/locations/{item_id}", dependencies=[Depends(require("location:manage"))])
def delete_location(item_id: int, db: DB, user: CurrentUser):
    item = db.get(Location, item_id)
    if not item:
        raise BusinessError("LOCATION_NOT_FOUND", "库位不存在", 404)
    if db.scalar(select(Location.id).where(Location.parent_id == item_id)) or db.scalar(
        select(Material.id).where(Material.location_id == item_id)
    ) or item.bin_material_name:
        raise BusinessError("LOCATION_IN_USE", "存在子库位或物料，不能删除")
    db.execute(delete(Location).where(Location.id == item_id))
    db.commit()
    return {"message": "已删除"}


@router.get("/suppliers")
def suppliers(db: DB, user: CurrentUser):
    fields = ["id", "code", "name", "contact", "phone", "email", "lead_time_days", "is_active"]
    return [row_dict(x, fields) for x in db.scalars(select(Supplier).order_by(Supplier.name)).all()]


@router.post("/suppliers", status_code=201, dependencies=[Depends(require("supplier:manage"))])
def create_supplier(p: SupplierData, request: Request, db: DB, user: CurrentUser):
    item = Supplier(**p.model_dump())
    db.add(item)
    db.flush()
    add_audit(db, user.id, "supplier.create", "supplier", str(item.id), request.state.request_id)
    db.commit()
    return row_dict(item, ["id", *type(p).model_fields])


@router.put("/suppliers/{item_id}", dependencies=[Depends(require("supplier:manage"))])
def update_supplier(item_id: int, p: SupplierData, request: Request, db: DB, user: CurrentUser):
    item = db.get(Supplier, item_id)
    if not item:
        raise BusinessError("SUPPLIER_NOT_FOUND", "供应商不存在", 404)
    for k, v in p.model_dump().items():
        setattr(item, k, v)
    add_audit(db, user.id, "supplier.update", "supplier", str(item.id), request.state.request_id)
    db.commit()
    return row_dict(item, ["id", *type(p).model_fields])


@router.get("/low-stock")
def low_stock(db: DB, user: CurrentUser):
    items = db.scalars(
        select(Material)
        .where(
            Material.is_deleted.is_(False),
            Material.quantity - Material.reserved_quantity <= Material.safety_stock,
        )
        .order_by((Material.quantity - Material.reserved_quantity).asc())
    ).all()
    return [
        {
            "id": x.id,
            "code": x.code,
            "name": x.name,
            "quantity": x.quantity,
            "reserved_quantity": x.reserved_quantity,
            "available_quantity": x.available_quantity,
            "safety_stock": x.safety_stock,
            "target_stock": x.target_stock,
            "suggested_purchase": max(0, x.target_stock - x.available_quantity),
        }
        for x in items
    ]
