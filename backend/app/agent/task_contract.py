from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.intent_scope import route_intent_scope
from app.component_intelligence.extractor import RequirementExtractor
from app.power_design.requirements import extract_power_requirement, is_power_design_request

EntityKind = Literal[
    "material",
    "project",
    "product",
    "component",
    "cable",
    "power",
    "engineering_research",
    "global",
    "unknown",
]
RequestedFact = Literal[
    "material_identity",
    "inventory",
    "location",
    "low_stock",
    "project_bom",
    "bom_stock",
    "product_bom",
    "build_readiness",
    "component_search",
    "component_relations",
    "product_alternates",
    "relation_policy",
    "engineering_evidence",
    "component_evidence_comparison",
    "cable_search",
    "cable_detail",
    "power_design",
    "engineering_research",
    "product_bom_preview",
]
WriteIntent = Literal[
    "none",
    "reserve_inventory",
    "build_reservation",
    "unsupported_write",
]
CapabilityLimitation = Literal[
    "build_quantity_not_supported",
    "opened_package_not_supported",
]


class TaskContract(BaseModel):
    """Small deterministic execution contract, never a reasoning transcript."""

    entity_kind: EntityKind = "unknown"
    requested_facts: set[RequestedFact] = Field(default_factory=set)
    write_intent: WriteIntent = "none"
    capability_limitations: set[CapabilityLimitation] = Field(default_factory=set)
    requires_material_resolution: bool = False
    requires_project_resolution: bool = False
    requires_product_resolution: bool = False
    deterministic_material_resolution: bool = False
    build_quantity: int | None = None
    invalid_build_quantity: bool = False


