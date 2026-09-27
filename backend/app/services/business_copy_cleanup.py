from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import (
    ComponentRelationEvidenceLink,
    EngineeringDocument,
    EngineeringDocumentPage,
    EvidenceAnchor,
    ProductBomAlternateEvidenceLink,
)
from app.services.engineering_evidence import SYNTHETIC_FIXTURE_SOURCE

FORBIDDEN_PATTERNS = (
    r"resume/project demonstration",
    r"resume project",
    r"for resume",
    r"portfolio demo",
    r"portfolio demonstration",
    r"synthetic portfolio",
    r"synthetic data",
    r"synthetic inbound",
    r"stock is synthetic",
    r"demo stock",
    r"demo metadata",
    r"demo module",
    r"demo electromechanical sensor",
    r"demo dc/dc module",
    r"governed synthetic inbound",
    r"current quantity comes only from",
    r"秋招",
    r"作品集",
    r"作品展示",
    r"求职",
    r"简历项目",
    r"面试展示",
    r"内部工程",
    r"业务数据\s*[:：]",
    r"项目数据\s*[:：]",
    r"演示数据\s*[:：]",
    r"当前数量仅来自受控的库存入库流程",
    r"demo only",
    r"synthetic test data",
    r"showcase",
    r"anonymous portfolio",
    r"governed movement",
    r"\bdemonstrat(?:e|es|ed|ing|ion)\b",
    r"deliberately",
    r"no private product identity",
    r"演示",
)
FORBIDDEN_RE = re.compile("|".join(f"(?:{item})" for item in FORBIDDEN_PATTERNS), re.I)

_DROP_LINES = (
    re.compile(r"^Synthetic Portfolio cable catalog item\.?$", re.I),
    re.compile(r"^线缆目录物料。当前数量仅来自受控的库存入库流程。?$"),
    re.compile(
        r"^Current quantity comes only from the governed synthetic inbound movement\.?$",
        re.I,
    ),
    re.compile(
        r"^Portfolio demo synthetic data;?\s*for resume/project demonstration only\.?$",
        re.I,
    ),
    re.compile(r"^\[\s*Portfolio Demo(?: v2)?\s*\]$", re.I),
    re.compile(r"^内部培训用安全物料样本；技术参数仅来自已标记的结构化字段。$"),
    re.compile(
        r"^Portfolio Demo(?: v2)?;\s*LCSC statement used only for catalog identity;.*$",
        re.I,
    ),
    re.compile(
        r"^Synthetic product-specific engineering approval for demonstrating scoped alternates\.?$",
        re.I,
    ),
)
_STRIP_PATTERNS = (
    re.compile(r"(?:[;；]\s*)?demo stock is synthetic\.?", re.I),
    re.compile(r"(?:[;；]\s*)?stock is synthetic\.?", re.I),
    re.compile(r"(?:[;；]\s*)?demo metadata\b", re.I),
    re.compile(
        r"\bdemo\s+(?=(?:module|electromechanical sensor|dc/dc module)\b)",
        re.I,
    ),
    re.compile(r"\[\s*Portfolio Demo(?: v2)?\s*\]", re.I),
    re.compile(r"业务数据\s*[:：]\s*"),
    re.compile(r"项目数据\s*[:：]\s*"),
    re.compile(r"演示数据\s*[:：]\s*"),
    re.compile(r"(?:内部工程)+(?:项目)?\s*[:：·|\-]?\s*"),
    re.compile(r"Portfolio review\s*[:：]?\s*", re.I),
    re.compile(r"Portfolio Demo(?: v2)?(?: only)?\s*[:：;；\-]?\s*", re.I),
    re.compile(r"Portfolio Showcase|Demo Only|Synthetic Test Data", re.I),
    re.compile(r"Synthetic Portfolio", re.I),
    re.compile(r"for resume/project demonstration only\.?", re.I),
    re.compile(r"Resume Project|Job Hunting", re.I),
    re.compile(r"Anonymous Portfolio", re.I),
    re.compile(r"秋招作品集演示\s*[:：]?\s*"),
    re.compile(r"作品集演示\s*[:：]?\s*"),
    re.compile(r"作品集产品"),
)

