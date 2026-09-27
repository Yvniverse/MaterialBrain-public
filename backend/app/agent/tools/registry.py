import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ValidationError

from app.agent.episode import canonical_hash
from app.core.exceptions import BusinessError
from app.schemas.agent import (
    AgentToolEvent,
    AgentUIAction,
    CableSearchArgs,
    ComponentEvidenceCompareArgs,
    ComponentRelationsArgs,
    ComponentRequirementArgs,
    DatasheetEvidenceArgs,
    LowStockArgs,
    MaterialIdArgs,
    PowerDesignArgs,
    ProductBomAlternatesArgs,
    ProductBomArgs,
    ProductBuildReadinessArgs,
    ProjectBomArgs,
    ProposeBuildMaterialReservationArgs,
    ProposeInventoryReservationArgs,
    SearchMaterialsArgs,
    SearchProductsArgs,
    SearchProjectsArgs,
)

from .cables import get_cable_detail, search_cables
from .common import ToolContext
from .components import search_components_by_requirement
from .evidence import compare_component_evidence, search_datasheet_evidence
from .inventory import get_inventory_availability, get_low_stock_materials
from .locations import find_material_locations
from .materials import get_material_detail, search_materials
from .picking import (
    PickingReadinessArgs,
    PickTaskArgs,
    get_build_picking_readiness,
    get_next_pick_stop,
    get_pick_task,
)
from .power import plan_power_design
from .products import (
    analyze_product_build_readiness,
    get_product_bom,
    search_products,
)
from .projects import analyze_project_bom_stock, get_project_bom, search_projects
from .proposals import (
    propose_build_material_reservation,
    propose_inventory_reservation,
)
from .relations import get_component_relations, get_product_bom_alternates

ToolHandler = Callable[[ToolContext, Any], dict]


ToolRiskLevel = Literal["read", "proposal", "approval_required", "prohibited_for_agent"]
ToolSideEffect = Literal["none", "proposal", "business_write"]
ToolIdempotency = Literal["none", "optional", "required"]


@dataclass(frozen=True)
class ToolCapability:
    risk_level: ToolRiskLevel
    side_effect: ToolSideEffect
    idempotency: ToolIdempotency
    human_approval: bool
    mcp_exposed: bool
    schema_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "side_effect": self.side_effect,
            "idempotency": self.idempotency,
            "human_approval": self.human_approval,
            "mcp_exposed": self.mcp_exposed,
            "schema_version": self.schema_version,
        }


READ_ONLY_CAPABILITY = ToolCapability(
    risk_level="read",
    side_effect="none",
    idempotency="none",
    human_approval=False,
    mcp_exposed=True,
    schema_version="1",
)

PROPOSAL_CAPABILITY = ToolCapability(
    risk_level="proposal",
    side_effect="proposal",
    idempotency="required",
    human_approval=True,
    mcp_exposed=False,
    schema_version="1",
)


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler
    entity_key: str
    capability: ToolCapability
    permissions: tuple[str, ...] = ("material:view",)


@dataclass
class ToolExecution:
    tool_call_id: str
    name: str
    output: dict
    event: AgentToolEvent
    entity_key: str | None = None
    ui_actions: list[AgentUIAction] = field(default_factory=list)
    proposal_ids: list[int] = field(default_factory=list)

    def tool_message(self) -> dict:
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "content": json.dumps(jsonable_encoder(self.output), ensure_ascii=False),
        }


def _permission_granted(granted: set[str], required: str) -> bool:
    """Return whether an agent tool's required permission is satisfied.

    Project management includes read access for agent tools.  Keeping this
    closure here avoids broadening the HTTP/API permission dependency while
    making tool authorization consistent with the existing project-suggestion
    rules.
    """

    return (
        "*" in granted
        or required in granted
        or required == "project:view"
        and "project:manage" in granted
    )


