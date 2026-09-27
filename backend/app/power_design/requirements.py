from __future__ import annotations

import re
from decimal import Decimal

from pydantic import BaseModel, Field

from app.component_intelligence.extractor import RequirementExtractor


class PowerRequirement(BaseModel):
    input_voltage_v: Decimal | None = None
    output_voltage_v: Decimal | None = None
    load_current_a: Decimal | None = None
    load_current_min_a: Decimal | None = None
    load_current_max_a: Decimal | None = None
    load_current_range_a: tuple[Decimal, Decimal] | None = None
    load_current_cases_a: list[Decimal] = Field(default_factory=list)
    analog_load_current_a: Decimal | None = None
    digital_load_current_a: Decimal | None = None
    topology_choice: str | None = None
    intermediate_voltage_v: Decimal | None = None
    direct_ldo_comparison_input_voltage_v: Decimal | None = None
    efficiency_preference: str | None = None
    noise_preference: str | None = None
    size_preference: str | None = None
    thermal_preference: str | None = None
    topology_constraints: list[str] = Field(default_factory=list)
    location_requested: bool = False
    raw_text: str

    @property
    def has_load_current(self) -> bool:
        return any(
            value is not None
            for value in (
                self.load_current_a,
                self.load_current_min_a,
                self.load_current_max_a,
                self.load_current_range_a,
            )
        )

    @property
    def effective_load_current_a(self) -> Decimal | None:
        if self.load_current_max_a is not None:
            return self.load_current_max_a
        if self.load_current_a is not None:
            return self.load_current_a
        if self.load_current_range_a is not None:
            return self.load_current_range_a[1]
        return self.load_current_min_a


_LOAD_RANGE = re.compile(
    r"(?:负载|电流)[^，。？！?]{0,18}?"
    r"(?P<minimum>\d+(?:\.\d+)?)\s*(?P<min_unit>mA|A)\s*"
    r"(?:-|~|～|至|到)\s*"
    r"(?P<maximum>\d+(?:\.\d+)?)\s*(?P<max_unit>mA|A)",
    re.IGNORECASE,
)
_LOAD_LIMIT = re.compile(
    r"(?:负载|电流)[^，。？！?]{0,18}?"
    r"(?:可能到|最多到|不超过|至多|最高到|到|至)\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mA|A)",
    re.IGNORECASE,
)
_LOAD_APPROX = re.compile(
    r"(?:负载|电流)[^，。？！?]{0,18}?"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mA|A)\s*(?:左右|大约|大概|约)?",
    re.IGNORECASE,
)
_TOTAL_LOAD = re.compile(
    r"(?:系统|整体|总计|合计)?\s*(?:总负载|总电流|负载总计|系统负载)"
    r"[^，。；;]{0,10}?(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mA|A)",
    re.IGNORECASE,
)
_RAIL_CURRENT = re.compile(
    r"(?P<rail>模拟|数字|analog|digital)[^，。；;]{0,18}?"
    r"(?:支路|电源|电流|负载|rail)?[^，。；;]{0,12}?"
    r"(?:按|约|大约|大概|负载|电流)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mA|A)",
    re.IGNORECASE,
)
_CURRENT_VALUE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mA|A)(?![A-Za-z])", re.I)
_STAGED_POWER_CHAIN = re.compile(
    r"(?P<input>\d+(?:\.\d+)?)\s*V\s*(?:→|->)\s*"
    r"(?P<intermediate>\d+(?:\.\d+)?)\s*V\s*(?:buck|降压)\s*"
    r"(?:→|->|后接)\s*(?P<output>\d+(?:\.\d+)?)\s*V\s*(?:ldo|线性稳压)",
    re.IGNORECASE,
)
_STAGED_POWER_CHAIN_CN = re.compile(
    r"(?P<input>\d+(?:\.\d+)?)\s*V\s*输入[^。！？!?；;]{0,24}?"
    r"(?:先\s*)?(?:buck|降压)\s*(?:到|至|输出为)\s*"
    r"(?P<intermediate>\d+(?:\.\d+)?)\s*V[^。！？!?；;]{0,16}?"
    r"(?:再|然后|后接|接着)\s*(?:ldo|线性稳压)\s*(?:到|至|输出为)\s*"
    r"(?P<output>\d+(?:\.\d+)?)\s*V",
    re.IGNORECASE,
)
_INTERMEDIATE_VOLTAGE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*V\s*(?:中间(?:电压|轨)|intermediate(?:\s+rail)?)"
    r"|(?:二级\s*)?(?:后级\s*)?LDO\s*(?:输入|入口)\s*(?:改为|改成|设为|为|=|是)?\s*"
    r"(?P<ldo_value>\d+(?:\.\d+)?)\s*V",
    re.IGNORECASE,
)
_DIRECT_LDO_COMPARISON = re.compile(
    r"(?:和|与|对比|相比|比较)[^，。；;？！?]{0,12}?"
    r"(?P<value>\d+(?:\.\d+)?)\s*V\s*直接\s*(?:LDO|线性稳压)",
    re.IGNORECASE,
)
_DIRECT_LDO_REQUEST = re.compile(
    r"直接\s*(?:用|走|接)?\s*(?:LDO|线性稳压)",
    re.IGNORECASE,
)


