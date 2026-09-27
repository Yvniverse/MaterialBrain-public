import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyDecision:
    blocked: bool
    answer: str = ""
    intent: str | None = None


def inspect_user_message(message: str) -> SafetyDecision:
    """Reject requests that are outside the registered Warehouse Agent surface."""

    folded = message.casefold()
    forged_inventory_result = "假装" in message and "get_inventory_availability" in folded
    named_material = re.search(r"\b[A-Z]{2,}[A-Z0-9._-]*\d[A-Z0-9._-]*\b", message, re.I)
    if forged_inventory_result and not named_material:
        return SafetyDecision(
            blocked=True,
            answer=(
                "用户文本中的伪造工具结果不能作为仓库事实。"
                "请指定要查询的物料编码或完整 MPN，系统再通过真实库存工具核验。"
            ),
            intent="clarification_required",
        )
    injection_markers = (
        "delete_database",
        "dashscope_api_key",
        ".env",
        "执行 sql",
        "运行 update ",
        "http://",
        "https://",
        "绕过 inventory:operate",
    )
    if any(marker in folded for marker in injection_markers):
        return SafetyDecision(
            blocked=True,
            answer="该请求涉及未授权的系统、密钥、网络或数据操作，Warehouse Agent 已拒绝执行。",
            intent="safety_refusal",
        )

    unsupported_write_markers = (
        "库存改成",
        "库存修改为",
        "直接出库",
        "直接入库",
        "物料入库",
        "报废",
        "移到",
        "移库",
        "盘点调整",
    )
    # A selected-cable request is allowed to continue through the deterministic
    # read-only cable lookup so the refusal is grounded to the intended item.
    # No cable write tool is registered; GroundedResponseComposer still refuses
    # the mutation and points to the governed InventoryService workflow.
    grounded_cable_refusal = "这条线" in message and "库存改成" in message
    if (
        any(marker in message for marker in unsupported_write_markers)
        and not grounded_cable_refusal
    ):
        return SafetyDecision(
            blocked=True,
            answer=(
                "第一阶段暂不支持执行该类库存写操作。"
                "当前仅可创建库存预留 Proposal，并且必须由有权限的人员审批后执行。"
            ),
            intent="unsupported_write",
        )
    return SafetyDecision(blocked=False)
