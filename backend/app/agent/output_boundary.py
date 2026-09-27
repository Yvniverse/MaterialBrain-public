from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_PRIVATE_MARKERS = re.compile(
    r"(?:system\s*prompt|developer\s*prompt|系统提示词|开发者提示词|"
    r"tool\s*list|工具列表|i\s+need\s+to\s+reason|让我分析|让我们看工具|"
    r"self[- ]?correction|策略调整|最终决定|chain\s+of\s+thought|"
    r"reasoning_content)",
    re.I,
)
_MARKDOWN_LIST = re.compile(r"(?m)^\s*(?:[-*]|\d+[.)])\s+")
_MARKDOWN_EMPHASIS = re.compile(r"\*\*|__")
_MARKDOWN_TABLE_ROW = re.compile(r"(?m)^\s*\|.*\|\s*$")
_PROHIBITED_INVENTORY_PROVENANCE = re.compile(
    r"(?:"
    r"实物\s*(?:复核|核验|确认|验证|盘点|清点)|"
    r"(?:库存|库位|数量|物料).{0,12}(?:实物|现场)?\s*"
    r"(?:复核|核验|确认|验证|盘点|清点)|"
    r"(?:建议|请|需要|应当|必须).{0,12}"
    r"(?:实物|现场|盘点|清点).{0,12}"
    r"(?:复核|核验|确认|验证|盘点|清点)?|"
    r"(?:未|尚未|没有|无法).{0,6}(?:实物|现场)?\s*"
    r"(?:复核|核验|确认|验证|盘点|清点)|"
    r"(?:demo|synthetic)\s*(?:stock|inventory|quantity)|"
    r"(?:演示|合成)\s*(?:库存|数量|物料)|"
    r"(?:库存|库位).{0,12}(?:来源|标记|分流)|"
    r"quantity(?:_|\s+)is(?:_|\s+)exact.{0,8}(?:false|no|否|非)|"
    r"(?:数量|库位).{0,8}(?:精确性|准确性).{0,8}(?:false|否|不|未)"
    r")",
    re.I,
)
_STRUCTURED_KEYS = {
    "material_candidates",
    "project_candidates",
    "product_candidates",
    "component_search",
    "power_design",
    "cable_search",
    "cable_detail",
    "inventory",
    "material_inventories",
    "locations",
    "low_stock",
    "project_bom",
    "bom_analysis",
    "product_bom",
    "build_readiness",
    "component_relations",
    "product_bom_alternates",
    "engineering_evidence",
    "component_evidence_comparison",
    "engineering_research",
}

_PUBLIC_COPY_REPLACEMENTS = (
    (re.compile(r"(?:[;；]\s*)?demo stock is synthetic\.?", re.I), ""),
    (re.compile(r"(?:[;；]\s*)?stock is synthetic\.?", re.I), ""),
    (re.compile(r"(?:[;；]\s*)?demo metadata\b", re.I), ""),
    (
        re.compile(
            r"\bdemo\s+(?=(?:module|electromechanical sensor|dc/dc module)\b)",
            re.I,
        ),
        "",
    ),
    (re.compile(r"Synthetic Portfolio cable catalog item\.?", re.I), ""),
    (
        re.compile(
            r"Current quantity comes only from the governed synthetic inbound movement\.?",
            re.I,
        ),
        "",
    ),
    (
        re.compile(
            r"Portfolio demo synthetic data;?\s*for resume/project demonstration only\.?",
            re.I,
        ),
        "",
    ),
    (re.compile(r"\[\s*Portfolio Demo(?: v2)?\s*\]", re.I), ""),
    (re.compile(r"业务数据\s*[:：]\s*"), ""),
    (re.compile(r"项目数据\s*[:：]\s*"), ""),
    (re.compile(r"演示数据\s*[:：]\s*"), ""),
    (re.compile(r"线缆目录物料。当前数量仅来自受控的库存入库流程。?"), ""),
    (
        re.compile(
            r"cable catalog item\. Current quantity comes only from the governed movement\.?",
            re.I,
        ),
        "",
    ),
    (re.compile(r"(?:内部工程)+(?:项目)?\s*[:：·|\-]?\s*"), ""),
    (re.compile(r"秋招作品展示[:：]?"), ""),
    (re.compile(r"作品集演示|作品集产品|秋招|作品展示|简历项目|面试展示|求职|作品集"), ""),
    (
        re.compile(
            r"Portfolio Demo(?: v2)?|Portfolio Showcase|Demo Only|Synthetic Test Data",
            re.I,
        ),
        "",
    ),
    (re.compile(r"Resume Project|Job Hunting", re.I), ""),
    (re.compile(r"Portfolio review\s*[:：]?", re.I), ""),
    (re.compile(r"Synthetic Portfolio", re.I), ""),
    (re.compile(r"Anonymous Portfolio", re.I), ""),
)


def sanitize_public_business_copy(content: str) -> str:
    text = content
    for pattern, replacement in _PUBLIC_COPY_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"^[\s;；,.。·|：:\-]+", "", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


@dataclass(frozen=True)
class PublicAnswerBoundaryResult:
    content: str
    blocked: bool
    category: str = ""


class PublicAnswerBoundary:
    """One-way boundary from provider output to public answer text."""

    @staticmethod
    def _has_structured_result(entities: dict[str, Any]) -> bool:
        return any(entities.get(key) for key in _STRUCTURED_KEYS)

    def inspect(
        self,
        content: str,
        *,
        entities: dict[str, Any] | None = None,
    ) -> PublicAnswerBoundaryResult:
        text = sanitize_public_business_copy(str(content or "").strip())
        entities = entities or {}
        category = ""
        engineering_answer = bool(
            entities.get("engineering_research") or entities.get("power_design")
        )
        if _PRIVATE_MARKERS.search(text):
            category = "private_reasoning_or_instruction"
        elif _PROHIBITED_INVENTORY_PROVENANCE.search(text):
            category = "prohibited_inventory_provenance"
        elif self._has_structured_result(entities) and (
            _MARKDOWN_LIST.search(text)
            or _MARKDOWN_EMPHASIS.search(text)
            or _MARKDOWN_TABLE_ROW.search(text)
        ):
            if not engineering_answer:
                category = "duplicate_structured_markdown"
        if not category:
            return PublicAnswerBoundaryResult(content=text, blocked=False)

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        logger.warning(
            "agent_public_output_blocked category=%s sha256=%s",
            category,
            digest,
        )
        fallback = (
            "已完成查询，请查看下方结构化结果。"
            if self._has_structured_result(entities)
            else "本次回答未通过公开输出安全检查，请换一种方式描述任务。"
        )
        return PublicAnswerBoundaryResult(
            content=fallback,
            blocked=True,
            category=category,
        )
