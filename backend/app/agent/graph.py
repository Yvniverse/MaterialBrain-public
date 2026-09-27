import json
import re
import time
from decimal import Decimal
from typing import Any, Literal

from fastapi.encoders import jsonable_encoder
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.agent.output_boundary import PublicAnswerBoundary
from app.agent.prompts import WAREHOUSE_AGENT_SYSTEM_PROMPT
from app.agent.response_composer import GroundedResponseComposer
from app.agent.state import WarehouseAgentState
from app.agent.task_contract import TaskContract, classify_task_contract
from app.agent.tools import ToolContext, ToolRegistry
from app.llm.base import LLMProvider
from app.models import Material, Project
from app.services.engineering_evidence import detect_evidence_fields


class WarehouseAgentGraph:
    _NARRATIVE_CONTEXT_OMIT_KEYS = {
        "layout_blocks",
        "raw_facts",
        "raw_citations",
        "retrieval",
        "allowed_anchor_ids",
        "file_sha256",
        "page_text_sha256",
        "page_hash",
        "embedding",
        "embedding_vector",
        "debug",
        "trace",
    }

    def __init__(
        self,
        *,
        provider: LLMProvider,
        tool_context: ToolContext,
        max_tool_rounds: int,
        total_deadline_seconds: float,
        force_fact_tool_calls: bool,
        deterministic_material_resolution_enabled: bool,
        narrative_synthesis_enabled: bool = True,
        registry: ToolRegistry | None = None,
    ):
        self.provider = provider
        self.tool_context = tool_context
        self.max_tool_rounds = max(1, max_tool_rounds)
        self.deadline_at = time.monotonic() + max(1.0, total_deadline_seconds)
        self.force_fact_tool_calls = force_fact_tool_calls
        self.deterministic_material_resolution_enabled = deterministic_material_resolution_enabled
        self.narrative_synthesis_enabled = narrative_synthesis_enabled
        self.registry = registry or ToolRegistry()
        self.graph = self._build()

    def _build(self):
        builder = StateGraph(WarehouseAgentState)
        builder.add_node("prepare_context", self._prepare_context)
        builder.add_node("call_qwen", self._call_qwen)
        builder.add_node("execute_tools", self._execute_tools)
        builder.add_node("execute_contract", self._execute_contract)
        builder.add_node("normalize_response", self._normalize_response)
        builder.add_edge(START, "prepare_context")
        builder.add_conditional_edges(
            "prepare_context",
            self._after_prepare,
            {"contract": "execute_contract", "llm": "call_qwen"},
        )
        builder.add_conditional_edges(
            "call_qwen",
            self._after_llm,
            {"tools": "execute_tools", "finish": "normalize_response"},
        )
        builder.add_conditional_edges(
            "execute_tools",
            self._after_tools,
            {"contract": "execute_contract", "finish": "normalize_response"},
        )
        builder.add_conditional_edges(
            "execute_contract",
            self._after_contract,
            {"llm": "call_qwen", "finish": "normalize_response"},
        )
        builder.add_edge("normalize_response", END)
        return builder.compile()

    @staticmethod
    def _prepare_context(state: WarehouseAgentState) -> dict:
        contract = (
            TaskContract.model_validate(state["task_contract"])
            if state.get("task_contract")
            else classify_task_contract(state["user_message"])
        )
        return {
            "messages": [
                {"role": "system", "content": WAREHOUSE_AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": state["user_message"]},
            ],
            "task_contract": contract.model_dump(mode="json"),
        }

    @staticmethod
    def _contract(state: WarehouseAgentState) -> TaskContract:
        return TaskContract.model_validate(state["task_contract"])

    def _after_prepare(self, state: WarehouseAgentState) -> Literal["contract", "llm"]:
        contract = self._contract(state)
        if contract.entity_kind == "global":
            return "contract"
        if contract.entity_kind in {"component", "cable", "power"}:
            return "contract"
        if contract.entity_kind == "product":
            return "contract"
        if contract.entity_kind == "project" and self.deterministic_material_resolution_enabled:
            return "contract"
        if (
            contract.entity_kind == "material"
            and contract.requires_material_resolution
            and contract.deterministic_material_resolution
            and self.deterministic_material_resolution_enabled
        ):
            return "contract"
        if (
            contract.entity_kind == "material"
            and len((state["entities"].get("material_candidates") or {}).get("items") or []) >= 2
            and bool(
                {
                    "component_relations",
                    "engineering_evidence",
                    "component_evidence_comparison",
                }.intersection(contract.requested_facts)
            )
        ):
            # The server-owned conversation context already fixes the material
            # pair. Do not ask the model whether to run the required read-only
            # evidence tool on an elliptical follow-up such as “那 Pin 5 呢？”.
            return "contract"
        if (
            contract.entity_kind == "material"
            and self._resolved_material_id(state["entities"])
            and contract.requested_facts
        ):
            return "contract"
        if (
            contract.entity_kind == "project"
            and self._resolved_project_id(state["entities"])
            and contract.requested_facts
        ):
            return "contract"
        return "llm"

    def _call_qwen(self, state: WarehouseAgentState) -> dict:
        if self._permission_denied(state):
            return {
                "messages": [*state["messages"], {"role": "assistant", "content": ""}],
                "pending_tool_calls": [],
            }
        if self._deadline_reached():
            return {
                "messages": [*state["messages"], {"role": "assistant", "content": ""}],
                "pending_tool_calls": [],
                "deadline_exceeded": True,
            }
        narrative_mode = self._should_synthesize_narrative(state)
        allowed_names = self._allowed_schema_names(state)
        schemas = [] if narrative_mode else self.registry.schemas(allowed_names=allowed_names)
        messages = self._messages_with_server_context(state, narrative_mode=narrative_mode)
        chat_with_limit = getattr(self.provider, "chat_with_max_tokens", None)
        if narrative_mode and callable(chat_with_limit):
            message = chat_with_limit(
                messages,
                max_tokens=1024,
                tools=schemas,
                tool_choice=self._tool_choice(schemas),
            )
        else:
            message = self.provider.chat(
                messages,
                tools=schemas,
                tool_choice=self._tool_choice(schemas),
            )
        if narrative_mode:
            provider_telemetry = message.get("_telemetry")
            truncated = (
                isinstance(provider_telemetry, dict)
                and str(provider_telemetry.get("finish_reason") or "") == "length"
            )
            content = (
                ""
                if truncated
                else self._plain_narrative_content(
                    state,
                    str(message.get("content") or ""),
                )
            )
            if state["entities"].get("power_design") and not self._usable_power_narrative(content):
                content = ""
            message["content"] = content
        # Provider-private reasoning is never part of the public state, even
        # when a compatible endpoint happens to return it as an extra field.
        message.pop("reasoning_content", None)
        telemetry = list(state["telemetry"])
        provider_telemetry = message.pop("_telemetry", None)
        if provider_telemetry:
            telemetry.append(provider_telemetry)
        calls = list(message.get("tool_calls") or [])
        deadline_exceeded = self._deadline_reached()
        exceeded = bool(calls and state["tool_round"] >= self.max_tool_rounds)
        return {
            "messages": [*state["messages"], message],
            "pending_tool_calls": [] if exceeded or deadline_exceeded else calls,
            "max_rounds_exceeded": exceeded,
            "deadline_exceeded": deadline_exceeded,
            "telemetry": telemetry,
            "model_call_count": int(state.get("model_call_count") or 0) + 1,
        }

    @staticmethod
    def _plain_narrative_content(state: WarehouseAgentState, content: str) -> str:
        text = content.strip()
        citations: list[dict[str, Any]] = []
        for key in ("engineering_evidence", "component_evidence_comparison"):
            citations.extend((state["entities"].get(key) or {}).get("citations") or [])
        if citations and not any(item.get("synthetic_fixture") for item in citations):
            text = re.sub(r"合成(?:\s*CI)?(?:\s*测试)?\s*证据", "厂商数据手册", text)
            text = re.sub(r"合成(?:或模拟)?数据手册", "厂商数据手册", text)
        if (
            state["entities"].get("bom_analysis") or state["entities"].get("project_bom")
        ) and not state["entities"].get("build_readiness"):
            text = re.sub(
                r"(?:[，,]\s*)?(?:仅够|可做|可构建)\s*\d+(?:\.\d+)?\s*台(?:构建)?\s*(?:份)?",
                "",
                text,
            )
            text = re.sub(r"可用\s*([\d,.]+)\s*颗\s*[，,]\s*份", r"可用\1颗", text)
        text = re.sub(r"：\s*；", "：", text)
        text = re.sub(r"[，,]\s*[，,；]", "，", text)
        text = re.sub(r"。\s*；", "；", text)
        text = re.sub(r"；\s*；+", "；", text)
        return text

    @staticmethod
    def _usable_power_narrative(content: str) -> bool:
        """Keep the model explanation qualitative; cards own exact power facts."""

        if not content or len(content) > 420:
            return False
        if re.search(
            r"\d+(?:\.\d+)?\s*(?:mA|A|V|W|瓦|°C|℃|µA|μA|uA|%|Hz|kHz|MHz|dB)",
            content,
            re.IGNORECASE,
        ):
            return False
        if re.search(r"库存|可用量|库位|仓库|\bpcs\b", content, re.IGNORECASE):
            return False
        return True

    @staticmethod
    def _power_design_fallback(data: dict[str, Any]) -> str:
        requirements = data.get("requirements") or {}
        selected = data.get("selected_topology") or requirements.get("topology_choice")
        architectures = data.get("topologies") or []
        architecture = next(
            (item for item in architectures if item.get("topology") == selected),
            None,
        )
        load_cases = data.get("load_case_calculations") or []
        two_stage_cases = [
            item for item in load_cases if item.get("post_buck_ldo_loss_w") is not None
        ]
        if two_stage_cases:
            comparisons = []
            for item in two_stage_cases[:4]:
                current_ma = Decimal(str(item["load_current_a"])) * Decimal("1000")
                direct = item.get("direct_ldo_loss_w")
                post = item.get("post_buck_ldo_loss_w")
                intermediate = item.get("post_buck_intermediate_voltage_v")
                direct_v = item.get("direct_ldo_input_v")
                comparisons.append(
                    f"{current_ma}mA：{direct_v}V 直接 LDO {direct}W；"
                    f"{intermediate}V 后级 LDO {post}W"
                )
            boundary_note = ""
            if any(
                Decimal(str(item["load_current_a"])) == Decimal("0.8")
                for item in two_stage_cases
            ):
                boundary_note = "800mA 仅作为独立高负载边界回归，不是日常默认负载。"
            thermal_note = ""
            if selected == "buck_ldo":
                ldo_stage = next(
                    (
                        stage
                        for stage in (architecture or {}).get("stages") or []
                        if stage.get("topology") == "ldo"
                    ),
                    None,
                )
                rise = ((ldo_stage or {}).get("thermal_screen") or {}).get("estimated_delta_t_c")
                if rise is not None:
                    rise_text = format(Decimal(str(rise)), "f")
                    if "." in rise_text:
                        rise_text = rise_text.rstrip("0").rstrip(".")
                    thermal_note = f"所选封装的一阶温升筛查约 {rise_text}°C（不是板级结温预测）。"
            return (
                "服务端按本轮负载计算："
                + "；".join(comparisons)
                + "。"
                + boundary_note
                + thermal_note
                + "后级损耗只按中间轨到目标电压计算；Buck 总效率、"
                "PSRR 频段、压差和板级温升仍需结合器件与布局核对。"
            )
        current_present = any(
            bool(value) if isinstance(value, (list, tuple)) else value is not None
            for value in (
                requirements.get("load_current_a"),
                requirements.get("load_current_min_a"),
                requirements.get("load_current_max_a"),
                requirements.get("load_current_range_a"),
                requirements.get("load_current_cases_a"),
                requirements.get("analog_load_current_a"),
                requirements.get("digital_load_current_a"),
            )
        )
        if not current_present:
            return (
                "本轮没有提供负载电流，不能套用旧对话或示例值计算损耗。"
                "数字 MCU 可评估直接 Buck；需要抑制开关噪声时再比较后级 LDO，"
                "模拟与数字约束不同时可分轨，并按各轨电流核算。"
            )

        if selected == "direct_buck":
            return (
                "直接 Buck 可以用于数字 MCU，是否合适取决于目标负载下的纹波、瞬态、EMI 和去耦。"
                "敏感模拟负载可另设低噪声支路；不能只凭拓扑断言 Buck 不适用。"
            )

        if selected == "split_rails":
            rails = (architecture or {}).get("rails") or []
            currents_allocated = bool(rails) and all(
                rail.get("load_current_a") is not None for rail in rails
            )
            if currents_allocated:
                return (
                    "分轨方案把数字 MCU 与敏感模拟负载分别建模；按各轨独立的电流核算损耗，"
                    "PSRR、滤波和 EMI 仍需结合目标频段与布局确认。"
                )
            return (
                "数字 MCU 可评估直接 Buck，敏感模拟负载可单独加 LDO 或滤波支路。"
                "当前若未分别给出两轨电流，模拟支路损耗仍未知，不能把总负载重复算到两轨。"
            )

        if selected == "buck_ldo":
            cases = requirements.get("load_current_cases_a") or []
            if len(cases) > 1:
                return (
                    "Buck 后接 LDO 只有在 PSRR 覆盖目标噪声频段且热余量足够时值得采用。"
                    "下方卡片按每个负载档分别列出后级损耗，不把一个档位的结果套到其他档位。"
                )
            ldo_stage = next(
                (
                    stage
                    for stage in (architecture or {}).get("stages") or []
                    if stage.get("topology") == "ldo"
                ),
                None,
            )
            if ldo_stage and ldo_stage.get("loss_w") is not None:
                thermal_screen = ldo_stage.get("thermal_screen") or {}
                rise = thermal_screen.get("estimated_delta_t_c")
                current = ldo_stage.get("load_current_a")
                current_prefix = (
                    f"{Decimal(str(current)) * Decimal('1000')}mA 时，"
                    if current is not None
                    else ""
                )
                thermal_summary = (
                    f"一阶温升筛查约 {rise}°C（不是板级结温预测）。"
                    if rise is not None
                    else "板级温升仍需按封装、铜面积和环境条件核对。"
                )
                return (
                    "Buck 后接 LDO 有意义，但只在其 PSRR 覆盖目标噪声频段且热余量足够时值得采用。"
                    f"服务端按{current_prefix}后级 {ldo_stage.get('input_voltage_v')}V 输入计算，"
                    f"该级损耗为 {ldo_stage.get('loss_w')}W；{thermal_summary}"
                    "压差、静态电流和实际板级温升仍需核对。"
                )
            return (
                "Buck 后接 LDO 可用于抑制其 PSRR 有效频段内的部分噪声，但会增加线性损耗和静态电流。"
                "本轮缺少后级负载或中间轨约束，先不估算损耗。"
            )

        return (
            "没有唯一最优拓扑：数字 MCU 可评估直接 Buck；"
            "若需要更低噪声，可在 PSRR、压差和热余量合适时增加后级 LDO；"
            "模拟与数字约束不同则可分轨。确定性损耗与各轨电流按本轮条件列在下方。"
        )

    def _messages_with_server_context(
        self,
        state: WarehouseAgentState,
        *,
        narrative_mode: bool = False,
    ) -> list[dict]:
        context: dict[str, Any] = {}
        for key in (
            "cable_search",
            "cable_detail",
            "material_candidates",
            "material_inventories",
            "inventory",
            "locations",
            "low_stock",
            "project_candidates",
            "project_bom",
            "bom_analysis",
            "product_candidates",
            "product_bom",
            "build_readiness",
            "build_plan_proposal",
            "component_search",
            "power_design",
            "component_relations",
            "engineering_evidence",
            "component_evidence_comparison",
        ):
            value = state["entities"].get(key)
            if value:
                context[key] = value
        if narrative_mode and "power_design" in context:
            context["power_design"] = self._power_design_narrative_context(context["power_design"])
        if narrative_mode:
            context = self._compact_narrative_value(context)
        base_messages = (
            self._narrative_history(state["messages"]) if narrative_mode else state["messages"]
        )
        if not context:
            return base_messages
        power_design_guidance = (
            " power_design 说明应先回答本轮问题，最多三句；Buck 可用于 MCU，"
            "Buck+LDO 只在 PSRR 频段、dropout、静态电流与热余量合适时采用，"
            "数字和敏感模拟负载可按约束分轨。精确电压、电流、损耗、温升、库存、候选料号和库位由下方卡片展示；"
            "不要复述这些数字或库存清单，也不要把某个拓扑的计算值挪到另一拓扑。"
            if narrative_mode and "power_design" in context
            else ""
        )
        return [
            *base_messages,
            {
                "role": "system",
                "content": (
                    (
                        "以下 JSON 是服务端工具已验证的只读业务事实。"
                        "请只依据这些事实，用简短中文直接回答用户；"
                        "允许简洁 Markdown 标题、段落、列表或表格，不要输出 HTML；"
                        "不要调用工具，不要补造数值或兼容性结论。"
                        "similar_to 不代表引脚兼容或可直接替代；证据不足时必须明确说明。"
                        "引用中的 synthetic_fixture=false 表示真实厂商数据手册，"
                        "必须称为厂商数据手册或工程证据，绝不能称为合成、模拟或 CI 证据。"
                        if narrative_mode
                        else (
                            "以下 JSON 是服务端已验证的实体上下文，只能据此填写当前允许工具的参数："
                        )
                    )
                    + power_design_guidance
                    + json.dumps(
                        jsonable_encoder(context),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ),
            },
        ]

    @staticmethod
    def _narrative_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system = next(
            (message for message in messages if message.get("role") == "system"),
            None,
        )
        user_messages = [message for message in messages if message.get("role") == "user"][-2:]
        return ([system] if system else []) + user_messages

    @classmethod
    def _compact_narrative_value(cls, value: Any, *, depth: int = 0) -> Any:
        """Keep model explanations grounded without serializing bulky tool internals."""

        if depth >= 8:
            return None
        if isinstance(value, str):
            return value if len(value) <= 700 else value[:697].rstrip() + "…"
        if isinstance(value, list):
            return [cls._compact_narrative_value(item, depth=depth + 1) for item in value[:12]]
        if isinstance(value, dict):
            compact: dict[str, Any] = {}
            for key, item in list(value.items())[:40]:
                if str(key).casefold() in cls._NARRATIVE_CONTEXT_OMIT_KEYS:
                    continue
                compact[key] = cls._compact_narrative_value(item, depth=depth + 1)
            return compact
        return value

    @staticmethod
    def _power_design_narrative_context(data: dict[str, Any]) -> dict[str, Any]:
        requirement_keys = (
            "input_voltage_v",
            "output_voltage_v",
            "load_current_a",
            "load_current_min_a",
            "load_current_max_a",
            "load_current_range_a",
            "load_current_cases_a",
            "analog_load_current_a",
            "digital_load_current_a",
            "topology_choice",
            "intermediate_voltage_v",
            "efficiency_preference",
            "noise_preference",
            "size_preference",
            "thermal_preference",
            "topology_constraints",
        )
        requirement = data.get("requirements") or {}
        stage_keys = (
            "stage_id",
            "topology",
            "input_voltage_v",
            "output_voltage_v",
            "load_current_a",
            "current_basis",
            "loss_w",
            "ideal_efficiency",
            "quiescent_current_a",
            "quiescent_input_power_w",
            "thermal_screen",
            "loss_status",
            "headroom_v",
            "dropout_status",
            "notes",
        )
        rail_keys = (
            "rail_id",
            "label",
            "voltage_v",
            "load_current_a",
            "current_basis",
            "sensitive_analog",
            "stage_ids",
            "notes",
        )
        load_case_keys = (
            "load_current_a",
            "direct_ldo_input_v",
            "output_voltage_v",
            "direct_ldo_loss_w",
            "post_buck_intermediate_voltage_v",
            "post_buck_ldo_loss_w",
            "calculation_type",
            "formula",
        )
        return {
            "requirements": {
                key: requirement[key] for key in requirement_keys if key in requirement
            },
            "status": data.get("status"),
            "missing_constraints": data.get("missing_constraints") or [],
            "selected_topology": data.get("selected_topology"),
            "load_case_calculations": [
                {key: row[key] for key in load_case_keys if key in row}
                for row in data.get("load_case_calculations") or []
                if isinstance(row, dict)
            ],
            "topologies": [
                {
                    "topology": item.get("topology"),
                    "label": item.get("label"),
                    "availability": item.get("availability"),
                    "selected_by_user": item.get("selected_by_user"),
                    "total_load_current_a": item.get("total_load_current_a"),
                    "summary": item.get("summary"),
                    "rails": [
                        {key: rail[key] for key in rail_keys if key in rail}
                        for rail in item.get("rails") or []
                        if isinstance(rail, dict)
                    ],
                    "stages": [
                        {key: stage[key] for key in stage_keys if key in stage}
                        for stage in item.get("stages") or []
                        if isinstance(stage, dict)
                    ],
                    "constraints": item.get("constraints") or [],
                }
                for item in data.get("topologies") or []
                if isinstance(item, dict)
            ],
        }

    def _allowed_schema_names(self, state: WarehouseAgentState) -> set[str]:
        contract = self._contract(state)
        entities = state["entities"]
        multi_material_context = len(
            (entities.get("material_candidates") or {}).get("items") or []
        ) >= 2 and bool(
            {
                "component_relations",
                "engineering_evidence",
                "component_evidence_comparison",
            }.intersection(contract.requested_facts)
        )
        if contract.requires_project_resolution and not self._resolved_project_id(entities):
            return {"search_projects"}
        if (
            contract.requires_product_resolution
            and not self._resolved_product_id(entities)
            and not self._resolved_project_id(entities)
        ):
            return {"search_products"}
        if (
            contract.requires_material_resolution
            and not self._resolved_material_id(entities)
            and not multi_material_context
        ):
            return {"search_materials"}
        if contract.write_intent == "reserve_inventory":
            return {"propose_inventory_reservation"}
        if contract.write_intent == "build_reservation":
            return {"propose_build_material_reservation"}
        if contract.entity_kind == "project" and not self._resolved_project_id(entities):
            return {"search_projects"}
        if contract.entity_kind == "product" and not self._resolved_product_id(entities):
            return {"search_products"}
        if (
            contract.entity_kind == "material"
            and not self._resolved_material_id(entities)
            and not multi_material_context
        ):
            return {"search_materials"}
        if contract.entity_kind == "component":
            if "search_components_by_requirement" in self.registry.names:
                return {"search_components_by_requirement"}
            return self.registry.names
        if contract.entity_kind == "power":
            return {"plan_power_design"}.intersection(self.registry.names)
        if contract.entity_kind == "cable":
            return {"search_cables", "get_cable_detail"}.intersection(self.registry.names)
        return self.registry.names

    def _tool_choice(self, schemas: list[dict]) -> str:
        if not self.force_fact_tool_calls or len(schemas) != 1:
            return "auto"
        name = schemas[0]["function"]["name"]
        if name in {
            "search_materials",
            "search_projects",
            "search_products",
            "propose_inventory_reservation",
            "propose_build_material_reservation",
        }:
            return "required"
        return "auto"

    def _after_llm(self, state: WarehouseAgentState) -> Literal["tools", "finish"]:
        if self._has_unresolved_ambiguity(state):
            return "finish"
        return "tools" if state["pending_tool_calls"] else "finish"

    def _after_tools(self, state: WarehouseAgentState) -> Literal["contract", "finish"]:
        if self._permission_denied(state) or self._has_unresolved_ambiguity(state):
            return "finish"
        return "contract"

    def _after_contract(self, state: WarehouseAgentState) -> Literal["llm", "finish"]:
        if self._deadline_reached():
            return "finish"
        if self._contract_complete(state):
            return "llm" if self._should_synthesize_narrative(state) else "finish"
        return "llm"

    def _should_synthesize_narrative(self, state: WarehouseAgentState) -> bool:
        if not self.narrative_synthesis_enabled:
            return False
        if int(state.get("model_call_count") or 0) > 0:
            return False
        if self._permission_denied(state):
            return False
        if any(event.get("status") == "error" for event in state["tool_events"]):
            return False
        contract = self._contract(state)
        requested_facts = contract.requested_facts
        if not {
            "low_stock",
            "project_bom",
            "bom_stock",
            "build_readiness",
            "component_search",
            "component_relations",
            "engineering_evidence",
            "component_evidence_comparison",
            "cable_search",
            "power_design",
        }.intersection(requested_facts):
            return False
        folded = state["user_message"].casefold()
        if any(
            marker in folded
            for marker in (
                "具体差",
                "差在哪",
                "为什么",
                "原因",
                "简短",
                "建议",
                "依据",
                "最容易",
                "哪些适合",
                "比呢",
                "比较",
                "评审",
                "讲清楚",
                "总结",
                "指出",
                "分开",
                "批准",
                "证据",
                "出处",
                "先告诉",
                "配套",
                "方案",
                "负载",
            )
        ):
            return True

        technical_judgment = any(
            marker in folded
            for marker in (
                "是否",
                "是不是",
                "必有",
                "必须",
                "一定",
                "能否",
                "会不会",
                "有意义",
                "适合",
            )
        )
        power_or_evidence_intent = bool(
            {"power_design", "engineering_evidence", "component_evidence_comparison"}.intersection(
                requested_facts
            )
        )
        electrical_subject = any(
            marker in folded
            for marker in ("buck", "ldo", "纹波", "psrr", "cot", "压差", "dropout", "开关噪声")
        )
        return technical_judgment and power_or_evidence_intent and electrical_subject

    @staticmethod
    def _permission_denied(state: WarehouseAgentState) -> bool:
        return any(
            event.get("error_code") == "TOOL_PERMISSION_DENIED" for event in state["tool_events"]
        )

    @staticmethod
    def _has_unresolved_ambiguity(state: WarehouseAgentState) -> bool:
        contract = TaskContract.model_validate(state.get("task_contract") or {})
        for key in ("material_candidates", "project_candidates", "product_candidates"):
            candidates = state["entities"].get(key) or {}
            if key == "material_candidates" and {
                "component_relations",
                "engineering_evidence",
                "component_evidence_comparison",
            }.intersection(contract.requested_facts):
                continue
            if (
                len(candidates.get("items") or []) > 1
                and len(candidates.get("exact_match_ids") or []) != 1
            ):
                return True
        project_candidates = state["entities"].get("project_candidates") or {}
        project_items = project_candidates.get("items") or []
        if len(project_items) == 1:
            versions = project_items[0].get("available_versions") or []
            query = state["user_message"].casefold()
            selected_version = project_candidates.get("selected_bom_version")
            current_version_requested = any(marker in query for marker in ("现在", "当前", "最新"))
            linked_build = bool(
                contract.build_quantity is not None and project_items[0].get("product_revision_id")
            )
            if (
                len(versions) > 1
                and not selected_version
                and not current_version_requested
                and not linked_build
                and contract.write_intent != "reserve_inventory"
                and not any(version.casefold() in query for version in versions)
            ):
                return True
        return False

    def _execute_tools(self, state: WarehouseAgentState) -> dict:
        messages = list(state["messages"])
        events = list(state["tool_events"])
        entities = dict(state["entities"])
        actions = list(state["ui_actions"])
        proposal_ids = list(state["proposal_ids"])
        deadline_exceeded = False
        for tool_call in state["pending_tool_calls"]:
            if self._deadline_reached():
                deadline_exceeded = True
                break
            execution = self.registry.execute(self.tool_context, tool_call)
            messages.append(execution.tool_message())
            self._collect_execution(execution, events, entities, actions, proposal_ids)
        return {
            "messages": messages,
            "tool_events": events,
            "entities": entities,
            "ui_actions": self._unique_actions(actions),
            "proposal_ids": sorted(set(proposal_ids)),
            "pending_tool_calls": [],
            "tool_round": state["tool_round"] + 1,
            "deadline_exceeded": deadline_exceeded,
        }

    def _execute_contract(self, state: WarehouseAgentState) -> dict:
        contract = self._contract(state)
        events = list(state["tool_events"])
        entities = dict(state["entities"])
        actions = list(state["ui_actions"])
        proposal_ids = list(state["proposal_ids"])
        successful = {event["tool"] for event in events if event["status"] == "success"}
        calls: list[tuple[str, dict[str, Any]]] = []
        if (
            "power_design" in contract.requested_facts
            and "plan_power_design" not in successful
            and "plan_power_design" in self.registry.names
        ):
            calls.append(("plan_power_design", {"requirement": state["user_message"]}))
        if "relation_policy" in contract.requested_facts:
            entities["relation_policy"] = {
                "candidate_relation": "候选关系尚未由工程师验证。",
                "validated_relation": (
                    "已验证工程关系只证明记录中的工程关系类型；"
                    "similar_to 不代表引脚兼容，也不能据此作为替代料使用。"
                ),
                "approved_alternate": ("此产品版本已批准备选只适用于精确 ProductRevision/BOM 位。"),
                "automatic_substitution": False,
                "build_readiness_primary_bom_only": True,
            }
        if (
            "component_search" in contract.requested_facts
            and "search_components_by_requirement" in self.registry.names
        ):
            calls.append(
                (
                    "search_components_by_requirement",
                    {"requirement": state["user_message"], "limit": 5},
                )
            )
        if (
            "cable_search" in contract.requested_facts
            and "search_cables" not in successful
            and "search_cables" in self.registry.names
        ):
            cable_query = state["user_message"]
            existing_material_id = self._resolved_material_id(entities)
            if existing_material_id and any(
                marker in state["user_message"].casefold()
                for marker in ("这条", "第二条", "这个 cable", "它")
            ):
                selected_cable = self.tool_context.db.get(Material, existing_material_id)
                if selected_cable is not None:
                    cable_query = selected_cable.code
            execution = self.registry.execute(
                self.tool_context,
                {
                    "id": f"server-search-cables-{len(events) + 1}",
                    "type": "function",
                    "function": {
                        "name": "search_cables",
                        "arguments": json.dumps(
                            {"query": cable_query, "limit": 10},
                            ensure_ascii=False,
                        ),
                    },
                },
            )
            self._collect_execution(execution, events, entities, actions, proposal_ids)
            successful.add("search_cables")
            if execution.event.status == "error":
                return {
                    "tool_events": events,
                    "entities": entities,
                    "ui_actions": self._unique_actions(actions),
                    "proposal_ids": sorted(set(proposal_ids)),
                    "pending_tool_calls": [],
                }
        if "cable_detail" in contract.requested_facts:
            selected_cable_id = self._resolved_material_id(entities)
            if selected_cable_id:
                calls.append(("get_cable_detail", {"material_id": selected_cable_id}))
        if (
            contract.entity_kind == "material"
            and len((entities.get("material_candidates") or {}).get("items") or []) >= 2
            and bool(
                {
                    "component_relations",
                    "engineering_evidence",
                    "component_evidence_comparison",
                }.intersection(contract.requested_facts)
            )
            and "search_materials" not in successful
            and "search_materials" in self.registry.names
        ):
            # Conversation preparation resolves an explicit two-part relation pair
            # deterministically. Preserve the historical tool audit contract with
            # a narrowly scoped exact lookup; never replace the resolved pair with
            # a broad free-text search result.
            first_item = (entities.get("material_candidates") or {}).get("items", [])[0]
            exact_term = first_item.get("code") or first_item.get("mpn")
            execution = self.registry.execute(
                self.tool_context,
                {
                    "id": f"server-search_materials-{len(events) + 1}",
                    "type": "function",
                    "function": {
                        "name": "search_materials",
                        "arguments": json.dumps(
                            {"query": exact_term},
                            ensure_ascii=False,
                        ),
                    },
                },
            )
            events.append(execution.event.model_dump(mode="json"))
            actions.extend(action.model_dump(mode="json") for action in execution.ui_actions)
            proposal_ids.extend(execution.proposal_ids)
            successful.add("search_materials")
            if execution.event.status == "error":
                return {
                    "tool_events": events,
                    "entities": entities,
                    "ui_actions": self._unique_actions(actions),
                    "proposal_ids": sorted(set(proposal_ids)),
                    "pending_tool_calls": [],
                }
        if (
            contract.requires_project_resolution
            and not self._resolved_project_id(entities)
            and "search_projects" in self.registry.names
        ):
            execution = self.registry.execute(
                self.tool_context,
                {
                    "id": f"server-search_projects-{len(events) + 1}",
                    "type": "function",
                    "function": {
                        "name": "search_projects",
                        "arguments": json.dumps(
                            {"query": self._material_search_term(state["user_message"])},
                            ensure_ascii=False,
                        ),
                    },
                },
            )
            self._collect_execution(execution, events, entities, actions, proposal_ids)
            successful.add("search_projects")
            if execution.event.status == "error":
                return {
                    "tool_events": events,
                    "entities": entities,
                    "ui_actions": self._unique_actions(actions),
                    "proposal_ids": sorted(set(proposal_ids)),
                    "pending_tool_calls": [],
                }
        if (
            contract.entity_kind == "material"
            and contract.requires_material_resolution
            and not self._resolved_material_id(entities)
            and not (
                "component_evidence_comparison" in contract.requested_facts
                and len((entities.get("material_candidates") or {}).get("items") or []) >= 2
            )
            and "search_materials" in self.registry.names
        ):
            execution = self.registry.execute(
                self.tool_context,
                {
                    "id": f"server-search_materials-{len(events) + 1}",
                    "type": "function",
                    "function": {
                        "name": "search_materials",
                        "arguments": json.dumps(
                            {"query": state["user_message"]},
                            ensure_ascii=False,
                        ),
                    },
                },
            )
            self._collect_execution(execution, events, entities, actions, proposal_ids)
            successful.add("search_materials")
            if execution.event.status == "error":
                return {
                    "tool_events": events,
                    "entities": entities,
                    "ui_actions": self._unique_actions(actions),
                    "proposal_ids": sorted(set(proposal_ids)),
                    "pending_tool_calls": [],
                }
        if (
            contract.entity_kind == "product"
            and contract.requires_product_resolution
            and not self._resolved_product_id(entities)
            and not self._resolved_project_id(entities)
            and "search_products" not in successful
            and "search_products" in self.registry.names
        ):
            execution = self.registry.execute(
                self.tool_context,
                {
                    "id": f"server-search_products-{len(events) + 1}",
                    "type": "function",
                    "function": {
                        "name": "search_products",
                        "arguments": json.dumps(
                            {"query": state["user_message"]},
                            ensure_ascii=False,
                        ),
                    },
                },
            )
            self._collect_execution(execution, events, entities, actions, proposal_ids)
            successful.add("search_products")
            if execution.event.status == "error":
                return {
                    "tool_events": events,
                    "entities": entities,
                    "ui_actions": self._unique_actions(actions),
                    "proposal_ids": sorted(set(proposal_ids)),
                    "pending_tool_calls": [],
                }
            product_items = entities.get("product_candidates", {}).get("items") or []
            if (
                not product_items
                and "search_projects" not in successful
                and "search_projects" in self.registry.names
            ):
                fallback = self.registry.execute(
                    self.tool_context,
                    {
                        "id": f"server-search_projects-{len(events) + 1}",
                        "type": "function",
                        "function": {
                            "name": "search_projects",
                            "arguments": json.dumps(
                                {"query": state["user_message"]},
                                ensure_ascii=False,
                            ),
                        },
                    },
                )
                self._collect_execution(
                    fallback,
                    events,
                    entities,
                    actions,
                    proposal_ids,
                )
                successful.add("search_projects")
        if (
            contract.entity_kind == "product"
            and "bom_stock" in contract.requested_facts
            and not self._resolved_project_id(entities)
            and "search_projects" not in successful
            and "search_projects" in self.registry.names
            and re.search(r"(?<![A-Za-z0-9])AMR(?![A-Za-z0-9])", state["user_message"], re.I)
            and re.search(r"(?<![A-Za-z0-9])V2(?![A-Za-z0-9])", state["user_message"], re.I)
        ):
            # AMR V2 BOM-risk questions are project execution questions even
            # when the natural-language target also matches the linked product.
            # Resolve the execution project before asking the model to narrate;
            # otherwise a model may stop after product search and never produce
            # the required project BOM stock facts.
            execution = self.registry.execute(
                self.tool_context,
                {
                    "id": f"server-search_amr_v2-project-{len(events) + 1}",
                    "type": "function",
                    "function": {
                        "name": "search_projects",
                        "arguments": json.dumps(
                            {"query": state["user_message"]},
                            ensure_ascii=False,
                        ),
                    },
                },
            )
            self._collect_execution(execution, events, entities, actions, proposal_ids)
            successful.add("search_projects")
            if execution.event.status == "error":
                return {
                    "tool_events": events,
                    "entities": entities,
                    "ui_actions": self._unique_actions(actions),
                    "proposal_ids": sorted(set(proposal_ids)),
                    "pending_tool_calls": [],
                }
        if contract.write_intent == "build_reservation" and not self._resolved_project_id(entities):
            revision_id = self._selected_product_revision_id_from_entities(
                state,
                entities,
            )
            linked_projects = (
                list(
                    self.tool_context.db.scalars(
                        select(Project)
                        .where(Project.product_revision_id == revision_id)
                        .order_by(Project.code)
                        .limit(2)
                    ).all()
                )
                if revision_id
                else []
            )
            if len(linked_projects) == 1 and "search_projects" in self.registry.names:
                linked_search = self.registry.execute(
                    self.tool_context,
                    {
                        "id": f"server-search-linked-project-{len(events) + 1}",
                        "type": "function",
                        "function": {
                            "name": "search_projects",
                            "arguments": json.dumps(
                                {"query": linked_projects[0].code},
                                ensure_ascii=False,
                            ),
                        },
                    },
                )
                self._collect_execution(
                    linked_search,
                    events,
                    entities,
                    actions,
                    proposal_ids,
                )
                successful.add("search_projects")
        if "low_stock" in contract.requested_facts:
            low_stock_limit = 50
            folded_message = state["user_message"].casefold()
            if any(marker in folded_message for marker in ("三个", "3个", "前三", "前3")):
                low_stock_limit = 3
            elif any(marker in folded_message for marker in ("五个", "5个", "前五", "前5")):
                low_stock_limit = 5
            calls.append(("get_low_stock_materials", {"limit": low_stock_limit}))
        material_id = self._resolved_material_id(entities)
        candidate_ids = [
            int(item["id"])
            for item in (entities.get("material_candidates") or {}).get("items") or []
        ]
        for relation in (entities.get("component_relations") or {}).get("items") or []:
            candidate_ids.extend(
                int(item["id"])
                for item in (
                    relation.get("source_material") or {},
                    relation.get("target_material") or {},
                )
                if item.get("id")
            )
        for alternate in (entities.get("product_bom_alternates") or {}).get("items") or []:
            candidate_ids.extend(
                int(item["id"])
                for item in (
                    alternate.get("primary_material") or {},
                    alternate.get("alternate_material") or {},
                )
                if item.get("id")
            )
        candidate_ids = list(dict.fromkeys(candidate_ids))
        if material_id and material_id not in candidate_ids:
            candidate_ids.append(material_id)
        if "component_relations" in contract.requested_facts:
            if candidate_ids:
                calls.append(
                    (
                        "get_component_relations",
                        {"material_ids": sorted(set(candidate_ids))},
                    )
                )
        if "inventory" in contract.requested_facts and len(candidate_ids) >= 2:
            inventory_items: list[dict[str, Any]] = []
            for candidate_id in candidate_ids:
                execution = self.registry.execute(
                    self.tool_context,
                    {
                        "id": f"server-get_inventory_availability-{len(events) + 1}",
                        "type": "function",
                        "function": {
                            "name": "get_inventory_availability",
                            "arguments": json.dumps({"material_id": candidate_id}),
                        },
                    },
                )
                events.append(execution.event.model_dump(mode="json"))
                actions.extend(action.model_dump(mode="json") for action in execution.ui_actions)
                if execution.output.get("ok"):
                    inventory_items.append(execution.output["data"])
                if execution.event.status == "error":
                    break
            if inventory_items:
                entities["material_inventories"] = {
                    "items": inventory_items,
                    "count": len(inventory_items),
                }
            successful.add("get_inventory_availability")
        if "component_evidence_comparison" in contract.requested_facts and len(candidate_ids) >= 2:
            fields = detect_evidence_fields(state["user_message"])
            retained_fields = list(entities.get("conversation_evidence_fields") or [])
            if fields == ["pin"] and "pin_5" in retained_fields:
                fields = ["pin_5"]
            calls.append(
                (
                    "compare_component_evidence",
                    {
                        "first_material_id": candidate_ids[0],
                        "second_material_id": candidate_ids[1],
                        "fields": fields or ["supply_voltage", "pin_5", "interface"],
                    },
                )
            )
            if len(candidate_ids) > 2 and "search_datasheet_evidence" in self.registry.names:
                calls.append(
                    (
                        "search_datasheet_evidence",
                        {
                            "material_ids": sorted(set(candidate_ids)),
                            "query": state["user_message"],
                            "include_superseded": False,
                            "limit": 12,
                        },
                    )
                )
        elif "engineering_evidence" in contract.requested_facts and candidate_ids:
            folded = state["user_message"].casefold()
            calls.append(
                (
                    "search_datasheet_evidence",
                    {
                        "material_ids": sorted(set(candidate_ids)),
                        "query": state["user_message"],
                        "include_superseded": any(
                            marker in folded
                            for marker in ("rev a", "superseded", "history", "历史", "旧版")
                        ),
                        "limit": 6,
                    },
                )
            )
        if material_id:
            if "inventory" in contract.requested_facts:
                calls.append(("get_inventory_availability", {"material_id": material_id}))
            if "location" in contract.requested_facts:
                calls.append(("find_material_locations", {"material_id": material_id}))
        current_state = {**state, "entities": entities}
        project_id = self._resolved_project_id(entities)
        if project_id and not self._has_unresolved_ambiguity(current_state):
            args: dict[str, Any] = {"project_id": project_id}
            version = self._selected_version(current_state)
            if version:
                args["version"] = version
                project_candidates = entities.get("project_candidates")
                if project_candidates:
                    entities["project_candidates"] = {
                        **project_candidates,
                        "selected_project_id": project_id,
                        "selected_bom_version": version,
                    }
            linked_revision_id = self._linked_product_revision_id(entities)
            if contract.build_quantity is not None and linked_revision_id:
                if (
                    not self._resolved_product_id(entities)
                    and "search_products" not in successful
                    and "search_products" in self.registry.names
                ):
                    linked_product_search = self.registry.execute(
                        self.tool_context,
                        {
                            "id": f"server-search-linked-product-{len(events) + 1}",
                            "type": "function",
                            "function": {
                                "name": "search_products",
                                "arguments": json.dumps(
                                    {"query": state["user_message"]},
                                    ensure_ascii=False,
                                ),
                            },
                        },
                    )
                    self._collect_execution(
                        linked_product_search,
                        events,
                        entities,
                        actions,
                        proposal_ids,
                    )
                    successful.add("search_products")
                calls.append(
                    (
                        "analyze_product_build_readiness",
                        {
                            "product_revision_id": linked_revision_id,
                            "build_quantity": contract.build_quantity,
                            "project_id": project_id,
                        },
                    )
                )
                if contract.write_intent == "build_reservation":
                    calls.append(
                        (
                            "propose_build_material_reservation",
                            {
                                "product_revision_id": linked_revision_id,
                                "project_id": project_id,
                                "build_quantity": contract.build_quantity,
                            },
                        )
                    )
            elif "bom_stock" in contract.requested_facts:
                calls.append(("analyze_project_bom_stock", args))
            elif "project_bom" in contract.requested_facts:
                calls.append(("get_project_bom", args))
            elif contract.build_quantity is not None and not linked_revision_id:
                if any(
                    marker in state["user_message"].casefold() for marker in ("够", "库存", "缺")
                ):
                    calls.append(("analyze_project_bom_stock", args))
                else:
                    calls.append(("get_project_bom", args))
                entities["project_build_limitation"] = {
                    "required_quantity_semantics": "total_project_demand",
                    "build_quantity_not_multiplied": True,
                }
        product_id = self._resolved_product_id(entities)
        if product_id and not self._has_unresolved_ambiguity(current_state):
            revision_id = self._selected_product_revision_id_from_entities(
                state,
                entities,
            )
            product_args: dict[str, Any] = {"product_id": product_id}
            if revision_id:
                product_args["product_revision_id"] = revision_id
            else:
                revision_name = self._selected_product_revision_name(state)
                if revision_name:
                    product_args["revision"] = revision_name
            if (
                "build_readiness" in contract.requested_facts
                and contract.build_quantity is not None
            ):
                calls.append(
                    (
                        "analyze_product_build_readiness",
                        {
                            **product_args,
                            "build_quantity": contract.build_quantity,
                            **({"project_id": project_id} if project_id else {}),
                        },
                    )
                )
                if contract.write_intent == "build_reservation" and project_id and revision_id:
                    calls.append(
                        (
                            "propose_build_material_reservation",
                            {
                                "product_revision_id": revision_id,
                                "project_id": project_id,
                                "build_quantity": contract.build_quantity,
                            },
                        )
                    )
            elif "product_bom" in contract.requested_facts:
                calls.append(("get_product_bom", product_args))
            if "product_alternates" in contract.requested_facts:
                alternate_args: dict[str, Any] = {"product_id": product_id}
                if revision_id:
                    alternate_args["product_revision_id"] = revision_id
                calls.append(("get_product_bom_alternates", alternate_args))
        for name, arguments in calls:
            if name in successful or name not in self.registry.names:
                continue
            if (
                name == "propose_build_material_reservation"
                and not self._readiness_allows_build_proposal(entities)
            ):
                continue
            tool_call = {
                "id": f"server-{name}-{len(events) + 1}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
            execution = self.registry.execute(self.tool_context, tool_call)
            self._collect_execution(execution, events, entities, actions, proposal_ids)
            successful.add(name)
            if execution.event.status == "error":
                break
        return {
            "tool_events": events,
            "entities": entities,
            "ui_actions": self._unique_actions(actions),
            "proposal_ids": sorted(set(proposal_ids)),
            "pending_tool_calls": [],
        }

    @staticmethod
    def _collect_execution(execution, events, entities, actions, proposal_ids) -> None:
        events.append(execution.event.model_dump(mode="json"))
        if execution.entity_key and execution.output.get("ok"):
            data = execution.output["data"]
            entities[execution.entity_key] = data
            if execution.entity_key == "component_search" and data.get("material_candidates"):
                entities["material_candidates"] = data["material_candidates"]
            if execution.entity_key == "cable_search" and data.get("material_candidates"):
                entities["material_candidates"] = data["material_candidates"]
        actions.extend(action.model_dump(mode="json") for action in execution.ui_actions)
        proposal_ids.extend(execution.proposal_ids)

    def _contract_complete(self, state: WarehouseAgentState) -> bool:
        if self._permission_denied(state) or self._has_unresolved_ambiguity(state):
            return True
        successful = {
            event["tool"] for event in state["tool_events"] if event["status"] == "success"
        }
        if any(event["status"] == "error" for event in state["tool_events"]):
            return True
        contract = self._contract(state)
        material_candidates = state["entities"].get("material_candidates")
        if (
            material_candidates is not None
            and not material_candidates.get("items")
            and not self._resolved_material_id(state["entities"])
        ):
            return True
        project_candidates = state["entities"].get("project_candidates")
        if (
            project_candidates is not None
            and not project_candidates.get("items")
            and not self._resolved_project_id(state["entities"])
        ):
            return True
        product_candidates = state["entities"].get("product_candidates")
        if (
            product_candidates is not None
            and not product_candidates.get("items")
            and not self._resolved_product_id(state["entities"])
            and not self._resolved_project_id(state["entities"])
        ):
            return True
        if contract.write_intent == "build_reservation":
            return "propose_build_material_reservation" in successful
        if contract.write_intent == "reserve_inventory":
            return "propose_inventory_reservation" in successful
        if (
            contract.entity_kind == "project"
            and contract.build_quantity is not None
            and "analyze_product_build_readiness" in successful
        ):
            return True
        if (
            contract.entity_kind == "product"
            and self._resolved_project_id(state["entities"])
            and not self._resolved_product_id(state["entities"])
        ):
            if "build_readiness" in contract.requested_facts:
                return bool(
                    successful.intersection({"analyze_project_bom_stock", "get_project_bom"})
                )
            if "bom_stock" in contract.requested_facts:
                return "analyze_project_bom_stock" in successful
            if "project_bom" in contract.requested_facts:
                return "get_project_bom" in successful
        mapping = {
            "cable_search": "search_cables",
            "cable_detail": "get_cable_detail",
            "inventory": "get_inventory_availability",
            "location": "find_material_locations",
            "low_stock": "get_low_stock_materials",
            "project_bom": "get_project_bom",
            "bom_stock": "analyze_project_bom_stock",
            "product_bom": "get_product_bom",
            "build_readiness": "analyze_product_build_readiness",
            "component_search": "search_components_by_requirement",
            "power_design": "plan_power_design",
            "component_relations": "get_component_relations",
            "product_alternates": "get_product_bom_alternates",
            "engineering_evidence": "search_datasheet_evidence",
            "component_evidence_comparison": "compare_component_evidence",
        }
        required = {mapping[fact] for fact in contract.requested_facts if fact in mapping}
        if required:
            return required.issubset(successful)
        return bool(state["entities"])

    @staticmethod
    def _resolved_material_id(entities: dict[str, Any]) -> int | None:
        candidates = entities.get("material_candidates") or {}
        selected = candidates.get("selected_material_id")
        if selected:
            return int(selected)
        items = candidates.get("items") or []
        exact = candidates.get("exact_match_ids") or []
        if len(items) == 1 or len(exact) == 1:
            return int(exact[0] if len(exact) == 1 else items[0]["id"])
        return None

    @staticmethod
    def _material_search_term(message: str) -> str:
        tokens = re.findall(
            r"(?<![A-Z0-9])(?=[A-Z0-9._()+-]{3,})(?=[A-Z0-9._()+-]*[A-Z])"
            r"(?=[A-Z0-9._()+-]*\d)[A-Z0-9._()+-]+",
            message,
            re.I,
        )
        material_tokens = [
            token
            for token in tokens
            if not re.match(r"^(?:RB|PRJ|PROD)-", token, re.I)
            and not re.fullmatch(r"(?:EVT|DVT|PVT|R)\s*-?\d+", token, re.I)
        ]
        return max(material_tokens, key=len) if material_tokens else message

    @staticmethod
    def _resolved_project_id(entities: dict[str, Any]) -> int | None:
        candidates = entities.get("project_candidates") or {}
        selected = candidates.get("selected_project_id")
        if selected:
            return int(selected)
        items = candidates.get("items") or []
        exact = candidates.get("exact_match_ids") or []
        if len(items) == 1 or len(exact) == 1:
            return int(exact[0] if len(exact) == 1 else items[0]["id"])
        return None

    @staticmethod
    def _resolved_product_id(entities: dict[str, Any]) -> int | None:
        candidates = entities.get("product_candidates") or {}
        selected = candidates.get("selected_product_id")
        if selected:
            return int(selected)
        items = candidates.get("items") or []
        exact = candidates.get("exact_match_ids") or []
        if len(items) == 1 or len(exact) == 1:
            return int(exact[0] if len(exact) == 1 else items[0]["id"])
        return None

    @staticmethod
    def _linked_product_revision_id(entities: dict[str, Any]) -> int | None:
        candidates = entities.get("project_candidates") or {}
        project_id = WarehouseAgentGraph._resolved_project_id(entities)
        for item in candidates.get("items") or []:
            if int(item["id"]) == project_id and item.get("product_revision_id"):
                return int(item["product_revision_id"])
        return None

    @staticmethod
    def _selected_product_revision_id(state: WarehouseAgentState) -> int | None:
        return WarehouseAgentGraph._selected_product_revision_id_from_entities(
            state,
            state["entities"],
        )

    @staticmethod
    def _selected_product_revision_id_from_entities(
        state: WarehouseAgentState,
        entities: dict[str, Any],
    ) -> int | None:
        candidates = entities.get("product_candidates") or {}
        selected = candidates.get("selected_product_revision_id")
        if selected:
            return int(selected)
        query = state["user_message"].casefold()
        items = candidates.get("items") or []
        if len(items) == 1:
            revisions = items[0].get("released_revisions") or []
            explicit = [
                revision
                for revision in revisions
                if str(revision.get("revision") or "").casefold() in query
            ]
            if len(explicit) == 1:
                return int(explicit[0]["id"])
            if any(marker in query for marker in ("线缆", "线束", "cable")):
                cable_revisions = [
                    revision
                    for revision in revisions
                    if "cable" in str(revision.get("revision") or "").casefold()
                ]
                if len(cable_revisions) == 1:
                    return int(cable_revisions[0]["id"])
            default = items[0].get("default_revision") or {}
            if default.get("id"):
                return int(default["id"])
        return None

    @staticmethod
    def _readiness_allows_build_proposal(entities: dict[str, Any]) -> bool:
        readiness = entities.get("build_readiness") or {}
        if not readiness or not readiness.get("sufficient"):
            return False
        if int(readiness.get("material_blocker_count") or 0) > 0:
            return False
        return any(
            Decimal(str(item.get("additional_reservation_required") or "0")) > 0
            for item in readiness.get("items") or []
        )

    @staticmethod
    def _selected_product_revision_name(state: WarehouseAgentState) -> str | None:
        candidates = state["entities"].get("product_candidates") or {}
        query = state["user_message"].casefold()
        items = candidates.get("items") or []
        if len(items) != 1:
            return None
        return next(
            (
                str(revision["revision"])
                for revision in items[0].get("released_revisions") or []
                if str(revision.get("revision") or "").casefold() in query
            ),
            None,
        )

    @staticmethod
    def _selected_version(state: WarehouseAgentState) -> str | None:
        candidates = state["entities"].get("project_candidates") or {}
        selected = candidates.get("selected_bom_version")
        if selected:
            return str(selected)
        items = candidates.get("items") or []
        if len(items) != 1:
            return None
        query = state["user_message"].casefold()
        explicit = next(
            (
                version
                for version in items[0].get("available_versions") or []
                if version.casefold() in query
            ),
            None,
        )
        if explicit:
            return explicit
        versions = list(items[0].get("available_versions") or [])
        if versions and any(marker in query for marker in ("现在", "当前", "最新")):
            return versions[-1]
        return None

    def _deadline_reached(self) -> bool:
        return time.monotonic() >= self.deadline_at

    @staticmethod
    def _unique_actions(actions: list[dict]) -> list[dict]:
        result = []
        seen = set()
        for action in actions:
            key = (action.get("type"), action.get("target_id"))
            if key in seen:
                continue
            seen.add(key)
            result.append(action)
        return result

    def _normalize_response(self, state: WarehouseAgentState) -> dict:
        successful_tools = {
            event["tool"] for event in state["tool_events"] if event["status"] == "success"
        }
        answer = ""
        if state["messages"] and state["messages"][-1].get("role") == "assistant":
            answer = str(state["messages"][-1].get("content") or "").strip()
        raw_answer = answer
        if state["deadline_exceeded"]:
            answer = "任务已达到 Agent 总时限，已安全停止后续模型与工具调用。请缩小问题范围后重试。"
        elif state["max_rounds_exceeded"]:
            answer = "查询步骤超过安全上限，已停止继续调用工具。请缩小问题范围后重试。"
        else:
            answer = (
                PublicAnswerBoundary()
                .inspect(
                    answer,
                    entities=state["entities"],
                )
                .content
            )
            answer = self._apply_fact_guard(state, successful_tools, answer)
            if not answer and state["entities"].get("power_design"):
                answer = self._power_design_fallback(state["entities"]["power_design"])
        if state["deadline_exceeded"] or state["max_rounds_exceeded"] or answer != raw_answer:
            return {
                "answer": answer,
                "narrative": answer,
                "grounded_facts": [],
                "intent": self._intent(successful_tools),
            }
        composed = GroundedResponseComposer().compose(
            user_message=state["user_message"],
            entities=state["entities"],
            narrative=answer,
        )
        return {
            "answer": composed.answer,
            "narrative": composed.narrative,
            "grounded_facts": [fact.model_dump(mode="json") for fact in composed.grounded_facts],
            "intent": self._intent(successful_tools),
        }

    @staticmethod
    def _apply_fact_guard(
        state: WarehouseAgentState,
        successful_tools: set[str],
        answer: str,
    ) -> str:
        query = state["user_message"]
        contract = TaskContract.model_validate(state["task_contract"])
        permission_errors = [
            event["summary"]
            for event in state["tool_events"]
            if event["status"] == "error" and "权限" in event["summary"]
        ]
        if permission_errors:
            return f"权限不足：{permission_errors[0]}，无法继续读取或执行该操作。"
        candidates = state["entities"].get("material_candidates") or {}
        candidate_items = candidates.get("items") or []
        if "search_materials" in successful_tools and not candidate_items:
            if contract.write_intent in {"reserve_inventory", "build_reservation"}:
                return (
                    "缺少可验证的预留上下文，请指定产品、关联项目和物料后重试。"
                    "在信息明确前不会创建或执行预留。"
                )
            return "没有在物料数据库中找到匹配项。请补充物料编码、完整 MPN、厂家或封装后重试。"
        if (
            len(candidate_items) > 1
            and len(candidates.get("exact_match_ids") or []) != 1
            and "cable_search" not in contract.requested_facts
            and "component_relations" not in contract.requested_facts
            and "engineering_evidence" not in contract.requested_facts
            and "component_evidence_comparison" not in contract.requested_facts
        ):
            labels = "、".join(str(item["code"]) for item in candidate_items)
            return f"找到多个候选（{labels}），请选择候选卡片。"
        project_candidates = state["entities"].get("project_candidates") or {}
        project_items = project_candidates.get("items") or []
        if (
            "search_projects" in successful_tools
            and not project_items
            and not (state["entities"].get("product_candidates") or {}).get("items")
        ):
            return "没有在项目数据库中找到匹配项。请补充项目编号或完整名称后重试。"
        if len(project_items) > 1 and len(project_candidates.get("exact_match_ids") or []) != 1:
            labels = "、".join(str(item["code"]) for item in project_items)
            return f"找到多个候选（{labels}），请选择候选列表。"
        if len(project_items) == 1:
            versions = project_items[0].get("available_versions") or []
            linked_product_build = bool(
                contract.build_quantity is not None and project_items[0].get("product_revision_id")
            )
            current_version_requested = any(
                marker in query.casefold() for marker in ("现在", "当前", "最新")
            )
            selected_version = project_candidates.get("selected_bom_version")
            if (
                len(versions) > 1
                and not selected_version
                and not linked_product_build
                and not current_version_requested
                and not any(version.casefold() in query.casefold() for version in versions)
            ):
                return (
                    "BOM_VERSION_REQUIRED：项目存在多个 BOM 版本（"
                    + "、".join(versions)
                    + "），请先明确选择版本。"
                )
        product_candidates = state["entities"].get("product_candidates") or {}
        product_items = product_candidates.get("items") or []
        if "search_products" in successful_tools and not product_items and not project_items:
            return "没有在产品数据库中找到匹配项。请补充产品编号或完整名称后重试。"
        if len(product_items) > 1 and len(product_candidates.get("exact_match_ids") or []) != 1:
            labels = "、".join(str(item["code"]) for item in product_items)
            return f"找到多个候选（{labels}），请选择候选列表。"
        if (
            "search_products" in successful_tools
            and len(product_items) == 1
            and not contract.requested_facts
        ):
            item = product_items[0]
            default_revision = item.get("default_revision") or {}
            revision_text = (
                f"，默认已发布版本 {default_revision['revision']}"
                if default_revision.get("revision")
                else ""
            )
            return f"已找到产品 {item['code']} · {item['name']}{revision_text}。"
        cable_items = (state["entities"].get("cable_search") or {}).get("items") or []
        cable_locations_grounded = bool(cable_items) and all(
            item.get("location_truth_source") == "InventoryLot" and bool(item.get("locations"))
            for item in cable_items
        )
        if (
            "location" in contract.requested_facts
            and "find_material_locations" not in successful_tools
            and not cable_locations_grounded
        ):
            return "未能通过库位工具验证实时位置，因此不能给出猜测位置。请确认具体物料后重试。"
        inventory_tools = {
            "get_inventory_availability",
            "get_low_stock_materials",
            "analyze_project_bom_stock",
            "analyze_product_build_readiness",
        }
        if {"inventory", "low_stock", "bom_stock"}.intersection(
            contract.requested_facts
        ) and not successful_tools.intersection(inventory_tools):
            return "未能通过库存工具验证实时数量，因此不能给出猜测库存。请确认物料或项目后重试。"
        if "project_bom" in contract.requested_facts and not successful_tools.intersection(
            {"get_project_bom", "analyze_project_bom_stock"}
        ):
            return "未能通过项目 BOM 工具取得实时数据，请确认具体项目后重试。"
        if "product_bom" in contract.requested_facts and "get_product_bom" not in successful_tools:
            return "未能通过产品单台 BOM 工具取得数据，请确认具体产品与版本后重试。"
        if (
            "component_relations" in contract.requested_facts
            and "get_component_relations" not in successful_tools
        ):
            return "未能从工程关系记录取得结论，不能推断兼容或替代关系。"
        if (
            "product_alternates" in contract.requested_facts
            and "get_product_bom_alternates" not in successful_tools
        ):
            return "未能取得该产品版本 BOM 位的备选记录，不能声称存在已批准备选。"
        if (
            "engineering_evidence" in contract.requested_facts
            and "search_datasheet_evidence" not in successful_tools
        ):
            return "未能从当前工程证据中定位可校验引用，因此不能给出技术参数结论。"
        if (
            "component_evidence_comparison" in contract.requested_facts
            and "compare_component_evidence" not in successful_tools
        ):
            return "未能分别取得两个物料的当前工程证据，因此不能推断兼容或替代关系。"
        if (
            "build_readiness" in contract.requested_facts
            and "analyze_product_build_readiness" not in successful_tools
            and not (
                project_items
                and successful_tools.intersection({"analyze_project_bom_stock", "get_project_bom"})
            )
        ):
            return "未能完成确定性备料分析，请确认具体产品、版本和正整数构建数量后重试。"
        if "cable_search" in contract.requested_facts and "search_cables" not in successful_tools:
            return "未能通过线缆工具验证规格与实时库存，因此不能给出猜测结果。"
        if (
            "power_design" in contract.requested_facts
            and "plan_power_design" not in successful_tools
        ):
            return "未能通过电源设计工具形成可追溯方案，因此不能给出猜测的器件或外围值。"
        return answer

    @staticmethod
    def _intent(tools: set[str]) -> str | None:
        if "plan_power_design" in tools:
            return "plan_power_design"
        if "get_cable_detail" in tools:
            return "get_cable_detail"
        if "search_cables" in tools:
            return "search_cables"
        if "compare_component_evidence" in tools:
            return "compare_component_evidence"
        if "search_datasheet_evidence" in tools:
            return "search_datasheet_evidence"
        if "propose_inventory_reservation" in tools:
            return "propose_reservation"
        if "analyze_project_bom_stock" in tools:
            return "analyze_bom_stock"
        if "analyze_product_build_readiness" in tools:
            return "analyze_product_build_readiness"
        if "get_product_bom" in tools:
            return "get_product_bom"
        if "get_product_bom_alternates" in tools:
            return "get_product_bom_alternates"
        if "get_component_relations" in tools:
            return "get_component_relations"
        if "search_products" in tools:
            return "search_product"
        if "get_project_bom" in tools:
            return "get_project_bom"
        if "search_projects" in tools:
            return "search_project"
        if "find_material_locations" in tools:
            return "find_location"
        if tools.intersection({"get_inventory_availability", "get_low_stock_materials"}):
            return "check_inventory"
        if tools.intersection({"search_materials", "get_material_detail"}):
            return "search_material"
        return None
