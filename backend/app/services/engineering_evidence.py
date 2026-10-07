from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from pypdf import PdfReader
from sqlalchemy import delete, func, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models import (
    EngineeringDocument,
    EngineeringDocumentBlock,
    EngineeringDocumentPage,
    EvidenceAnchor,
    Material,
    Product,
    ProductRevision,
    User,
)
from app.power_design.requirements import extract_power_requirement
from app.services.audit import add_audit
from app.services.evidence_layout import extract_layout_document, layout_block_data

EXTRACTION_VERSION = "pypdf-text-v1"
SYNTHETIC_FIXTURE_SOURCE = "synthetic_fixture"
STRUCTURED_FACT_FIELDS = {
    "supply_voltage",
    "input_voltage",
    "input_voltage_absolute_max",
    "output_voltage",
    "output_current",
    "resolution_bits",
    "interface",
    "package",
    "pin",
    "gain",
    "purpose",
    "topology",
    "thermal_resistance",
    "power_dissipation",
    "peripheral",
    "variant",
}


def is_synthetic_fixture_document(document: EngineeringDocument) -> bool:
    """Return the explicit provenance flag, independent of document genre."""

    return document.source_type == SYNTHETIC_FIXTURE_SOURCE


QUERY_SYNONYMS = {
    "供电": ["supply", "voltage", "vcc", "vdd"],
    "电源": ["supply", "voltage", "vcc", "vdd"],
    "输入电压": ["input", "voltage", "vin"],
    "输出电流": ["output", "current"],
    "引脚": ["pin", "pinout"],
    "封装": ["package"],
    "分辨率": ["resolution", "bit"],
    "接口": ["interface"],
    "输出电压": ["output", "voltage", "vout"],
    "拓扑": ["topology", "buck", "ldo", "linear", "switching"],
    "热阻": ["thermal", "resistance", "theta"],
    "损耗": ["power", "dissipation", "thermal"],
    "电感": ["inductor"],
    "电容": ["capacitor"],
    "补偿": ["compensation"],
    "纹波": ["ripple"],
    "当前": ["current"],
    "历史": ["superseded", "revision"],
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_page_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    # Some vendor PDFs expose binary drawing operators as C0 control bytes.
    # PostgreSQL text rejects NUL and these bytes have no visible evidence
    # meaning, so remove them while preserving tabs and line breaks.
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", normalized)
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def extract_pdf_pages(pdf_path: Path) -> list[dict[str, Any]]:
    reader = PdfReader(str(pdf_path))
    pages = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = normalize_page_text(page.extract_text() or "")
        pages.append(
            {
                "page_number": page_number,
                "text_content": text,
                "text_sha256": sha256_bytes(text.encode("utf-8")),
            }
        )
    return pages


def extract_pdf_layout(
    pdf_path: Path,
    *,
    pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Add a versioned layout index without changing the legacy page text."""

    return extract_layout_document(pdf_path, base_pages=pages)


def _page_model_kwargs(page_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_number": page_data["page_number"],
        "text_content": page_data["text_content"],
        "text_sha256": page_data["text_sha256"],
        "native_text_quality": page_data.get("native_text_quality"),
        "ocr_status": page_data.get("ocr_status", "not_assessed"),
    }


def _persist_layout_blocks(
    db: Session,
    document: EngineeringDocument,
    page_rows: dict[int, EngineeringDocumentPage],
    layout: dict[str, Any],
) -> None:
    """Persist a deterministic, idempotent layout index for one source SHA."""

    version = str(layout["extraction_version"])
    source_sha = str(document.file_sha256)
    document.extraction_version = version
    for page_data in layout["pages"]:
        page = page_rows[int(page_data["page_number"])]
        desired = list(page_data.get("blocks") or [])
        existing = list(
            db.scalars(
                select(EngineeringDocumentBlock).where(
                    EngineeringDocumentBlock.document_page_id == page.id,
                    EngineeringDocumentBlock.extractor_version == version,
                )
            ).all()
        )
        if len(existing) == len(desired) and all(
            row.source_sha256 == source_sha
            and row.text_sha256 == item["text_sha256"]
            and row.block_type == item["block_type"]
            for row, item in zip(
                sorted(existing, key=lambda item: item.block_index), desired, strict=True
            )
        ):
            continue
        if existing:
            db.execute(
                delete(EngineeringDocumentBlock).where(
                    EngineeringDocumentBlock.document_page_id == page.id,
                    EngineeringDocumentBlock.extractor_version == version,
                )
            )
        for item in desired:
            db.add(
                EngineeringDocumentBlock(
                    document_page_id=page.id,
                    block_index=int(item["block_index"]),
                    block_type=item["block_type"],
                    reading_order=int(item["reading_order"]),
                    text_content=item["text_content"],
                    text_sha256=item["text_sha256"],
                    bbox=item.get("bbox"),
                    location_status=item.get("location_status", "location_unavailable"),
                    provenance=item.get("provenance"),
                    extractor_version=version,
                    source_sha256=source_sha,
                )
            )


def _layout_blocks_by_page(
    db: Session,
    rows: list[tuple[EvidenceAnchor, EngineeringDocumentPage, EngineeringDocument]],
) -> dict[int, list[EngineeringDocumentBlock]]:
    page_ids = list({page.id for _anchor, page, _document in rows})
    if not page_ids:
        return {}
    blocks = list(
        db.scalars(
            select(EngineeringDocumentBlock)
            .where(EngineeringDocumentBlock.document_page_id.in_(page_ids))
            .order_by(
                EngineeringDocumentBlock.document_page_id, EngineeringDocumentBlock.reading_order
            )
        ).all()
    )
    document_by_page = {page.id: document for _anchor, page, document in rows}
    return {
        page_id: [
            block
            for block in blocks
            if block.document_page_id == page_id
            and block.extractor_version == document_by_page[page_id].extraction_version
            and block.source_sha256 == document_by_page[page_id].file_sha256
        ]
        for page_id in page_ids
    }


def _range_fact(field: str, text: str) -> dict[str, Any] | None:
    match = re.search(
        r"(?P<minimum>\d+(?:\.\d+)?)\s*V\s+to\s+(?P<maximum>\d+(?:\.\d+)?)\s*V",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return {
        "field": field,
        "min": float(match.group("minimum")),
        "max": float(match.group("maximum")),
        "unit": "V",
    }


def deterministic_structured_facts(section: str, text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    lowered = f"{section}\n{text}".casefold()
    if "input voltage" in lowered:
        fact = _range_fact("input_voltage", text)
        if fact:
            facts.append(fact)
    elif any(marker in lowered for marker in ("supply", "operating conditions", "vcc", "vdd")):
        fact = _range_fact("supply_voltage", text)
        if fact:
            facts.append(fact)

    current = re.search(
        r"maximum continuous output-current evidence value is\s*(\d+(?:\.\d+)?)\s*A",
        text,
        re.IGNORECASE,
    )
    if current:
        facts.append({"field": "output_current", "value": float(current.group(1)), "unit": "A"})
    resolution = re.search(r"\b(\d{1,3})\s*[- ]?bit\b", text, re.IGNORECASE)
    if resolution:
        facts.append({"field": "resolution_bits", "value": int(resolution.group(1)), "unit": "bit"})

    supported_text = " ".join(
        line
        for line in text.splitlines()
        if "not asserted" not in line.casefold() and "does not" not in line.casefold()
    )
    interfaces = [
        name
        for name in ("CAN-FD", "CAN", "SPI", "I2C", "UART")
        if re.search(rf"\b{re.escape(name)}\b", supported_text, re.IGNORECASE)
    ]
    if "CAN-FD" in interfaces and "CAN" in interfaces:
        interfaces.remove("CAN")
    if interfaces and any(marker in lowered for marker in ("interface", "interfaces", "can-fd")):
        facts.append({"field": "interface", "values": interfaces})

    package = re.search(r"Package:\s*([A-Za-z0-9-]+)", text, re.IGNORECASE)
    if package:
        facts.append({"field": "package", "value": package.group(1)})

    pin_five = re.search(
        r"Pin\s*5(?:\s+(?:is|as|was)|\s*=|\s*:)?\s*([A-Z][A-Z0-9_-]+)",
        text,
        re.IGNORECASE,
    )
    if not pin_five and "\n5\n" in text:
        pin_five = re.search(r"\n5\n([A-Z][A-Z0-9_-]+)\n", text)
    if pin_five:
        facts.append({"field": "pin", "number": 5, "name": pin_five.group(1).upper()})
    return [fact for fact in facts if fact["field"] in STRUCTURED_FACT_FIELDS]


def _section_excerpt(text: str, heading: str) -> str:
    position = text.find(heading)
    return text[position:].strip() if position >= 0 else text.strip()


def _verified_excerpt(text: str, phrases: list[str], *, radius: int = 900) -> str:
    # PDF text extraction may split one visible phrase across arbitrary runs.
    # Match and persist the whitespace-collapsed page text so the manifest
    # verification is stable across the preflight and ingestion paths.
    normalized = " ".join(normalize_page_text(text).split())
    folded = normalized.casefold()
    def phrase_position(phrase: str) -> int:
        normalized_phrase = " ".join(phrase.split()).casefold()
        position = folded.find(normalized_phrase)
        if position >= 0:
            return position
        # Vendor extractors commonly split 2.2nF, 3.3V, and part numbers with
        # spaces.  Allow whitespace between phrase characters while retaining
        # the original page text as the cited source.
        compact_phrase = "".join(normalized_phrase.split())
        pattern = r"\s*".join(re.escape(character) for character in compact_phrase)
        match = re.search(pattern, normalized, re.IGNORECASE)
        return match.start() if match else -1

    positions = [phrase_position(phrase) for phrase in phrases]
    if any(position < 0 for position in positions):
        raise BusinessError(
            "EVIDENCE_EXPECTED_TEXT_MISSING",
            "厂商数据手册页缺少清单要求的验证文本。",
            409,
        )
    start = max(0, min(positions) - 160)
    end = min(len(normalized), max(positions) + radius)
    return normalized[start:end].strip()


class EngineeringEvidenceIngestionService:
    """Deterministic text-PDF ingestion. OCR and model calls are intentionally absent."""

    def __init__(self, db: Session, user: User, request_id: str):
        self.db = db
        self.user = user
        self.request_id = request_id
        self.last_reconciled_anchor_ids: list[int] = []

    def _scope_ids(self, definition: dict[str, Any]) -> tuple[int | None, int | None]:
        if definition["scope_type"] == "material":
            material = self.db.scalar(
                select(Material).where(
                    Material.code == definition["material_code"],
                    Material.is_deleted.is_(False),
                )
            )
            if material is None:
                raise BusinessError("EVIDENCE_MATERIAL_NOT_FOUND", "证据物料不存在", 404)
            return material.id, None
        product = self.db.scalar(select(Product).where(Product.code == definition["product_code"]))
        if product is None:
            raise BusinessError("EVIDENCE_PRODUCT_NOT_FOUND", "证据产品不存在", 404)
        revision = self.db.scalar(
            select(ProductRevision).where(
                ProductRevision.product_id == product.id,
                ProductRevision.revision == definition["revision"],
            )
        )
        if revision is None:
            raise BusinessError("EVIDENCE_REVISION_NOT_FOUND", "证据产品版本不存在", 404)
        return None, revision.id

    def _ensure_existing_layout(self, document: EngineeringDocument, pdf_path: Path) -> None:
        """Backfill blocks for a legacy document when its source file is available."""

        if not pdf_path.is_file():
            return
        existing_blocks = self.db.scalar(
            select(func.count(EngineeringDocumentBlock.id)).where(
                EngineeringDocumentBlock.source_sha256 == document.file_sha256
            )
        )
        if existing_blocks:
            return
        pages = extract_pdf_pages(pdf_path)
        if len(pages) != document.page_count:
            return
        layout = extract_pdf_layout(pdf_path, pages=pages)
        page_rows = {
            page.page_number: page
            for page in self.db.scalars(
                select(EngineeringDocumentPage).where(
                    EngineeringDocumentPage.document_id == document.id
                )
            ).all()
        }
        if len(page_rows) != len(pages):
            return
        for page_data in layout["pages"]:
            page = page_rows[int(page_data["page_number"])]
            page.native_text_quality = page_data.get("native_text_quality")
            page.ocr_status = page_data.get("ocr_status", "not_assessed")
        _persist_layout_blocks(self.db, document, page_rows, layout)
        self.db.commit()
        self.db.refresh(document)

    def ingest_fixture(
        self,
        definition: dict[str, Any],
        pdf_path: Path,
        *,
        supersedes_document_id: int | None = None,
    ) -> tuple[EngineeringDocument, bool]:
        file_bytes = pdf_path.read_bytes()
        actual_sha = sha256_bytes(file_bytes)
        if actual_sha != definition["sha256"]:
            raise BusinessError(
                "EVIDENCE_FILE_SHA_MISMATCH",
                "工程证据文件 SHA-256 与清单不一致。",
                409,
            )
        material_id, product_revision_id = self._scope_ids(definition)
        existing_sha = self.db.scalar(
            select(EngineeringDocument).where(EngineeringDocument.file_sha256 == actual_sha)
        )
        if existing_sha is not None:
            if (
                existing_sha.material_id != material_id
                or existing_sha.product_revision_id != product_revision_id
                or existing_sha.document_key != definition["document_key"]
            ):
                raise BusinessError(
                    "EVIDENCE_SHA_SCOPE_CONFLICT",
                    "相同文件 SHA 已登记到不同工程范围。",
                    409,
                )
            # Phase 2.4 initially used the generic sample_seed provenance.
            # Every document accepted by this manifest-bound ingestion path is a
            # synthetic CI fixture, including product-scoped engineering notes.
            if existing_sha.source_type == "sample_seed":
                existing_sha.source_type = SYNTHETIC_FIXTURE_SOURCE
                self.db.flush()
            self._ensure_existing_layout(existing_sha, pdf_path)
            return existing_sha, False

        pages = extract_pdf_pages(pdf_path)
        layout = extract_pdf_layout(pdf_path, pages=pages)
        if len(pages) != int(definition["page_count"]):
            raise BusinessError("EVIDENCE_PAGE_COUNT_MISMATCH", "证据页数与清单不一致", 409)
        if not pages or not all(page["text_content"] for page in pages):
            raise BusinessError(
                "TEXT_EXTRACTION_UNAVAILABLE",
                "PDF 没有可用文本；Phase 2.4 不使用 OCR。",
                422,
            )
        for expected in definition["expected_pages"]:
            page = pages[int(expected["page"]) - 1]
            normalized = " ".join(page["text_content"].split())
            for phrase in expected["expected_phrases"]:
                if " ".join(phrase.split()) not in normalized:
                    raise BusinessError(
                        "EVIDENCE_EXPECTED_TEXT_MISSING",
                        f"第 {expected['page']} 页缺少清单要求的合成测试文本。",
                        409,
                    )

        document = EngineeringDocument(
            document_key=definition["document_key"],
            scope_type=definition["scope_type"],
            material_id=material_id,
            product_revision_id=product_revision_id,
            document_type=definition["document_type"],
            title=definition["title"],
            manufacturer=definition["manufacturer"],
            document_revision=definition["document_revision"],
            document_date=date.fromisoformat(definition["document_date"]),
            source_type=SYNTHETIC_FIXTURE_SOURCE,
            source_url="",
            original_filename=definition["filename"],
            storage_key=f"evals/evidence/fixtures/synthetic_datasheets/{definition['filename']}",
            file_sha256=actual_sha,
            page_count=len(pages),
            status=definition["status"],
            supersedes_document_id=supersedes_document_id,
            ingest_status="pending",
            ingest_error="",
            extraction_version=layout["extraction_version"],
            created_by_id=self.user.id,
        )
        self.db.add(document)
        self.db.flush()
        layout_pages = {int(page["page_number"]): page for page in layout["pages"]}
        page_rows: dict[int, EngineeringDocumentPage] = {}
        for page_data, expected in zip(pages, definition["expected_pages"], strict=True):
            page = EngineeringDocumentPage(
                document_id=document.id,
                **_page_model_kwargs(layout_pages[int(page_data["page_number"])]),
            )
            self.db.add(page)
            self.db.flush()
            page_rows[page.page_number] = page
            excerpt = _section_excerpt(page.text_content, expected["heading"])
            facts = deterministic_structured_facts(expected["heading"], excerpt)
            self.db.add(
                EvidenceAnchor(
                    document_page_id=page.id,
                    section_title=expected["heading"],
                    excerpt_text=excerpt,
                    excerpt_sha256=sha256_bytes(excerpt.encode("utf-8")),
                    structured_fact={"facts": facts} if facts else None,
                    anchor_source="deterministic_parser" if facts else "extracted",
                    created_by_id=self.user.id,
                )
            )
        _persist_layout_blocks(self.db, document, page_rows, layout)
        if supersedes_document_id is not None:
            previous = self.db.get(EngineeringDocument, supersedes_document_id)
            if previous is None:
                raise BusinessError(
                    "EVIDENCE_SUPERSEDED_DOCUMENT_NOT_FOUND",
                    "被替代的工程文档不存在。",
                    409,
                )
            previous.status = "superseded"
        document.ingest_status = "ready"
        add_audit(
            self.db,
            self.user.id,
            "engineering_evidence.ingest",
            "engineering_document",
            str(document.id),
            self.request_id,
            after={
                "document_key": document.document_key,
                "file_sha256": document.file_sha256,
                "page_count": document.page_count,
                "synthetic_fixture": is_synthetic_fixture_document(document),
            },
        )
        self.db.commit()
        self.db.refresh(document)
        return document, True

    def _reconcile_vendor_anchors(
        self,
        document: EngineeringDocument,
        definition: dict[str, Any],
        pdf_path: Path,
    ) -> list[int]:
        """Idempotently reconcile facts on an exact existing vendor document.

        The document, PDF identity, pages, excerpts, and MPN/revision metadata
        remain immutable. Only a uniquely identified anchor's structured fact
        payload may be corrected through this path; duplicate or ambiguous
        targets fail closed instead of creating a second anchor.
        """

        pages = extract_pdf_pages(pdf_path)
        if len(pages) != int(document.page_count):
            raise BusinessError("EVIDENCE_PAGE_COUNT_MISMATCH", "证据页数与清单不一致", 409)
        page_rows = {
            page.page_number: page
            for page in self.db.scalars(
                select(EngineeringDocumentPage).where(
                    EngineeringDocumentPage.document_id == document.id
                )
            ).all()
        }
        if len(page_rows) != len(pages):
            raise BusinessError(
                "EVIDENCE_RECONCILE_PAGE_IDENTITY_MISMATCH",
                "现有工程证据页结构与源 PDF 不一致，拒绝隐式重建。",
                409,
            )
        page_by_id = {page.id: page for page in page_rows.values()}
        anchors = list(
            self.db.scalars(
                select(EvidenceAnchor)
                .join(
                    EngineeringDocumentPage,
                    EvidenceAnchor.document_page_id == EngineeringDocumentPage.id,
                )
                .where(EngineeringDocumentPage.document_id == document.id)
            ).all()
        )
        anchors_by_page: dict[int, list[EvidenceAnchor]] = {}
        for anchor in anchors:
            page = page_by_id.get(anchor.document_page_id)
            if page is not None:
                anchors_by_page.setdefault(page.page_number, []).append(anchor)

        updated_ids: list[int] = []
        for definition_anchor in definition.get("anchors") or []:
            page_number = int(definition_anchor["page"])
            page = page_rows.get(page_number)
            if page is None:
                raise BusinessError("EVIDENCE_ANCHOR_PAGE_INVALID", "证据锚点页码无效", 409)
            expected_excerpt = _verified_excerpt(
                pages[page_number - 1]["text_content"],
                list(definition_anchor["expected_phrases"]),
            )
            matches = [
                anchor
                for anchor in anchors_by_page.get(page_number, [])
                if anchor.section_title == definition_anchor["section_title"]
            ]
            if len(matches) != 1:
                raise BusinessError(
                    "EVIDENCE_RECONCILE_ANCHOR_NOT_UNIQUE",
                    "工程证据修正目标不是唯一锚点，拒绝创建或删除锚点。",
                    409,
                    details={
                        "document_key": document.document_key,
                        "page": page_number,
                        "section_title": definition_anchor["section_title"],
                        "match_count": len(matches),
                    },
                )
            anchor = matches[0]
            expected_excerpt_sha = sha256_bytes(expected_excerpt.encode("utf-8"))
            if anchor.excerpt_sha256 != expected_excerpt_sha:
                raise BusinessError(
                    "EVIDENCE_RECONCILE_EXCERPT_MISMATCH",
                    "工程证据锚点摘录与同 SHA 源 PDF 不一致，拒绝隐式覆盖。",
                    409,
                    details={"anchor_id": anchor.id, "page": page_number},
                )
            facts = list(definition_anchor.get("facts") or [])
            unknown_fields = {
                fact.get("field")
                for fact in facts
                if fact.get("field") not in STRUCTURED_FACT_FIELDS
            }
            if unknown_fields:
                raise BusinessError(
                    "EVIDENCE_STRUCTURED_FACT_INVALID",
                    "数据手册清单包含不支持的结构化事实字段。",
                    409,
                    details={"fields": sorted(str(item) for item in unknown_fields)},
                )
            expected_structured_fact = {"facts": facts} if facts else None
            if anchor.structured_fact != expected_structured_fact:
                anchor.structured_fact = expected_structured_fact
                anchor.anchor_source = "deterministic_parser" if facts else "extracted"
                updated_ids.append(anchor.id)

        if updated_ids:
            add_audit(
                self.db,
                self.user.id,
                "engineering_evidence.reconcile_vendor_anchors",
                "engineering_document",
                str(document.id),
                self.request_id,
                after={
                    "document_key": document.document_key,
                    "file_sha256": document.file_sha256,
                    "updated_anchor_ids": sorted(updated_ids),
                    "inventory_or_bom_write": False,
                },
            )
            self.db.commit()
            self.db.refresh(document)
        return sorted(updated_ids)

    def ingest_datasheet(
        self,
        definition: dict[str, Any],
        pdf_path: Path,
        *,
        storage_key: str,
    ) -> tuple[EngineeringDocument, bool]:
        """Ingest one content-verified vendor PDF without OCR or model calls."""

        if definition.get("document_type") != "datasheet":
            raise BusinessError("EVIDENCE_DOCUMENT_TYPE_INVALID", "仅允许导入厂商数据手册", 409)
        file_bytes = pdf_path.read_bytes()
        actual_sha = sha256_bytes(file_bytes)
        if actual_sha != definition["sha256"]:
            raise BusinessError(
                "EVIDENCE_FILE_SHA_MISMATCH",
                "工程证据文件 SHA-256 与清单不一致。",
                409,
            )
        material_id, product_revision_id = self._scope_ids(definition)
        existing_sha = self.db.scalar(
            select(EngineeringDocument).where(EngineeringDocument.file_sha256 == actual_sha)
        )
        if existing_sha is not None:
            if (
                existing_sha.material_id != material_id
                or existing_sha.product_revision_id != product_revision_id
                or existing_sha.document_key != definition["document_key"]
            ):
                raise BusinessError(
                    "EVIDENCE_SHA_SCOPE_CONFLICT",
                    "相同文件 SHA 已登记到不同工程范围。",
                    409,
                )
            self.last_reconciled_anchor_ids = self._reconcile_vendor_anchors(
                existing_sha, definition, pdf_path
            )
            self._ensure_existing_layout(existing_sha, pdf_path)
            return existing_sha, False
        if self.db.scalar(
            select(EngineeringDocument.id).where(
                EngineeringDocument.document_key == definition["document_key"]
            )
        ):
            raise BusinessError(
                "EVIDENCE_DOCUMENT_KEY_CONFLICT",
                "数据手册文档键已对应其他文件；必须显式登记新版关系。",
                409,
            )

        pages = extract_pdf_pages(pdf_path)
        layout = extract_pdf_layout(pdf_path, pages=pages)
        if len(pages) != int(definition["page_count"]):
            raise BusinessError("EVIDENCE_PAGE_COUNT_MISMATCH", "证据页数与清单不一致", 409)
        for anchor in definition["anchors"]:
            page_number = int(anchor["page"])
            if page_number < 1 or page_number > len(pages):
                raise BusinessError("EVIDENCE_ANCHOR_PAGE_INVALID", "证据锚点页码无效", 409)
            _verified_excerpt(pages[page_number - 1]["text_content"], anchor["expected_phrases"])

        document = EngineeringDocument(
            document_key=definition["document_key"],
            scope_type=definition["scope_type"],
            material_id=material_id,
            product_revision_id=product_revision_id,
            document_type="datasheet",
            title=definition["title"],
            manufacturer=definition["manufacturer"],
            document_revision=definition["document_revision"],
            document_date=date.fromisoformat(definition["document_date"]),
            source_type="vendor_upload",
            source_url=definition["source_url"],
            original_filename=pdf_path.name,
            storage_key=storage_key,
            file_sha256=actual_sha,
            page_count=len(pages),
            status="current",
            supersedes_document_id=None,
            ingest_status="pending",
            ingest_error="",
            extraction_version=layout["extraction_version"],
            created_by_id=self.user.id,
        )
        self.db.add(document)
        self.db.flush()
        layout_pages = {int(page["page_number"]): page for page in layout["pages"]}
        page_rows: dict[int, EngineeringDocumentPage] = {}
        for page_data in pages:
            page = EngineeringDocumentPage(
                document_id=document.id,
                **_page_model_kwargs(layout_pages[int(page_data["page_number"])]),
            )
            self.db.add(page)
            self.db.flush()
            page_rows[page.page_number] = page
        for anchor_definition in definition["anchors"]:
            page_number = int(anchor_definition["page"])
            page = page_rows[page_number]
            excerpt = _verified_excerpt(
                page.text_content,
                list(anchor_definition["expected_phrases"]),
            )
            facts = list(anchor_definition.get("facts") or [])
            unknown_fields = {
                fact.get("field")
                for fact in facts
                if fact.get("field") not in STRUCTURED_FACT_FIELDS
            }
            if unknown_fields:
                raise BusinessError(
                    "EVIDENCE_STRUCTURED_FACT_INVALID",
                    "数据手册清单包含不支持的结构化事实字段。",
                    409,
                    details={"fields": sorted(str(item) for item in unknown_fields)},
                )
            self.db.add(
                EvidenceAnchor(
                    document_page_id=page.id,
                    section_title=anchor_definition["section_title"],
                    excerpt_text=excerpt,
                    excerpt_sha256=sha256_bytes(excerpt.encode("utf-8")),
                    structured_fact={"facts": facts} if facts else None,
                    anchor_source="deterministic_parser" if facts else "extracted",
                    created_by_id=self.user.id,
                )
            )
        _persist_layout_blocks(self.db, document, page_rows, layout)
        document.ingest_status = "ready"
        add_audit(
            self.db,
            self.user.id,
            "engineering_evidence.ingest_vendor_datasheet",
            "engineering_document",
            str(document.id),
            self.request_id,
            after={
                "document_key": document.document_key,
                "file_sha256": document.file_sha256,
                "page_count": document.page_count,
                "synthetic_fixture": False,
            },
        )
        self.db.commit()
        self.db.refresh(document)
        return document, True


def _fact_list(anchor: EvidenceAnchor) -> list[dict[str, Any]]:
    structured = anchor.structured_fact or {}
    facts = structured.get("facts") if isinstance(structured, dict) else None
    return list(facts or [])


def _normalized_identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _material_identity_tokens(material: Material) -> set[str]:
    values = [material.mpn, material.code, (material.attributes or {}).get("orderable_mpn")]
    tokens: set[str] = set()
    for value in values:
        for part in re.split(r"[/,;\s]+", str(value or "")):
            normalized = _normalized_identity(part)
            if normalized:
                tokens.add(normalized)
    return tokens


def _fact_identity_values(fact: dict[str, Any]) -> list[str]:
    values = [fact.get("variant"), fact.get("orderable_mpn")]
    return [normalized for value in values if (normalized := _normalized_identity(value))]


def _identity_matches_material(identity: str, material_tokens: set[str]) -> bool:
    if not identity:
        return False
    if identity in material_tokens:
        return True
    # Sample materials may carry an orderable suffix while the evidence
    # fact uses the family MPN (for example TLV761 vs TLV76133DCYR). Do not
    # apply this shortening rule to two same-length sibling variants.
    return any(
        len(identity) >= 6
        and len(token) >= 6
        and (identity.startswith(token) or token.startswith(identity))
        and identity[:5] == token[:5]
        for token in material_tokens
    )


def _explicit_family_variant_requested(query: str, variant: str) -> bool:
    normalized_query = _normalized_identity(query)
    if not variant or _normalized_identity(variant) not in normalized_query:
        return False
    folded = query.casefold()
    if not any(
        marker in folded
        for marker in (
            "比较",
            "对比",
            "compare",
            "cover",
            "封面",
            "系列",
            "variant",
            "pin",
            "引脚",
            "package",
            "封装",
        )
    ):
        return False

    normalized_variant = str(variant).casefold()
    mentions = list(re.finditer(re.escape(normalized_variant), folded))
    if not mentions:
        return False
    negative_markers = ("不要", "别", "勿", "不可", "不能", "禁止", "do not", "don't", "not")
    for mention in mentions:
        before = folded[max(0, mention.start() - 28) : mention.start()]
        after = folded[mention.end() : mention.end() + 28]
        if any(marker in before for marker in negative_markers):
            continue
        if any(marker in after for marker in ("混用", "mix", "混淆")):
            continue
        return True
    return False


def _fact_matches_material_scope(
    fact: dict[str, Any],
    material: Material,
    query: str,
    requested_variants: set[str] | None = None,
) -> tuple[bool, str]:
    variants = _fact_identity_values(fact)
    if not variants:
        return True, "generic_family_fact"
    source_context = str(fact.get("source_context") or "").casefold()
    if (
        "cover" in source_context
        and any(marker in query.casefold() for marker in ("cover", "封面", "典型应用"))
    ):
        return True, "explicit_cover_context"
    if requested_variants and not requested_variants.intersection(variants):
        return False, "excluded_variant"
    material_tokens = _material_identity_tokens(material)
    if any(_identity_matches_material(variant, material_tokens) for variant in variants):
        return True, "target_variant"
    if any(_explicit_family_variant_requested(query, variant) for variant in variants):
        return True, "explicit_family_variant"
    return False, "excluded_variant"


def _dedupe_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fact in facts:
        key = json.dumps(
            {
                key: value
                for key, value in fact.items()
                if key not in {"scope_match", "material_id", "anchor_id"}
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(fact)
    return result


def _query_tokens(query: str) -> list[str]:
    lowered = query.casefold()
    tokens = re.findall(r"[a-z0-9][a-z0-9_.-]*", lowered)
    for marker, aliases in QUERY_SYNONYMS.items():
        if marker in query:
            tokens.extend(aliases)
    pin_reference = re.search(
        r"(?:pin\s*5|引脚\s*5|(?:第\s*)?5\s*(?:号)?脚)",
        query,
        re.IGNORECASE,
    )
    if pin_reference:
        tokens.extend(["pin", "5"])
    return list(dict.fromkeys(token for token in tokens if len(token) > 1))


def detect_evidence_fields(query: str) -> list[str]:
    lowered = query.casefold()
    fields = []
    chinese_pin = re.search(r"(?:第\s*)?(?P<number>\d{1,3})\s*(?:号)?脚", query)
    explicit_pin = re.search(r"(?:pin|引脚)\s*(?P<number>\d{1,3})", query, re.IGNORECASE)
    pin_match = chinese_pin or explicit_pin
    pin_number = int(pin_match.group("number")) if pin_match else None
    if pin_number == 5:
        fields.append("pin_5")
    elif pin_number is not None or "pin" in lowered or "引脚" in query:
        fields.append("pin")
    asks_absolute_input = any(
        value in lowered for value in ("absolute maximum", "absolute max", "abs max")
    ) or any(value in query for value in ("绝对最大", "绝对额定", "最大额定"))
    if "input voltage" in lowered or "vin" in lowered or "输入电压" in query:
        fields.append("input_voltage")
    elif any(value in lowered for value in ("supply", "voltage", "vcc", "vdd")) or any(
        value in query for value in ("供电", "电源")
    ):
        fields.append("supply_voltage")
    if "fixed output" in lowered or "固定输出" in query or "输出电压" in query:
        fields.append("output_voltage")
    if "output current" in lowered or "输出电流" in query:
        fields.append("output_current")
    if "resolution" in lowered or "bit" in lowered or "分辨率" in query:
        fields.append("resolution_bits")
    if (
        any(value in lowered for value in ("interface", "can", "spi", "i2c", "uart"))
        or "接口" in query
    ):
        fields.append("interface")
    if "package" in lowered or "封装" in query:
        fields.append("package")
    if "gain" in lowered or "增益" in query:
        fields.append("gain")
    if any(
        value in lowered
        for value in ("compensation", "ripple", "bootstrap", "capacitor", "inductor", "bst")
    ) or any(value in query for value in ("补偿", "纹波", "电容", "电感", "容量", "耐压")):
        fields.append("peripheral")
    if asks_absolute_input:
        fields.append("input_voltage_absolute_max")
    if any(value in lowered for value in ("power dissipation", "thermal loss")) or any(
        value in query for value in ("损耗", "功耗")
    ):
        fields.append("power_dissipation")
    if any(value in lowered for value in ("thermal resistance", "theta")) or any(
        value in query for value in ("热阻", "散热")
    ):
        fields.append("thermal_resistance")
    if any(value in lowered for value in ("why", "purpose", "function")) or any(
        value in query for value in ("为什么", "作用", "用途")
    ):
        fields.append("purpose")
    return list(dict.fromkeys(fields))


def _negative_topic_request(query: str, markers: tuple[str, ...]) -> bool:
    """Recognize a nearby Chinese/English instruction that excludes a topic."""

    folded = query.casefold()
    for marker in markers:
        pattern = (
            r"(?:不要|别|不(?:要|应)|without|do\s+not)"
            rf"[^。！？；,，.!?]{{0,48}}{re.escape(marker)}"
        )
        if re.search(pattern, folded, re.IGNORECASE):
            return True
    return False


def _requested_peripheral_topic(query: str) -> str | None:
    """Return the sole requested LM5164 peripheral topic, if unambiguous."""

    folded = query.casefold()
    topics: set[str] = set()
    if any(marker in folded for marker in ("bst", "bootstrap")) and not _negative_topic_request(
        query, ("bst", "bootstrap")
    ):
        topics.add("bst")
    if (
        any(marker in folded for marker in ("cot", "ripple")) or "纹波" in query
    ) and not _negative_topic_request(query, ("cot", "ripple", "纹波")):
        topics.add("cot_ripple")
    return next(iter(topics)) if len(topics) == 1 else None


def _peripheral_fact_matches_topic(fact: dict[str, Any], topic: str) -> bool:
    text = json.dumps(fact, ensure_ascii=False, default=str).casefold()
    if topic == "bst":
        return "bst" in text or "bootstrap" in text
    return "cot" in text or "ripple" in text or "纹波" in text


def _exact_power_conditions(query: str) -> tuple[Decimal, Decimal, Decimal] | None:
    """Parse exact LDO inputs without turning a range into a fake point value."""

    try:
        requirement = extract_power_requirement(query)
    except (TypeError, ValueError, ArithmeticError):
        return None
    current = requirement.load_current_a
    if current is None or requirement.load_current_range_a is not None:
        return None
    if (
        requirement.input_voltage_v is None
        or requirement.output_voltage_v is None
        or current <= 0
        or requirement.input_voltage_v <= requirement.output_voltage_v
    ):
        return None
    return (
        requirement.input_voltage_v,
        requirement.output_voltage_v,
        current,
    )


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _deterministic_linear_power_fact(
    query: str,
    raw_facts: list[dict[str, Any]],
    material_id: int,
) -> dict[str, Any] | None:
    """Derive LDO loss only when the vendor anchor carries matching conditions."""

    conditions = _exact_power_conditions(query)
    if conditions is None:
        return None
    vin, vout, current = conditions
    has_linear_topology = any(
        fact.get("material_id") == material_id
        and fact.get("field") == "topology"
        and str(fact.get("value") or "").casefold() == "linear"
        for fact in raw_facts
    )
    if not has_linear_topology:
        return None
    for fact in raw_facts:
        if fact.get("material_id") != material_id or fact.get("field") != "power_dissipation":
            continue
        anchor_conditions = fact.get("conditions")
        if not isinstance(anchor_conditions, dict):
            continue
        try:
            matches = all(
                Decimal(str(anchor_conditions.get(key))) == expected
                for key, expected in zip(
                    ("vin_v", "vout_v", "load_current_a"), conditions, strict=True
                )
            )
        except (TypeError, ValueError, ArithmeticError):
            matches = False
        if not matches:
            continue
        loss = (vin - vout) * current
        loss_text = _decimal_text(loss)
        return {
            "field": "power_dissipation",
            "value": float(loss),
            "unit": "W",
            "variant": fact.get("variant"),
            "fact_type": "derived_calculation",
            "calculation": (
                f"({ _decimal_text(vin)} - {_decimal_text(vout)}) × "
                f"{_decimal_text(current)} = {loss_text} W"
            ),
            "conditions": {
                "vin_v": float(vin),
                "vout_v": float(vout),
                "load_current_a": float(current),
            },
            "source_context": "服务端 Decimal 计算；已与厂商功耗条件锚点匹配",
            "anchor_id": fact.get("anchor_id"),
            "material_id": material_id,
            "scope_match": "target_variant",
        }
    return None


def _thermal_package_context(fact: dict[str, Any]) -> str | None:
    value = fact.get("value")
    if not isinstance(value, dict) or not value:
        return None
    labels: list[str] = []
    for key in value:
        normalized = str(key).replace("SOT223", "SOT-223").replace("TO252", "TO-252")
        if "_" in normalized:
            variant, package = normalized.split("_", 1)
            labels.append(f"{variant} ({package})")
        else:
            labels.append(normalized)
    return " / ".join(labels)


def _thermal_package_fact(fact: dict[str, Any]) -> dict[str, Any] | None:
    package_context = _thermal_package_context(fact)
    if not package_context:
        return None
    return {
        "field": "package",
        "value": package_context,
        "variant": fact.get("variant"),
        "fact_type": "thermal_package_context",
        "conditions": "数据手册热阻表的封装标签；RθJA 为结至环境热阻",
        "anchor_id": fact.get("anchor_id"),
        "material_id": fact.get("material_id"),
        "scope_match": fact.get("scope_match", "target_variant"),
    }


def citation_data(
    anchor: EvidenceAnchor,
    page: EngineeringDocumentPage,
    document: EngineeringDocument,
    *,
    layout_blocks: Iterable[EngineeringDocumentBlock] | None = None,
    ranking: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = {
        "document_id": document.id,
        "document_key": document.document_key,
        "document_title": document.title,
        "document_revision": document.document_revision,
        "document_status": document.status,
        "page": page.page_number,
        "physical_page": page.page_number,
        "page_reference": "PDF physical page (1-based)",
        "section": anchor.section_title,
        "anchor_id": anchor.id,
        "excerpt": anchor.excerpt_text,
        "file_sha256": document.file_sha256,
        "page_text_sha256": page.text_sha256,
        "synthetic_fixture": is_synthetic_fixture_document(document),
        "layout_blocks": [layout_block_data(block) for block in (layout_blocks or [])],
    }
    if ranking is not None:
        value["ranking"] = ranking
    return value


class EvidenceRetrievalService:
    def __init__(self, db: Session):
        self.db = db

    def _materials(self, material_ids: Iterable[int]) -> dict[int, Material]:
        ids = list(dict.fromkeys(material_ids))
        rows = {
            item.id: item
            for item in self.db.scalars(
                select(Material).where(Material.id.in_(ids), Material.is_deleted.is_(False))
            ).all()
        }
        if set(ids) != set(rows):
            raise BusinessError("MATERIAL_NOT_FOUND", "证据查询包含不存在的物料", 404)
        return rows

    def _vector_status(self) -> str:
        """Report environment capability without treating a vector score as fact."""

        if self.db.bind is None or self.db.bind.dialect.name != "postgresql":
            return "VECTOR_BLOCKED_BY_ENVIRONMENT"
        try:
            available = self.db.execute(
                sql_text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).first()
        except Exception:  # pragma: no cover - database capability probe
            return "VECTOR_BLOCKED_BY_ENVIRONMENT"
        return "available_not_enabled" if available else "VECTOR_BLOCKED_BY_ENVIRONMENT"

    def search_material_evidence(
        self,
        *,
        material_ids: list[int],
        query: str,
        include_superseded: bool = False,
        limit: int = 6,
    ) -> dict[str, Any]:
        materials = self._materials(material_ids)
        allowed_statuses = ["current", "superseded"] if include_superseded else ["current"]
        statement = (
            select(EvidenceAnchor, EngineeringDocumentPage, EngineeringDocument)
            .join(
                EngineeringDocumentPage,
                EvidenceAnchor.document_page_id == EngineeringDocumentPage.id,
            )
            .join(
                EngineeringDocument,
                EngineeringDocumentPage.document_id == EngineeringDocument.id,
            )
            .where(
                EngineeringDocument.scope_type == "material",
                EngineeringDocument.material_id.in_(materials),
                EngineeringDocument.status.in_(allowed_statuses),
                EngineeringDocument.ingest_status == "ready",
            )
        )
        tokens = _query_tokens(query)
        desired_fields = detect_evidence_fields(query)
        fts_anchor_ids: set[int] = set()
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql" and tokens:
            document_text = func.concat_ws(
                " ", EvidenceAnchor.section_title, EvidenceAnchor.excerpt_text
            )
            search_query = func.plainto_tsquery("simple", " ".join(tokens))
            search_vector = func.to_tsvector("simple", document_text)
            fts_statement = statement.where(search_vector.op("@@")(search_query)).order_by(
                func.ts_rank_cd(search_vector, search_query).desc(),
                EngineeringDocumentPage.page_number,
            )
            fts_anchor_ids = {row[0].id for row in self.db.execute(fts_statement).all()}
        rows = list(self.db.execute(statement).all())
        layout_by_page = _layout_blocks_by_page(self.db, rows)
        asks_history = include_superseded and any(
            marker in query.casefold() for marker in ("rev a", "superseded", "history")
        )

        def rank_detail(row) -> tuple[tuple[float, int, int], dict[str, Any]]:
            anchor, page, document = row
            layout_text = " ".join(block.text_content for block in layout_by_page.get(page.id, []))
            haystack = f"{anchor.section_title}\n{anchor.excerpt_text}\n{layout_text}".casefold()
            lexical_hits = [token for token in tokens if token in haystack]
            score = float(len(lexical_hits))
            signals: list[str] = ["lexical"] if lexical_hits else []
            if anchor.id in fts_anchor_ids:
                score += 3.0
                signals.append("postgres_fts")
            fact_fields = {fact.get("field") for fact in _fact_list(anchor)}
            fact_hits = {
                "pin" if field == "pin_5" else field for field in desired_fields
            }.intersection(fact_fields)
            score += 5.0 * len(fact_hits)
            if fact_hits:
                signals.append("structured_fact")
            layout_hits = [
                token
                for token in tokens
                if any(
                    token in block.text_content.casefold()
                    for block in layout_by_page.get(page.id, [])
                )
            ]
            if layout_hits:
                score += 1.5
                signals.append("layout_block")
            material = materials.get(document.material_id)
            material_mpn = (material.mpn if material else "") or ""
            mpn_tokens = re.findall(r"[a-z0-9][a-z0-9.-]*", material_mpn.casefold())
            exact_mpn = bool(
                material_mpn
                and (
                    material_mpn.casefold() in query.casefold()
                    or (mpn_tokens and set(mpn_tokens).issubset(set(tokens)))
                )
            )
            if exact_mpn:
                score += 8.0
                signals.append("exact_mpn")
            variant_hits = [
                str(fact.get("variant") or fact.get("orderable_mpn") or "").casefold()
                for fact in _fact_list(anchor)
                if fact.get("variant") or fact.get("orderable_mpn")
            ]
            if any(item and item in query.casefold() for item in variant_hits):
                score += 12.0
                signals.append("exact_variant")
            if asks_history and document.status == "superseded":
                score += 20
                signals.append("explicit_history")
            if not asks_history and document.status == "current":
                score += 2
                signals.append("current_revision")
            return (score, -page.page_number, -anchor.id), {
                "score": round(score, 3),
                "signals": list(dict.fromkeys(signals)),
                "strategy": "fts+structured+layout",
            }

        def rank(row) -> tuple[float, int, int]:
            return rank_detail(row)[0]

        rows.sort(key=rank, reverse=True)
        selected = rows[:limit]
        raw_citations = [
            citation_data(
                *row,
                layout_blocks=layout_by_page.get(row[1].id, []),
                ranking=rank_detail(row)[1],
            )
            for row in selected
        ]
        raw_facts = [
            {
                "material_id": document.material_id,
                "anchor_id": anchor.id,
                **fact,
            }
            for anchor, _page, document in selected
            for fact in _fact_list(anchor)
        ]
        scoped_facts: list[dict[str, Any]] = []
        scope_matches: dict[int, str] = {}
        requested_variants = {
            variant
            for fact in raw_facts
            for variant in _fact_identity_values(fact)
            if _explicit_family_variant_requested(query, variant)
        }
        for fact in raw_facts:
            material = materials.get(int(fact["material_id"]))
            if material is None:
                continue
            matches, scope_match = _fact_matches_material_scope(
                fact,
                material,
                query,
                requested_variants=requested_variants,
            )
            scope_matches[int(fact["anchor_id"])] = scope_match
            if matches:
                scoped_facts.append({**fact, "scope_match": scope_match})
        scoped_facts = _dedupe_facts(scoped_facts)
        desired_fact_fields = {"pin" if field == "pin_5" else field for field in desired_fields}
        if "power_dissipation" in desired_fact_fields:
            linear_material_ids = {
                int(fact["material_id"])
                for fact in raw_facts
                if fact.get("field") == "topology"
                and str(fact.get("value") or "").casefold() == "linear"
            }
            for material_id in linear_material_ids:
                derived = _deterministic_linear_power_fact(query, raw_facts, material_id)
                if derived is None:
                    continue
                scoped_facts = [
                    fact
                    for fact in scoped_facts
                    if not (
                        fact.get("field") == "power_dissipation"
                        and int(fact.get("material_id") or -1) == material_id
                        and fact.get("anchor_id") == derived.get("anchor_id")
                    )
                ]
                scoped_facts.append(derived)
        if "thermal_resistance" in desired_fact_fields:
            for fact in scoped_facts:
                if fact.get("field") != "thermal_resistance":
                    continue
                fact["conditions"] = (
                    "数据手册热阻表；RθJA 为结至环境热阻；"
                    f"封装标签：{_thermal_package_context(fact) or '见引用'}"
                )
                package_fact = _thermal_package_fact(fact)
                if package_fact and not any(
                    existing.get("field") == "package"
                    and existing.get("anchor_id") == package_fact.get("anchor_id")
                    for existing in scoped_facts
                ):
                    scoped_facts.append(package_fact)
        scoped_facts = _dedupe_facts(scoped_facts)
        supporting_facts = [
            fact for fact in scoped_facts if fact.get("field") in desired_fact_fields
        ]
        peripheral_topic = (
            _requested_peripheral_topic(query)
            if "peripheral" in desired_fact_fields
            else None
        )
        if peripheral_topic:
            supporting_facts = [
                fact
                for fact in supporting_facts
                if _peripheral_fact_matches_topic(fact, peripheral_topic)
            ]
        relevant_anchor_ids = {int(fact["anchor_id"]) for fact in supporting_facts}
        citations = (
            [
                {
                    **citation,
                    "variant_scope": scope_matches.get(
                        int(citation["anchor_id"]), "generic_family_fact"
                    ),
                    "matched_fact_fields": sorted(
                        {
                            str(fact.get("field"))
                            for fact in supporting_facts
                            if int(fact.get("anchor_id") or -1) == int(citation["anchor_id"])
                        }
                    ),
                }
                for citation in raw_citations
                if not desired_fields or not relevant_anchor_ids or int(citation["anchor_id"])
                in relevant_anchor_ids
            ]
            or raw_citations
        )
        # A bare ``pin = VIO`` is technically correct but not self-explanatory to a
        # warehouse/engineering user. When the exact grounded pin fact resolves to
        # VIO, include only same-anchor companion facts that explain that rail. This
        # keeps the first turn deterministic and useful without asking the model to
        # invent meaning or broadening to unrelated document facts.
        if "pin_5" in desired_fields:
            vio_anchor_ids = {
                int(fact["anchor_id"])
                for fact in supporting_facts
                if fact.get("field") == "pin"
                and str(fact.get("name") or "").casefold() == "vio"
                and fact.get("anchor_id") is not None
            }
            if vio_anchor_ids:
                companion_fields = {"interface", "supply_voltage", "purpose"}
                supporting_facts.extend(
                    fact
                    for fact in scoped_facts
                    if int(fact.get("anchor_id") or -1) in vio_anchor_ids
                    and fact.get("field") in companion_fields
                )
                # Keep sibling variant facts distinct.  Keying only by anchor
                # and field would collapse MCP2561FD/SPLIT into the later
                # MCP2562FD/VIO fact when a comparison asks for both variants.
                supporting_facts = _dedupe_facts(supporting_facts)
        numeric_tokens = re.findall(r"\d+(?:\.\d+)?", query)
        selected_text = " ".join(item["excerpt"] for item in citations)
        structured_text = json.dumps(supporting_facts, ensure_ascii=False, default=str)
        numeric_supported = not numeric_tokens or all(
            token in selected_text
            or token in structured_text
            or (
                token.isdigit()
                and len(token) > 1
                and f"{float(token) / 1000:g}" in structured_text
            )
            for token in numeric_tokens
        )
        lexical_supported = not tokens or any(token in selected_text.casefold() for token in tokens)
        supported_fields = {str(fact.get("field")) for fact in supporting_facts}
        missing_desired_fields = sorted(desired_fact_fields - supported_fields)
        technical_claim = bool(desired_fields or numeric_tokens) or any(
            marker in query.casefold()
            for marker in ("can", "can-fd", "pin", "voltage", "supply", "interface", "接口", "引脚")
        )
        insufficient = (
            not citations
            or (
                technical_claim
                and (
                    not supporting_facts
                    or bool(missing_desired_fields)
                    or not numeric_supported
                )
            )
            or (not desired_fields and not lexical_supported)
        )
        return {
            "material_scope": [
                {
                    "id": item.id,
                    "code": item.code,
                    "name": item.name,
                    "mpn": item.mpn,
                    "package": item.package,
                    "manufacturer": item.manufacturer,
                }
                for item in materials.values()
            ],
            "query": query,
            "include_superseded": include_superseded,
            "facts": supporting_facts if desired_fields else scoped_facts,
            "citations": citations,
            "raw_facts": raw_facts,
            "raw_citations": raw_citations,
            "allowed_anchor_ids": [item["anchor_id"] for item in citations],
            "evidence_coverage": "insufficient" if insufficient else "supported",
            "evidence_coverage_details": {
                "requested_fields": sorted(desired_fact_fields),
                "supported_fields": sorted(supported_fields),
                "missing_fields": missing_desired_fields,
                "numeric_supported": numeric_supported,
            },
            "conclusion": "当前证据不足" if insufficient else "证据已定位",
            "automatic_decision": False,
            "read_only": True,
            "retrieval": {
                "strategy": "fts+structured+layout",
                "vector_status": self._vector_status(),
                "reranker": "deterministic_exact_identity",
                "variant_filtering": "exact_material_or_explicit_family_variant",
                "selected_anchor_count": len(citations),
                "layout_extraction_versions": sorted(
                    {
                        str(item.get("extractor_version"))
                        for citation in citations
                        for item in citation.get("layout_blocks", [])
                        if item.get("extractor_version")
                    }
                ),
            },
        }

    def validate_citations(
        self,
        *,
        anchor_ids: list[int],
        allowed_anchor_ids: list[int],
        material_ids: list[int],
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        if not set(anchor_ids).issubset(set(allowed_anchor_ids)):
            raise BusinessError(
                "EVIDENCE_CITATION_NOT_ALLOWED",
                "引用包含不在本次服务端检索白名单中的锚点。",
                409,
            )
        statuses = ["current", "superseded"] if include_superseded else ["current"]
        rows = list(
            self.db.execute(
                select(EvidenceAnchor, EngineeringDocumentPage, EngineeringDocument)
                .join(
                    EngineeringDocumentPage,
                    EvidenceAnchor.document_page_id == EngineeringDocumentPage.id,
                )
                .join(
                    EngineeringDocument,
                    EngineeringDocumentPage.document_id == EngineeringDocument.id,
                )
                .where(
                    EvidenceAnchor.id.in_(anchor_ids),
                    EngineeringDocument.scope_type == "material",
                    EngineeringDocument.material_id.in_(material_ids),
                    EngineeringDocument.status.in_(statuses),
                    EngineeringDocument.ingest_status == "ready",
                )
            ).all()
        )
        if {row[0].id for row in rows} != set(anchor_ids):
            raise BusinessError(
                "EVIDENCE_CITATION_SCOPE_INVALID",
                "引用页或文档版本不属于当前服务端证据范围。",
                409,
            )
        layout_by_page = _layout_blocks_by_page(self.db, rows)
        return [
            citation_data(*row, layout_blocks=layout_by_page.get(row[1].id, [])) for row in rows
        ]


def _fact_value(facts: list[dict[str, Any]], field: str) -> Any:
    # A single parsed pin is not a complete pinout comparison. Generic pin
    # compatibility therefore stays unknown; only an explicitly requested
    # pin number (currently pin_5 in the deterministic fixture vocabulary)
    # may be compared.
    if field == "pin":
        return None
    normalized = "pin" if field == "pin_5" else field
    matches = [fact for fact in facts if fact.get("field") == normalized]
    if field == "pin_5":
        matches = [fact for fact in matches if fact.get("number") == 5]
    if not matches:
        return None
    fact = matches[0]
    if "min" in fact and "max" in fact:
        return (fact["min"], fact["max"], fact.get("unit"))
    if "values" in fact:
        return tuple(sorted(fact["values"]))
    if field == "pin_5":
        return fact.get("name")
    return fact.get("value")


class EvidenceComparisonService:
    def __init__(self, db: Session):
        self.db = db
        self.retrieval = EvidenceRetrievalService(db)

    def compare(
        self,
        *,
        first_material_id: int,
        second_material_id: int,
        fields: list[str],
    ) -> dict[str, Any]:
        materials = self.retrieval._materials([first_material_id, second_material_id])
        rows = []
        all_citations: dict[int, dict[str, Any]] = {}
        for field in fields:
            query = {
                "pin_5": "current pin 5 pinout",
                "pin": "current pinout",
                "supply_voltage": "current supply voltage VCC VDD",
                "input_voltage": "input voltage VIN",
                "output_current": "output current",
                "resolution_bits": "resolution bits",
                "interface": "supported interface",
                "package": "package",
            }.get(field, field)
            first = self.retrieval.search_material_evidence(
                material_ids=[first_material_id], query=query, limit=5
            )
            second = self.retrieval.search_material_evidence(
                material_ids=[second_material_id], query=query, limit=5
            )
            first_value = _fact_value(first["facts"], field)
            second_value = _fact_value(second["facts"], field)
            result = (
                "unknown"
                if first_value is None or second_value is None
                else ("same" if first_value == second_value else "different")
            )
            for citation in [*first["citations"], *second["citations"]]:
                all_citations[citation["anchor_id"]] = citation
            normalized_field = "pin" if field == "pin_5" else field
            first_facts = [
                fact
                for fact in first["facts"]
                if fact.get("field") == normalized_field
                and (field != "pin_5" or fact.get("number") == 5)
            ]
            second_facts = [
                fact
                for fact in second["facts"]
                if fact.get("field") == normalized_field
                and (field != "pin_5" or fact.get("number") == 5)
            ]
            rows.append(
                {
                    "field": field,
                    "first": first_value,
                    "second": second_value,
                    "result": result,
                    "first_facts": first_facts,
                    "second_facts": second_facts,
                    "first_anchor_ids": first["allowed_anchor_ids"],
                    "second_anchor_ids": second["allowed_anchor_ids"],
                }
            )
        pin_difference = any(
            row["field"] in {"pin", "pin_5"} and row["result"] == "different" for row in rows
        )
        unknown = any(row["result"] == "unknown" for row in rows)
        return {
            "materials": [
                {"id": item.id, "code": item.code, "name": item.name, "mpn": item.mpn}
                for item in materials.values()
            ],
            "comparisons": rows,
            "differences": [row for row in rows if row["result"] == "different"],
            "unknowns": [row["field"] for row in rows if row["result"] == "unknown"],
            "citations": list(all_citations.values()),
            "conclusion": (
                "当前证据不支持引脚兼容"
                if pin_difference
                else ("当前证据不足" if unknown else "证据比较完成")
            ),
            "pin_compatible_supported": False if pin_difference or unknown else None,
            "automatic_decision": False,
            "read_only": True,
        }