TOOLS = (
    RegisteredTool(
        "get_build_picking_readiness",
        "只读：生产预留和实际库位拣货准备度。",
        PickingReadinessArgs,
        get_build_picking_readiness,
        "picking_readiness",
        READ_ONLY_CAPABILITY,
        permissions=("picking:view",),
    ),
    RegisteredTool(
        "get_pick_task",
        "只读：读取确定拣货任务，不确认库存写入。",
        PickTaskArgs,
        get_pick_task,
        "pick_task",
        READ_ONLY_CAPABILITY,
        permissions=("picking:view",),
    ),
    RegisteredTool(
        "get_next_pick_stop",
        "只读：确定任务下一站、精确库位及取料数量。",
        PickTaskArgs,
        get_next_pick_stop,
        "next_pick_stop",
        READ_ONLY_CAPABILITY,
        permissions=("picking:view",),
    ),
    RegisteredTool(
        "search_cables",
        "按线缆类型、连接器、间距、Pin 数/转换、触点方向、端头、长度和最小可用库存"
        "确定性搜索线缆。长度只参与软排序；FFC/FPC 方向不明确且同向/反向并存时必须追问。"
        "只读，不自动选择替代线缆。",
        CableSearchArgs,
        search_cables,
        "cable_search",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "get_cable_detail",
        "按已确定 material_id 读取线缆规格、实时库存和 InventoryLot 实际库位。"
        "attributes.storage_location 仅是描述性回退，不是库位事实。",
        MaterialIdArgs,
        get_cable_detail,
        "cable_detail",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "search_components_by_requirement",
        "按明确工程要求搜索只读候选器件。仅用于选型/要求型查询；普通 SKU/MPN 查询继续使用 "
        "search_materials。输出始终是待工程验证候选，不代表兼容或替代获批。",
        ComponentRequirementArgs,
        search_components_by_requirement,
        "component_search",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "plan_power_design",
        "按输入/输出电压先确定 Buck 与 LDO 拓扑，再从已审计器件证据中筛选兼容 PMIC；"
        "返回库存、实际库位、证据引用、外围角色和服务端确定性损耗。只读，不能调用模型替代证据。",
        PowerDesignArgs,
        plan_power_design,
        "power_design",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "search_materials",
        "根据物料编码、名称、MPN、规格或厂家寻找候选物料。用户描述尚未对应确定 "
        "material_id 时先调用；多个候选必须全部返回，不得自行选择。此工具只做身份识别，"
        "不返回实时库存或库位；唯一候选后必须继续调用用户问题所需的事实工具。",
        SearchMaterialsArgs,
        search_materials,
        "material_candidates",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "get_material_detail",
        "查询一个已确定 material_id 的物料主数据与必要库存摘要。",
        MaterialIdArgs,
        get_material_detail,
        "material_detail",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "get_component_relations",
        "读取已登记的候选或已验证工程关系。similar_to 不是替代批准；只有显式 validated "
        "pin_compatible 记录才可陈述引脚兼容。此工具只读，不能验证或批准关系。",
        ComponentRelationsArgs,
        get_component_relations,
        "component_relations",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "search_datasheet_evidence",
        "在服务端已限定的物料范围内检索当前工程证据，并返回可校验的文档/页码/锚点引用。"
        "仅在用户明确询问 datasheet、技术参数或证据时使用；默认排除旧版和撤回文档。只读，"
        "不能验证、批准、撤销关系或备选。",
        DatasheetEvidenceArgs,
        search_datasheet_evidence,
        "engineering_evidence",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "compare_component_evidence",
        "分别在两个已解析物料的当前文档范围内比较技术证据；缺失字段必须返回 unknown。"
        "只读且不产生兼容、替代或批准决策。",
        ComponentEvidenceCompareArgs,
        compare_component_evidence,
        "component_evidence_comparison",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "get_inventory_availability",
        "查询一个已确定 material_id 的实时库存、预留、可用量及安全库存状态。"
        "用户询问数量、库存、还有多少或安全库存时必须调用；实时数量必须来自此工具。",
        MaterialIdArgs,
        get_inventory_availability,
        "inventory",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "find_material_locations",
        "查询一个已确定 material_id 的实时物理位置，合并主库位与所有有库存的 InventoryLot。"
        "用户询问在哪里、库位或位置时必须调用。",
        MaterialIdArgs,
        find_material_locations,
        "locations",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "search_projects",
        "根据项目编号或名称寻找候选项目。用户只提供项目名称且没有确定 project_id 时先调用。",
        SearchProjectsArgs,
        search_projects,
        "project_candidates",
        READ_ONLY_CAPABILITY,
        permissions=("project:view",),
    ),
    RegisteredTool(
        "search_products",
        "根据产品编号或产品名称寻找候选产品，并返回已发布版本与默认版本。产品单台 BOM 和按台数"
        "备料查询必须先解析 Product，不得使用历史 Project BOM 代替。",
        SearchProductsArgs,
        search_products,
        "product_candidates",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "get_product_bom",
        "读取确定产品版本的单台 BOM。quantity_per_unit 表示生产一台成品的用量。",
        ProductBomArgs,
        get_product_bom,
        "product_bom",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "analyze_product_build_readiness",
        "确定性分析生产 N 台产品是否备料充足。服务端负责全部 Decimal 乘法、缺料、安全库存风险"
        "与最大可构建数量；此工具只读，不预留、不修改库存。",
        ProductBuildReadinessArgs,
        analyze_product_build_readiness,
        "build_readiness",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "get_product_bom_alternates",
        "读取精确 ProductRevision/BOM 位范围内的候选或已批准备选。批准不得跨产品泄漏，"
        "且备选库存不参与本阶段 Build Readiness 或 BuildPlan 算术。此工具只读。",
        ProductBomAlternatesArgs,
        get_product_bom_alternates,
        "product_bom_alternates",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "get_project_bom",
        "查询一个确定 project_id 的当前数据库 BOM；可按 version 过滤。",
        ProjectBomArgs,
        get_project_bom,
        "project_bom",
        READ_ONLY_CAPABILITY,
        permissions=("project:view",),
    ),
    RegisteredTool(
        "analyze_project_bom_stock",
        "按数据库现有 required_quantity 分析项目 BOM 的实时可用量、项目已预留量与缺料，"
        "不乘以额外构建台数。",
        ProjectBomArgs,
        analyze_project_bom_stock,
        "bom_analysis",
        READ_ONLY_CAPABILITY,
        permissions=("project:view",),
    ),
    RegisteredTool(
        "get_low_stock_materials",
        "查询实时可用库存严格低于安全库存且安全库存大于 0 的物料。",
        LowStockArgs,
        get_low_stock_materials,
        "low_stock",
        READ_ONLY_CAPABILITY,
    ),
    RegisteredTool(
        "propose_build_material_reservation",
        "根据已发布 ProductRevision、明确关联 Project 和构建数量创建不可变 BuildPlan，"
        "再生成待人工审批的 reserve_inventory Proposal。模型不得提供任何物料数量；"
        "服务端会扣除当前项目已有预留。批准前绝不修改库存。",
        ProposeBuildMaterialReservationArgs,
        propose_build_material_reservation,
        "build_plan_proposal",
        PROPOSAL_CAPABILITY,
        permissions=("project:manage", "inventory:operate"),
    ),
    RegisteredTool(
        "propose_inventory_reservation",
        "为确定项目和物料清单创建待人工审批的 reserve_inventory Proposal。"
        "此工具绝不修改库存或预留数量。",
        ProposeInventoryReservationArgs,
        propose_inventory_reservation,
        "proposal",
        PROPOSAL_CAPABILITY,
        permissions=("project:manage", "inventory:operate"),
    ),
)