_EXACT_REPLACEMENTS = {
    "cable catalog item. Current quantity comes only from the governed movement": "",
    "Synthetic Portfolio cable catalog item. Current quantity comes only from the governed "
    "synthetic inbound movement.": "",
    "Deliberately one unit unallocated to demonstrate partial location distribution": (
        "One unit remains unallocated."
    ),
    "No lot allocation on purpose; demonstrates primary location without exact location "
    "quantity": (
        "No lot allocation is recorded; the primary location does not imply an exact location "
        "quantity."
    ),
    "Deliberately one unit unallocated; key Component Intelligence demo material": (
        "One unit remains unallocated."
    ),
    "Anonymous Portfolio BOM derivative; no private product identity": "",
    "BOM derivative; no private product identity": "",
    "秋招作品集演示：MCU、通信、电源与驱动 IC": "MCU、通信、电源与驱动 IC",
    "秋招作品集演示：编码器、IMU、ToF、电流检测、ADC": (
        "编码器 · IMU · ToF · 电流检测 · ADC"
    ),
    "秋招作品集演示：0603/0805 常用阻容": "0603/0805 常用阻容",
    "秋招作品集演示：连接器、线束与小型模块": "连接器、线束与小型模块",
    "秋招作品集演示：48V 功率模块、电机驱动板、计算模块": (
        "48V 功率模块、电机驱动板与计算模块"
    ),
    "作品集演示：多版本 BOM、CAN-FD、48V 动力与故意保留的 1 项缺料。required_quantity "
    "表示当前项目总需求，不是单台用量": (
        "多版本 BOM、CAN-FD、48V 动力与 1 项待补物料。required_quantity "
        "表示当前项目总需求，不是单台用量。"
    ),
    "作品集演示：编码器、电机驱动、项目预留与明确缺料": (
        "编码器、电机驱动、项目预留与明确缺料。"
    ),
    "作品集演示：库存基本充足的健康项目，用于与缺料项目形成对比": (
        "库存基本充足的项目，可用于与缺料项目形成对比。"
    ),
    "作品集演示：计算模块低库存与真实缺料；后续适合接入 Vision/RAG": (
        "计算模块低库存与真实缺料。"
    ),
    "作品集演示：48V→5V 模块、BMS、功率 MOS、电流检测；同时包含部分库位未分配": (
        "48V→5V 模块、BMS、功率 MOS、电流检测；同时包含部分库位未分配。"
    ),
    "作品集演示：力传感器 + 24-bit ADC + 编码器 + 电机控制": (
        "力传感器、24-bit ADC、编码器与电机控制。"
    ),
    "作品集演示：无线 MCU、BMS、电源与 CAN；暂停项目用于状态筛选演示": (
        "无线 MCU、BMS、电源与 CAN；当前项目状态为暂停。"
    ),
    "无线 MCU、BMS、电源与 CAN；暂停项目用于状态筛选演示": (
        "无线 MCU、BMS、电源与 CAN；当前项目状态为暂停。"
    ),
    "ICU 传感器、MEMS 麦克风、STM32 与精密电源组合，用于真多轮 BOM 演示": (
        "ICU 传感器、MEMS 麦克风、STM32 与精密电源组合。"
    ),
    "机器人移动底盘作品集产品": "机器人移动底盘产品",
}

MUTABLE_COPY_FIELDS: dict[str, tuple[str, ...]] = {
    "locations": ("manager", "notes", "bin_content_notes"),
    "materials": ("specification", "notes"),
    "projects": ("notes",),
    "bom_items": ("notes",),
    "products": ("description",),
    "product_revisions": ("notes",),
    "product_bom_items": ("notes",),
    "component_relations": (
        "confidence_note",
        "evidence_summary",
        "rejected_reason",
        "revoked_reason",
    ),
    "product_bom_alternates": (
        "usage_condition",
        "engineering_note",
        "rejected_reason",
        "revoked_reason",
    ),
    "stock_movements": ("reason", "notes"),
}
def clean_user_visible_copy(value: str | None) -> str:
    source = str(value or "").strip()
    if source in _EXACT_REPLACEMENTS:
        return _EXACT_REPLACEMENTS[source]
    punctuation_trimmed = source.rstrip(".。")
    if punctuation_trimmed in _EXACT_REPLACEMENTS:
        return _EXACT_REPLACEMENTS[punctuation_trimmed]
    lines: list[str] = []
    for raw_line in source.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or any(pattern.fullmatch(line) for pattern in _DROP_LINES):
            continue
        for pattern in _STRIP_PATTERNS:
            line = pattern.sub("", line)
        line = re.sub(r"\bsynthetic\s+(?:demo|data|inbound)\b", "", line, flags=re.I)
        line = re.sub(r"^[\s;；,.。·|：:\-]+", "", line)
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _redacted_sample(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]} len:{len(value)}"


