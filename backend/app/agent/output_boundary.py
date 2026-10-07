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
    "navigation_lab",
    "navigation_plan",
}





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
        text = str(content or "").strip()
        entities = entities or {}
        category = ""
        engineering_answer = bool(
            entities.get("engineering_research") or entities.get("power_design")
        )
        if _PRIVATE_MARKERS.search(text):
            category = "private_reasoning_or_instruction"
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
