"""Server-owned picking queries; never calls a provider or a write tool."""

import re

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, update

from app.core.exceptions import BusinessError
from app.models import AgentConversationContext, BuildPlan, PickTask
from app.schemas.agent import AgentQueryResponse
from app.services.picking import PickingService


def answer_picking_query(db, user, snapshot, message, request_id):
    if not re.search(r"拣|拿料|取料|下一站|这个抽屉拿|类似.*先拿|路线|PK-[A-Z0-9]+", message, re.I):
        return None
    # A read-only engineering-BOM request may mention picking only to forbid
    # creating a task.  Do not route that negative clause to the picking
    # permission gate; the engineering-research route must remain reachable.
    if re.search(r"不要创建[^。！？]{0,24}拣料任务", message):
        return None
    if not {"*", "picking:view"}.intersection(user.role.permissions or []):
        raise BusinessError("PICK_PERMISSION_REQUIRED", "缺少查看拣货权限", 403)
    service = PickingService(db)
    entities, pending = {}, {}
    answer = "请指定生产计划或拣货任务编号，或从项目 / 生产任务选择任务。"
    intent = "picking_clarification"
    task_match = re.search(r"PK-[A-Z0-9]+", message, re.I)
    plan_match = re.search(r"BP-[A-Z0-9]+", message, re.I)
    task = (
        db.scalar(select(PickTask).where(PickTask.pick_task_no == task_match[0].upper()))
        if task_match
        else None
    )
    if (
        not task
        and not task_match
        and not plan_match
        and snapshot.pending_disambiguation.get("kind") == "picking"
    ):
        task = db.get(PickTask, snapshot.pending_disambiguation.get("pick_task_id"))
    plan = (
        db.scalar(select(BuildPlan).where(BuildPlan.plan_no == plan_match[0].upper()))
        if plan_match
        else None
    )
    if not task and not plan and snapshot.selected_project_id and not task_match and not plan_match:
        plans = list(
            db.scalars(
                select(BuildPlan)
                .where(BuildPlan.project_id == snapshot.selected_project_id)
                .order_by(BuildPlan.id.desc())
                .limit(2)
            )
        )
        if len(plans) == 1:
            plan = plans[0]
    if task:
        detail = service.detail(task.id)
        entities["pick_task"] = detail
        pending = {"kind": "picking", "pick_task_id": task.id}
        stop = detail["next_stop"]
        answer = (
            f"{task.pick_task_no}：{task.status}。下一站 {stop['full_path']}，"
            f"库位 {stop['location_code']}，{stop['material_code']} "
            f"还需取 {stop['remaining_quantity']}。"
            if stop
            else f"{task.pick_task_no}：{task.status}，没有待取料站点。"
        )
        intent = "picking_next_stop"
        if "路线" in message:
            route = detail["route_plan"]
            answer += f"\n路线策略 {route.get('strategy', 'hierarchy_v1')}。"
            if task.warehouse_map_id:
                label = (
                    "基于示例仓库图的推荐路线"
                    if route.get("calibration_status") == "demo_synthetic"
                    else "基于配置仓库图的推荐路线"
                )
                answer += f"{label}；配置图距离 {route.get('total_distance_m', 0)} m。"
            else:
                answer += "先按最少实际库位数量分配，再按稳定库位层级排序，不代表物理最短路线。"
            answer += "\n" + route.get("optimization_note", "")
    elif plan:
        facts = service.readiness(plan.id)
        entities["picking_readiness"] = facts
        answer = "可开始拣货。" if facts["executable"] else "当前仅可预览，预留或实际库位尚未就绪。"
        answer += "\n" + "\n".join(
            f"{i['code']}：账面 {i['book_quantity']}；实际库位可定位 {i['locatable_quantity']}；"
            f"未定位 {i['unlocated_quantity']}；当前可分配 {i['allocatable_now']}；"
            f"缺口 {i['location_shortage']}。"
            for i in facts["items"]
        )
        intent = "picking_readiness"
    elif snapshot.selected_product_revision_id and snapshot.last_build_quantity:
        facts = service.preview_product(
            snapshot.selected_product_revision_id,
            snapshot.last_build_quantity,
            snapshot.selected_project_id,
        )
        entities["picking_readiness"] = facts
        answer = (
            f"{facts['product']['name']} × {facts['build_quantity']} 台，"
            "仅预览取料库位，未创建任务或预留。"
        )
        for row in facts["items"]:
            stops = (
                "；".join(f"{a['full_path']}：{a['planned_quantity']}" for a in row["allocations"])
                or "暂无可分配的实际库位"
            )
            answer += f"\n{row['code']}：{stops}；库位缺口 {row['location_shortage']}。"
        intent = "picking_preview"
    if re.search(r"确认|直接.*(?:拿|取|扣)|帮我.*(?:拿|取|扣)", message):
        answer += (
            "\n库存确认必须由操作员在拣货任务中核对库位、物料和数量后明确确认；"
            "Agent 不会自动扣库存。"
        )
        intent = "picking_confirmation_required"
    if re.search(r"类似|替代|旁边.*先拿", message):
        answer += "\nsimilar_to 不授权替代取料；请先完成上游 BOM alternate 和生产计划审批。"
    changed = db.execute(
        update(AgentConversationContext)
        .where(
            AgentConversationContext.id == snapshot.id,
            AgentConversationContext.user_id == user.id,
            AgentConversationContext.context_version == snapshot.context_version,
        )
        .values(
            pending_disambiguation=pending,
            context_version=snapshot.context_version + 1,
            last_intent=intent,
        )
    )
    if changed.rowcount != 1:
        db.rollback()
        raise BusinessError("AGENT_CONVERSATION_CONFLICT", "对话已更新，请重试", 409)
    db.commit()
    return AgentQueryResponse(
        answer=answer,
        narrative=answer,
        intent=intent,
        entities=jsonable_encoder(entities),
        request_id=request_id,
        conversation_id=snapshot.id,
        execution_mode="deterministic",
        model_call_count=0,
    )
