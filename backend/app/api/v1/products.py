from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.agent.proposals import ProposalService
from app.api.deps import DB, CurrentUser, require
from app.core.exceptions import BusinessError
from app.models import BuildPlan, Material, Product, ProductBomItem, ProductRevision
from app.schemas.agent import AgentActionProposalOut
from app.schemas.products import (
    BuildPlanCreateRequest,
    BuildPlanReservationRequest,
    BuildReadinessRequest,
    ProductBomItemCreate,
    ProductBomItemUpdate,
    ProductCreate,
    ProductRevisionCloneRequest,
    ProductRevisionCreate,
    ProductRevisionReleaseRequest,
    ProductRevisionUpdate,
    ProductUpdate,
)
from app.services.audit import add_audit
from app.services.build_plans import BuildPlanService, build_plan_data
from app.services.build_readiness import BuildReadinessService
from app.services.product_revisions import (
    clone_revision as clone_product_revision,
)
from app.services.product_revisions import (
    release_revision as release_product_revision,
)
from app.services.product_revisions import (
    require_revision_editable,
)

router = APIRouter(tags=["产品与单台 BOM"])


def _product_data(product: Product) -> dict:
    return {
        "id": product.id,
        "code": product.code,
        "name": product.name,
        "description": product.description,
        "lifecycle_status": product.lifecycle_status,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }


def _revision_data(revision: ProductRevision) -> dict:
    return {
        "id": revision.id,
        "product_id": revision.product_id,
        "revision": revision.revision,
        "status": revision.status,
        "is_default": revision.is_default,
        "notes": revision.notes,
        "released_at": revision.released_at,
        "bom_hash": revision.bom_hash,
        "released_by_id": revision.released_by_id,
        "created_at": revision.created_at,
        "updated_at": revision.updated_at,
    }


