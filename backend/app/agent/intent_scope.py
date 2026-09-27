from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IntentScope = Literal[
    "product_bom_alternate",
    "product_build_readiness",
    "component_relation",
    "cable_search",
    "component_search",
    "unknown",
]


@dataclass(frozen=True)
class IntentScopeDecision:
    intent: IntentScope
    mentions_product_scope: bool = False
    mentions_cable: bool = False
    mentions_relation: bool = False
    mentions_alternate: bool = False


_CABLE = re.compile(
    r"(?:线缆|端子线|双头线|排线|软排线|摄像头线|相机线|FFC|FPC|IPEX|同轴线|"
    r"PORTFOLIO-CBL-[A-Z0-9._/-]+|"
    r"\d+(?:\.\d+)?\s*mm\s*\d+\s*(?:pin|p)\b|"
    r"\d+\s*(?:pin|p)?\s*(?:→|->|转|to)\s*\d+\s*(?:pin|p)\b)",
    re.I,
)
_RELATION = re.compile(
    r"(?:similar[_ -]?to|pin[- _]?compatible|引脚兼容|验证(?:过|了)?的?关系|"
    r"工程关系|相似关系|相似器件|替代关系|直接互换|能互换|可互换|直接替代|直接换)",
    re.I,
)
_ALTERNATE = re.compile(
    r"(?:备选|替代料|替它|能替|换成|先顶上|能顶|可顶|替换(?:成|为)|批准备选)",
    re.I,
)
_PRODUCT_SCOPE = re.compile(
    r"(?:产品|单台|BOM|板上|那块板|这个板|版本|revision|"
    r"PROD-[A-Z0-9-]+|(?:EVT|DVT|PVT)-R?\d+|Atlas)",
    re.I,
)
_UNIVERSAL_PRODUCT_ALTERNATE = re.compile(
    r"(?:所有|全部|任意|任何)\s*产品[^？?。！!]*(?:替代|备选|互换)",
    re.I,
)
_PRODUCT_BUILD_READINESS = re.compile(
    r"(?:(?:做|生产|构建)\s*-?\d+(?:\.\d+)?\s*(?:台|套|个)|"
    r"(?:本次|这次|此次)?构建(?:所需|需求|后)|"
    r"构建后安全库存|projected_free_available_after_build)",
    re.I,
)


def route_intent_scope(message: str) -> IntentScopeDecision:
    """Apply stable semantic precedence before any generic model routing.

    Product/BOM alternates are scoped engineering decisions, so they win over
    cable similarity. Explicit relation semantics win over generic component
    or material search. A conversation continuation is resolved by the
    server-owned context service before this message-level router is called.
    """

    mentions_cable = (
        bool(_CABLE.search(message))
        or bool(re.search(r"(?:这批|这根|那根|这条|那条)线", message))
        or (bool(_ALTERNATE.search(message)) and any(marker in message for marker in ("线", "缆")))
    )
    mentions_relation = bool(_RELATION.search(message))
    mentions_alternate = bool(_ALTERNATE.search(message))
    mentions_product_scope = bool(_PRODUCT_SCOPE.search(message))
    universal_product_alternate = bool(_UNIVERSAL_PRODUCT_ALTERNATE.search(message))

    mentions_product_build = bool(_PRODUCT_BUILD_READINESS.search(message))

    if universal_product_alternate:
        # A universal cross-product approval cannot exist: validated relations
        # are global engineering facts, while alternate approval is scoped to
        # one ProductRevision/BOM position.
        intent: IntentScope = "component_relation"
    elif mentions_alternate and (mentions_cable or mentions_product_scope):
        intent: IntentScope = "product_bom_alternate"
    elif mentions_relation:
        intent = "component_relation"
    elif mentions_cable:
        intent = "cable_search"
    elif mentions_product_build:
        intent = "product_build_readiness"
    else:
        intent = "unknown"
    return IntentScopeDecision(
        intent=intent,
        mentions_product_scope=mentions_product_scope,
        mentions_cable=mentions_cable,
        mentions_relation=mentions_relation,
        mentions_alternate=mentions_alternate,
    )
