import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.policies import ActionGuard
from app.core.exceptions import BusinessError
from app.models import AgentActionProposal, BuildPlan, BuildPlanItem, Material, Project, User
from app.schemas.agent import (
    ProposeBuildMaterialReservationArgs,
    ProposeInventoryReservationArgs,
    ReservationProposalPayload,
)
from app.services.audit import add_audit
from app.services.build_plans import BuildPlanService
from app.services.inventory import InventoryService


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ProposalService:
    def __init__(
        self,
        db: Session,
        user: User,
        request_id: str,
        client_operation_id: str | None = None,
    ):
        self.db = db
        self.user = user
        self.request_id = request_id
        self.client_operation_id = client_operation_id or uuid.uuid4().hex

    def create_reservation(
        self,
        args: ProposeInventoryReservationArgs,
        *,
        commit: bool = True,
        source: str = "manual",
        build_plan_id: int | None = None,
        build_plan_snapshot_hash: str | None = None,
    ) -> AgentActionProposal:
        project = self.db.get(Project, args.project_id)
        if not project:
            raise BusinessError("PROJECT_NOT_FOUND", "项目不存在", 404)

        material_ids = [item.material_id for item in args.items]
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
                "预留建议包含不存在的物料",
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

        shortages = [
            {
                "material_id": item.material_id,
                "requested": str(item.quantity),
                "available": str(by_id[item.material_id].available_quantity),
            }
            for item in args.items
            if item.quantity > by_id[item.material_id].available_quantity
        ]
        if shortages:
            raise BusinessError(
                "INSUFFICIENT_AVAILABLE_STOCK",
                "当前库存不足，未创建预留 Proposal",
                details={"shortages": shortages},
            )

        payload = ReservationProposalPayload(
            project_id=args.project_id,
            items=args.items,
            reason=args.reason,
            source=source,
            build_plan_id=build_plan_id,
            build_plan_snapshot_hash=build_plan_snapshot_hash,
        )
        payload_data = payload.model_dump(mode="json")
        payload_hash = hashlib.sha256(
            json.dumps(
                payload_data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        existing = self.db.scalar(
            select(AgentActionProposal).where(
                AgentActionProposal.created_by_id == self.user.id,
                AgentActionProposal.client_operation_id == self.client_operation_id,
                AgentActionProposal.action_type == "reserve_inventory",
            )
        )
        if existing:
            if existing.payload_hash != payload_hash:
                raise BusinessError(
                    "PROPOSAL_IDEMPOTENCY_CONFLICT",
                    "同一业务操作 ID 已用于不同的 Proposal 内容",
                    409,
                )
            return existing
        proposal = AgentActionProposal(
            proposal_no=f"AP-{uuid.uuid4().hex[:18].upper()}",
            action_type="reserve_inventory",
            status="pending",
            payload=payload_data,
            reason=args.reason,
            created_by_id=self.user.id,
            request_id=self.request_id,
            client_operation_id=self.client_operation_id,
            payload_hash=payload_hash,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        self.db.add(proposal)
        try:
            self.db.flush()
            add_audit(
                self.db,
                self.user.id,
                "agent.proposal.create",
                "agent_action_proposal",
                str(proposal.id),
                self.request_id,
                after={
                    "proposal_no": proposal.proposal_no,
                    "action_type": proposal.action_type,
                    "project_id": args.project_id,
                    "item_count": len(args.items),
                },
            )
            if commit:
                self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            existing = self.db.scalar(
                select(AgentActionProposal).where(
                    AgentActionProposal.created_by_id == self.user.id,
                    AgentActionProposal.client_operation_id == self.client_operation_id,
                    AgentActionProposal.action_type == "reserve_inventory",
                )
            )
            if existing and existing.payload_hash == payload_hash:
                return existing
            raise BusinessError(
                "PROPOSAL_IDEMPOTENCY_CONFLICT",
                "业务操作 ID 冲突，未创建重复 Proposal",
                409,
            ) from exc
        if commit:
            self.db.refresh(proposal)
        return proposal

    def create_build_plan_reservation(
        self,
        args: ProposeBuildMaterialReservationArgs,
    ) -> dict:
        plan = BuildPlanService(
            self.db,
            self.user.id,
            self.request_id,
        ).create(
            product_revision_id=args.product_revision_id,
            project_id=args.project_id,
            build_quantity=args.build_quantity,
            client_operation_id=f"build-plan:{self.client_operation_id}",
            notes=args.reason,
            commit=False,
        )
        return self._create_for_plan(plan, args.reason)

    def create_reservation_for_plan(self, plan_id: int, reason: str) -> dict:
        plan = self.db.scalar(
            select(BuildPlan).where(BuildPlan.id == plan_id).with_for_update()
        )
        if plan is None:
            raise BusinessError("BUILD_PLAN_NOT_FOUND", "构建计划不存在", 404)
        return self._create_for_plan(plan, reason)

    def _create_for_plan(self, plan: BuildPlan, reason: str) -> dict:
        if plan.status == "reservation_pending" and plan.reservation_proposal_id:
            proposal = self.db.get(
                AgentActionProposal,
                plan.reservation_proposal_id,
            )
            if proposal is None:
                plan.status = "stale"
                plan.stale_reason = "关联的预留 Proposal 不存在"
                self._audit_build_plan_terminal(
                    plan,
                    "stale",
                    code="PROPOSAL_NOT_FOUND",
                    success=False,
                )
                self.db.commit()
                raise BusinessError(
                    "BUILD_PLAN_PROPOSAL_TERMINAL",
                    "原预留审批已失效，请重新分析并生成新的构建计划。",
                    409,
                    details={"proposal_status": "missing"},
                )
            if (
                proposal.status == "pending"
                and proposal.expires_at
                and _as_utc(proposal.expires_at) <= datetime.now(UTC)
            ):
                proposal.status = "expired"
                proposal.decided_at = datetime.now(UTC)
                proposal.error_message = "Proposal 已过期"
                add_audit(
                    self.db,
                    self.user.id,
                    "agent.proposal.expire",
                    "agent_action_proposal",
                    str(proposal.id),
                    self.request_id,
                    before={"status": "pending"},
                    after={"status": "expired"},
                    success=False,
                )
            if proposal.status == "pending":
                return {"plan": plan, "proposal": proposal, "fully_reserved": False}
            self._sync_build_plan_on_proposal_terminal(
                proposal,
                reason=proposal.error_message or f"Proposal 状态为 {proposal.status}",
                code=f"PROPOSAL_{proposal.status.upper()}",
            )
            self.db.commit()
            if proposal.status == "executed":
                self.db.refresh(plan)
                return {"plan": plan, "proposal": None, "fully_reserved": True}
            raise BusinessError(
                "BUILD_PLAN_PROPOSAL_TERMINAL",
                "原预留审批已结束，请重新分析并生成新的构建计划。",
                409,
                details={"proposal_status": proposal.status},
            )
        if plan.status == "reserved":
            return {"plan": plan, "proposal": None, "fully_reserved": True}
        if plan.status != "ready":
            raise BusinessError(
                "BUILD_PLAN_NOT_READY",
                "当前构建计划不能创建预留 Proposal",
                409,
                details={"status": plan.status},
            )

        plan_items = list(
            self.db.scalars(
                select(BuildPlanItem)
                .where(BuildPlanItem.build_plan_id == plan.id)
                .order_by(BuildPlanItem.material_id)
            ).all()
        )
        additional_items = [
            {
                "material_id": item.material_id,
                "quantity": item.additional_reservation_required,
            }
            for item in plan_items
            if item.additional_reservation_required > 0
        ]
        if not additional_items:
            plan.status = "reserved"
            plan.reserved_at = datetime.now(UTC)
            add_audit(
                self.db,
                self.user.id,
                "build_plan.already_fully_reserved",
                "build_plan",
                str(plan.id),
                self.request_id,
                after={"status": "reserved", "proposal_created": False},
            )
            self.db.commit()
            self.db.refresh(plan)
            return {"plan": plan, "proposal": None, "fully_reserved": True}

        proposal_args = ProposeInventoryReservationArgs(
            project_id=plan.project_id,
            items=additional_items,
            reason=reason,
        )
        proposal = self.create_reservation(
            proposal_args,
            commit=False,
            source="build_plan",
            build_plan_id=plan.id,
            build_plan_snapshot_hash=plan.snapshot_hash,
        )
        plan.status = "reservation_pending"
        plan.reservation_proposal_id = proposal.id
        add_audit(
            self.db,
            self.user.id,
            "build_plan.reservation_proposed",
            "build_plan",
            str(plan.id),
            self.request_id,
            before={"status": "ready"},
            after={
                "status": "reservation_pending",
                "proposal_id": proposal.id,
                "additional_item_count": len(additional_items),
            },
        )
        self.db.commit()
        self.db.refresh(plan)
        self.db.refresh(proposal)
        return {"plan": plan, "proposal": proposal, "fully_reserved": False}

    def approve(self, proposal_id: int) -> AgentActionProposal:
        proposal = self.db.scalar(
            select(AgentActionProposal)
            .where(AgentActionProposal.id == proposal_id)
            .with_for_update()
        )
        if not proposal:
            raise BusinessError("PROPOSAL_NOT_FOUND", "Proposal 不存在", 404)

        try:
            payload = ActionGuard(self.db).validate_reservation(
                proposal,
                self.user.role.permissions,
            )
            now = datetime.now(UTC)
            proposal.status = "approved"
            proposal.approved_by_id = self.user.id
            proposal.decided_at = now
            self.db.flush()

            result = InventoryService(
                self.db,
                self.user.id,
                self.request_id,
            ).reserve_batch(
                payload.project_id,
                [item.model_dump(mode="json") for item in payload.items],
                f"agent-proposal-{proposal.id}",
                payload.reason,
                notes=f"Warehouse Agent Proposal {proposal.proposal_no}",
                commit=False,
            )
            build_plan = None
            if payload.source == "build_plan":
                build_plan = self.db.scalar(
                    select(BuildPlan)
                    .where(BuildPlan.id == payload.build_plan_id)
                    .with_for_update()
                )
                if build_plan is None:
                    raise BusinessError(
                        "BUILD_PLAN_STALE",
                        "构建计划已过期，请重新分析当前库存并生成新的预留方案。",
                        409,
                    )
                build_plan.status = "reserved"
                build_plan.reserved_at = now
                build_plan.stale_reason = ""
            proposal.status = "executed"
            proposal.execution_result = result
            proposal.executed_at = now
            proposal.error_message = ""
            add_audit(
                self.db,
                self.user.id,
                "agent.proposal.approve",
                "agent_action_proposal",
                str(proposal.id),
                self.request_id,
                before={"status": "pending"},
                after={"status": "approved"},
            )
            add_audit(
                self.db,
                self.user.id,
                "agent.proposal.execute",
                "agent_action_proposal",
                str(proposal.id),
                self.request_id,
                after={"status": "executed", "result": result},
            )
            if build_plan is not None:
                add_audit(
                    self.db,
                    self.user.id,
                    "build_plan.reserve",
                    "build_plan",
                    str(build_plan.id),
                    self.request_id,
                    before={"status": "reservation_pending"},
                    after={"status": "reserved", "proposal_id": proposal.id},
                )
            self.db.commit()
            self.db.refresh(proposal)
            return proposal
        except BusinessError as exc:
            self.db.rollback()
            if exc.code == "PROPOSAL_NOT_PENDING" or exc.status_code == 403:
                raise
            failed = self.db.scalar(
                select(AgentActionProposal)
                .where(AgentActionProposal.id == proposal_id)
                .with_for_update()
            )
            if failed and failed.status == "pending":
                failed.status = "expired" if exc.code == "PROPOSAL_EXPIRED" else "failed"
                failed.approved_by_id = self.user.id
                failed.decided_at = datetime.now(UTC)
                failed.error_message = exc.message[:2000]
                add_audit(
                    self.db,
                    self.user.id,
                    "agent.proposal.failed",
                    "agent_action_proposal",
                    str(failed.id),
                    self.request_id,
                    after={"status": failed.status, "code": exc.code},
                    success=False,
                )
                self._sync_build_plan_on_proposal_terminal(
                    failed,
                    reason=exc.message,
                    code=exc.code,
                )
                self.db.commit()
            raise
        except Exception:
            self.db.rollback()
            failed = self.db.get(AgentActionProposal, proposal_id)
            if failed and failed.status == "pending":
                failed.status = "failed"
                failed.approved_by_id = self.user.id
                failed.decided_at = datetime.now(UTC)
                failed.error_message = "执行库存预留时发生内部错误"
                add_audit(
                    self.db,
                    self.user.id,
                    "agent.proposal.failed",
                    "agent_action_proposal",
                    str(failed.id),
                    self.request_id,
                    after={"status": "failed", "code": "INTERNAL_ERROR"},
                    success=False,
                )
                self._sync_build_plan_on_proposal_terminal(
                    failed,
                    reason=failed.error_message,
                    code="INTERNAL_ERROR",
                )
                self.db.commit()
            raise

    def reject(self, proposal_id: int, reason: str) -> AgentActionProposal:
        proposal = self.db.scalar(
            select(AgentActionProposal)
            .where(AgentActionProposal.id == proposal_id)
            .with_for_update()
        )
        if not proposal:
            raise BusinessError("PROPOSAL_NOT_FOUND", "Proposal 不存在", 404)
        permissions = set(self.user.role.permissions or [])
        can_reject_any = "*" in permissions or "inventory:operate" in permissions
        if proposal.created_by_id != self.user.id and not can_reject_any:
            raise BusinessError("PROPOSAL_REJECT_FORBIDDEN", "无权拒绝该 Proposal", 403)
        if proposal.status != "pending":
            raise BusinessError("PROPOSAL_NOT_PENDING", "Proposal 已处理", 409)
        proposal.status = "rejected"
        proposal.decided_at = datetime.now(UTC)
        proposal.error_message = reason
        self._sync_build_plan_on_proposal_terminal(
            proposal,
            reason=reason,
            code="PROPOSAL_REJECTED",
        )
        add_audit(
            self.db,
            self.user.id,
            "agent.proposal.reject",
            "agent_action_proposal",
            str(proposal.id),
            self.request_id,
            before={"status": "pending"},
            after={"status": "rejected", "reason": reason},
        )
        self.db.commit()
        self.db.refresh(proposal)
        return proposal

    def _sync_build_plan_on_proposal_terminal(
        self,
        proposal: AgentActionProposal,
        *,
        reason: str,
        code: str,
    ) -> BuildPlan | None:
        try:
            payload = ReservationProposalPayload.model_validate(proposal.payload)
        except ValueError:
            return None
        if payload.source != "build_plan" or payload.build_plan_id is None:
            return None
        plan = self.db.scalar(
            select(BuildPlan)
            .where(BuildPlan.id == payload.build_plan_id)
            .with_for_update()
        )
        if plan is None or plan.status != "reservation_pending":
            return plan
        if proposal.status == "executed":
            target = "reserved"
            plan.reserved_at = proposal.executed_at or datetime.now(UTC)
            plan.stale_reason = ""
        elif proposal.status == "rejected":
            target = "cancelled"
            plan.stale_reason = reason[:2000]
        else:
            target = "stale"
            plan.stale_reason = reason[:2000]
        plan.status = target
        self._audit_build_plan_terminal(
            plan,
            target,
            code=code,
            success=target == "reserved",
        )
        return plan

    def _audit_build_plan_terminal(
        self,
        plan: BuildPlan,
        target_status: str,
        *,
        code: str,
        success: bool,
    ) -> None:
        add_audit(
            self.db,
            self.user.id,
            f"build_plan.{target_status}",
            "build_plan",
            str(plan.id),
            self.request_id,
            before={"status": "reservation_pending"},
            after={"status": target_status, "code": code},
            success=success,
        )
