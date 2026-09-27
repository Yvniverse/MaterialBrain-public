"""Zero-model, read-only smoke for the Phase 2.5.5 vendor datasheet baseline."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import (
    EngineeringDocument,
    EngineeringDocumentPage,
    EvidenceAnchor,
    Material,
)
from app.services.engineering_evidence import EvidenceRetrievalService


def _material_id(db, code: str) -> int:
    value = db.scalar(
        select(Material.id).where(Material.code == code, Material.is_deleted.is_(False))
    )
    if value is None:
        raise SystemExit(f"Vendor evidence smoke material is missing: {code}")
    return int(value)


def _managed_vendor_files_match(documents: list[EngineeringDocument]) -> bool:
    root = settings.engineering_evidence_storage_dir.resolve()
    for document in documents:
        key = PurePosixPath(str(document.storage_key or "").replace("\\", "/"))
        expected_name = f"{str(document.file_sha256 or '').lower()}.pdf"
        if key.parts[:2] != ("evidence", "vendor") or key.name != expected_name:
            return False
        path = (root.joinpath(*key.parts)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_name[:-4]:
            return False
    return True


def main() -> int:
    with SessionLocal() as db:
        before = {
            "documents": int(
                db.scalar(select(func.count()).select_from(EngineeringDocument)) or 0
            ),
            "pages": int(
                db.scalar(select(func.count()).select_from(EngineeringDocumentPage)) or 0
            ),
            "anchors": int(db.scalar(select(func.count()).select_from(EvidenceAnchor)) or 0),
        }
        documents = list(db.scalars(select(EngineeringDocument)).all())
        retrieval = EvidenceRetrievalService(db)
        mcp = retrieval.search_material_evidence(
            material_ids=[_material_id(db, "PORT-CAN-MCP2562FD")],
            query="Pin 5 VIO",
            limit=3,
        )
        mcp_purpose = retrieval.search_material_evidence(
            material_ids=[_material_id(db, "PORT-CAN-MCP2562FD")],
            query="为什么 MCP2562FD 有 VIO？",
            limit=3,
        )
        tcan = retrieval.search_material_evidence(
            material_ids=[_material_id(db, "PORT-CAN-TCAN1044")],
            query="supply voltage VIO 1.7 5.5",
            limit=3,
        )
        tcan_material = db.get(Material, _material_id(db, "PORT-CAN-TCAN1044"))
        sn65 = retrieval.search_material_evidence(
            material_ids=[_material_id(db, "PORT-CAN-SN65HVD230")],
            query="interface CAN 1 Mbps",
            limit=3,
        )
        after = {
            "documents": int(
                db.scalar(select(func.count()).select_from(EngineeringDocument)) or 0
            ),
            "pages": int(
                db.scalar(select(func.count()).select_from(EngineeringDocumentPage)) or 0
            ),
            "anchors": int(db.scalar(select(func.count()).select_from(EvidenceAnchor)) or 0),
        }

    results = {
        "exact_counts": before == {"documents": 8, "pages": 722, "anchors": 10},
        "all_documents_are_vendor_datasheets": len(documents) == 8
        and all(
            item.source_type == "vendor_upload"
            and item.document_type == "datasheet"
            and item.ingest_status == "ready"
            for item in documents
        ),
        "managed_vendor_files_match_manifest_sha": _managed_vendor_files_match(documents),
        "synthetic_fixture_documents": sum(
            item.source_type == "synthetic_fixture" for item in documents
        ),
        "mcp_pin_5_vio_supported": mcp["evidence_coverage"] == "supported"
        and any(
            fact.get("field") == "pin"
            and fact.get("number") == 5
            and fact.get("name") == "VIO"
            for fact in mcp["facts"]
        ),
        "mcp_vio_purpose_supported": mcp_purpose["evidence_coverage"] == "supported"
        and any(
            fact.get("field") == "purpose"
            and "内部电平转换" in str(fact.get("value"))
            and "CAN 控制器" in str(fact.get("value"))
            for fact in mcp_purpose["facts"]
        ),
        "tcan_vio_range_supported": tcan["evidence_coverage"] == "supported"
        and any(
            fact.get("field") == "supply_voltage"
            and fact.get("min") == 1.7
            and fact.get("max") == 5.5
            for fact in tcan["facts"]
        ),
        "tcan_can_fd_structured_capability": tcan_material is not None
        and "CAN-FD" in (tcan_material.attributes or {}).get("interfaces", []),
        "sn65_classic_can_supported": sn65["evidence_coverage"] == "supported"
        and any(
            fact.get("field") == "interface" and fact.get("values") == ["CAN"]
            for fact in sn65["facts"]
        ),
        "citations_are_real_vendor_pages": all(
            citation.get("synthetic_fixture") is False
            and citation.get("page") == 1
            and citation.get("file_sha256")
            for result in (mcp, tcan, sn65)
            for citation in result["citations"]
        ),
        "model_calls": 0,
        "ocr_calls": 0,
        "read_only_counts_unchanged": before == after,
    }
    print(json.dumps(results, ensure_ascii=False, indent=2))
    required = (
        "exact_counts",
        "all_documents_are_vendor_datasheets",
        "managed_vendor_files_match_manifest_sha",
        "mcp_pin_5_vio_supported",
        "mcp_vio_purpose_supported",
        "tcan_vio_range_supported",
        "tcan_can_fd_structured_capability",
        "sn65_classic_can_supported",
        "citations_are_real_vendor_pages",
        "read_only_counts_unchanged",
    )
    return 0 if results["synthetic_fixture_documents"] == 0 and all(
        results[key] for key in required
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
