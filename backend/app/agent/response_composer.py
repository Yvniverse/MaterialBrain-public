from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.schemas.agent import GroundedFact


def display_quantity(value: Any) -> str:
    """Format a quantity for prose without changing canonical Decimal/API values."""

    if value is None:
        return "0"
    try:
        decimal_value = Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        return str(value)
    if not decimal_value.is_finite():
        return str(value)
    rendered = format(decimal_value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def _hashable_evidence_value(value: Any) -> Any:
    """Keep structured evidence values usable in the duplicate-detection key."""

    if isinstance(value, dict):
        return tuple(
            sorted(
                (
                    str(key),
                    _hashable_evidence_value(item),
                )
                for key, item in value.items()
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(_hashable_evidence_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_hashable_evidence_value(item) for item in value), key=repr))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


@dataclass(frozen=True)
class ComposedResponse:
    answer: str
    narrative: str
    grounded_facts: list[GroundedFact]


class GroundedResponseComposer:
    """Compose critical warehouse facts without letting the LLM restate them."""

    def compose(
        self,
        *,
        user_message: str,
        entities: dict[str, Any],
        narrative: str,
    ) -> ComposedResponse:
        sections: list[str] = []
        facts: list[GroundedFact] = []

        inventory = entities.get("inventory")
        if inventory:
            self._inventory(inventory, facts, sections)
        else:
            for item in (entities.get("material_inventories") or {}).get("items") or []:
                self._inventory(item, facts, sections)

        locations = entities.get("locations")
        if locations:
            self._locations(locations, facts, sections)

        bom = entities.get("bom_analysis") or entities.get("project_bom")
        if bom:
            self._bom(bom, facts, sections, user_message=user_message)

        build_readiness = entities.get("build_readiness")
        if build_readiness:
            self._build_readiness(build_readiness, facts, sections)
        elif entities.get("product_bom"):
            self._product_bom(entities["product_bom"], facts, sections)

        low_stock = entities.get("low_stock")
        if low_stock:
            self._low_stock(low_stock, facts, sections)

        cable_search = entities.get("cable_search")
        cable_detail = entities.get("cable_detail")
        if cable_search:
            self._cable_search(cable_search, facts, sections, user_message=user_message)
        elif cable_detail:
            self._cable_detail(cable_detail, facts, sections, user_message=user_message)

        component_search = entities.get("component_search")
        if component_search:
            self._component_search(component_search, sections)

        power_design = entities.get("power_design")
        if power_design:
            self._power_design(power_design, facts, sections)

        engineering_research = entities.get("engineering_research")
        if engineering_research:
            self._engineering_research(engineering_research, facts, sections)

        component_relations = entities.get("component_relations")
        if component_relations:
            self._component_relations(component_relations, facts, sections)

        product_alternates = entities.get("product_bom_alternates")
        if product_alternates:
            self._product_alternates(product_alternates, facts, sections)

        engineering_evidence = entities.get("engineering_evidence")
        if engineering_evidence:
            self._engineering_evidence(engineering_evidence, facts, sections)

        evidence_comparison = entities.get("component_evidence_comparison")
        if evidence_comparison:
            self._evidence_comparison(evidence_comparison, facts, sections)

        relation_policy = entities.get("relation_policy")
        if relation_policy:
            sections.append(
                "候选关系尚未经过工程验证；已验证工程关系也只证明记录中的关系类型。"
                "similar_to 不代表引脚兼容，也不能据此作为替代料使用。\n"
                "候选产品备选仍处于未批准状态。"
                "“此产品版本已批准备选”只适用于精确产品版本和 BOM 位，"
                "不会自动进入 Build Readiness 或 BuildPlan。"
            )

        proposal = entities.get("proposal")
        if proposal:
            facts.append(
                GroundedFact(
                    kind="proposal",
                    source_tool="propose_inventory_reservation",
                    entity_id=proposal.get("id"),
                    field="status",
                    value=proposal.get("status"),
                    label="Proposal 状态",
                )
            )
            sections.append(
                f"已创建待审批 Proposal {proposal.get('proposal_no')}。"
                "批准前库存和预留数量不会变化。"
            )

        build_plan_proposal = entities.get("build_plan_proposal")
        if build_plan_proposal:
            self._build_plan_proposal(build_plan_proposal, facts, sections)

        limitation = (
            ""
            if (build_readiness or entities.get("product_bom"))
            else self._known_limitation(user_message)
        )
        if limitation:
            sections.insert(0, limitation)

        if sections:
            answer = "\n".join(sections)
            if narrative and entities.get("engineering_research"):
                answer = f"{narrative.strip()}\n\n{answer}"
            return ComposedResponse(
                answer=answer,
                narrative=narrative,
                grounded_facts=facts,
            )
        final_narrative = narrative or "本次未能生成有效回答，请换一种方式描述任务。"
        return ComposedResponse(
            answer=final_narrative,
            narrative=final_narrative,
            grounded_facts=[],
        )

    @staticmethod
    def _inventory(data: dict, facts: list[GroundedFact], sections: list[str]) -> None:
        material_id = data.get("material_id")
        unit = data.get("unit")
        fields = (
            ("quantity", "当前库存"),
            ("reserved_quantity", "已预留"),
            ("available_quantity", "可用库存"),
            ("safety_stock", "安全库存"),
        )
        for field, label in fields:
            facts.append(
                GroundedFact(
                    kind="inventory",
                    source_tool="get_inventory_availability",
                    entity_id=material_id,
                    field=field,
                    value=data.get(field),
                    unit=unit,
                    label=label,
                )
            )
        sections.append(
            f"{data.get('code')} · {data.get('mpn') or data.get('name')}："
            f"当前库存 {display_quantity(data.get('quantity'))} {unit}，"
            f"预留占用 {display_quantity(data.get('reserved_quantity'))} {unit}，"
            f"可用 {display_quantity(data.get('available_quantity'))} {unit}，"
            f"安全库存 {display_quantity(data.get('safety_stock'))} {unit}。"
        )

    @staticmethod
    def _locations(data: dict, facts: list[GroundedFact], sections: list[str]) -> None:
        material_id = data.get("material_id")
        reconciliation_fields = (
            ("material_quantity", "账面库存"),
            ("lot_quantity_total", "库位库存合计"),
            ("unallocated_quantity", "未精确分配库存"),
            ("distribution_status", "库位分布状态"),
        )
        for field, label in reconciliation_fields:
            facts.append(
                GroundedFact(
                    kind="reconciliation",
                    source_tool="find_material_locations",
                    entity_id=material_id,
                    field=field,
                    value=data.get(field),
                    label=label,
                )
            )

        status = data.get("distribution_status")
        if status == "inconsistent":
            sections.append(
                f"{data.get('code')} 的账面库存为 "
                f"{display_quantity(data.get('material_quantity'))}，"
                f"库位库存合计为 {display_quantity(data.get('lot_quantity_total'))}，"
                "分布数据不一致。"
                "完成盘点前不能声称确定库位。"
            )
            return

        location_lines = []
        for location in data.get("locations") or []:
            fact_fields = [
                ("location_id", "库位 ID"),
                ("full_path", "库位路径"),
            ]
            if location.get("quantity_is_exact"):
                fact_fields.append(("quantity_at_location", "库位数量"))
            for field, label in fact_fields:
                facts.append(
                    GroundedFact(
                        kind="location",
                        source_tool="find_material_locations",
                        entity_id=location.get("location_id"),
                        field=field,
                        value=location.get(field),
                        label=label,
                    )
                )
            if location.get("quantity_is_exact"):
                location_lines.append(
                    f"{location.get('full_path')}"
                    f"（{display_quantity(location.get('quantity_at_location'))}）"
                )
            else:
                location_lines.append(f"{location.get('full_path')}（主数据登记，数量未分配）")
        if location_lines:
            sections.append("已登记库位：" + "；".join(location_lines) + "。")
        else:
            sections.append("当前没有登记可确认的实际库位。")
        if status == "partial":
            sections.append(
                "库位分布仅部分完成：还有 "
                f"{display_quantity(data.get('unallocated_quantity'))} 未精确分配到库位。"
            )

    @staticmethod
    def _bom(
        data: dict,
        facts: list[GroundedFact],
        sections: list[str],
        *,
        user_message: str = "",
    ) -> None:
        project = data.get("project") or {}
        version = data.get("version") or "未标版本"
        lines = [f"{project.get('code')} · {project.get('name')}，BOM {version}："]
        folded = user_message.casefold()
        if any(
            marker in folded
            for marker in (
                "带我去找",
                "带我去拿",
                "带我找",
                "去哪拿",
                "从哪拿",
                "开始找料",
                "开始拿料",
            )
        ):
            lines.append(
                "我先按当前 InventoryLot 的物理库存库位列出可取位置。"
                "这些位置不是项目级 PickAllocation，也没有生成拣料顺序；"
                "真正的逐站 Picking 需要后续 Picking Core。"
            )
        for item in data.get("items") or []:
            material_id = item.get("material_id")
            for field, label in (
                ("required_quantity", "BOM 总需求"),
                ("reserved_for_project", "项目已预留"),
                ("shortage", "缺料"),
            ):
                if field in item:
                    facts.append(
                        GroundedFact(
                            kind="bom",
                            source_tool=(
                                "analyze_project_bom_stock"
                                if "shortage" in item
                                else "get_project_bom"
                            ),
                            entity_id=material_id,
                            field=field,
                            value=item.get(field),
                            unit=item.get("unit"),
                            label=label,
                        )
                    )
            line = (
                f"- {item.get('code')}：总需求 "
                f"{display_quantity(item.get('required_quantity'))}，"
                f"可用 {display_quantity(item.get('available_quantity'))}，"
                f"项目预留占用 {display_quantity(item.get('reserved_for_project'))}"
            )
            if "shortage" in item:
                line += f"，缺料 {display_quantity(item.get('shortage'))}"
            lines.append(line + "。")
        lines.append(str(data.get("quantity_semantics") or ""))
        sections.append("\n".join(lines))

    @staticmethod
    def _low_stock(data: dict, facts: list[GroundedFact], sections: list[str]) -> None:
        lines = [f"当前共有 {data.get('count', 0)} 项低库存物料："]
        for item in data.get("items") or []:
            for field, label in (
                ("quantity", "当前库存"),
                ("reserved_quantity", "已预留"),
                ("available_quantity", "可用库存"),
                ("safety_stock", "安全库存"),
            ):
                facts.append(
                    GroundedFact(
                        kind="inventory",
                        source_tool="get_low_stock_materials",
                        entity_id=item.get("material_id"),
                        field=field,
                        value=item.get(field),
                        unit=item.get("unit"),
                        label=label,
                    )
                )
            lines.append(
                f"- {item.get('code')}：可用 "
                f"{display_quantity(item.get('available_quantity'))}，"
                f"安全库存 {display_quantity(item.get('safety_stock'))}。"
            )
        sections.append("\n".join(lines))

    @staticmethod
    def _product_bom(data: dict, facts: list[GroundedFact], sections: list[str]) -> None:
        product = data.get("product") or {}
        revision = data.get("revision") or {}
        lines = [
            f"{product.get('code')} · {product.get('name')} · {revision.get('revision')} 单台 BOM："
        ]
        for item in data.get("items") or []:
            facts.append(
                GroundedFact(
                    kind="product_bom",
                    source_tool="get_product_bom",
                    entity_id=item.get("material_id"),
                    field="quantity_per_unit",
                    value=item.get("quantity_per_unit"),
                    unit=item.get("unit"),
                    label="单台用量",
                )
            )
            lines.append(
                f"- {item.get('code')} · {item.get('mpn') or item.get('name')}："
                f"单台用量 {display_quantity(item.get('quantity_per_unit'))} "
                f"{item.get('unit')}"
            )
        lines.append("以上为产品单台用量，不是 Project BOM 总需求。")
        sections.append("\n".join(lines))

    @staticmethod
    def _cable_search(
        data: dict,
        facts: list[GroundedFact],
        sections: list[str],
        *,
        user_message: str = "",
    ) -> None:
        folded = user_message.casefold()
        if any(marker in folded for marker in ("订单", "买了", "采购数量")):
            sections.append(
                "不是。采购或订单数量不能作为当前库存；"
                "当前库存只以 Material/InventoryLot 的记录为准。"
            )
        if any(marker in folded for marker in ("采购单价", "库存价值", "会计")):
            sections.append(
                "不是。导入的参考采购单价不是已确认的会计库存价值，不能据此形成财务结论。"
            )
        if "storage_location" in folded:
            sections.append(
                "不是。storage_location 只是描述性回退字段；可操作的实际库位必须来自 InventoryLot。"
            )
        if "方向含义" in folded or ("端子线" in folded and "ffc" in folded):
            sections.append(
                "不完全一样。FFC 的同向/反向描述两端裸露触点朝向；"
                "端子线的方向字段是连接器装配描述，"
                "不能直接套用为同一种工程结论。"
            )
        if any(marker in folded for marker in ("自动换", "自动替换", "自动改")):
            sections.append(
                "不能自动替换。长度接近只用于候选排序，不代表兼容，也不会改动已发布产品 BOM。"
            )
        if any(marker in folded for marker in ("库存改成", "直接改库存")):
            sections.append(
                "不能直接改写库存。线缆库存变更仍须走正常审批，并由 InventoryService 执行。"
            )
        constraints = data.get("constraints") or {}
        if (
            constraints.get("cable_kind") == "rf_coax"
            and constraints.get("connector_pitch_mm") is not None
        ):
            sections.append(
                "射频同轴线不使用端子/FFC 的 pitch 作为兼容约束；请改用同轴规格和连接器型号筛选。"
            )
        if data.get("needs_direction_disambiguation"):
            sections.append(
                str(data.get("clarification") or "触点方向需要同向(A型)还是反向(B型)？")
            )
            return
        result_state = str(data.get("result_state") or "")
        constraints = data.get("constraints") or {}
        requested_identity = constraints.get("connector_a") or constraints.get("connector_b")
        if result_state in {"near_match", "no_match"} and requested_identity:
            sections.append(
                f"⚠ 未找到与 {requested_identity} 完全相同的精确型号；"
                "以下结果仅是近似候选，不能视为该型号的确认匹配。"
            )
        items = data.get("items") or []
        if not items:
            sections.append(
                "没有找到满足这些明确线缆约束的库存记录。可以放宽长度或补充连接器型号。"
            )
            return
        lines = [f"找到 {len(items)} 条匹配线缆："]
        for item in items:
            facts.extend(
                [
                    GroundedFact(
                        kind="cable",
                        source_tool="search_cables",
                        entity_id=item.get("material_id"),
                        field="available_quantity",
                        value=item.get("available_quantity"),
                        unit=item.get("unit"),
                        label="可用库存",
                    ),
                    GroundedFact(
                        kind="cable",
                        source_tool="search_cables",
                        entity_id=item.get("material_id"),
                        field="direction",
                        value=item.get("direction"),
                        label="触点方向",
                    ),
                ]
            )
            pin_text = (
                f"{item.get('pin_count')}→{item.get('pin_count_b')} Pin"
                if item.get("pin_count_b")
                else f"{item.get('pin_count')} Pin"
                if item.get("pin_count")
                else ""
            )
            spec = " · ".join(
                str(value)
                for value in (
                    f"{item.get('connector_pitch_mm')} mm"
                    if item.get("connector_pitch_mm")
                    else "",
                    pin_text,
                    f"{display_quantity(item.get('length_cm'))} cm",
                    {"same": "同向", "reverse": "反向"}.get(item.get("direction"), ""),
                )
                if value
            )
            location = (item.get("locations") or [{}])[0].get("full_path")
            lines.append(
                f"- {item.get('code')} · {item.get('name')}：{spec}；"
                f"可用 {display_quantity(item.get('available_quantity'))} {item.get('unit')}"
                + (f"；{location}" if location else "；尚无可确认的实际库位")
            )
        sections.append("\n".join(lines))

    @staticmethod
    def _cable_detail(
        data: dict,
        facts: list[GroundedFact],
        sections: list[str],
        *,
        user_message: str = "",
    ) -> None:
        GroundedResponseComposer._cable_search(
            {
                "items": [data],
                "needs_direction_disambiguation": False,
            },
            facts,
            sections,
            user_message=user_message,
        )

    @staticmethod
    def _build_readiness(data: dict, facts: list[GroundedFact], sections: list[str]) -> None:
        product = data.get("product") or {}
        revision = data.get("revision") or {}
        build_quantity = data.get("build_quantity")
        sufficient = bool(data.get("sufficient"))
        lines = [
            f"{product.get('name')} · {revision.get('revision')}",
            f"计划：{build_quantity} 台",
            (
                "✓ 当前库存可满足"
                if sufficient
                else f"✕ 当前暂时备不齐，缺 {data.get('shortage_count')} 类物料"
            ),
            f"最大可立即构建：{display_quantity(data.get('max_buildable_units'))} 台",
        ]
        facts.extend(
            [
                GroundedFact(
                    kind="build_readiness",
                    source_tool="analyze_product_build_readiness",
                    entity_id=revision.get("id"),
                    field="sufficient",
                    value=sufficient,
                    label="库存可满足",
                ),
                GroundedFact(
                    kind="build_readiness",
                    source_tool="analyze_product_build_readiness",
                    entity_id=revision.get("id"),
                    field="max_buildable_units",
                    value=data.get("max_buildable_units"),
                    unit="台",
                    label="最大可立即构建",
                ),
            ]
        )
        shortages = [
            item
            for item in data.get("items") or []
            if Decimal(str(item.get("shortage") or "0")) > 0
        ]
        for item in data.get("items") or []:
            for field, label in (
                ("quantity_per_unit", "单台用量"),
                ("required_total", "计划总需求"),
                ("reserved_for_project", "当前项目已预留"),
                ("additional_reservation_required", "本次还需新增预留"),
                ("coverage", "当前可覆盖"),
                ("shortage", "缺料"),
                ("projected_free_available_after_build", "构建后可用库存"),
            ):
                facts.append(
                    GroundedFact(
                        kind="build_readiness",
                        source_tool="analyze_product_build_readiness",
                        entity_id=item.get("material_id"),
                        field=field,
                        value=item.get(field),
                        unit=item.get("unit"),
                        label=label,
                    )
                )
        blockers = [item for item in data.get("items") or [] if item.get("material_blocker")]
        if blockers:
            lines.append("暂时无法生成构建计划，单台 BOM 仍引用已停用物料：")
            lines.extend(f"- {item.get('code')} · {item.get('name')}" for item in blockers)
        if shortages:
            lines.append("缺料明细：")
            for item in shortages:
                lines.append(
                    f"- {item.get('code')} · {item.get('mpn') or item.get('name')}："
                    f"需要 {display_quantity(item.get('required_total'))}，"
                    f"可覆盖 {display_quantity(item.get('coverage'))}，"
                    f"缺 {display_quantity(item.get('shortage'))} {item.get('unit')}"
                )
                for alternate in item.get("approved_alternates") or []:
                    lines.append(
                        f"  已批准备选（仅信息）：{alternate.get('code')} · "
                        f"{alternate.get('mpn') or alternate.get('name')}。"
                        "未计入上述覆盖量或最大可构建数。"
                    )
        # A hard shortage is already reported above in red.  Keep the amber
        # safety-stock warning disjoint so the answer and candidate card use
        # the same user-facing count.
        safety_risk_count = sum(
            bool(item.get("below_safety_after_build"))
            and Decimal(str(item.get("shortage") or "0")) == 0
            for item in data.get("items") or []
        )
        if safety_risk_count:
            lines.append(f"⚠ {safety_risk_count} 类物料构建后将低于安全库存。")
        lines.append("本次仅分析备料情况，没有预留或修改库存。")
        sections.append("\n".join(lines))

    @staticmethod
    def _build_plan_proposal(
        data: dict,
        facts: list[GroundedFact],
        sections: list[str],
    ) -> None:
        plan = data.get("build_plan") or {}
        product = plan.get("product") or {}
        revision = plan.get("revision") or {}
        project = plan.get("project") or {}
        if data.get("fully_reserved"):
            sections.append(
                f"构建计划 {plan.get('plan_no')}：{product.get('name')} "
                f"{revision.get('revision')} × "
                f"{display_quantity(plan.get('build_quantity'))} 台所需物料，"
                f"已由项目 {project.get('code')} 的现有预留完全覆盖；未创建空 Proposal。"
            )
            return

        proposal = data.get("proposal") or data
        facts.append(
            GroundedFact(
                kind="proposal",
                source_tool="propose_build_material_reservation",
                entity_id=proposal.get("id"),
                field="status",
                value=proposal.get("status"),
                label="Proposal 状态",
            )
        )
        reserved = [
            item
            for item in plan.get("items") or []
            if Decimal(str(item.get("reserved_for_project_at_plan") or "0")) > 0
        ]
        additional = [
            item
            for item in plan.get("items") or []
            if Decimal(str(item.get("additional_reservation_required") or "0")) > 0
        ]
        lines = [
            f"已生成构建计划 {plan.get('plan_no')} 和待审批 Proposal "
            f"{proposal.get('proposal_no')}。",
            f"来源：{product.get('name')} · {revision.get('revision')} × "
            f"{display_quantity(plan.get('build_quantity'))} 台",
            f"关联项目：{project.get('code')} · {project.get('name')}",
        ]
        if reserved:
            lines.append("当前项目已预留：")
            lines.extend(
                f"- {item.get('code')}："
                f"{display_quantity(item.get('reserved_for_project_at_plan'))} "
                f"{item.get('unit')}"
                for item in reserved[:5]
            )
        lines.append("本次还需新增预留：")
        lines.extend(
            f"- {item.get('code')}："
            f"{display_quantity(item.get('additional_reservation_required'))} "
            f"{item.get('unit')}"
            for item in additional[:8]
        )
        if len(additional) > 8:
            lines.append(f"- 另有 {len(additional) - 8} 类物料，请在审批卡查看全部。")
        lines.append("库存尚未修改，需要人工批准。")
        sections.append("\n".join(lines))

    @staticmethod
    def _component_search(data: dict, sections: list[str]) -> None:
        candidates = data.get("candidates") or []
        if not candidates:
            sections.append("没有找到同时满足这些明确要求的可信候选器件。")
        else:
            heading = (
                "候选相似器件："
                if (data.get("query") or {}).get("replacement_intent")
                else "找到以下候选器件："
            )
            lines = [heading]
            for item in candidates:
                reasons = (item.get("match_reasons") or [])[:4]
                reason_text = "；".join(reasons)
                inventory = item.get("inventory") or {}
                lines.append(
                    f"- {item.get('code')} · {item.get('mpn') or item.get('name')}"
                    f"：可用 {display_quantity(inventory.get('available_quantity'))} "
                    f"{inventory.get('unit')}" + (f"；{reason_text}" if reason_text else "")
                )
                for relation in item.get("validated_relations") or []:
                    related = relation.get("related_material") or {}
                    lines.append(
                        f"  已验证工程关系：{relation.get('relation_type')} · "
                        f"{related.get('mpn') or related.get('code')}。"
                        "不构成跨产品通用批准。"
                    )
            sections.append("\n".join(lines))
        sections.append(str(data.get("engineering_caveat") or "所有候选仍需工程验证。"))

    @staticmethod
    def _component_relations(
        data: dict,
        facts: list[GroundedFact],
        sections: list[str],
    ) -> None:
        items = data.get("items") or []
        if not items:
            sections.append(
                "没有找到已登记的候选或已验证工程关系。"
                "因此不能推断引脚兼容，也不能据此作为替代料使用。"
            )
            return
        lines = ["器件工程关系："]
        for item in items:
            source = item.get("source_material") or {}
            target = item.get("target_material") or {}
            status = item.get("status")
            relation_type = item.get("relation_type")
            label = "已验证工程关系" if status == "validated" else "候选关系（未验证）"
            facts.append(
                GroundedFact(
                    kind="component_relation",
                    source_tool="get_component_relations",
                    entity_id=item.get("id"),
                    field="status",
                    value=status,
                    label=label,
                )
            )
            lines.append(
                f"- {source.get('mpn') or source.get('code')} ↔ "
                f"{target.get('mpn') or target.get('code')}：{label} · "
                f"{relation_type}。"
            )
            if item.get("evidence_summary"):
                lines.append(f"  证据摘要：{item['evidence_summary']}")
        if not data.get("pin_compatible_validated"):
            lines.append("未发现显式已验证的 pin_compatible 记录，不能声称引脚兼容。")
        lines.append("⚠ similar_to 不代表替代料批准；是否可用还需要具体产品版本/BOM 位批准。")
        sections.append("\n".join(lines))

    @staticmethod
    def _power_design(
        data: dict,
        facts: list[GroundedFact],
        sections: list[str],
    ) -> None:
        requirements = data.get("requirements") or {}
        input_v = requirements.get("input_voltage_v")
        output_v = requirements.get("output_voltage_v")
        lines = [
            f"电源设计：{input_v}V → {output_v}V；先按拓扑筛选，再核对器件证据。",
        ]
        for branch in data.get("branches") or []:
            topology = str(branch.get("topology") or "").upper()
            candidates = branch.get("candidates") or []
            names = "、".join(
                str(item.get("mpn") or item.get("code")) for item in candidates
            ) or "没有找到已审计且兼容的在库候选"
            lines.append(f"{topology}：{names}。")
            for candidate in candidates:
                inventory = candidate.get("inventory") or {}
                locations = candidate.get("locations") or {}
                location_names = "、".join(
                    str(item.get("full_path"))
                    for item in locations.get("locations") or []
                    if item.get("full_path")
                ) or "暂无可确认实际库位"
                lines.append(
                    f"  {candidate.get('code')}：可用 {inventory.get('available_quantity')} "
                    f"{inventory.get('unit')}；库位 {location_names}。"
                )
                for calculation in candidate.get("calculations") or []:
                    if calculation.get("status") == "calculated":
                        efficiency = display_quantity(
                            Decimal(str(calculation.get("ideal_efficiency")))
                        )
                        lines.append(
                            f"  服务端计算：LDO 损耗 {calculation.get('loss_w')}W，"
                            f"理想效率 {efficiency}。"
                        )
            for citation in branch.get("citations") or []:
                facts.append(
                    GroundedFact(
                        kind="power_design",
                        source_tool="plan_power_design",
                        entity_id=(candidates[0].get("material_id") if candidates else None),
                        field="evidence",
                        value={
                            "mpn": citation.get("mpn"),
                            "source_url": citation.get("source_url"),
                            "datasheet_url": citation.get("datasheet_url"),
                            "verified_facts": citation.get("verified_facts"),
                        },
                        label="电源器件一手证据",
                    )
                )
            if any(
                citation.get("managed_evidence_anchor") is False
                for citation in branch.get("citations") or []
            ):
                lines.append(
                    "证据 provenance bridge：官方器件清单/厂商页面已核验；"
                    "当前不是受管 EngineeringEvidence 页锚点（P1 迁移债务）。"
                )
        if data.get("missing_constraints"):
            lines.append("待补约束：" + "、".join(data["missing_constraints"]) + "。")
        lines.append(
            "外围角色已列出；未被器件数据手册/参考设计锚定的精确阻容、电感值不在此处臆造。"
        )
        sections.append("\n".join(lines))

    @staticmethod
    def _product_alternates(
        data: dict,
        facts: list[GroundedFact],
        sections: list[str],
    ) -> None:
        items = data.get("items") or []
        if data.get("scope_required"):
            sections.append(
                "产品备选必须限定到具体 ProductRevision 和 BOM 位。"
                "备选库存不会自动计入 Build Readiness 或 BuildPlan。"
            )
            return
        if not items:
            sections.append("当前产品版本/BOM 位没有候选或已批准备选记录；不能引用其他产品的批准。")
            return
        lines = ["产品 BOM 备选："]
        for item in items:
            product = item.get("product") or {}
            revision = item.get("revision") or {}
            primary = item.get("primary_material") or {}
            alternate = item.get("alternate_material") or {}
            approved = item.get("status") == "approved"
            label = "此产品版本已批准备选" if approved else "候选备选，尚未批准"
            facts.append(
                GroundedFact(
                    kind="product_alternate",
                    source_tool="get_product_bom_alternates",
                    entity_id=item.get("id"),
                    field="status",
                    value=item.get("status"),
                    label=label,
                )
            )
            lines.append(
                f"- {product.get('code')} {revision.get('revision')} · "
                f"主料 {primary.get('code')} → {alternate.get('code')}：{label}。"
            )
            if item.get("usage_condition"):
                lines.append(f"  使用条件：{item['usage_condition']}")
            lines.append("  ⚠ 该状态仅适用于上述产品版本的当前 BOM 位，不是跨产品通用批准。")
        lines.append("本阶段仅展示备选信息；主料缺口与最大可构建数仍按主 BOM 计算。")
        sections.append("\n".join(lines))

    @staticmethod
    def _engineering_evidence(
        data: dict,
        facts: list[GroundedFact],
        sections: list[str],
    ) -> None:
        evidence_facts = data.get("facts") or []
        citations = data.get("citations") or []
        lines = [str(data.get("conclusion") or "当前证据不足") + "。"]
        seen_fact_keys: set[tuple[Any, ...]] = set()
        if evidence_facts:
            lines.append("可验证的结构化事实：")
            for fact in evidence_facts:
                field_name = str(fact.get("field") or "evidence")
                fact_key = (
                    field_name,
                    _hashable_evidence_value(fact.get("variant")),
                    _hashable_evidence_value(fact.get("name")),
                    _hashable_evidence_value(fact.get("value")),
                    _hashable_evidence_value(fact.get("min")),
                    _hashable_evidence_value(fact.get("max")),
                    _hashable_evidence_value(fact.get("unit")),
                )
                if fact_key in seen_fact_keys:
                    continue
                seen_fact_keys.add(fact_key)
                raw_value = fact.get("name", fact.get("value", fact.get("values", "—")))
                if "min" in fact and "max" in fact:
                    value = f"{fact.get('min')}–{fact.get('max')} {fact.get('unit') or ''}".strip()
                elif isinstance(raw_value, dict):
                    value = "；".join(f"{key}={item}" for key, item in raw_value.items())
                elif isinstance(raw_value, (list, tuple)):
                    value = "、".join(str(item) for item in raw_value)
                else:
                    value = raw_value
                if (
                    fact.get("unit")
                    and "min" not in fact
                    and "max" not in fact
                ):
                    value = f"{value} {fact['unit']}"
                fact_type = str(fact.get("fact_type") or "")
                if fact_type == "derived_calculation":
                    condition = fact.get("calculation") or fact.get("conditions") or "见证据"
                    value = f"{value}（派生计算，条件：{condition}）"
                elif fact.get("conditions"):
                    value = f"{value}（条件：{fact['conditions']}）"
                facts.append(
                    GroundedFact(
                        kind="engineering_evidence",
                        source_tool="search_datasheet_evidence",
                        entity_id=fact.get("anchor_id"),
                        field=field_name,
                        value=value,
                        unit=fact.get("unit"),
                        label=(
                            f"工程证据 · 变体 {fact.get('variant')}"
                            if fact.get("variant")
                            else "工程证据"
                        ),
                    )
                )
                field_label = {
                    "purpose": "作用",
                    "interface": "接口",
                    "supply_voltage": "供电范围",
                    "input_voltage": (
                        "推荐输入范围"
                        if fact_type == "recommended_operating"
                        else "输入电压"
                    ),
                    "input_voltage_absolute_max": "绝对最大输入电压",
                    "power_dissipation": "功耗",
                    "thermal_resistance": "热阻",
                    "gain": "增益",
                    "package": "封装",
                }.get(field_name, field_name)
                if field_name == "pin" and fact.get("number") is not None:
                    field_label = f"Pin {fact.get('number')}"
                context: list[str] = []
                if fact.get("variant"):
                    context.append(f"变体 {fact['variant']}")
                if fact.get("source_context"):
                    source_context = str(fact["source_context"])
                    context.append(
                        "封面典型应用" if "cover" in source_context.casefold() else source_context
                    )
                suffix = f"（{'；'.join(context)}）" if context else ""
                lines.append(f"- {field_label}：{value}{suffix}")
        field_names = {str(fact.get("field") or "") for fact in evidence_facts}
        if {"input_voltage", "input_voltage_absolute_max"}.issubset(field_names):
            lines.append("绝对最大值不是推荐工作范围，设计时应保留输入瞬态与裕量。")
        if citations:
            lines.append("引用：")
            for citation in citations:
                fixture = "合成 CI 证据" if citation.get("synthetic_fixture") else "工程证据"
                lines.append(
                    f"- {fixture} · {citation.get('document_title')} "
                    f"{citation.get('document_revision')} · p.{citation.get('page')} · "
                    f"{citation.get('section')}"
                )
        lines.append("证据检索只读，不能据此自动验证、批准或撤销工程关系/产品备选。")
        sections.append("\n".join(lines))

    @staticmethod
    def _engineering_research(
        data: dict,
        facts: list[GroundedFact],
        sections: list[str],
    ) -> None:
        plan = data.get("plan") or {}
        draft = data.get("draft") or {}
        requirements = plan.get("requirements") or data.get("requirements") or {}
        vin = requirements.get("input_voltage_v")
        vout = requirements.get("output_voltage_v")
        current = requirements.get("load_current_a") or requirements.get("load_current_max_a")
        status_labels = {
            "found": "已找到",
            "not_found": "未找到",
            "needs_constraints": "仍缺约束",
            "sufficient": "充分",
            "partial": "部分",
            "insufficient": "不足",
            "not_checked": "未检查",
            "reviewable": "可审阅",
            "not_formed": "未形成",
            "stocked_matched": "规格匹配且库存/库位已查",
            "shortage": "规格匹配但可用量不足",
            "no_matching_material": "规格已知但没有匹配物料",
            "needs_design_selection": "规格或工况待确认（不判为缺料）",
            "ambiguous_candidates": "候选不唯一或属性未知",
            "stocked_location_unassigned": "有库存但库位未确认",
        }
        round_number = plan.get("round") or 1
        candidate_status = status_labels.get(data.get("candidate_status"), "未知")
        evidence_status = status_labels.get(data.get("evidence_status"), "未知")
        draft_status = status_labels.get(data.get("draft_status"), "未知")
        lines = [
            (
                f"工程研究第 {round_number} 轮：{vin}V → {vout}V / "
                f"{(str(current) + 'A') if current is not None else '未提供电流'}；"
                "确定性只读，多步骤工具轨迹已保留。"
            ),
            "状态轴："
            f"候选 {candidate_status}；"
            f"页级证据 {evidence_status}；"
            f"工程草案 {draft_status}。",
            str(draft.get("conclusion") or "当前不能形成有证据的工程草案。"),
        ]
        for architecture in draft.get("topologies") or []:
            label = architecture.get("label") or architecture.get("topology") or "电源方案"
            lines.append(f"架构候选：{label}。{architecture.get('summary') or ''}")
            for rail in architecture.get("rails") or []:
                rail_current = rail.get("load_current_a")
                current_label = f"{rail_current}A" if rail_current is not None else "电流待分配"
                lines.append(
                    f"  电源轨：{rail.get('label')} · {rail.get('voltage_v')}V · {current_label}。"
                )
            for stage in architecture.get("stages") or []:
                if stage.get("loss_w") is not None:
                    lines.append(
                        f"  服务端 LDO 损耗：{stage.get('input_voltage_v')}V → "
                        f"{stage.get('output_voltage_v')}V / "
                        f"{stage.get('load_current_a')}A = {stage.get('loss_w')}W。"
                    )
        for label, branch_key in (("Buck", "buck"), ("LDO", "ldo")):
            branch = draft.get(branch_key) or {}
            lines.append(f"{label}：{branch.get('summary') or '当前没有足够的在库与资料依据。'}")
            for candidate in branch.get("candidates") or []:
                inventory = candidate.get("inventory") or {}
                locations = candidate.get("locations") or {}
                material_id = candidate.get("material_id")
                if inventory.get("available_quantity") is not None:
                    facts.append(
                        GroundedFact(
                            kind="engineering_research",
                            source_tool="get_inventory_availability",
                            entity_id=material_id,
                            field="available_quantity",
                            value=inventory.get("available_quantity"),
                            unit=inventory.get("unit"),
                            label="研究候选可用库存",
                        )
                    )
                if locations.get("distribution_status") is not None:
                    facts.append(
                        GroundedFact(
                            kind="engineering_research",
                            source_tool="find_material_locations",
                            entity_id=material_id,
                            field="distribution_status",
                            value=locations.get("distribution_status"),
                            label="研究候选库位分布状态",
                        )
                        )
                facts.append(
                    GroundedFact(
                        kind="engineering_research",
                        source_tool="search_datasheet_evidence",
                        entity_id=material_id,
                        field="evidence_coverage",
                        value=(
                            candidate.get("evidence_status")
                            or candidate.get("evidence_coverage")
                        ),
                        label="研究候选页级证据状态",
                    )
                )
                paths = "、".join(
                    str(item.get("full_path"))
                    for item in locations.get("locations") or []
                    if item.get("full_path")
                ) or "暂无可确认实际库位"
                lines.append(
                    f"  - {candidate.get('mpn') or candidate.get('code')}："
                    f"可用 {inventory.get('available_quantity', '未知')} "
                    f"{inventory.get('unit', '')}；库位 {paths}；证据 "
                    f"{status_labels.get(candidate.get('evidence_status'), '未知')}。"
                )
                if candidate.get("electrical_thermal_judgment"):
                    lines.append(f"    工程判断：{candidate['electrical_thermal_judgment']}")
                if candidate.get("evidence_gaps"):
                    lines.append("    证据缺口：" + "；".join(candidate["evidence_gaps"]) + "。")
                for calculation in candidate.get("calculations") or []:
                    if calculation.get("status") == "calculated":
                        facts.append(
                            GroundedFact(
                                kind="engineering_research",
                                source_tool="engineering_fact_verifier",
                                entity_id=material_id,
                                field="ldo_loss_w",
                                value=calculation.get("loss_w"),
                                unit="W",
                                label="服务端派生 LDO 损耗",
                            )
                        )
                        lines.append(
                            f"    服务端派生：LDO 损耗 {calculation.get('loss_w')}W，"
                            f"理想效率 {calculation.get('ideal_efficiency')}。"
                        )
                thermal = candidate.get("thermal_analysis") or {}
                if thermal.get("status") == "illustrative_first_order":
                    for point in thermal.get("points") or []:
                        lines.append(
                            f"    热筛查：{point.get('package')} 的 RθJA "
                            f"{display_quantity(point.get('rtheta_ja_c_per_w'))} °C/W，"
                            f"一阶温升约 {display_quantity(point.get('first_order_rise_c'))} °C。"
                        )
                    if thermal.get("warning"):
                        lines.append(f"    热判断边界：{thermal['warning']}")
                elif thermal.get("warning"):
                    lines.append(f"    热证据：{thermal['warning']}")
                citations = candidate.get("citations") or []
                if citations:
                    citation_text = []
                    for citation in citations[:4]:
                        title = (
                            citation.get("document_title")
                            or citation.get("document_key")
                            or "工程证据"
                        )
                        revision = citation.get("document_revision") or ""
                        page = citation.get("page") or citation.get("physical_page") or "?"
                        physical_page = citation.get("physical_page")
                        page_label = f"p.{page}"
                        if physical_page and str(physical_page) != str(page):
                            page_label += f"/实体页 {physical_page}"
                        citation_text.append(f"{title} {revision} {page_label}".strip())
                    lines.append("    引用：" + "；".join(citation_text) + "。")
        peripheral_rows = draft.get("peripheral_requirements") or data.get(
            "peripheral_requirements"
        ) or []
        bom_draft = draft.get("engineering_bom_draft") or data.get(
            "engineering_bom_draft"
        ) or {}
        if peripheral_rows:
            lines.append("外围逐项结果（只读 Engineering BOM Draft）：")
            primary_id = (
                draft.get("selected_primary_material_id")
                or data.get("selected_primary_material_id")
            )
            if primary_id:
                lines.append(f"- 主芯片 material_id={primary_id}；未修改正式 Product BOM。")
            for row in peripheral_rows:
                role = row.get("role") or "外围需求"
                value = row.get("value") or row.get("constraint_value") or "待确认"
                status = str(row.get("selection_status") or "unknown")
                status_text = status_labels.get(status, status)
                required = row.get("required_quantity") or "未知"
                available = row.get("available_quantity")
                matched = row.get("matched_mpn") or row.get("matched_code")
                location_items = row.get("location") or []
                paths = "、".join(
                    str(item.get("full_path") or item.get("code"))
                    for item in location_items
                    if isinstance(item, dict)
                    and (item.get("full_path") or item.get("code"))
                ) or "暂无可确认实际库位"
                spec_parts = [str(value)]
                if row.get("rated_voltage_v"):
                    spec_parts.append(f"{row['rated_voltage_v']} V")
                if row.get("dielectric"):
                    spec_parts.append(str(row["dielectric"]))
                if row.get("package"):
                    spec_parts.append(str(row["package"]))
                lines.append(
                    f"- {role}：规格 {' / '.join(spec_parts)}；需求 {required}；"
                    f"{status_text}；匹配 {matched or '无'}；可用 "
                    f"{available if available is not None else '未知'}；库位 {paths}。"
                )
                if row.get("shortage_quantity") not in (None, "0", 0):
                    lines.append(f"  短缺数量：{row['shortage_quantity']}。")
                if row.get("material_candidate_ids"):
                    lines.append(
                        "  搜索到的候选 material_id："
                        + ", ".join(str(item) for item in row["material_candidate_ids"])
                        + "；不满足规格的相似器件未作为合格替代。"
                    )
                for candidate in row.get("candidates") or []:
                    if candidate.get("match_status") == "exact":
                        continue
                    candidate_inventory = candidate.get("inventory") or {}
                    candidate_locations = candidate.get("locations") or {}
                    candidate_paths = "、".join(
                        str(item.get("full_path") or item.get("code"))
                        for item in candidate_locations.get("locations") or []
                        if isinstance(item, dict)
                        and (item.get("full_path") or item.get("code"))
                    ) or "暂无可确认实际库位"
                    if candidate_inventory or candidate_locations:
                        lines.append(
                            "  候选 material_id="
                            f"{candidate.get('material_id')} 已查询库存/库位：可用 "
                            f"{candidate_inventory.get('available_quantity', '未知')} "
                            f"{candidate_inventory.get('unit', '')}；库位 {candidate_paths}；"
                            "该候选未满足外围规格，未作为合格匹配或替代料。"
                        )
                for unknown in row.get("unknowns") or []:
                    lines.append(f"  保持未知：{unknown}")
                source = row.get("source_anchor") or {}
                if source:
                    title = source.get("document_title") or source.get("document_key") or "工程证据"
                    revision = source.get("document_revision") or ""
                    page = source.get("page") or source.get("physical_page") or "?"
                    lines.append(f"  PDF 依据：{title} {revision} p.{page}。")
                matched_id = row.get("matched_material_id")
                if matched_id is not None:
                    facts.append(
                        GroundedFact(
                            kind="engineering_research",
                            source_tool="get_inventory_availability",
                            entity_id=matched_id,
                            field="peripheral_available_quantity",
                            value=available,
                            label=f"外围 {role} 可用库存",
                        )
                    )
                    facts.append(
                        GroundedFact(
                            kind="engineering_research",
                            source_tool="find_material_locations",
                            entity_id=matched_id,
                            field="peripheral_location_status",
                            value=row.get("location_status"),
                            label=f"外围 {role} 库位状态",
                        )
                    )
                facts.append(
                    GroundedFact(
                        kind="engineering_research",
                        source_tool="search_materials",
                        entity_id=matched_id,
                        field="peripheral_selection_status",
                        value={
                            "role": role,
                            "status": status,
                            "candidate_ids": row.get("material_candidate_ids") or [],
                        },
                        label=f"外围 {role} 规格匹配状态",
                    )
                )
                source = row.get("source_anchor") or {}
                if source:
                    facts.append(
                        GroundedFact(
                            kind="engineering_research",
                            source_tool="search_datasheet_evidence",
                            entity_id=primary_id,
                            field="peripheral_pdf_anchor",
                            value=source,
                            label=f"外围 {role} PDF 依据",
                        )
                    )
            lines.append(
                "BOM 草稿状态："
                f"{status_labels.get(bom_draft.get('status'), bom_draft.get('status', '未知'))}；"
                "只读，不自动修改正式 Product BOM、不批准替代料、不结算 Picking。"
            )
        if draft.get("manual_review"):
            lines.append("人工审核清单：")
            lines.extend(f"- {item}" for item in draft["manual_review"])
        if draft.get("unknowns"):
            lines.append("保持未知：")
            lines.extend(f"- {item}" for item in draft["unknowns"])
        lines.append("本草案不会自动修改 BOM、库存、预留或 Picking 结算。")
        sections.append("\n".join(lines))

    @staticmethod
    def _evidence_comparison(
        data: dict,
        facts: list[GroundedFact],
        sections: list[str],
    ) -> None:
        materials = data.get("materials") or []
        names = [item.get("mpn") or item.get("code") for item in materials]
        heading = " 与 ".join(str(item) for item in names if item) or "两个物料"
        lines = [f"{heading} 的当前证据比较："]
        labels = {"same": "相同", "different": "不同", "unknown": "证据不足"}
        for row in data.get("comparisons") or []:
            outcome = labels.get(str(row.get("result")), str(row.get("result")))
            field_label = str(row.get("field") or "comparison")
            row_facts = [
                *[item for item in row.get("first_facts") or [] if isinstance(item, dict)],
                *[item for item in row.get("second_facts") or [] if isinstance(item, dict)],
            ]
            if any(str(item.get("rail") or "").casefold() == "vio" for item in row_facts):
                field_label = f"{field_label} (VIO)"
            lines.append(
                f"- {field_label}：{row.get('first')} / {row.get('second')}（{outcome}）"
            )
            facts.append(
                GroundedFact(
                    kind="evidence_comparison",
                    source_tool="compare_component_evidence",
                    field=str(row.get("field") or "comparison"),
                    value={
                        "first": row.get("first"),
                        "second": row.get("second"),
                        "result": row.get("result"),
                    },
                    label="证据比较",
                )
            )
        lines.append(str(data.get("conclusion") or "当前证据不足") + "。")
        lines.append("比较不会产生兼容、替代或跨产品批准结论。")
        citations = data.get("citations") or []
        if citations:
            lines.append("引用：")
            for citation in citations:
                fixture = "合成 CI 证据" if citation.get("synthetic_fixture") else "工程证据"
                lines.append(
                    f"- {fixture} · {citation.get('document_title')} "
                    f"{citation.get('document_revision')} · p.{citation.get('page')} · "
                    f"{citation.get('section')}"
                )
        sections.append("\n".join(lines))

    @staticmethod
    def _known_limitation(message: str) -> str:
        if any(word in message for word in ("拆封", "开封", "包装状态", "开封盘")):
            return "当前 InventoryLot 未记录拆封或包装状态，不能优先选择已拆封库存。"
        if "台" in message and any(word in message.upper() for word in ("BOM", "ROBOT")):
            return (
                "当前项目 BOM 的 required_quantity 已是总需求，系统不能按台数再次相乘；"
                "以下只展示当前项目 BOM。"
            )
        return ""
