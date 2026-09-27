import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models import (
    BuildPlan,
    BuildPlanItem,
    Material,
    Product,
    ProductRevision,
    Project,
)
from app.services.build_readiness import BuildReadinessService
from app.services.product_revisions import canonical_decimal, canonical_hash


class BuildPlanService:
    """Create immutable, inventory-read-only build snapshots."""

    def __init__(self, db: Session, user_id: int, request_id: str):
        self.db = db
        self.user_id = user_id
        self.request_id = request_id

    def create(
        self,
        *,
        product_revision_id: int,
        project_id: int,
        build_quantity: int,
        client_operation_id: str,
        notes: str = "",
        commit: bool = True,
    ) -> BuildPlan:
        if not client_operation_id.strip():
            raise BusinessError(
                "BUILD_PLAN_OPERATION_ID_REQUIRED",
                "构建计划必须包含业务操作 ID",
                400,
            )
        existing = self.db.scalar(
            select(BuildPlan).where(
                BuildPlan.created_by_id == self.user_id,
                BuildPlan.client_operation_id == client_operation_id,
            )
        )
        if existing is not None:
            self._validate_idempotent_payload(
                existing,
                product_revision_id=product_revision_id,
                project_id=project_id,
                build_quantity=build_quantity,
            )
            return existing

        revision = self.db.scalar(
            select(ProductRevision)
            .where(ProductRevision.id == product_revision_id)
            .with_for_update()
        )
        if (
            revision is None
            or revision.status != "released"
            or not revision.bom_hash
        ):
            raise BusinessError(
                "PRODUCT_REVISION_NOT_RELEASED",
                "只有已发布且具有 BOM 快照的产品版本可以创建构建计划",
                409,
            )
        project = self.db.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if project is None:
            raise BusinessError("PROJECT_NOT_FOUND", "项目不存在", 404)
        if project.product_revision_id != revision.id:
            raise BusinessError(
                "PROJECT_PRODUCT_REVISION_MISMATCH",
                "项目没有明确关联当前产品版本",
                409,
            )

        readiness = BuildReadinessService(self.db).analyze(
            revision.id,
            build_quantity,
            project.id,
        )
        blockers = [
            item for item in readiness["items"] if item.get("material_blocker")
        ]
        if blockers:
            raise BusinessError(
                "BUILD_PLAN_HAS_UNAVAILABLE_MATERIAL",
                "产品 BOM 包含当前不可用于构建的物料",
                409,
                details={"materials": [item["code"] for item in blockers]},
            )
        if not readiness["sufficient"]:
            raise BusinessError(
                "BUILD_PLAN_INSUFFICIENT_STOCK",
                "当前库存不足，不能生成可执行的构建预留计划",
                409,
                details={
                    "shortages": [
                        item
                        for item in readiness["items"]
                        if Decimal(str(item["shortage"])) > 0
                    ]
                },
            )

        snapshot_rows = [self._snapshot_row(item) for item in readiness["items"]]
        snapshot_hash = canonical_hash(
            {
                "product_revision_id": revision.id,
                "product_bom_hash": revision.bom_hash,
                "project_id": project.id,
                "build_quantity": build_quantity,
                "items": sorted(
                    [
                        {
                            "material_id": row["material_id"],
                            "quantity_per_unit": canonical_decimal(
                                row["quantity_per_unit"]
                            ),
                            "required_total": canonical_decimal(row["required_total"]),
                            "reserved_for_project_at_plan": canonical_decimal(
                                row["reserved_for_project_at_plan"]
                            ),
                            "additional_reservation_required": canonical_decimal(
                                row["additional_reservation_required"]
                            ),
                        }
                        for row in snapshot_rows
                    ],
                    key=lambda item: item["material_id"],
                ),
            }
        )
        plan = BuildPlan(
            plan_no=f"BP-{uuid.uuid4().hex[:18].upper()}",
            product_revision_id=revision.id,
            project_id=project.id,
            build_quantity=build_quantity,
            product_bom_hash=revision.bom_hash,
            snapshot_hash=snapshot_hash,
            status="ready",
            created_by_id=self.user_id,
            source_request_id=self.request_id,
            client_operation_id=client_operation_id,
            notes=notes,
        )
        self.db.add(plan)
        try:
            self.db.flush()
            for row in snapshot_rows:
                self.db.add(BuildPlanItem(build_plan_id=plan.id, **row))
            self.db.flush()
            if commit:
                self.db.commit()
                self.db.refresh(plan)
        except IntegrityError as exc:
            self.db.rollback()
            existing = self.db.scalar(
                select(BuildPlan).where(
                    BuildPlan.created_by_id == self.user_id,
                    BuildPlan.client_operation_id == client_operation_id,
                )
            )
            if existing is not None:
                self._validate_idempotent_payload(
                    existing,
                    product_revision_id=product_revision_id,
                    project_id=project_id,
                    build_quantity=build_quantity,
                )
                return existing
            raise BusinessError(
                "BUILD_PLAN_IDEMPOTENCY_CONFLICT",
                "构建计划业务操作 ID 冲突",
                409,
            ) from exc
        return plan

    def items(self, plan_id: int) -> list[BuildPlanItem]:
        return list(
            self.db.scalars(
                select(BuildPlanItem)
                .where(BuildPlanItem.build_plan_id == plan_id)
                .order_by(BuildPlanItem.material_id)
            ).all()
        )

    @staticmethod
    def _validate_idempotent_payload(
        plan: BuildPlan,
        *,
        product_revision_id: int,
        project_id: int,
        build_quantity: int,
    ) -> None:
        if (
            plan.product_revision_id != product_revision_id
            or plan.project_id != project_id
            or plan.build_quantity != build_quantity
        ):
            raise BusinessError(
                "BUILD_PLAN_IDEMPOTENCY_CONFLICT",
                "同一业务操作 ID 已用于不同的构建计划",
                409,
            )

    @staticmethod
    def _snapshot_row(item: dict[str, Any]) -> dict[str, Any]:
        blocker = item.get("material_blocker")
        return {
            "material_id": int(item["material_id"]),
            "quantity_per_unit": Decimal(str(item["quantity_per_unit"])),
            "required_total": Decimal(str(item["required_total"])),
            "available_quantity_at_plan": Decimal(str(item["available_quantity"])),
            "reserved_for_project_at_plan": Decimal(
                str(item["reserved_for_project"])
            ),
            "additional_reservation_required": Decimal(
                str(item["additional_reservation_required"])
            ),
            "projected_free_available_after_build": Decimal(
                str(item["projected_free_available_after_build"])
            ),
            "safety_stock_at_plan": Decimal(str(item["safety_stock"])),
            "below_safety_after_build": bool(item["below_safety_after_build"]),
            "material_status_at_plan": blocker or "active",
        }


