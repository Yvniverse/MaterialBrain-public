from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import case, func, or_, select

from app.api.deps import DB, CurrentUser, require
from app.core.exceptions import BusinessError
from app.models import Category, Location, Material
from app.schemas.domain import MaterialCreate, MaterialOut, MaterialUpdate
from app.services.audit import add_audit

router = APIRouter(prefix="/materials", tags=["物料"])


def _category_scope_ids(db: DB, category_id: int) -> list[int]:
    categories = db.execute(select(Category.id, Category.parent_id)).all()
    if category_id not in {row.id for row in categories}:
        raise BusinessError("CATEGORY_NOT_FOUND", "分类不存在", 404)
    children: dict[int, list[int]] = {}
    for row in categories:
        if row.parent_id is not None:
            children.setdefault(row.parent_id, []).append(row.id)
    result = []
    pending = [category_id]
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.append(current)
        pending.extend(children.get(current, []))
    return result


def _location_scope_ids(db: DB, location_id: int) -> list[int]:
    locations = db.execute(select(Location.id, Location.parent_id)).all()
    if location_id not in {row.id for row in locations}:
        raise BusinessError("LOCATION_NOT_FOUND", "库位不存在", 404)
    children: dict[int, list[int]] = {}
    for row in locations:
        if row.parent_id is not None:
            children.setdefault(row.parent_id, []).append(row.id)
    result = []
    pending = [location_id]
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.append(current)
        pending.extend(children.get(current, []))
    return result


@router.get("")
def list_materials(
    db: DB,
    user: CurrentUser,
    q: str = "",
    category_id: int | None = None,
    include_descendants: bool = False,
    uncategorized: bool = False,
    location_id: int | None = None,
    include_location_descendants: bool = False,
    low_stock: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort: str = "updated_at",
    order: str = "desc",
):
    if category_id and uncategorized:
        raise BusinessError("INVALID_CATEGORY_FILTER", "分类筛选与未分类筛选不能同时使用")
    query = select(Material).where(Material.is_deleted.is_(False))
    if q:
        query = query.where(
            or_(
                Material.code.ilike(f"%{q}%"),
                Material.name.ilike(f"%{q}%"),
                Material.mpn.ilike(f"%{q}%"),
            )
        )
    if uncategorized:
        query = query.where(Material.category_id.is_(None))
    elif category_id:
        if include_descendants:
            query = query.where(Material.category_id.in_(_category_scope_ids(db, category_id)))
        else:
            query = query.where(Material.category_id == category_id)
    if location_id:
        if include_location_descendants:
            query = query.where(Material.location_id.in_(_location_scope_ids(db, location_id)))
        else:
            query = query.where(Material.location_id == location_id)
    if low_stock:
        query = query.where(Material.quantity - Material.reserved_quantity <= Material.safety_stock)
    filtered = query.subquery()
    stats = db.execute(
        select(
            func.count(filtered.c.id),
            func.coalesce(func.sum(filtered.c.quantity), 0),
            func.coalesce(func.sum(filtered.c.reserved_quantity), 0),
            func.coalesce(func.sum(filtered.c.quantity - filtered.c.reserved_quantity), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            filtered.c.quantity - filtered.c.reserved_quantity
                            <= filtered.c.safety_stock,
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        )
    ).one()
    total = stats[0]
    sortable = {
        "code": Material.code,
        "name": Material.name,
        "quantity": Material.quantity,
        "updated_at": Material.updated_at,
    }
    column = sortable.get(sort, Material.updated_at)
    query = (
        query.order_by(column.asc() if order == "asc" else column.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [MaterialOut.model_validate(item) for item in db.scalars(query).all()],
        "total": total,
        "page": page,
        "page_size": page_size,
        "summary": {
            "quantity": str(stats[1]),
            "reserved_quantity": str(stats[2]),
            "available_quantity": str(stats[3]),
            "low_stock_count": stats[4],
        },
    }


@router.post(
    "",
    response_model=MaterialOut,
    status_code=201,
    dependencies=[Depends(require("material:manage"))],
)
def create_material(payload: MaterialCreate, request: Request, db: DB, user: CurrentUser):
    if db.scalar(select(Material.id).where(Material.code == payload.code)):
        raise BusinessError("MATERIAL_CODE_EXISTS", "物料编码已存在", 409)
    material = Material(**payload.model_dump(), created_by_id=user.id, updated_by_id=user.id)
    db.add(material)
    db.flush()
    add_audit(
        db,
        user.id,
        "material.create",
        "material",
        str(material.id),
        request.state.request_id,
        after={"code": material.code, "name": material.name},
    )
    db.commit()
    return material


@router.get("/{material_id}", response_model=MaterialOut)
def get_material(material_id: int, db: DB, user: CurrentUser):
    material = db.scalar(
        select(Material).where(Material.id == material_id, Material.is_deleted.is_(False))
    )
    if not material:
        raise BusinessError("MATERIAL_NOT_FOUND", "物料不存在", 404)
    return material


@router.put(
    "/{material_id}", response_model=MaterialOut, dependencies=[Depends(require("material:manage"))]
)
def update_material(
    material_id: int, payload: MaterialUpdate, request: Request, db: DB, user: CurrentUser
):
    material = db.get(Material, material_id)
    if not material or material.is_deleted:
        raise BusinessError("MATERIAL_NOT_FOUND", "物料不存在", 404)
    before = {"name": material.name, "mpn": material.mpn, "location_id": material.location_id}
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(material, key, value)
    material.updated_by_id = user.id
    add_audit(
        db,
        user.id,
        "material.update",
        "material",
        str(material.id),
        request.state.request_id,
        before=before,
        after=payload.model_dump(exclude_unset=True, mode="json"),
    )
    db.commit()
    return material


@router.delete("/{material_id}", dependencies=[Depends(require("material:manage"))])
def delete_material(material_id: int, request: Request, db: DB, user: CurrentUser):
    material = db.get(Material, material_id)
    if not material or material.is_deleted:
        raise BusinessError("MATERIAL_NOT_FOUND", "物料不存在", 404)
    if material.quantity != 0 or material.reserved_quantity != 0:
        raise BusinessError("MATERIAL_HAS_STOCK", "有库存或预留的物料不能删除")
    material.is_deleted = True
    material.is_active = False
    add_audit(
        db, user.id, "material.delete", "material", str(material.id), request.state.request_id
    )
    db.commit()
    return {"message": "物料已软删除"}
