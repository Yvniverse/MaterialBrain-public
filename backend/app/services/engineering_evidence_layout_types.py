from __future__ import annotations

from typing import Any, TypedDict


class LayoutExtractionResult(TypedDict):
    pages: list[dict[str, Any]]
    extraction_version: str
    provider: dict[str, Any]
    ocr_policy: str
