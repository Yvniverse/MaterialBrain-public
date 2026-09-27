"""Versioned, fail-closed layout extraction for engineering evidence PDFs.

The existing pypdf page text remains the compatibility/fallback representation.
PyMuPDF is used only to add layout blocks and table provenance when it is
available.  Nothing from this module is an engineering fact by itself: facts
still have to be promoted into the existing EvidenceAnchor chain.
"""

from __future__ import annotations

import importlib.util
import math
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.services.engineering_evidence_layout_types import (  # type: ignore[import-not-found]
    LayoutExtractionResult,
)

PYMUPDF_EXTRACTION_VERSION = "pymupdf-blocks-v1"
PYPDF_LAYOUT_FALLBACK_VERSION = "pypdf-text-layout-fallback-v1"
OCR_DEFERRED = "OCR_DEFERRED"


def _load_pymupdf():
    """Return the installed PyMuPDF module without making it mandatory."""

    if importlib.util.find_spec("pymupdf") is not None:
        import pymupdf  # type: ignore[import-not-found]

        return pymupdf
    if importlib.util.find_spec("fitz") is not None:
        import fitz  # type: ignore[import-not-found]

        return fitz
    return None


def _normalize_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", normalized)
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _native_text_quality(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chars = len("".join(lines))
    numeric_tokens = len(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    if chars == 0:
        quality = "none"
    elif chars < 40 or not lines:
        quality = "low"
    else:
        quality = "good"
    return {
        "quality": quality,
        "character_count": chars,
        "line_count": len(lines),
        "numeric_token_count": numeric_tokens,
    }


def _ocr_status(quality: str) -> str:
    if quality == "good":
        return "not_needed"
    # OCR is deliberately deferred.  A future offline opt-in route may inspect
    # this status, but ingestion never runs OCR implicitly.
    return OCR_DEFERRED


def _valid_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in numbers):
        return None
    x0, y0, x1, y1 = numbers
    if x1 <= x0 or y1 <= y0:
        return None
    return [round(item, 3) for item in numbers]


def _bbox_intersection_ratio(first: list[float] | None, second: list[float] | None) -> float:
    if not first or not second:
        return 0.0
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    intersection = (x1 - x0) * (y1 - y0)
    first_area = max((first[2] - first[0]) * (first[3] - first[1]), 1.0)
    return intersection / first_area


def _is_heading(text: str, spans: list[dict[str, Any]]) -> bool:
    compact = " ".join(text.split())
    if not compact or len(compact) > 140 or "\n" in text:
        return False
    if re.match(r"^(?:\d+(?:\.\d+)*|[A-Z][A-Z0-9-]*)[.)]?\s+", compact):
        return True
    return bool(spans) and all(int(span.get("flags", 0)) & 16 for span in spans)


def _classify_block(text: str, spans: list[dict[str, Any]] | None = None) -> str:
    lowered = text.casefold()
    if any(
        marker in lowered
        for marker in (
            "application circuit",
            "typical application",
            "reference design",
            "schematic",
            "应用电路",
            "典型应用",
            "参考设计",
            "原理图",
        )
    ):
        return "application_circuit"
    if re.search(r"\b(?:pin|pinout|vcc|vdd|gnd|vio|canh|canl)\b", lowered) and (
        re.search(r"\bpin\s*\d+", lowered) or "pinout" in lowered
    ):
        return "pin_description"
    if _is_heading(text, spans or []):
        return "heading"
    if "\t" in text or re.search(r"\s{3,}\S+\s{3,}", text):
        return "unparsed"
    return "paragraph"


def _block(
    *,
    text: str,
    block_type: str,
    reading_order: int,
    bbox: list[float] | None,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    text = _normalize_text(text)
    return {
        "block_index": reading_order,
        "block_type": block_type,
        "reading_order": reading_order,
        "text_content": text,
        "text_sha256": _sha256(text),
        "bbox": bbox,
        "location_status": "available" if bbox else "location_unavailable",
        "provenance": provenance,
    }


def _table_rows(table: Any) -> list[list[Any]] | None:
    try:
        rows = table.extract()
    except Exception:  # pragma: no cover - provider version differences
        return None
    if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
        return None
    return rows


def _table_provenance(table: Any, rows: list[list[Any]]) -> dict[str, Any]:
    raw_cells = list(getattr(table, "cells", []) or [])
    width = max((len(row) for row in rows), default=0)
    structured_rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        cells: list[dict[str, Any]] = []
        for column_index, value in enumerate(row):
            flat_index = row_index * width + column_index
            cell_bbox = _valid_bbox(raw_cells[flat_index]) if flat_index < len(raw_cells) else None
            cells.append(
                {
                    "column_index": column_index,
                    "text": "" if value is None else str(value),
                    "bbox": cell_bbox,
                    "location_status": "available" if cell_bbox else "location_unavailable",
                }
            )
        structured_rows.append({"row_index": row_index, "cells": cells})
    return {
        "provider": "PyMuPDF.find_tables",
        "rows": structured_rows,
    }


def _pymupdf_pages(
    pdf_path: Path, base_pages: list[dict[str, Any]], fitz: Any
) -> list[list[dict[str, Any]]]:
    document = fitz.open(str(pdf_path))
    try:
        if len(document) != len(base_pages):
            raise ValueError("page_count_mismatch")
        pages: list[list[dict[str, Any]]] = []
        for page in document:
            candidates: list[dict[str, Any]] = []
            table_boxes: list[list[float]] = []
            try:
                finder = getattr(page, "find_tables", None)
                tables = finder() if callable(finder) else None
                for table in list(getattr(tables, "tables", []) or []):
                    rows = _table_rows(table)
                    table_bbox = _valid_bbox(getattr(table, "bbox", None))
                    if not rows or not table_bbox:
                        continue
                    table_boxes.append(table_bbox)
                    table_text = "\n".join(
                        " | ".join("" if value is None else str(value) for value in row)
                        for row in rows
                    )
                    candidates.append(
                        {
                            "text": table_text,
                            "block_type": "table",
                            "bbox": table_bbox,
                            "provenance": _table_provenance(table, rows),
                        }
                    )
            except Exception:  # pragma: no cover - provider/parser differences
                # A table parser failure must not turn ordinary text into a
                # fabricated table.  The text-block path below remains valid.
                pass

            try:
                text_dict = page.get_text("dict", sort=True)
            except TypeError:  # older PyMuPDF without sort keyword
                text_dict = page.get_text("dict")
            for raw_block in list(text_dict.get("blocks", []) or []):
                if raw_block.get("type") != 0:
                    continue
                lines = list(raw_block.get("lines", []) or [])
                spans = [span for line in lines for span in list(line.get("spans", []) or [])]
                text = "\n".join(
                    "".join(
                        str(span.get("text") or "") for span in list(line.get("spans", []) or [])
                    )
                    for line in lines
                )
                text = _normalize_text(text)
                if not text:
                    continue
                bbox = _valid_bbox(raw_block.get("bbox"))
                if any(
                    _bbox_intersection_ratio(bbox, table_box) >= 0.6 for table_box in table_boxes
                ):
                    continue
                candidates.append(
                    {
                        "text": text,
                        "block_type": _classify_block(text, spans),
                        "bbox": bbox,
                        "provenance": {"provider": "PyMuPDF.get_text", "span_count": len(spans)},
                    }
                )
            candidates.sort(
                key=lambda item: ((item["bbox"] or [0, 0])[1], (item["bbox"] or [0, 0])[0])
            )
            page_blocks = []
            for order, item in enumerate(candidates, start=1):
                page_blocks.append(
                    _block(
                        text=item["text"],
                        block_type=item["block_type"],
                        reading_order=order,
                        bbox=item["bbox"],
                        provenance=item["provenance"],
                    )
                )
            pages.append(page_blocks)
        return pages
    finally:
        document.close()


def _pypdf_fallback_pages(base_pages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    pages: list[list[dict[str, Any]]] = []
    for page in base_pages:
        blocks: list[dict[str, Any]] = []
        for _order, line in enumerate(
            (line.strip() for line in str(page["text_content"]).splitlines()), start=1
        ):
            if not line:
                continue
            block_type = _classify_block(line)
            if block_type == "unparsed":
                provenance = {
                    "provider": "pypdf-text",
                    "reason": "layout_provider_unavailable",
                    "table_recovery": "unparsed",
                }
            else:
                provenance = {"provider": "pypdf-text", "reason": "layout_provider_unavailable"}
            blocks.append(
                _block(
                    text=line,
                    block_type=block_type,
                    reading_order=len(blocks) + 1,
                    bbox=None,
                    provenance=provenance,
                )
            )
        pages.append(blocks)
    return pages


def extract_layout_document(
    pdf_path: Path,
    *,
    base_pages: list[dict[str, Any]] | None = None,
) -> LayoutExtractionResult:
    """Extract typed blocks while keeping pypdf text as the stable page source."""

    if base_pages is None:
        reader = PdfReader(str(pdf_path))
        base_pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = _normalize_text(page.extract_text() or "")
            base_pages.append(
                {
                    "page_number": page_number,
                    "text_content": text,
                    "text_sha256": _sha256(text),
                }
            )
    provider = _load_pymupdf()
    extraction_version = PYPDF_LAYOUT_FALLBACK_VERSION
    provider_data: dict[str, Any] = {
        "name": "pypdf-text",
        "version": "fallback",
        "layout_available": False,
    }
    try:
        if provider is not None:
            block_pages = _pymupdf_pages(pdf_path, base_pages, provider)
            extraction_version = PYMUPDF_EXTRACTION_VERSION
            provider_data = {
                "name": "PyMuPDF",
                "version": str(
                    getattr(provider, "__version__", None)
                    or getattr(provider, "pymupdf_version", None)
                    or "unknown"
                ),
                "layout_available": True,
            }
        else:
            block_pages = _pypdf_fallback_pages(base_pages)
    except Exception as exc:  # pragma: no cover - exercised by provider failures
        block_pages = _pypdf_fallback_pages(base_pages)
        provider_data["fallback_reason"] = type(exc).__name__

    pages: list[dict[str, Any]] = []
    for page, blocks in zip(base_pages, block_pages, strict=True):
        quality = _native_text_quality(str(page["text_content"]))
        pages.append(
            {
                **page,
                "native_text_quality": quality,
                "ocr_status": _ocr_status(str(quality["quality"])),
                "blocks": blocks,
            }
        )
    return {
        "pages": pages,
        "extraction_version": extraction_version,
        "provider": provider_data,
        "ocr_policy": "native_text_first_selective_offline_opt_in",
    }


def layout_block_data(block: Any) -> dict[str, Any]:
    """Return a public, non-factual layout preview for one persisted block."""

    return {
        "id": block.id,
        "block_type": block.block_type,
        "reading_order": block.reading_order,
        "text": block.text_content,
        "text_sha256": block.text_sha256,
        "bbox": block.bbox,
        "location_status": block.location_status,
        "provenance": block.provenance,
        "extractor_version": block.extractor_version,
        "source_sha256": block.source_sha256,
    }
