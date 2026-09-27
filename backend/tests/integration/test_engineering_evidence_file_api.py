import hashlib
import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import EngineeringDocument


def _create_document(*, material_id: int, user_id: int, storage_key: str, payload: bytes) -> int:
    with SessionLocal() as db:
        document = EngineeringDocument(
            document_key=f"REAL-PDF-{uuid.uuid4().hex}",
            scope_type="material",
            material_id=material_id,
            document_type="datasheet",
            title="Real vendor datasheet",
            manufacturer="Vendor",
            document_revision="R1",
            source_type="vendor_upload",
            source_url="",
            original_filename="vendor.pdf",
            storage_key=storage_key,
            file_sha256=hashlib.sha256(payload).hexdigest(),
            page_count=1,
            status="current",
            ingest_status="ready",
            extraction_version="test",
            created_by_id=user_id,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        return document.id


def test_authenticated_evidence_pdf_is_inline_and_sha_verified(
    client, admin, material, tmp_path, monkeypatch
):
    storage = tmp_path / "storage"
    payload = b"%PDF-1.4\n% test vendor evidence\n"
    pdf = storage / "evidence" / "vendor" / f"{hashlib.sha256(payload).hexdigest()}.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(payload)
    monkeypatch.setattr(settings, "engineering_evidence_storage_dir", storage)

    document_id = _create_document(
        material_id=material["id"],
        user_id=admin["id"],
        storage_key=f"evidence/vendor/{hashlib.sha256(payload).hexdigest()}.pdf",
        payload=payload,
    )
    response = client.get(f"/api/v1/evidence-documents/{document_id}/file")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["x-evidence-sha256"] == hashlib.sha256(payload).hexdigest()
    assert response.content == payload


def test_evidence_pdf_rejects_intake_staging_path(
    client, admin, material, tmp_path, monkeypatch
):
    storage = tmp_path / "storage"
    payload = b"%PDF-1.4\n% staging only\n"
    pdf = storage / "imports" / "phase2_5_4" / "vendor.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(payload)
    monkeypatch.setattr(settings, "engineering_evidence_storage_dir", storage)

    document_id = _create_document(
        material_id=material["id"],
        user_id=admin["id"],
        storage_key="imports/phase2_5_4/vendor.pdf",
        payload=payload,
    )
    response = client.get(f"/api/v1/evidence-documents/{document_id}/file")

    assert response.status_code == 404
    assert response.json()["code"] == "EVIDENCE_FILE_UNAVAILABLE"


def test_evidence_pdf_rejects_storage_path_traversal(
    client, admin, material, tmp_path, monkeypatch
):
    storage = tmp_path / "storage"
    storage.mkdir()
    payload = b"%PDF-1.4\n% outside\n"
    (tmp_path / "outside.pdf").write_bytes(payload)
    monkeypatch.setattr(settings, "engineering_evidence_storage_dir", storage)

    document_id = _create_document(
        material_id=material["id"],
        user_id=admin["id"],
        storage_key="../outside.pdf",
        payload=payload,
    )
    response = client.get(f"/api/v1/evidence-documents/{document_id}/file")

    assert response.status_code == 404
    assert response.json()["code"] == "EVIDENCE_FILE_UNAVAILABLE"
