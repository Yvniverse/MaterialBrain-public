import hashlib
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, require
from app.core.config import settings
from app.core.exceptions import BusinessError
from app.models import (
    EngineeringDocument,
    EngineeringDocumentBlock,
    EngineeringDocumentPage,
    EvidenceAnchor,
    Material,
)
from app.schemas.evidence import EvidenceCompareRequest, EvidenceSearchRequest
from app.services.engineering_evidence import (
    EvidenceComparisonService,
    EvidenceRetrievalService,
    citation_data,
    is_synthetic_fixture_document,
)
from app.services.evidence_layout import layout_block_data

router = APIRouter(tags=["工程证据"])


def _document_file_path(document: EngineeringDocument) -> Path:
    if (
        is_synthetic_fixture_document(document)
        or document.source_type != "vendor_upload"
        or document.ingest_status != "ready"
    ):
        raise BusinessError("EVIDENCE_DOCUMENT_NOT_FOUND", "工程证据文档不存在", 404)

    key = PurePosixPath(str(document.storage_key or "").replace("\\", "/"))
    expected_name = f"{str(document.file_sha256 or '').lower()}.pdf"
    if (
        not document.storage_key
        or key.is_absolute()
        or ".." in key.parts
        or key.parts[:2] != ("evidence", "vendor")
        or key.name != expected_name
    ):
        raise BusinessError("EVIDENCE_FILE_UNAVAILABLE", "工程证据原始文件不可用", 404)

    root = settings.engineering_evidence_storage_dir.resolve()
    path = (root.joinpath(*key.parts)).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != ".pdf" or not path.is_file():
        raise BusinessError("EVIDENCE_FILE_UNAVAILABLE", "工程证据原始文件不可用", 404)
    if hashlib.sha256(path.read_bytes()).hexdigest() != str(document.file_sha256).lower():
        raise BusinessError("EVIDENCE_FILE_INTEGRITY_FAILED", "工程证据原始文件校验失败", 409)
    return path


def _document_data(document: EngineeringDocument) -> dict:
    return {
        "id": document.id,
        "document_key": document.document_key,
        "scope_type": document.scope_type,
        "material_id": document.material_id,
        "product_revision_id": document.product_revision_id,
        "document_type": document.document_type,
        "title": document.title,
        "manufacturer": document.manufacturer,
        "document_revision": document.document_revision,
        "document_date": document.document_date,
        "original_filename": document.original_filename,
        "file_sha256": document.file_sha256,
        "page_count": document.page_count,
        "status": document.status,
        "supersedes_document_id": document.supersedes_document_id,
        "ingest_status": document.ingest_status,
        "extraction_version": document.extraction_version,
        "synthetic_fixture": is_synthetic_fixture_document(document),
    }


@router.get(
    "/materials/{material_id}/evidence-documents",
    dependencies=[Depends(require("material:view"))],
)
def list_material_evidence_documents(
    material_id: int,
    db: DB,
    user: CurrentUser,
    include_history: bool = Query(default=False),
):
    del user
    material = db.get(Material, material_id)
    if material is None or material.is_deleted:
        raise BusinessError("MATERIAL_NOT_FOUND", "物料不存在", 404)
    statuses = ["current", "superseded", "withdrawn"] if include_history else ["current"]
    rows = list(
        db.scalars(
            select(EngineeringDocument)
            .where(
                EngineeringDocument.scope_type == "material",
                EngineeringDocument.material_id == material_id,
                EngineeringDocument.status.in_(statuses),
                EngineeringDocument.source_type != "synthetic_fixture",
            )
            .order_by(EngineeringDocument.document_date.desc(), EngineeringDocument.id.desc())
        ).all()
    )
    return {"items": [_document_data(item) for item in rows], "count": len(rows)}


@router.get(
    "/evidence-documents/{document_id}",
    dependencies=[Depends(require("material:view"))],
)
def get_evidence_document(document_id: int, db: DB, user: CurrentUser):
    del user
    document = db.get(EngineeringDocument, document_id)
    if document is None:
        raise BusinessError("EVIDENCE_DOCUMENT_NOT_FOUND", "工程证据文档不存在", 404)
    if is_synthetic_fixture_document(document):
        raise BusinessError("EVIDENCE_DOCUMENT_NOT_FOUND", "工程证据文档不存在", 404)
    return _document_data(document)