def audit_database_copy(db: Session) -> dict[str, Any]:
    fields: Counter[tuple[str, str]] = Counter()
    samples: dict[tuple[str, str], str] = {}
    total = 0
    for table_name, field_names in MUTABLE_COPY_FIELDS.items():
        table = Base.metadata.tables[table_name]
        for field_name in field_names:
            column = table.c[field_name]
            for _row_id, raw_value in db.execute(
                select(table.c.id, column).where(column.is_not(None))
            ):
                value = str(raw_value or "")
                if not value or not FORBIDDEN_RE.search(value):
                    continue
                key = (table.name, column.name)
                fields[key] += 1
                total += 1
                samples.setdefault(key, _redacted_sample(value))
    return {
        "forbidden_match_count": total,
        "fields": [
            {
                "table": table,
                "field": field,
                "match_count": count,
                "sample": samples[(table, field)],
                "cleanup_rule": "strip forbidden boilerplate; blank boilerplate-only copy",
            }
            for (table, field), count in sorted(fields.items())
        ],
    }


def cleanup_database_copy(db: Session) -> dict[str, Any]:
    changes: Counter[tuple[str, str]] = Counter()
    for table_name, field_names in MUTABLE_COPY_FIELDS.items():
        table = Base.metadata.tables[table_name]
        for field_name in field_names:
            column = table.c[field_name]
            for row_id, raw_value in db.execute(
                select(table.c.id, column).where(column.is_not(None))
            ):
                before = str(raw_value or "")
                after = clean_user_visible_copy(before)
                if before == after:
                    continue
                db.execute(update(table).where(table.c.id == row_id).values({field_name: after}))
                changes[(table_name, field_name)] += 1
    return {
        "changed_row_fields": sum(changes.values()),
        "fields": [
            {"table": table, "field": field, "changed": count}
            for (table, field), count in sorted(changes.items())
        ],
    }


def remove_synthetic_business_evidence(db: Session) -> dict[str, int]:
    document_ids = list(
        db.scalars(
            select(EngineeringDocument.id).where(
                EngineeringDocument.source_type == SYNTHETIC_FIXTURE_SOURCE
            )
        ).all()
    )
    if not document_ids:
        return {
            "documents_deleted": 0,
            "pages_deleted": 0,
            "anchors_deleted": 0,
            "relation_links_deleted": 0,
            "alternate_links_deleted": 0,
        }
    page_ids = list(
        db.scalars(
            select(EngineeringDocumentPage.id).where(
                EngineeringDocumentPage.document_id.in_(document_ids)
            )
        ).all()
    )
    anchor_ids = list(
        db.scalars(
            select(EvidenceAnchor.id).where(EvidenceAnchor.document_page_id.in_(page_ids))
        ).all()
    )
    relation_links = db.execute(
        delete(ComponentRelationEvidenceLink).where(
            ComponentRelationEvidenceLink.evidence_anchor_id.in_(anchor_ids)
        )
    ).rowcount
    alternate_links = db.execute(
        delete(ProductBomAlternateEvidenceLink).where(
            ProductBomAlternateEvidenceLink.evidence_anchor_id.in_(anchor_ids)
        )
    ).rowcount
    anchors = db.execute(delete(EvidenceAnchor).where(EvidenceAnchor.id.in_(anchor_ids))).rowcount
    pages = db.execute(
        delete(EngineeringDocumentPage).where(EngineeringDocumentPage.id.in_(page_ids))
    ).rowcount
    documents = db.execute(
        delete(EngineeringDocument).where(EngineeringDocument.id.in_(document_ids))
    ).rowcount
    return {
        "documents_deleted": int(documents or 0),
        "pages_deleted": int(pages or 0),
        "anchors_deleted": int(anchors or 0),
        "relation_links_deleted": int(relation_links or 0),
        "alternate_links_deleted": int(alternate_links or 0),
    }