def _amps(value: str, unit: str) -> Decimal:
    amount = Decimal(value)
    return amount / Decimal("1000") if unit.casefold() == "ma" else amount


def _independent_current_cases(text: str) -> list[Decimal]:
    historical_markers = (
        "历史",
        "上一轮",
        "之前",
        "此前",
        "旧值",
        "示例",
        "压力测试",
        "压测",
        "不要沿用",
        "不要带入",
        "不继承",
    )
    comparison_between = re.compile(
        r"(?:和|与|对比|相比|/|\bvs\.?\b|\bversus\b|\bor\b|\band\b)",
        re.IGNORECASE,
    )
    values: list[Decimal] = []
    # An unrelated “和” or “比较” elsewhere in a long engineering prompt must
    # not turn historical examples or separate rail currents into load cases.
    for clause in re.split(r"[。！？!?；;]", text):
        matches = list(_CURRENT_VALUE.finditer(clause))
        if len(matches) < 2:
            continue
        current_matches = []
        for match in matches:
            prefix = clause[max(0, match.start() - 24) : match.start()].casefold()
            if any(marker in prefix for marker in historical_markers):
                continue
            current_matches.append(match)
        if len(current_matches) < 2:
            continue
        gaps = [
            clause[left.end() : right.start()]
            for left, right in zip(current_matches, current_matches[1:], strict=False)
        ]
        if any(re.search(r"(?:-|~|～|至|到)\s*$", gap) for gap in gaps):
            continue
        explicit_comparison = any(comparison_between.search(gap) for gap in gaps)
        explicit_each = any(marker in clause for marker in ("分别", "各自"))
        if not explicit_comparison and not explicit_each:
            continue
        values.extend(_amps(match.group("value"), match.group("unit")) for match in current_matches)
    return list(dict.fromkeys(values))


def _load_current(text: str, extracted) -> dict[str, object]:
    cases = _independent_current_cases(text)
    if cases:
        return {"load_current_a": cases[0], "load_current_cases_a": cases}
    range_match = _LOAD_RANGE.search(text)
    if range_match:
        minimum = _amps(range_match.group("minimum"), range_match.group("min_unit"))
        maximum = _amps(range_match.group("maximum"), range_match.group("max_unit"))
        return {
            "load_current_min_a": minimum,
            "load_current_max_a": maximum,
            "load_current_range_a": (minimum, maximum),
        }
    limit_match = _LOAD_LIMIT.search(text)
    if limit_match:
        maximum = _amps(limit_match.group("value"), limit_match.group("unit"))
        return {"load_current_max_a": maximum, "load_current_a": maximum}
    total_match = _TOTAL_LOAD.search(text)
    if total_match:
        current = _amps(total_match.group("value"), total_match.group("unit"))
        return {"load_current_a": current}
    approximate = _LOAD_APPROX.search(text)
    if approximate:
        current = _amps(approximate.group("value"), approximate.group("unit"))
        return {"load_current_a": current}
    if extracted.current_range_a is not None:
        return {
            "load_current_min_a": extracted.current_range_a.minimum,
            "load_current_max_a": extracted.current_range_a.maximum,
            "load_current_range_a": (
                extracted.current_range_a.minimum,
                extracted.current_range_a.maximum,
            ),
        }
    if extracted.current_max_a is not None:
        return {"load_current_max_a": extracted.current_max_a}
    if extracted.current_a is not None and not _RAIL_CURRENT.search(text):
        return {"load_current_a": extracted.current_a}
    return {}