def build_plan_data(db: Session, plan: BuildPlan) -> dict[str, Any]:
    revision = db.get(ProductRevision, plan.product_revision_id)
    product = db.get(Product, revision.product_id) if revision else None
    project = db.get(Project, plan.project_id)
    rows = list(
        db.execute(
            select(BuildPlanItem, Material)
            .join(Material, Material.id == BuildPlanItem.material_id)
            .where(BuildPlanItem.build_plan_id == plan.id)
            .order_by(Material.code)
        ).all()
    )
    return {
        "id": plan.id,
        "plan_no": plan.plan_no,
        "status": plan.status,
        "product_revision_id": plan.product_revision_id,
        "project_id": plan.project_id,
        "build_quantity": plan.build_quantity,
        "product_bom_hash": plan.product_bom_hash,
        "snapshot_hash": plan.snapshot_hash,
        "reservation_proposal_id": plan.reservation_proposal_id,
        "created_by_id": plan.created_by_id,
        "source_request_id": plan.source_request_id,
        "client_operation_id": plan.client_operation_id,
        "notes": plan.notes,
        "reserved_at": plan.reserved_at,
        "stale_reason": plan.stale_reason,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "product": (
            {"id": product.id, "code": product.code, "name": product.name}
            if product
            else None
        ),
        "revision": (
            {
                "id": revision.id,
                "revision": revision.revision,
                "status": revision.status,
            }
            if revision
            else None
        ),
        "project": (
            {"id": project.id, "code": project.code, "name": project.name}
            if project
            else None
        ),
        "items": [
            {
                "material_id": material.id,
                "code": material.code,
                "name": material.name,
                "unit": material.unit,
                "quantity_per_unit": str(item.quantity_per_unit),
                "required_total": str(item.required_total),
                "available_quantity_at_plan": str(item.available_quantity_at_plan),
                "reserved_for_project_at_plan": str(
                    item.reserved_for_project_at_plan
                ),
                "additional_reservation_required": str(
                    item.additional_reservation_required
                ),
                "projected_free_available_after_build": str(
                    item.projected_free_available_after_build
                ),
                "safety_stock_at_plan": str(item.safety_stock_at_plan),
                "below_safety_after_build": item.below_safety_after_build,
                "material_status_at_plan": item.material_status_at_plan,
            }
            for item, material in rows
        ],
    }