class ToolRegistry:
    def __init__(
        self,
        *,
        allowed_names: set[str] | None = None,
        component_intelligence_enabled: bool = False,
    ):
        enabled_names = allowed_names
        if not component_intelligence_enabled:
            base_names = allowed_names or {tool.name for tool in TOOLS}
            enabled_names = base_names - {"search_components_by_requirement"}
        selected = (
            TOOLS
            if enabled_names is None
            else (tool for tool in TOOLS if tool.name in enabled_names)
        )
        self._tools = {tool.name: tool for tool in selected}

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    @property
    def mcp_exposed_names(self) -> set[str]:
        return {
            tool.name
            for tool in self._tools.values()
            if tool.capability.mcp_exposed
            and tool.capability.risk_level == "read"
            and tool.capability.side_effect == "none"
        }

    def registered(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def capability_matrix(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "args_model": tool.args_model.__name__,
                "entity_key": tool.entity_key,
                "permissions": list(tool.permissions),
                **tool.capability.as_dict(),
            }
            for tool in self._tools.values()
        ]

    def schema_digest(self) -> str:
        payload = [
            {
                "name": tool.name,
                "parameters": tool.args_model.model_json_schema(),
                "permissions": list(tool.permissions),
                "capability": tool.capability.as_dict(),
            }
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
        ]
        canonical = json.dumps(
            jsonable_encoder(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def schemas(self, allowed_names: set[str] | None = None) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.args_model.model_json_schema(),
                },
            }
            for tool in self._tools.values()
            if allowed_names is None or tool.name in allowed_names
        ]

    def execute(self, ctx: ToolContext, tool_call: dict) -> ToolExecution:
        started = time.perf_counter()
        call_id = str(tool_call.get("id") or "tool-call")
        function = tool_call.get("function") or {}
        name = str(function.get("name") or "")
        raw_args = function.get("arguments") or "{}"
        argument_value: Any = raw_args
        if isinstance(raw_args, str):
            try:
                argument_value = json.loads(raw_args)
            except json.JSONDecodeError:
                # The raw value is never stored; canonical_hash only records a
                # fingerprint for the trace contract.
                argument_value = {
                    "invalid_json_sha256": hashlib.sha256(raw_args.encode()).hexdigest()
                }
        registered = self._tools.get(name)
        if not registered:
            return self._error(
                ctx,
                call_id,
                name or "unknown",
                "TOOL_NOT_ALLOWED",
                "该工具未在服务端白名单中注册",
                started,
                argument_value=argument_value,
                authorization="denied",
            )

        granted = set(ctx.user.role.permissions or [])
        missing_permissions = [
            permission
            for permission in registered.permissions
            if not _permission_granted(granted, permission)
        ]
        if missing_permissions:
            return self._error(
                ctx,
                call_id,
                name,
                "TOOL_PERMISSION_DENIED",
                f"缺少权限：{', '.join(missing_permissions)}",
                started,
                argument_value=argument_value,
                authorization="denied",
                registered=registered,
            )

        try:
            values = argument_value
            args = registered.args_model.model_validate(values)
            output = registered.handler(ctx, args)
        except (json.JSONDecodeError, ValidationError) as exc:
            return self._error(
                ctx,
                call_id,
                name,
                "TOOL_ARGUMENT_VALIDATION_ERROR",
                "工具参数校验失败",
                started,
                {"reason": str(exc)},
                argument_value=argument_value,
                authorization="allowed",
                registered=registered,
            )
        except BusinessError as exc:
            return self._error(
                ctx,
                call_id,
                name,
                exc.code,
                exc.message,
                started,
                exc.details,
                argument_value=argument_value,
                authorization="allowed",
                registered=registered,
            )
        except Exception:
            return self._error(
                ctx,
                call_id,
                name,
                "TOOL_EXECUTION_ERROR",
                "工具执行失败",
                started,
                argument_value=argument_value,
                authorization="allowed",
                registered=registered,
            )

        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        actions, proposal_ids = self._artifacts(name, output)
        execution = ToolExecution(
            tool_call_id=call_id,
            name=name,
            output={"ok": True, "data": output},
            event=AgentToolEvent(
                tool=name,
                status="success",
                summary=self._summary(name, output),
                duration_ms=duration_ms,
            ),
            entity_key=registered.entity_key,
            ui_actions=actions,
            proposal_ids=proposal_ids,
        )
        self._append_trace(
            ctx,
            call_id=call_id,
            name=name,
            registered=registered,
            argument_value=argument_value,
            authorization="allowed",
            status="success",
            error_code=None,
            duration_ms=execution.event.duration_ms,
            output=execution.output,
            entity_key=registered.entity_key,
        )
        return execution

    def _error(
        self,
        ctx: ToolContext,
        call_id: str,
        name: str,
        code: str,
        message: str,
        started: float,
        details: dict | None = None,
        *,
        argument_value: Any = None,
        authorization: str = "not_applicable",
        registered: RegisteredTool | None = None,
    ) -> ToolExecution:
        execution = ToolExecution(
            tool_call_id=call_id,
            name=name,
            output={
                "ok": False,
                "error": {"code": code, "message": message, "details": details or {}},
            },
            event=AgentToolEvent(
                tool=name,
                status="error",
                summary=message,
                duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
                error_code=code,
            ),
        )
        self._append_trace(
            ctx,
            call_id=call_id,
            name=name,
            registered=registered,
            argument_value=argument_value,
            authorization=authorization,
            status="error",
            error_code=code,
            duration_ms=execution.event.duration_ms,
            output=execution.output,
            entity_key=registered.entity_key if registered else None,
        )
        return execution

    @staticmethod
    def _append_trace(
        ctx: ToolContext,
        *,
        call_id: str,
        name: str,
        registered: RegisteredTool | None,
        argument_value: Any,
        authorization: str,
        status: str,
        error_code: str | None,
        duration_ms: int,
        output: dict[str, Any],
        entity_key: str | None,
    ) -> None:
        if ctx.trace_steps is None:
            return
        capability = registered.capability if registered else None
        ctx.trace_steps.append(
            {
                "sequence": len(ctx.trace_steps),
                "call_id": call_id,
                "tool": name or "unknown",
                "schema_version": capability.schema_version if capability else "unknown",
                "argument_hash": canonical_hash(argument_value),
                "authorization": authorization,
                "status": status,
                "error_code": error_code,
                "duration_ms": max(0, int(duration_ms)),
                "result_hash": canonical_hash(output),
                "entity_key": entity_key,
                "replayable": bool(
                    capability
                    and capability.risk_level == "read"
                    and capability.side_effect == "none"
                ),
            }
        )

    @staticmethod
    def _summary(name: str, output: dict) -> str:
        if name == "search_cables":
            return f"找到 {output['count']} 条匹配线缆"
        if name == "get_cable_detail":
            return f"已读取线缆 {output['code']}"
        if name == "search_materials":
            return f"找到 {output['count']} 个候选物料"
        if name == "search_components_by_requirement":
            return f"找到 {output['count']} 个工程候选器件"
        if name == "plan_power_design":
            return f"已生成电源设计方案，包含 {len(output.get('branches') or [])} 个拓扑分支"
        if name == "get_material_detail":
            return f"已读取物料 {output['code']}"
        if name == "get_component_relations":
            return f"找到 {output['count']} 条器件工程关系"
        if name == "search_datasheet_evidence":
            return f"找到 {len(output['citations'])} 条页码级工程证据"
        if name == "compare_component_evidence":
            return f"完成 {len(output['comparisons'])} 个证据字段比较"
        if name == "get_inventory_availability":
            return f"已查询 {output['code']} 的实时库存"
        if name == "find_material_locations":
            return f"找到 {output['count']} 个实际库位"
        if name == "get_project_bom":
            return f"已读取 {output['count']} 个 BOM 项"
        if name == "search_projects":
            return f"找到 {output['count']} 个候选项目"
        if name == "search_products":
            return f"找到 {output['count']} 个候选产品"
        if name == "get_product_bom":
            return f"已读取 {output['count']} 个单台 BOM 项"
        if name == "analyze_product_build_readiness":
            return (
                f"已分析 {output['build_quantity']} 台构建，{output['shortage_count']} 类物料短缺"
            )
        if name == "get_product_bom_alternates":
            return (
                f"找到 {output['approved_count']} 个已批准备选、"
                f"{output['candidate_count']} 个候选备选"
            )
        if name == "analyze_project_bom_stock":
            return f"完成 BOM 分析，{output['shortage_count']} 项缺料"
        if name == "get_low_stock_materials":
            return f"找到 {output['count']} 个低库存物料"
        if name == "propose_inventory_reservation":
            return f"已创建待审批 Proposal {output['proposal_no']}"
        if name == "propose_build_material_reservation":
            if output.get("fully_reserved"):
                return "项目现有预留已完全覆盖该构建需求"
            return (
                f"已创建构建计划 {output['build_plan']['plan_no']} 和待审批 "
                f"Proposal {output['proposal_no']}"
            )
        return "工具执行成功"

    @staticmethod
    def _artifacts(name: str, output: dict) -> tuple[list[AgentUIAction], list[int]]:
        actions: list[AgentUIAction] = []
        proposal_ids: list[int] = []
        if name == "search_cables":
            actions.extend(
                AgentUIAction(type="open_cable", target_id=item["material_id"])
                for item in output["items"]
            )
            location_ids = {
                location["location_id"]
                for item in output["items"]
                for location in item.get("locations", [])
            }
            actions.extend(
                AgentUIAction(type="focus_location", target_id=location_id)
                for location_id in sorted(location_ids)
            )
        elif name == "get_cable_detail":
            actions.append(AgentUIAction(type="open_cable", target_id=output["material_id"]))
            actions.extend(
                AgentUIAction(type="focus_location", target_id=item["location_id"])
                for item in output.get("locations", [])
            )
        elif name == "search_materials":
            actions.extend(
                AgentUIAction(type="open_material", target_id=item["id"])
                for item in output["items"]
            )
        elif name == "search_components_by_requirement":
            actions.extend(
                AgentUIAction(type="open_material", target_id=item["material_id"])
                for item in output["candidates"]
            )
        elif name in {"get_material_detail", "get_inventory_availability"}:
            target_id = output.get("id") or output.get("material_id")
            actions.append(AgentUIAction(type="open_material", target_id=target_id))
        elif name == "get_component_relations":
            related = {
                material["id"]
                for item in output["items"]
                for material in (
                    item["source_material"],
                    item["target_material"],
                )
            }
            actions.extend(
                AgentUIAction(type="open_material", target_id=material_id)
                for material_id in sorted(related)
            )
        elif name in {"search_datasheet_evidence", "compare_component_evidence"}:
            actions.extend(
                AgentUIAction(type="open_material", target_id=item["id"])
                for item in output.get("materials", output.get("material_scope", []))
            )
        elif name == "find_material_locations":
            if output.get("distribution_status") != "inconsistent":
                actions.extend(
                    AgentUIAction(type="focus_location", target_id=item["location_id"])
                    for item in output["locations"]
                )
        elif name == "search_projects":
            actions.extend(
                AgentUIAction(type="open_project", target_id=item["id"]) for item in output["items"]
            )
        elif name == "search_products":
            actions.extend(
                AgentUIAction(type="open_product", target_id=item["id"]) for item in output["items"]
            )
        elif name in {"get_product_bom", "analyze_product_build_readiness"}:
            actions.append(AgentUIAction(type="open_product", target_id=output["product"]["id"]))
        elif name == "get_product_bom_alternates":
            product_ids = {item["product"]["id"] for item in output["items"]}
            actions.extend(
                AgentUIAction(type="open_product", target_id=product_id)
                for product_id in sorted(product_ids)
            )
        elif name in {"get_project_bom", "analyze_project_bom_stock"}:
            actions.append(AgentUIAction(type="open_project", target_id=output["project"]["id"]))
        elif name in {
            "propose_inventory_reservation",
            "propose_build_material_reservation",
        } and output.get("id"):
            proposal_ids.append(output["id"])
            actions.append(
                AgentUIAction(
                    type="request_approval",
                    target_id=output["id"],
                    payload={"proposal_no": output["proposal_no"]},
                )
            )
        return actions, proposal_ids


READ_ONLY_TOOL_NAMES = {
    "search_cables",
    "get_cable_detail",
    "search_materials",
    "get_material_detail",
    "get_inventory_availability",
    "find_material_locations",
    "get_low_stock_materials",
    "search_projects",
    "get_project_bom",
    "analyze_project_bom_stock",
    "search_products",
    "get_product_bom",
    "analyze_product_build_readiness",
    "search_components_by_requirement",
    "plan_power_design",
    "get_component_relations",
    "get_product_bom_alternates",
    "search_datasheet_evidence",
    "compare_component_evidence",
}


class ReadOnlyToolRegistry(ToolRegistry):
    """Registry used by Production Shadow Eval; proposal capability does not exist."""

    def __init__(self, *, component_intelligence_enabled: bool = False):
        super().__init__(
            allowed_names=READ_ONLY_TOOL_NAMES,
            component_intelligence_enabled=component_intelligence_enabled,
        )