def _rail_currents(text: str) -> dict[str, Decimal | None]:
    currents: dict[str, Decimal | None] = {
        "analog_load_current_a": None,
        "digital_load_current_a": None,
    }
    for match in _RAIL_CURRENT.finditer(text):
        rail = match.group("rail").casefold()
        key = "analog_load_current_a" if rail in {"模拟", "analog"} else "digital_load_current_a"
        currents[key] = _amps(match.group("value"), match.group("unit"))
    return currents


def _topology_choice(text: str) -> str | None:
    folded = text.casefold()
    if any(
        marker in folded
        for marker in (
            "分轨",
            "分开供电",
            "独立模拟",
            "模拟支路",
            "模拟电源比数字",
            "数字/模拟",
            "split rail",
            "split-rail",
            "analog rail",
        )
    ):
        return "split_rails"
    if any(
        marker in folded
        for marker in (
            "后级 ldo",
            "后级ldo",
            "二级 ldo",
            "二级ldo",
            "后级线性稳压",
        )
    ) or (
        any(marker in folded for marker in ("两级", "二级"))
        and any(marker in folded for marker in ("ldo", "线性稳压"))
    ):
        return "buck_ldo"
    if re.search(r"buck\s*(?:\+|＋|→|->|后接|接)\s*ldo", folded):
        return "buck_ldo"
    if "buck+ldo" in folded or "buck ＋ ldo" in folded or "buck 后接 ldo" in folded:
        return "buck_ldo"
    if any(marker in folded for marker in ("直接 buck", "buck 直出", "direct buck")):
        return "direct_buck"
    return None