_BUILD_COUNT = re.compile(
    r"(?:生产|再生产|做|再做|按|计划构建)\s*(-?\d+(?:\.\d+)?)\s*(?:台|套|个)",
    re.I,
)
_BUILD_READINESS_COUNT = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(?:台|套|个)\s*(?:库存)?"
    r"(?:够不够|够吗|是否齐套|能否齐套|能不能齐套|齐不齐|齐套吗)",
    re.I,
)
_BUILD_FOLLOWUP_COUNT = re.compile(
    r"(?:那\s*)?(-?\d+(?:\.\d+)?)\s*(?:台|套|个)(?:\s*(?:呢|够不够|够吗))?",
    re.I,
)
_BUILD_RESERVATION_COUNT = re.compile(
    r"(?:这|那|按)?\s*(-?\d+(?:\.\d+)?)\s*(?:台|套|个)"
    r"(?:[^，。？！?]{0,16})(?:预留|方案)",
    re.I,
)
_RESERVATION_STATUS = re.compile(
    r"(?:已|被)\s*预留|预留(?:了)?\s*(?:多少|数量|情况|占用)|"
    r"(?:还需(?:要)?|需要)多少[^，。？！?]{0,16}(?:新增)?预留|新增预留(?:量|数量)?|"
    r"实际还能用",
    re.I,
)
_RESERVATION_ACTION = re.compile(
    r"(?:帮我|替我|给[^，。？！?]{0,40}|为[^，。？！?]{0,40})"
    r"[^，。？！?]{0,20}预留|(?:创建|申请|执行)\s*预留|预留\s*\d+(?:\.\d+)?",
    re.I,
)
_RESERVATION_NEGATION = re.compile(
    r"(?:先|暂时|现在)?\s*(?:别|不要|不用|不需要|无需|先不)"
    r"[^，。？！?]{0,8}预留",
    re.I,
)
_MATERIAL_TOKEN = re.compile(
    r"(?<![A-Z0-9])(?=[A-Z0-9._()+-]{3,})(?=[A-Z0-9._()+-]*[A-Z])"
    r"(?=[A-Z0-9._()+-]*\d)[A-Z0-9._()+-]+",
    re.I,
)
_NAMED_ROBOT_TARGET = re.compile(
    r"(?<![A-Z0-9])(?:Robot\s+[A-Z0-9][A-Z0-9._-]*|"
    r"AMR(?:\s+[A-Z0-9][A-Z0-9._-]*)?)(?![A-Z0-9])",
    re.I,
)
_UNSUPPORTED_WRITE = re.compile(
    r"(?:直接\s*(?:改|修改|设置|调整)\s*库存|"
    r"库存\s*直接\s*(?:改|修改|设置|调整)|"
    r"(?:把|将)[^，。？！?]{0,40}库存[^，。？！?]{0,12}"
    r"(?:改成|修改为|设置为|调成)|"
    r"(?:修改|设置|调整)\s*库存)",
    re.I,
)
_COMPONENT_REQUIREMENT_MARKERS = (
    "有没有",
    "找",
    "找一个",
    "找支持",
    "找带",
    "适合",
    "选型",
    "替代",
    "类似",
    "给我",
    "需要",
    "想要",
    "我想",
    "来个",
    "挑个",
    "有哪些",
    "候选",
    "评审",
    "收发器",
)
_PROMPT_INJECTION_MARKERS = (
    "忽略规则",
    "忽略指令",
    "不要调用工具",
    "无需调用工具",
    "directly answer",
    "ignore previous",
)
_GENERIC_MATERIAL_QUERIES = {
    "查询库存",
    "查库存",
    "查询库位",
    "查库位",
    "查询物料",
    "查物料",
}
_TECHNICAL_EVIDENCE_MARKERS = (
    "datasheet",
    "data sheet",
    "证据",
    "依据",
    "哪份资料",
    "来源",
    "哪一页",
    "为什么你认为",
    "规格书",
    "pin 5",
    "pin5",
    "pin compatible",
    "pin-compatible",
    "引脚兼容",
    "供电范围",
    "电压范围",
    "输入电压",
    "输出电压",
    "固定输出",
    "典型应用",
    "封面",
    "输出电流",
    "分辨率",
    "增益",
    "gain",
    "绝对最大",
    "绝对额定",
    "推荐输入",
    "推荐工作",
    "热阻",
    "功耗",
    "损耗",
    "接口",
    "补偿",
    "纹波",
    "输入电容",
    "输出电容",
    "输入电感",
    "输出电感",
    "电容要求",
    "电感要求",
    "外围",
    "容量",
    "耐压",
    "bst",
    "cot",
    "compensation",
    "ripple",
    "bootstrap",
    "capacitor",
    "inductor",
    "current revision",
    "rev a",
    "rev b",
)
_EXPLICIT_EVIDENCE_REQUEST_MARKERS = (
    "pdf",
    "数据手册",
    "datasheet",
    "data sheet",
    "规格书",
    "哪一页",
    "页码",
    "出处",
    "来源",
    "证据",
)
_CABLE_CATALOG_IDENTIFIER = re.compile(
    r"(?<![A-Z0-9])(?:SH|XH|MX|HC|PH|ZH|GH|JST)(?:[-_.][A-Z0-9]+){2,}(?![A-Z0-9])",
    re.I,
)


