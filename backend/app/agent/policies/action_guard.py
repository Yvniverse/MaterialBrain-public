from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.policies.approval import require_inventory_operator
from app.core.exceptions import BusinessError
from app.models import (
    AgentActionProposal,
    BuildPlan,
    BuildPlanItem,
    Material,
    ProductRevision,
    Project,
    ProjectReservation,
)
from app.schemas.agent import ReservationProposalPayload


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ActionGuard:
    """Server-side validation between human approval and stock execution."""

    def __init__(self, db: Session):
        self.db = db

    def validate_reservation(
        self,
        proposal: AgentActionProposal,
        permissions: list[str] | None,
    ) -> ReservationProposalPayload:
        require_inventory_operator(permissions)
        if proposal.status != "pending":
            raise BusinessError(
                "PROPOSAL_NOT_PENDING",
                "Proposal 已处理，不能重复执行",
                409,
                details={"status": proposal.status},
            )
        if proposal.expires_at and _as_utc(proposal.expires_at) <= datetime.now(UTC):
            raise BusinessError("PROPOSAL_EXPIRED", "Proposal 已过期", 409)
        if proposal.action_type != "reserve_inventory":
            raise BusinessError("UNSUPPORTED_AGENT_ACTION", "当前不支持该 Agent 操作", 400)

        try:
            payload = ReservationProposalPayload.model_validate(proposal.payload)
        except ValueError as exc:
            raise BusinessError(
                "INVALID_PROPOSAL_PAYLOAD",
                "Proposal 数据校验失败",
                details={"reason": str(exc)},
            ) from exc

        if payload.source == "build_plan":
            self._validate_build_plan(proposal, payload)
            return payload

        if not self.db.get(Project, payload.project_id):
            raise BusinessError("PROJECT_NOT_FOUND", "项目不存在", 404)

        material_ids = [item.material_id for item in payload.items]
        materials = list(
            self.db.scalars(
                select(Material).where(
                    Material.id.in_(material_ids),
                    Material.is_deleted.is_(False),
                )
            ).all()
        )
        by_id = {material.id: material for material in materials}
        missing = [material_id for material_id in material_ids if material_id not in by_id]
        if missing:
            raise BusinessError(
                "MATERIAL_NOT_FOUND",
                "Proposal 包含不存在的物料",
                404,
                details={"material_ids": missing},
            )
        inactive = [
            material_id
            for material_id in material_ids
            if not by_id[material_id].is_active
        ]
        if inactive:
            raise BusinessError(
                "MATERIAL_INACTIVE",
                "物料已停用，不能新增预留。",
                409,
                details={"material_ids": inactive},
            )

        shortages = []
        for item in payload.items:
            material = by_id[item.material_id]
            if item.quantity > material.available_quantity:
                shortages.append(
                    {
                        "material_id": material.id,
                        "requested": str(item.quantity),
                        "available": str(material.available_quantity),
                    }
                )
        if shortages:
            raise BusinessError(
                "INSUFFICIENT_AVAILABLE_STOCK",
                "批准时可用库存已不足",
                details={"shortages": shortages},
            )
        return payload

    def _validate_build_plan(
        self,
        proposal: AgentActionProposal,
        payload: ReservationProposalPayload,
    ) -> None:
        plan = self.db.scalar(
            select(BuildPlan)
            .where(BuildPlan.id == payload.build_plan_id)
            .with_for_update()
        )
        if plan is None:
            self._stale("构建计划不存在")
        if (
            plan.status != "reservation_pending"
            or plan.reservation_proposal_id != proposal.id
            or plan.snapshot_hash != payload.build_plan_snapshot_hash
        ):
            self._stale("构建计划与待审批 Proposal 的 provenance 不一致")

        revision = self.db.scalar(
            select(ProductRevision)
            .where(ProductRevision.id == plan.product_revision_id)
            .with_for_update()
        )
        if (
            revision is None
            or revision.status != "released"
            or revision.bom_hash != plan.product_bom_hash
        ):
            self._stale("产品版本状态或 BOM hash 已变化")

        project = self.db.scalar(
            select(Project).where(Project.id == plan.project_id).with_for_update()
        )
        if project is None or project.product_revision_id != plan.product_revision_id:
            self._stale("项目关联的产品版本已变化")
        if payload.project_id != plan.project_id:
            self._stale("Proposal 项目与构建计划不一致")

        plan_items = list(
            self.db.scalars(
                select(BuildPlanItem)
                .where(BuildPlanItem.build_plan_id == plan.id)
                .order_by(BuildPlanItem.material_id)
            ).all()
        )
        material_ids = [item.material_id for item in plan_items]
        materials = list(
            self.db.scalars(
                select(Material)
                .where(Material.id.in_(material_ids))
                .order_by(Material.id)
                .with_for_update()
            ).all()
        )
        by_id = {material.id: material for material in materials}
        unavailable = [
            material_id
            for material_id in material_ids
            if material_id not in by_id
            or by_id[material_id].is_deleted
            or not by_id[material_id].is_active
        ]
        if unavailable:
            self._stale(
                "构建计划中的物料已停用或删除",
                {"material_ids": unavailable},
            )

        expected = {
            item.material_id: item.additional_reservation_required
            for item in plan_items
            if item.additional_reservation_required > 0
        }
        proposed = {item.material_id: item.quantity for item in payload.items}
        if proposed != expected:
            self._stale("Proposal 数量与已批准的构建计划快照不一致")

        reservations = list(
            self.db.scalars(
                select(ProjectReservation)
                .where(
                    ProjectReservation.project_id == plan.project_id,
                    ProjectReservation.material_id.in_(material_ids),
                )
                .order_by(ProjectReservation.material_id)
                .with_for_update()
            ).all()
        )
        current_reservations = {
            reservation.material_id: reservation.quantity
            for reservation in reservations
        }
        changed_baselines = [
            item.material_id
            for item in plan_items
            if current_reservations.get(item.material_id, 0)
            != item.reserved_for_project_at_plan
        ]
        if changed_baselines:
            self._stale(
                "项目现有预留基线已变化",
                {"material_ids": changed_baselines},
            )

        shortages = [
            {
                "material_id": material_id,
                "requested": str(quantity),
                "available": str(by_id[material_id].available_quantity),
            }
            for material_id, quantity in expected.items()
            if quantity > by_id[material_id].available_quantity
        ]
        if shortages:
            self._stale("当前可用库存已不足", {"shortages": shortages})

    @staticmethod
    def _stale(message: str, details: dict | None = None) -> None:
        raise BusinessError(
            "BUILD_PLAN_STALE",
            "构建计划已过期，请重新分析当前库存并生成新的预留方案。",
            409,
            details={"reason": message, **(details or {})},
        )