def _require_product(db: DB, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在", 404)
    return product


def _require_revision(db: DB, revision_id: int) -> ProductRevision:
    revision = db.get(ProductRevision, revision_id)
    if revision is None:
        raise BusinessError("PRODUCT_REVISION_NOT_FOUND", "产品版本不存在", 404)
    return revision


def _require_build_plan(db: DB, plan_id: int) -> BuildPlan:
    plan = db.get(BuildPlan, plan_id)
    if plan is None:
        raise BusinessError("BUILD_PLAN_NOT_FOUND", "构建计划不存在", 404)
    return plan


@router.get("/products", dependencies=[Depends(require("material:view"))])
def list_products(db: DB, user: CurrentUser):
    products = list(db.scalars(select(Product).order_by(Product.code)).all())
    revision_counts = dict(
        db.execute(
            select(ProductRevision.product_id, func.count(ProductRevision.id)).group_by(
                ProductRevision.product_id
            )
        ).all()
    )
    defaults = {
        revision.product_id: revision
        for revision in db.scalars(
            select(ProductRevision).where(
                ProductRevision.is_default.is_(True),
                ProductRevision.status == "released",
            )
        ).all()
    }
    return [
        {
            **_product_data(product),
            "revision_count": int(revision_counts.get(product.id, 0)),
            "default_revision": (
                _revision_data(defaults[product.id]) if product.id in defaults else None
            ),
        }
        for product in products
    ]


@router.post(
    "/products",
    status_code=201,
    dependencies=[Depends(require("project:manage"))],
)
def create_product(
    payload: ProductCreate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    if db.scalar(select(Product.id).where(Product.code == payload.code)):
        raise BusinessError("PRODUCT_CODE_EXISTS", "产品编号已存在", 409)
    product = Product(**payload.model_dump())
    db.add(product)
    db.flush()
    after = _product_data(product)
    add_audit(
        db,
        user.id,
        "product.create",
        "product",
        str(product.id),
        request.state.request_id,
        after=jsonable_encoder(after),
    )
    db.commit()
    return after


@router.get("/products/{product_id}", dependencies=[Depends(require("material:view"))])
def get_product(product_id: int, db: DB, user: CurrentUser):
    product = _require_product(db, product_id)
    revisions = list(
        db.scalars(
            select(ProductRevision)
            .where(ProductRevision.product_id == product.id)
            .order_by(ProductRevision.revision)
        ).all()
    )
    return {**_product_data(product), "revisions": [_revision_data(row) for row in revisions]}


@router.put(
    "/products/{product_id}",
    dependencies=[Depends(require("project:manage"))],
)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    product = _require_product(db, product_id)
    before = _product_data(product)
    values = payload.model_dump(exclude_unset=True)
    if "code" in values and db.scalar(
        select(Product.id).where(
            Product.code == values["code"],
            Product.id != product.id,
        )
    ):
        raise BusinessError("PRODUCT_CODE_EXISTS", "产品编号已存在", 409)
    for key, value in values.items():
        setattr(product, key, value)
    db.flush()
    after = _product_data(product)
    add_audit(
        db,
        user.id,
        "product.update",
        "product",
        str(product.id),
        request.state.request_id,
        before=jsonable_encoder(before),
        after=jsonable_encoder(after),
    )
    db.commit()
    return after


@router.post(
    "/products/{product_id}/revisions",
    status_code=201,
    dependencies=[Depends(require("project:manage"))],
)
def create_revision(
    product_id: int,
    payload: ProductRevisionCreate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    _require_product(db, product_id)
    if db.scalar(
        select(ProductRevision.id).where(
            ProductRevision.product_id == product_id,
            ProductRevision.revision == payload.revision,
        )
    ):
        raise BusinessError("PRODUCT_REVISION_EXISTS", "产品版本已存在", 409)
    if payload.status != "draft" or payload.released_at is not None:
        raise BusinessError(
            "PRODUCT_REVISION_RELEASE_REQUIRED",
            "新产品版本必须先创建为草稿，再通过发布操作生成 BOM 快照",
            409,
        )
    if payload.is_default:
        raise BusinessError(
            "PRODUCT_DEFAULT_REVISION_INVALID",
            "草稿版本不能设为默认版本",
            409,
        )
    values = payload.model_dump()
    revision = ProductRevision(product_id=product_id, **values)
    db.add(revision)
    db.flush()
    after = _revision_data(revision)
    add_audit(
        db,
        user.id,
        "product_revision.create",
        "product_revision",
        str(revision.id),
        request.state.request_id,
        after=jsonable_encoder(after),
    )
    db.commit()
    return after


@router.put(
    "/product-revisions/{revision_id}",
    dependencies=[Depends(require("project:manage"))],
)
def update_revision(
    revision_id: int,
    payload: ProductRevisionUpdate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    revision = _require_revision(db, revision_id)
    before = _revision_data(revision)
    values = payload.model_dump(exclude_unset=True)
    next_revision = values.get("revision", revision.revision)
    if db.scalar(
        select(ProductRevision.id).where(
            ProductRevision.product_id == revision.product_id,
            ProductRevision.revision == next_revision,
            ProductRevision.id != revision.id,
        )
    ):
        raise BusinessError("PRODUCT_REVISION_EXISTS", "产品版本已存在", 409)

    if revision.status == "draft":
        if values.get("status", "draft") != "draft" or values.get("is_default"):
            raise BusinessError(
                "PRODUCT_REVISION_RELEASE_REQUIRED",
                "草稿必须通过发布操作变为已发布版本，且发布后才能设为默认",
                409,
            )
        if values.get("released_at") is not None:
            raise BusinessError(
                "PRODUCT_REVISION_RELEASE_REQUIRED",
                "发布时间只能由发布操作设置",
                409,
            )
        values.pop("released_at", None)
    elif revision.status == "released":
        forbidden = set(values) - {"status", "is_default"}
        if forbidden or values.get("status", "released") not in {
            "released",
            "obsolete",
        }:
            raise BusinessError(
                "PRODUCT_REVISION_IMMUTABLE",
                "已发布版本不可修改；请克隆为新的草稿版本",
                409,
            )
        if values.get("status") == "obsolete":
            values["is_default"] = False
        elif values.get("is_default"):
            db.execute(
                update(ProductRevision)
                .where(
                    ProductRevision.product_id == revision.product_id,
                    ProductRevision.id != revision.id,
                )
                .values(is_default=False)
            )
    else:
        raise BusinessError(
            "PRODUCT_REVISION_IMMUTABLE",
            "已停用版本不可修改",
            409,
        )
    for key, value in values.items():
        setattr(revision, key, value)
    db.flush()
    after = _revision_data(revision)
    add_audit(
        db,
        user.id,
        "product_revision.update",
        "product_revision",
        str(revision.id),
        request.state.request_id,
        before=jsonable_encoder(before),
        after=jsonable_encoder(after),
    )
    db.commit()
    return after


@router.post(
    "/product-revisions/{revision_id}/release",
    dependencies=[Depends(require("project:manage"))],
)
def release_revision(
    revision_id: int,
    payload: ProductRevisionReleaseRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    current = _require_revision(db, revision_id)
    before = _revision_data(current)
    try:
        revision = release_product_revision(
            db,
            revision_id,
            released_by_id=user.id,
            make_default=payload.make_default,
        )
        after = _revision_data(revision)
        add_audit(
            db,
            user.id,
            "product_revision.release",
            "product_revision",
            str(revision.id),
            request.state.request_id,
            before=jsonable_encoder(before),
            after=jsonable_encoder(after),
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessError(
            "PRODUCT_DEFAULT_REVISION_CONFLICT",
            "同一产品只能有一个默认版本，请刷新后重试",
            409,
        ) from exc
    db.refresh(revision)
    return _revision_data(revision)


@router.post(
    "/product-revisions/{revision_id}/clone",
    status_code=201,
    dependencies=[Depends(require("project:manage"))],
)
def clone_revision(
    revision_id: int,
    payload: ProductRevisionCloneRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    source = _require_revision(db, revision_id)
    clone = clone_product_revision(
        db,
        revision_id,
        new_revision=payload.new_revision,
        notes=payload.notes,
    )
    after = _revision_data(clone)
    add_audit(
        db,
        user.id,
        "product_revision.clone",
        "product_revision",
        str(clone.id),
        request.state.request_id,
        before={"source_revision_id": source.id, "source_bom_hash": source.bom_hash},
        after=jsonable_encoder(after),
    )
    db.commit()
    db.refresh(clone)
    return _revision_data(clone)


@router.post(
    "/product-revisions/{revision_id}/bom",
    status_code=201,
    dependencies=[Depends(require("project:manage"))],
)
def create_bom_item(
    revision_id: int,
    payload: ProductBomItemCreate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    revision = _require_revision(db, revision_id)
    require_revision_editable(revision)
    material = db.get(Material, payload.material_id)
    if material is None or material.is_deleted:
        raise BusinessError("MATERIAL_NOT_FOUND", "物料不存在", 404)
    if db.scalar(
        select(ProductBomItem.id).where(
            ProductBomItem.product_revision_id == revision_id,
            ProductBomItem.material_id == payload.material_id,
        )
    ):
        raise BusinessError("PRODUCT_BOM_ITEM_EXISTS", "该物料已在单台 BOM 中", 409)
    item = ProductBomItem(product_revision_id=revision_id, **payload.model_dump())
    db.add(item)
    db.flush()
    after = {
        "id": item.id,
        "product_revision_id": item.product_revision_id,
        "material_id": item.material_id,
        "quantity_per_unit": str(item.quantity_per_unit),
        "notes": item.notes,
    }
    add_audit(
        db,
        user.id,
        "product_bom.create",
        "product_bom_item",
        str(item.id),
        request.state.request_id,
        after=jsonable_encoder(after),
    )
    db.commit()
    return after


@router.put(
    "/product-bom-items/{item_id}",
    dependencies=[Depends(require("project:manage"))],
)
def update_bom_item(
    item_id: int,
    payload: ProductBomItemUpdate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    item = db.get(ProductBomItem, item_id)
    if item is None:
        raise BusinessError("PRODUCT_BOM_ITEM_NOT_FOUND", "单台 BOM 项不存在", 404)
    revision = _require_revision(db, item.product_revision_id)
    require_revision_editable(revision)
    before = {
        "id": item.id,
        "product_revision_id": item.product_revision_id,
        "material_id": item.material_id,
        "quantity_per_unit": str(item.quantity_per_unit),
        "notes": item.notes,
    }
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.flush()
    after = {
        "id": item.id,
        "product_revision_id": item.product_revision_id,
        "material_id": item.material_id,
        "quantity_per_unit": str(item.quantity_per_unit),
        "notes": item.notes,
    }
    add_audit(
        db,
        user.id,
        "product_bom.update",
        "product_bom_item",
        str(item.id),
        request.state.request_id,
        before=jsonable_encoder(before),
        after=jsonable_encoder(after),
    )
    db.commit()
    return after


@router.delete(
    "/product-bom-items/{item_id}",
    dependencies=[Depends(require("project:manage"))],
)
def delete_bom_item(
    item_id: int,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    item = db.get(ProductBomItem, item_id)
    if item is None:
        raise BusinessError("PRODUCT_BOM_ITEM_NOT_FOUND", "单台 BOM 项不存在", 404)
    revision = _require_revision(db, item.product_revision_id)
    require_revision_editable(revision)
    before = {
        "id": item.id,
        "product_revision_id": item.product_revision_id,
        "material_id": item.material_id,
        "quantity_per_unit": str(item.quantity_per_unit),
        "notes": item.notes,
    }
    add_audit(
        db,
        user.id,
        "product_bom.delete",
        "product_bom_item",
        str(item.id),
        request.state.request_id,
        before=jsonable_encoder(before),
    )
    db.delete(item)
    db.commit()
    return {"deleted": True, "id": item_id}


@router.get(
    "/product-revisions/{revision_id}/bom",
    dependencies=[Depends(require("material:view"))],
)
def get_product_bom(revision_id: int, db: DB, user: CurrentUser):
    revision = _require_revision(db, revision_id)
    product = _require_product(db, revision.product_id)
    rows = list(
        db.execute(
            select(ProductBomItem, Material)
            .join(Material, Material.id == ProductBomItem.material_id)
            .where(ProductBomItem.product_revision_id == revision.id)
            .order_by(Material.code)
        ).all()
    )
    return {
        "product": _product_data(product),
        "revision": _revision_data(revision),
        "items": [
            {
                "id": item.id,
                "material_id": material.id,
                "code": material.code,
                "name": material.name,
                "mpn": material.mpn,
                "unit": material.unit,
                "quantity_per_unit": str(item.quantity_per_unit),
                "available_quantity": str(material.available_quantity),
                "safety_stock": str(material.safety_stock),
                "notes": item.notes,
            }
            for item, material in rows
        ],
        "count": len(rows),
        "quantity_semantics": "单台用量；不是 Project BOM 总需求",
    }


@router.post(
    "/product-revisions/{revision_id}/build-readiness",
    dependencies=[Depends(require("material:view"))],
)
def analyze_build_readiness(
    revision_id: int,
    payload: BuildReadinessRequest,
    db: DB,
    user: CurrentUser,
):
    return BuildReadinessService(db).analyze(
        revision_id,
        payload.build_quantity,
        payload.project_id,
    )


@router.post(
    "/build-plans",
    status_code=201,
    dependencies=[Depends(require("project:manage"))],
)
def create_build_plan(
    payload: BuildPlanCreateRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    plan = BuildPlanService(db, user.id, request.state.request_id).create(
        product_revision_id=payload.product_revision_id,
        project_id=payload.project_id,
        build_quantity=payload.build_quantity,
        client_operation_id=payload.client_operation_id,
        notes=payload.notes,
        commit=False,
    )
    add_audit(
        db,
        user.id,
        "build_plan.create",
        "build_plan",
        str(plan.id),
        request.state.request_id,
        after={
            "plan_no": plan.plan_no,
            "product_revision_id": plan.product_revision_id,
            "project_id": plan.project_id,
            "build_quantity": plan.build_quantity,
            "snapshot_hash": plan.snapshot_hash,
            "stock_changed": False,
        },
    )
    db.commit()
    db.refresh(plan)
    return build_plan_data(db, plan)


@router.get(
    "/build-plans/{plan_id}",
    dependencies=[Depends(require("material:view"))],
)
def get_build_plan(plan_id: int, db: DB, user: CurrentUser):
    return build_plan_data(db, _require_build_plan(db, plan_id))


@router.post(
    "/build-plans/{plan_id}/reservation-proposal",
    dependencies=[
        Depends(require("project:manage")),
        Depends(require("inventory:operate")),
    ],
)
def create_build_plan_reservation_proposal(
    plan_id: int,
    payload: BuildPlanReservationRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    outcome = ProposalService(
        db,
        user,
        request.state.request_id,
        client_operation_id=payload.client_operation_id,
    ).create_reservation_for_plan(plan_id, payload.reason)
    result = {
        "fully_reserved": outcome["fully_reserved"],
        "build_plan": build_plan_data(db, outcome["plan"]),
        "proposal": None,
    }
    if outcome["proposal"] is not None:
        result["proposal"] = AgentActionProposalOut.model_validate(
            outcome["proposal"]
        ).model_dump(mode="json")
    return result