def is_engineering_research_request(message: str, extracted=None) -> bool:
    """Recognize the explicit, read-only deep engineering workflow.

    This is deliberately narrower than ``is_power_design_request``.  The
    existing power-design route remains the right answer for a normal typed
    topology plan; this route is only for a request that explicitly asks the
    system to compare topologies, inspect warehouse truth and produce a draft
    that still needs human engineering review.
    """

    folded = message.casefold()
    peripheral_only_bom_request = (
        ("lm5164" in folded and "buck" in folded)
        and any(
            marker in folded
            for marker in (
                "engineering bom",
                "bom roles",
                "外围",
                "peripheral",
            )
        )
        and any(
            marker in folded
            for marker in ("inventory", "location", "datasheet", "evidence", "库存", "库位", "证据")
        )
    )
    if peripheral_only_bom_request:
        return True

    # Production-language bootstrap matching is an engineering-research turn,
    # even when it describes one existing Buck topology instead of comparing
    # two topologies. Keep the scope narrow so a pure "what does BST require?"
    # evidence question remains on the evidence route, while a request to
    # match real material plus inventory/location receives the typed draft and
    # selection context required by P3341.
    bootstrap_material_match_request = (
        "lm5164" in folded
        and "buck" in folded
        and any(
            marker in folded for marker in ("自举电容", "bootstrap capacitor", "bootstrap", "bst")
        )
        and any(
            marker in folded
            for marker in ("现有物料", "真实物料", "匹配", "找能用", "候选", "满足", "能用")
        )
        and any(marker in folded for marker in ("库存", "库位", "inventory", "location"))
    )
    if bootstrap_material_match_request:
        return True

    # A standalone comparison request may refer to the already-defined
    # bootstrap requirement without repeating the LM5164 part number. Keep it
    # on the typed engineering route when it explicitly asks for grounded
    # candidate comparison and defers selection; this must not broaden ordinary
    # datasheet/evidence questions into a power-design workflow.
    bootstrap_comparison_only_request = (
        any(marker in folded for marker in ("自举", "bootstrap", "bst"))
        and any(marker in folded for marker in ("比较", "对比", "候选", "多颗"))
        and any(
            marker in folded
            for marker in (
                "规格",
                "库存",
                "库位",
                "证据",
                "inventory",
                "location",
                "evidence",
            )
        )
        and any(
            marker in folded
            for marker in ("不要替我自动选", "不要替我自动定", "先比较", "不急着定")
        )
    )
    if bootstrap_comparison_only_request:
        return True

    power_requirement = extract_power_requirement(message)
    explicit_power_shape = (
        power_requirement.input_voltage_v is not None
        and power_requirement.output_voltage_v is not None
        and power_requirement.input_voltage_v != power_requirement.output_voltage_v
        and power_requirement.has_load_current
        and len(power_requirement.topology_constraints) >= 2
    )
    if not is_power_design_request(message, extracted) and not explicit_power_shape:
        return False
    compares_topologies = (
        ("buck" in folded and "ldo" in folded)
        or ("buck" in folded and "线性" in message)
        or ("开关" in message and "线性" in message)
    ) and any(
        marker in folded
        for marker in ("比较", "对比", "方案", "设计", "都查", "分别", "两种", "各自")
    )
    asks_for_grounding = any(
        marker in folded
        for marker in (
            "数据手册",
            "datasheet",
            "证据",
            "依据",
            "外围",
            "库位",
            "人工审核",
            "人工复核",
            "库存",
            "工程bom",
            "工程 bom",
        )
    )
    asks_for_engineering_deliverable = any(
        marker in folded
        for marker in (
            "各级损耗",
            "损耗",
            "工程bom",
            "工程 bom",
            "bom完整度",
            "bom 完整度",
            "完整度",
            "完整性",
            "工程草案",
            "草案",
            "选型",
            "候选",
        )
    )
    selected_multistage_topology = explicit_power_shape and (
        power_requirement.topology_choice == "buck_ldo"
        or {"buck", "ldo"}.issubset(set(power_requirement.topology_constraints))
    )
    if selected_multistage_topology and asks_for_engineering_deliverable and asks_for_grounding:
        return True
    return compares_topologies and asks_for_grounding


def is_product_bom_preview_request(message: str) -> bool:
    """Recognize the explicit read-only Engineering Draft -> Product BOM preview."""

    folded = message.casefold()
    preview_marker = any(
        marker in folded
        for marker in (
            "product bom preview",
            "bom preview",
            "bom预览",
            "预览正式bom",
            "预览产品bom",
            "预览产品 bom",
        )
    )
    engineering_draft_preview = (
        any(marker in folded for marker in ("工程草案", "engineering draft"))
        and any(marker in folded for marker in ("预览", "preview", "对比"))
        and any(marker in folded for marker in ("产品", "product", "bom"))
    )
    return preview_marker or engineering_draft_preview


