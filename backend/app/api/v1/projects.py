import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, require
from app.core.exceptions import BusinessError
from app.models import (
    BomItem,
    BuildPlan,
    BuildPlanItem,
    Product,
    ProductBomItem,
    ProductRevision,
    Project,
    PurchaseOrder,
)
from app.schemas.domain import BomData, ProjectData, PurchaseOrderData
from app.services.audit import add_audit

router = APIRouter(tags=["项目与采购"])


def serialize(obj, fields):
    return {k: getattr(obj, k) for k in fields}


PROJECT_FIELDS = [
    "id",
    "code",
    "name",
    "manager_id",
    "status",
    "start_date",
    "end_date",
    "notes",
    "members",
    "product_revision_id",
    "created_at",
    "updated_at",
]


@router.get("/projects")
def projects(db: DB, user: CurrentUser):
    return [
        serialize(x, PROJECT_FIELDS)
        for x in db.scalars(select(Project).order_by(Project.updated_at.desc())).all()
    ]


def _project_domain_view(db, project: Project, legacy_items: list[BomItem]) -> dict:
    revision = (
        db.get(ProductRevision, project.product_revision_id)
        if project.product_revision_id
        else None
    )
    product = db.get(Product, revision.product_id) if revision else None
    latest_plan = db.scalar(
        select(BuildPlan)
        .where(BuildPlan.project_id == project.id)
        .order_by(BuildPlan.updated_at.desc(), BuildPlan.id.desc())
        .limit(1)
    )
    product_bom_items = (
        list(
            db.scalars(
                select(ProductBomItem)
                .where(ProductBomItem.product_revision_id == revision.id)
                .order_by(ProductBomItem.id)
            ).all()
        )
        if revision
        else []
    )
    reasons: list[str] = []
    if revision and legacy_items and latest_plan is None:
        reasons.append(
            "已关联 ProductRevision，但仍存在 Project BomItem 且没有对应 BuildPlan 快照；"
            "这些条目按临时/维修/历史需求保留，不会自动覆盖产品版本 BOM。"
        )
    if latest_plan and legacy_items:
        plan_items = {
            item.material_id: Decimal(str(item.required_total))
            for item in db.scalars(
                select(BuildPlanItem).where(BuildPlanItem.build_plan_id == latest_plan.id)
            ).all()
        }
        legacy_by_material = {
            item.material_id: Decimal(str(item.required_quantity)) for item in legacy_items
        }
        for material_id, quantity in legacy_by_material.items():
            if material_id not in plan_items:
                reasons.append(f"Project BomItem 物料 {material_id} 未出现在最新 BuildPlan 快照中")
            elif plan_items[material_id] != quantity:
                reasons.append(
                    f"物料 {material_id} 的 Project BomItem 数量 {quantity} 与 BuildPlan 快照 "
                    f"{plan_items[material_id]} 不一致"
                )
        for material_id in plan_items:
            if material_id not in legacy_by_material:
                reasons.append(f"最新 BuildPlan 快照包含物料 {material_id}，Project BomItem 未记录")
    return {
        "execution_domain": {
            "label": "项目 / 生产任务",
            "build_plan_source": "产品关联项目的执行算术以 BuildPlan 快照为准",
            "legacy_bom_policy": (
                "Project BomItem 保留用于临时、维修或历史需求，不自动覆盖 ProductRevision BOM"
            ),
        },
        "linked_product_revision": (
            {
                "id": revision.id,
                "product_id": revision.product_id,
                "product_code": product.code if product else "",
                "product_name": product.name if product else "",
                "revision": revision.revision,
                "status": revision.status,
                "bom_hash": revision.bom_hash,
            }
            if revision
            else None
        ),
        "product_revision_bom": [
            {
                "id": item.id,
                "material_id": item.material_id,
                "quantity_per_unit": item.quantity_per_unit,
            }
            for item in product_bom_items
        ],
        "build_plan": (
            {
                "id": latest_plan.id,
                "plan_no": latest_plan.plan_no,
                "status": latest_plan.status,
                "build_quantity": latest_plan.build_quantity,
                "product_revision_id": latest_plan.product_revision_id,
                "snapshot_hash": latest_plan.snapshot_hash,
            }
            if latest_plan
            else None
        ),
        "bom_domain_warning": {
            "has_warning": bool(reasons),
            "message": (
                "检测到 Project BomItem 与产品执行快照可能漂移；未自动覆盖任何 BOM。"
                if reasons
                else "未发现 Project BomItem 与最新 BuildPlan 快照的已知漂移。"
            ),
            "reasons": reasons,
            "source": "read_only_drift_check",
        },
    }