def extract_power_requirement(text: str) -> PowerRequirement:
    raw = text.strip()
    extracted = RequirementExtractor().extract(raw)
    folded = raw.casefold()
    current = _load_current(raw, extracted)
    rail_currents = _rail_currents(raw)
    staged_chain = _STAGED_POWER_CHAIN.search(raw) or _STAGED_POWER_CHAIN_CN.search(raw)
    intermediate = _INTERMEDIATE_VOLTAGE.search(raw)
    direct_ldo_comparison = _DIRECT_LDO_COMPARISON.search(raw)
    direct_comparison_voltage = (
        Decimal(direct_ldo_comparison.group("value")) if direct_ldo_comparison else None
    )
    if direct_comparison_voltage is None and _DIRECT_LDO_REQUEST.search(raw):
        direct_comparison_voltage = extracted.input_voltage_v
    intermediate_voltage = (
        Decimal(staged_chain.group("intermediate"))
        if staged_chain
        else (
            Decimal(intermediate.group("value") or intermediate.group("ldo_value"))
            if intermediate
            else None
        )
    )
    if (
        direct_comparison_voltage is not None
        and extracted.input_voltage_v is not None
        and extracted.output_voltage_v is not None
        and extracted.input_voltage_v != direct_comparison_voltage
        and intermediate_voltage is None
    ):
        # In a comparison such as "5V→3.3V post-LDO vs 12V direct LDO",
        # the first voltage pair describes the post-LDO stage. Keep 12V as
        # system input and retain 5V as the explicitly stated intermediate rail.
        intermediate_voltage = extracted.input_voltage_v
    topology_constraints = []
    if any(marker in folded for marker in ("buck", "降压", "step-down", "dc/dc", "dcdc")):
        topology_constraints.append("buck")
    if any(marker in folded for marker in ("ldo", "线性稳压", "低噪声", "低噪")):
        topology_constraints.append("ldo")
    topology_choice = _topology_choice(raw)
    if topology_choice is None and staged_chain:
        topology_choice = "buck_ldo"
    if (
        topology_choice is None
        and intermediate_voltage is not None
        and any(marker in folded for marker in ("ldo", "线性稳压"))
    ):
        topology_choice = "buck_ldo"
    return PowerRequirement(
        input_voltage_v=(
            Decimal(staged_chain.group("input"))
            if staged_chain
            else direct_comparison_voltage or extracted.input_voltage_v
        ),
        output_voltage_v=(
            Decimal(staged_chain.group("output")) if staged_chain else extracted.output_voltage_v
        ),
        **current,
        **rail_currents,
        efficiency_preference=(
            "high" if any(marker in folded for marker in ("效率", "高效", "省电")) else None
        ),
        noise_preference=(
            "low" if any(marker in folded for marker in ("低噪", "噪声", "模拟前端")) else None
        ),
        size_preference=(
            "small" if any(marker in folded for marker in ("空间不大", "小型", "尺寸紧")) else None
        ),
        thermal_preference=(
            "strict" if any(marker in folded for marker in ("散热", "热设计", "温升")) else None
        ),
        topology_constraints=list(dict.fromkeys(topology_constraints)),
        topology_choice=topology_choice,
        intermediate_voltage_v=intermediate_voltage,
        direct_ldo_comparison_input_voltage_v=direct_comparison_voltage,
        location_requested=extracted.location_requested,
        raw_text=raw,
    )


def is_power_design_request(text: str, extracted=None) -> bool:
    query = extracted or RequirementExtractor().extract(text)
    folded = text.casefold()
    if query.input_voltage_v is None or query.output_voltage_v is None:
        return False
    if query.input_voltage_v == query.output_voltage_v:
        return False
    current_specified = any(
        value is not None
        for value in (
            query.current_a,
            query.current_min_a,
            query.current_max_a,
            query.current_range_a,
        )
    ) or bool(_independent_current_cases(text))
    page_reference_query = any(
        marker in folded for marker in ("pdf", "datasheet", "data sheet", "数据手册", "规格书")
    ) and any(marker in folded for marker in ("哪一页", "在哪页", "页码", "page"))
    inventory_intent = any(
        marker in folded for marker in ("库存", "可用", "库位", "数量", "还有")
    ) or ("在哪" in folded and not page_reference_query)
    component_search_intent = any(
        marker in folded
        for marker in (
            "找",
            "选型",
            "模块",
            "芯片",
            "器件",
            "候选",
            "替代",
            "component",
            "converter",
        )
    )
    explicit_current_design = (
        current_specified and not inventory_intent and not component_search_intent
    )
    # A voltage-conversion phrase alone is not enough to replace the existing
    # component-candidate workflow. Keep explicit part/module searches there;
    # an explicit load current on a conversion question is itself enough to
    # request a power-design calculation when the user is not asking for stock
    # or for a component candidate.
    design_intent = (
        any(
            marker in folded
            for marker in (
                "方案",
                "设计",
                "硬件",
                "配套",
                "负载",
                "电流",
                "供电",
                "电源架构",
                "拓扑",
                "比较",
                "对比",
                "纹波",
                "psrr",
                "后接 ldo",
                "是否有意义",
            )
        )
        or explicit_current_design
    )
    conversion_signal = (
        "转" in text
        or bool(re.search(r"(?:→|->)", text))
        or bool(re.search(r"\bto\b", folded))
        or any(marker in folded for marker in ("buck", "ldo", "降压", "升压", "电源", "电压转换"))
    )
    return conversion_signal and design_intent