def classify_task_contract(
    message: str,
    *,
    selected_material: bool = False,
    selected_project: bool = False,
    selected_product: bool = False,
    previous_build_quantity: int | None = None,
) -> TaskContract:
    """Classify only high-confidence Warehouse intents with deterministic rules."""

    folded = message.casefold()
    if _UNSUPPORTED_WRITE.search(message):
        return TaskContract(entity_kind="material", write_intent="unsupported_write")
    if is_product_bom_preview_request(message):
        return TaskContract(
            entity_kind="product",
            requested_facts={"product_bom_preview"},
            requires_product_resolution=not selected_product,
        )
    scope_decision = route_intent_scope(message)
    facts: set[RequestedFact] = set()
    limitations: set[CapabilityLimitation] = set()
    explicit_material = bool(_MATERIAL_TOKEN.search(message))
    deterministic_material_resolution = (
        message.strip() not in _GENERIC_MATERIAL_QUERIES
        and not any(marker in folded for marker in _PROMPT_INJECTION_MARKERS)
        and (explicit_material or len(message.strip()) >= 6)
    )

    reservation_status = bool(_RESERVATION_STATUS.search(message))
    reservation_action = bool(_RESERVATION_ACTION.search(message))
    reservation_negated = bool(_RESERVATION_NEGATION.search(message))
    reserve_intent = (
        "预留" in message
        and not reservation_negated
        and (reservation_action or not reservation_status)
    )
    write_intent: WriteIntent = "reserve_inventory" if reserve_intent else "none"

    build_match = _BUILD_COUNT.search(message) or _BUILD_READINESS_COUNT.search(message)
    if build_match is None and (selected_product or "预留" in message or "方案" in message):
        build_match = _BUILD_RESERVATION_COUNT.search(message) or (
            _BUILD_FOLLOWUP_COUNT.fullmatch(message.casefold().strip(" ？?。！!"))
            if selected_product
            else None
        )
    build_count = build_match is not None
    build_quantity: int | None = None
    invalid_build_quantity = False
    if build_match:
        try:
            parsed_quantity = Decimal(build_match.group(1))
            if parsed_quantity == parsed_quantity.to_integral_value():
                build_quantity = int(parsed_quantity)
            else:
                invalid_build_quantity = True
        except (InvalidOperation, ValueError):
            invalid_build_quantity = True
    build_plan_phrase = any(
        marker in folded
        for marker in ("预留方案", "生成方案", "只看方案", "把这些料预留", "把料预留")
    )
    if (
        build_quantity is None
        and selected_product
        and (
            reserve_intent
            or build_plan_phrase
            or any(
                marker in folded for marker in ("够不够", "够吗", "齐套吗", "是否够料", "先别预留")
            )
        )
        and previous_build_quantity is not None
    ):
        build_quantity = previous_build_quantity
        build_count = True
    opened_package = any(marker in message for marker in ("开封", "拆封", "开封盘"))
    if opened_package:
        limitations.add("opened_package_not_supported")

    location_requested = any(
        marker in folded
        for marker in (
            "在哪里",
            "在哪",
            "放哪",
            "位置",
            "库位",
            "哪个抽屉",
            "哪个柜子",
            "去哪拿",
            "去找",
            "是不是在",
        )
    ) or bool(re.search(r"在\s*[A-Z][A-Z0-9-]*\d", message, re.I))
    explicit_inventory_requested = reservation_status or any(
        marker in folded
        for marker in (
            "库存",
            "还有",
            "可用",
            "还能用",
            "数量",
            "剩",
            "拿几个",
            "帮我查",
            "get_inventory_availability",
        )
    )
    inventory_requested = (
        explicit_inventory_requested
        or "多少" in folded
        or bool(re.search(r"\d+(?:\.\d+)?\s*个", folded))
    )
    low_stock_requested = any(
        marker in folded
        for marker in ("低库存", "低于安全库存", "安全库存以下", "快没了", "库存预警", "补货")
    )
    global_low_stock_requested = low_stock_requested and any(
        marker in folded for marker in ("哪些", "所有", "全局", "列表", "有哪些")
    )
    stock_or_shortage = any(
        marker in folded
        for marker in (
            "缺",
            "库存够",
            "库存不足",
            "够不够",
            "够吗",
            "备料情况",
            "齐套",
            "卡住",
            "卡装配",
            "阻塞",
        )
    )
    product_contents_requested = any(
        container in folded for container in ("里面", "内部", "包含")
    ) and any(
        marker in folded for marker in ("用的是什么", "用了什么", "有哪些", "包含什么", "装的什么")
    )
    bom_requested = (
        "bom" in folded or "物料清单" in folded or build_count or product_contents_requested
    )

    natural_cable_pitch = bool(
        re.search(
            r"(?:间距|pitch)\s*(?:为|是|=|:|：)?\s*\d+(?:\.\d+)?\s*(?:mm)?",
            folded,
            re.I,
        )
        or re.search(
            r"(?<!\d)\d+(?:\.\d+)?\s*(?:mm\b|毫米|间距)",
            folded,
            re.I,
        )
    )
    natural_cable_pin = bool(re.search(r"(?<!\d)\d+\s*(?:pin|p)(?![A-Za-z0-9])", folded, re.I))
    natural_cable_context = any(
        marker in folded for marker in ("端子", "线", "线束", "排线", "cable")
    ) or bool(
        re.search(
            r"\d+(?:\.\d+)?\s*(?:到|至|[-~～])\s*"
            r"\d+(?:\.\d+)?\s*(?:cm\b|厘米|公分)",
            folded,
            re.I,
        )
    )
    natural_cable_constraint = natural_cable_pitch and natural_cable_pin and natural_cable_context
    cable_requested = (
        scope_decision.intent == "cable_search"
        or bool(_CABLE_CATALOG_IDENTIFIER.search(message))
        or any(
            marker in folded
            for marker in (
                "线缆",
                "端子线",
                "排线",
                "ffc",
                "fpc",
                "ipex",
                "同轴线",
                "pin 转",
                "pin转",
                "这条线",
                "第二条线",
                "cable",
                "portfolio-cbl-",
                "cbl-pf-",
                "storage_location",
                "订单里买了",
                "采购单价",
                "新对话：它在哪",
            )
        )
        and scope_decision.intent != "product_bom_alternate"
        or (
            bool(re.search(r"的线(?!性)", message))
            and scope_decision.intent != "product_bom_alternate"
        )
        or natural_cable_constraint
        or bool(
            re.search(r"\d+(?:\.\d+)?\s*mm.*?\d+\s*(?:pin|p)\b", folded, re.I)
            or re.search(r"\b(?:sh|xh|mx|ipex|rf1\.13)\s*\d", folded, re.I)
        )
        and scope_decision.intent != "product_bom_alternate"
    )
    if cable_requested and not bom_requested and not build_count:
        cable_policy_only = any(
            marker in folded
            for marker in (
                "订单里买了",
                "采购数量",
                "采购单价",
                "storage_location",
                "方向含义",
            )
        )
        facts = {"cable_search"}
        if location_requested and not cable_policy_only:
            facts.add("location")
        if inventory_requested and not cable_policy_only:
            facts.add("inventory")
        return TaskContract(
            entity_kind="cable",
            requested_facts=facts,
        )

    component_query = RequirementExtractor().extract(message)
    if is_engineering_research_request(message, component_query):
        return TaskContract(
            entity_kind="engineering_research",
            requested_facts={"engineering_research"},
        )
    if is_power_design_request(message, component_query):
        if any(marker in folded for marker in _EXPLICIT_EVIDENCE_REQUEST_MARKERS):
            return TaskContract(
                entity_kind="material",
                requested_facts={"power_design", "engineering_evidence"},
                requires_material_resolution=not selected_material,
                deterministic_material_resolution=deterministic_material_resolution,
            )
        return TaskContract(
            entity_kind="power",
            requested_facts={"power_design"},
        )

    relation_policy_requested = any(
        marker in folded
        for marker in (
            "similar_to 就",
            "候选关系和已验证关系",
            "候选器件关系是不是",
            "产品批准备选和全局器件关系",
            "产品备选 candidate",
            "productbomalternate 会自动",
            "productbomalternate会自动",
            "直接算进库存",
        )
    )
    if relation_policy_requested:
        return TaskContract(
            entity_kind="global",
            requested_facts={"relation_policy"},
        )

    if (
        selected_material
        and location_requested
        and any(marker in folded for marker in ("备选", "它", "这个", "那个"))
    ):
        return TaskContract(
            entity_kind="material",
            requested_facts={"location"},
        )

    alternate_requested = scope_decision.mentions_alternate or any(
        marker in folded
        for marker in (
            "备选",
            "批准备选",
            "自动换",
            "换成批",
            "能替",
            "替它",
        )
    )
    relation_requested = scope_decision.mentions_relation or any(
        marker in folded
        for marker in (
            "验证关系",
            "已验证关系",
            "相似器件",
            "相似关系",
            "替代关系",
            "pin compatible",
            "pincompatible",
            "引脚兼容",
            "能互换",
            "直接替代",
            "所有产品都能替代",
            "validated relation",
            "revoked relation",
            "撤销的关系",
            "已撤销的关系",
        )
    )
    technical_evidence_markers = _TECHNICAL_EVIDENCE_MARKERS
    material_tokens = [
        token
        for token in _MATERIAL_TOKEN.finditer(message)
        if not re.fullmatch(
            r"\d+(?:\.\d+)?(?:m?v|m?a|w|mm)?",
            token.group(0),
            re.IGNORECASE,
        )
    ]
    # Voltage values are ordinary business search constraints. They only become
    # engineering-evidence requests when the user explicitly asks for a source,
    # datasheet, revision, page, pin or other evidence concept.
    evidence_requested = any(marker in folded for marker in technical_evidence_markers)
    # Natural Chinese pin references such as "5 脚", "第5脚" and "5号脚"
    # are engineering-evidence intents even when the word "引脚" is omitted.
    evidence_requested = evidence_requested or bool(
        re.search(r"(?:第\s*)?\d{1,3}\s*(?:号)?脚", message, re.IGNORECASE)
    )
    if len(material_tokens) == 1 and any(
        marker in folded for marker in ("能跑", "是否支持", "支持吗", "支不支持")
    ):
        evidence_requested = True
    # "接口" is also ordinary component-search language (for example,
    # "想要数字式电流监测器，接口用 I2C").  Keep that candidate request on
    # the component route unless another evidence-specific marker is present;
    # exact-part questions still reach evidence because they do not carry a
    # component requirement marker.
    interface_only_component_request = (
        "接口" in folded
        and any(marker in folded for marker in _COMPONENT_REQUIREMENT_MARKERS)
        and bool(component_query.component_types or component_query.interfaces)
        and not any(marker in folded for marker in _TECHNICAL_EVIDENCE_MARKERS if marker != "接口")
    )
    if interface_only_component_request:
        evidence_requested = False
    if alternate_requested and any(marker in folded for marker in ("为什么", "依据", "证据")):
        evidence_requested = True
    if len(material_tokens) >= 2 and any(
        marker in folded for marker in ("比较", "compare", "一样", "差异", "兼容", "支持")
    ):
        evidence_requested = True
    # Two identifiers alone do not make a comparison request.  A negative
    # variant-bound question such as "MCP2562FD ... 不要把 MCP2561FD 的资料
    # 混用" must stay on the single-material evidence route; otherwise the
    # deterministic graph waits for two candidates and may fall through to an
    # unavailable narrative-model call.
    evidence_comparison_requested = (
        evidence_requested
        and len(material_tokens) >= 2
        and any(
            marker in folded
            for marker in (
                "比较",
                "对比",
                "compare",
                "一样",
                "差异",
                "兼容",
                "支持",
                "vs",
                "versus",
            )
        )
    )
    if relation_requested and (
        not alternate_requested or scope_decision.intent == "component_relation"
    ):
        requested = {"component_relations"}
        if explicit_inventory_requested:
            requested.add("inventory")
        if evidence_requested:
            requested.add(
                "engineering_evidence"
                if any(marker in folded for marker in ("批准", "approve", "review draft"))
                else "component_evidence_comparison"
            )
        return TaskContract(
            entity_kind="material",
            requested_facts=requested,
            requires_material_resolution=not selected_material,
            deterministic_material_resolution=deterministic_material_resolution,
        )

    if evidence_requested and not alternate_requested:
        requested_evidence = {
            "component_evidence_comparison"
            if evidence_comparison_requested
            else "engineering_evidence"
        }
        if explicit_inventory_requested:
            requested_evidence.add("inventory")
        if location_requested:
            requested_evidence.add("location")
        return TaskContract(
            entity_kind="material",
            requested_facts=requested_evidence,
            requires_material_resolution=not selected_material,
            deterministic_material_resolution=deterministic_material_resolution,
        )

    if (
        alternate_requested
        and not scope_decision.mentions_product_scope
        and scope_decision.intent != "product_bom_alternate"
        and not selected_product
        and (selected_material or explicit_material)
    ):
        # A material-scoped “can it replace this?” question can only inspect
        # audited global engineering relations. Product approval requires an
        # explicit ProductRevision/BOM-position scope and is routed separately.
        return TaskContract(
            entity_kind="material",
            requested_facts={"component_relations"},
            requires_material_resolution=not selected_material,
            deterministic_material_resolution=deterministic_material_resolution,
        )

    requirement_shaped = component_query.replacement_intent or (
        bool(component_query.component_types or component_query.interfaces)
        and (
            any(marker in folded for marker in _COMPONENT_REQUIREMENT_MARKERS)
            or any(marker in folded for marker in ("支持", "能跑", "谁能", "谁支持"))
        )
    )
    product_context_signal = (
        product_contents_requested
        or any(marker in folded for marker in ("产品", "单台", "bom", "evt-", "dvt-", "pvt-"))
        or bool(
            re.search(
                r"(?<![A-Z0-9])(?:PROD-[A-Z0-9-]+|(?:EVT|DVT|PVT)-R?\d+)",
                message,
                re.I,
            )
        )
    )
    if requirement_shaped and not alternate_requested and not product_context_signal:
        return TaskContract(
            entity_kind="component",
            requested_facts={"component_search"},
        )

    explicit_project = any(
        marker in folded for marker in ("项目", "project", "prj-", "bom")
    ) or bool(re.search(r"(?<![A-Z0-9])(?:RB|PRJ)-[A-Z0-9-]+", message, re.I))
    explicit_product = any(
        marker in folded for marker in ("产品", "单台", "product", "prod-")
    ) or bool(
        re.search(
            r"(?<![A-Z0-9])(?:PROD-[A-Z0-9-]+|(?:EVT|DVT|PVT)-R?\d+)",
            message,
            re.I,
        )
    )
    product_followup = selected_product and any(
        marker in folded for marker in ("版本", "revision", "evt-", "dvt-", "bom", "够不够", "够吗")
    )
    named_robot_target = bool(_NAMED_ROBOT_TARGET.search(message))
    product_build_readiness = scope_decision.intent == "product_build_readiness"
    build_reservation_intent = (
        (reserve_intent or build_plan_phrase)
        and build_quantity is not None
        and (selected_product or explicit_product or build_count)
    )
    if build_reservation_intent:
        write_intent = "build_reservation"
    dual_domain_named_target = named_robot_target and (
        product_build_readiness
        or build_reservation_intent
        or (
            not explicit_project
            and (build_count or stock_or_shortage or bom_requested or opened_package)
        )
    )
    product_query = (
        explicit_product
        or product_followup
        or dual_domain_named_target
        or alternate_requested
        or product_build_readiness
        or (build_count and not explicit_project)
        or build_reservation_intent
    )
    if product_query:
        if dual_domain_named_target and not build_count and not explicit_product:
            facts.add("bom_stock" if stock_or_shortage else "project_bom")
        elif build_count or stock_or_shortage or product_build_readiness:
            facts.add("build_readiness")
            if alternate_requested:
                facts.add("product_alternates")
        elif alternate_requested:
            facts.update({"product_bom", "product_alternates"})
        elif bom_requested:
            facts.add("product_bom")
        if evidence_requested:
            facts.add("engineering_evidence")
        return TaskContract(
            entity_kind="product",
            requested_facts=facts,
            write_intent=write_intent,
            capability_limitations=limitations,
            requires_product_resolution=True,
            requires_project_resolution=(build_reservation_intent and not selected_project),
            build_quantity=build_quantity,
            invalid_build_quantity=invalid_build_quantity,
        )
    project_pick_location_followup = selected_project and any(
        marker in folded
        for marker in (
            "带我去找",
            "带我去拿",
            "带我找",
            "去哪拿",
            "从哪拿",
            "去找",
            "开始找料",
            "开始拿料",
        )
    )
    project_followup = selected_project and any(
        marker in folded
        for marker in (
            "缺什么料",
            "缺的料",
            "缺料",
            "库存够",
            "够不够",
            "够吗",
            "bom",
            "卡住",
            "卡装配",
            "阻塞",
        )
    )
    project_followup = project_followup or project_pick_location_followup
    project_context = explicit_project or project_followup

    if (
        low_stock_requested
        and selected_material
        and not global_low_stock_requested
        and not project_context
    ):
        facts.add("inventory")
        return TaskContract(
            entity_kind="material",
            requested_facts=facts,
            requires_material_resolution=False,
        )

    if low_stock_requested and not project_context:
        facts.add("low_stock")
        return TaskContract(entity_kind="global", requested_facts=facts)

    if project_context:
        if build_count:
            limitations.add("build_quantity_not_supported")
        if stock_or_shortage or project_pick_location_followup:
            # A follow-up such as “带我去找” after a Project BOM result is
            # still a deterministic BOM/location task. Re-run the read-only
            # BOM stock analysis (which includes physical InventoryLot
            # locations) instead of falling through to the model.
            facts.add("bom_stock")
        elif bom_requested or opened_package:
            facts.add("project_bom")
        if reserve_intent and not facts:
            facts.add("project_bom")
        return TaskContract(
            entity_kind="project",
            requested_facts=facts,
            write_intent=write_intent,
            capability_limitations=limitations,
            requires_project_resolution=True,
            requires_material_resolution=(
                write_intent == "reserve_inventory"
                and build_quantity is None
                and bool(_MATERIAL_TOKEN.search(message))
            ),
            build_quantity=build_quantity,
            invalid_build_quantity=invalid_build_quantity,
        )

    if location_requested:
        facts.add("location")
    if inventory_requested:
        facts.add("inventory")
    if opened_package:
        facts.update({"inventory", "location"})
    if facts or reserve_intent:
        facts.add("material_identity")
        return TaskContract(
            entity_kind="material",
            requested_facts=facts,
            write_intent=write_intent,
            capability_limitations=limitations,
            requires_material_resolution=True,
            requires_project_resolution=reserve_intent,
            deterministic_material_resolution=deterministic_material_resolution,
        )

    if _MATERIAL_TOKEN.search(message):
        return TaskContract(
            entity_kind="material",
            requested_facts={"material_identity"},
            requires_material_resolution=True,
            deterministic_material_resolution=deterministic_material_resolution,
        )

    if selected_material and any(marker in folded for marker in ("它", "这个", "那个")):
        return TaskContract(
            entity_kind="material",
            requested_facts={"material_identity"},
            requires_material_resolution=False,
        )

    return TaskContract(
        entity_kind="unknown",
        requested_facts=facts,
        write_intent=write_intent,
        capability_limitations=limitations,
    )