@router.get(
    "/evidence-documents/{document_id}/file",
    dependencies=[Depends(require("material:view"))],
)
def open_evidence_document_file(document_id: int, db: DB, user: CurrentUser):
    del user
    document = db.get(EngineeringDocument, document_id)
    if document is None:
        raise BusinessError("EVIDENCE_DOCUMENT_NOT_FOUND", "工程证据文档不存在", 404)
    path = _document_file_path(document)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "X-Evidence-SHA256": str(document.file_sha256).lower(),
        },
    )


@router.get(
    "/evidence-documents/{document_id}/pages",
    dependencies=[Depends(require("material:view"))],
)
def list_evidence_document_pages(document_id: int, db: DB, user: CurrentUser):
    del user
    document = db.get(EngineeringDocument, document_id)
    if document is None:
        raise BusinessError("EVIDENCE_DOCUMENT_NOT_FOUND", "工程证据文档不存在", 404)
    if is_synthetic_fixture_document(document):
        raise BusinessError("EVIDENCE_DOCUMENT_NOT_FOUND", "工程证据文档不存在", 404)
    rows = list(
        db.execute(
            select(EngineeringDocumentPage, EvidenceAnchor)
            .outerjoin(
                EvidenceAnchor,
                EvidenceAnchor.document_page_id == EngineeringDocumentPage.id,
            )
            .where(EngineeringDocumentPage.document_id == document_id)
            .order_by(EngineeringDocumentPage.page_number, EvidenceAnchor.id)
        ).all()
    )
    page_ids = list({page.id for page, _anchor in rows})
    blocks = (
        list(
            db.scalars(
                select(EngineeringDocumentBlock)
                .where(
                    EngineeringDocumentBlock.document_page_id.in_(page_ids),
                    EngineeringDocumentBlock.extractor_version == document.extraction_version,
                    EngineeringDocumentBlock.source_sha256 == document.file_sha256,
                )
                .order_by(
                    EngineeringDocumentBlock.document_page_id,
                    EngineeringDocumentBlock.reading_order,
                )
            ).all()
        )
        if page_ids
        else []
    )
    blocks_by_page: dict[int, list[EngineeringDocumentBlock]] = {}
    for block in blocks:
        blocks_by_page.setdefault(block.document_page_id, []).append(block)
    pages: dict[int, dict] = {}
    for page, anchor in rows:
        item = pages.setdefault(
            page.id,
            {
                "id": page.id,
                "page_number": page.page_number,
                "text_sha256": page.text_sha256,
                "native_text_quality": page.native_text_quality,
                "ocr_status": page.ocr_status,
                "layout_blocks": [
                    layout_block_data(block) for block in blocks_by_page.get(page.id, [])
                ],
                "anchors": [],
            },
        )
        if anchor is not None:
            item["anchors"].append(
                citation_data(
                    anchor,
                    page,
                    document,
                    layout_blocks=blocks_by_page.get(page.id, []),
                )
            )
    return {"items": list(pages.values()), "count": len(pages)}


@router.post(
    "/evidence/search",
    dependencies=[Depends(require("material:view"))],
)
def search_engineering_evidence(
    payload: EvidenceSearchRequest,
    db: DB,
    user: CurrentUser,
):
    del user
    return EvidenceRetrievalService(db).search_material_evidence(
        material_ids=payload.material_ids,
        query=payload.query,
        include_superseded=payload.include_superseded,
        limit=payload.limit,
    )


@router.post(
    "/evidence/compare",
    dependencies=[Depends(require("material:view"))],
)
def compare_engineering_evidence(
    payload: EvidenceCompareRequest,
    db: DB,
    user: CurrentUser,
):
    del user
    return EvidenceComparisonService(db).compare(
        first_material_id=payload.first_material_id,
        second_material_id=payload.second_material_id,
        fields=list(payload.fields),
    )