@router.post("/projects", status_code=201, dependencies=[Depends(require("project:manage"))])
def create_project(p: ProjectData, request: Request, db: DB, user: CurrentUser):
    if db.scalar(select(Project.id).where(Project.code == p.code)):
        raise BusinessError("PROJECT_CODE_EXISTS", "项目编号已存在", 409)
    if p.product_revision_id is not None and not db.get(ProductRevision, p.product_revision_id):
        raise BusinessError("PRODUCT_REVISION_NOT_FOUND", "产品版本不存在", 404)
    item = Project(**p.model_dump())
    db.add(item)
    db.flush()
    add_audit(db, user.id, "project.create", "project", str(item.id), request.state.request_id)
    db.commit()
    return serialize(item, PROJECT_FIELDS)


@router.get("/projects/{item_id}")
def get_project(item_id: int, db: DB, user: CurrentUser):
    item = db.get(Project, item_id)
    if not item:
        raise BusinessError("PROJECT_NOT_FOUND", "项目不存在", 404)
    data = serialize(item, PROJECT_FIELDS)
    data["bom"] = [
        serialize(x, ["id", "version", "material_id", "required_quantity", "notes"])
        for x in db.scalars(select(BomItem).where(BomItem.project_id == item_id)).all()
    ]
    legacy_items = list(db.scalars(select(BomItem).where(BomItem.project_id == item_id)).all())
    data.update(_project_domain_view(db, item, legacy_items))
    return data


@router.put("/projects/{item_id}", dependencies=[Depends(require("project:manage"))])
def update_project(item_id: int, p: ProjectData, request: Request, db: DB, user: CurrentUser):
    item = db.get(Project, item_id)
    if not item:
        raise BusinessError("PROJECT_NOT_FOUND", "项目不存在", 404)
    if p.product_revision_id is not None and not db.get(ProductRevision, p.product_revision_id):
        raise BusinessError("PRODUCT_REVISION_NOT_FOUND", "产品版本不存在", 404)
    for k, v in p.model_dump().items():
        setattr(item, k, v)
    add_audit(db, user.id, "project.update", "project", str(item.id), request.state.request_id)
    db.commit()
    return serialize(item, PROJECT_FIELDS)


@router.post(
    "/projects/{item_id}/bom", status_code=201, dependencies=[Depends(require("project:manage"))]
)
def add_bom(item_id: int, p: BomData, db: DB, user: CurrentUser):
    if not db.get(Project, item_id):
        raise BusinessError("PROJECT_NOT_FOUND", "项目不存在", 404)
    item = BomItem(project_id=item_id, **p.model_dump())
    db.add(item)
    db.commit()
    return serialize(
        item, ["id", "project_id", "version", "material_id", "required_quantity", "notes"]
    )


@router.get("/purchase-orders", dependencies=[Depends(require("purchase:manage"))])
def purchase_orders(db: DB, user: CurrentUser):
    fields = [
        "id",
        "order_no",
        "supplier_id",
        "status",
        "items",
        "total_amount",
        "expected_date",
        "created_by_id",
        "created_at",
    ]
    return [
        serialize(x, fields)
        for x in db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc())).all()
    ]


@router.post(
    "/purchase-orders", status_code=201, dependencies=[Depends(require("purchase:manage"))]
)
def create_purchase_order(p: PurchaseOrderData, db: DB, user: CurrentUser):
    total = sum(float(x.get("quantity", 0)) * float(x.get("unit_price", 0)) for x in p.items)
    item = PurchaseOrder(
        order_no=f"PO-{uuid.uuid4().hex[:14].upper()}",
        supplier_id=p.supplier_id,
        items=p.items,
        total_amount=total,
        expected_date=p.expected_date,
        created_by_id=user.id,
    )
    db.add(item)
    db.commit()
    return serialize(item, ["id", "order_no", "status", "total_amount"])
