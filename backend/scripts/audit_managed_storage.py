"""Read-only DB-to-filesystem integrity audit for managed MaterialBrain files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.models import Attachment, EngineeringDocument


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_join(root: Path, relative: str) -> Path | None:
    key = PurePosixPath(str(relative or "").replace("\\", "/"))
    if not relative or key.is_absolute() or ".." in key.parts:
        return None
    path = root.joinpath(*key.parts).resolve()
    return path if path.is_relative_to(root) else None


def audit_status(failures: list[dict], orphans: list[str]) -> dict[str, str]:
    """Keep referential integrity and orphan policy independently visible."""

    integrity_status = "PASS" if not failures else "FAIL"
    orphan_status = "PASS" if not orphans else "WARN"
    overall_status = (
        "FAIL" if integrity_status == "FAIL" else "WARN" if orphan_status == "WARN" else "PASS"
    )
    return {
        "status": overall_status,
        "integrity_status": integrity_status,
        "orphan_status": orphan_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    storage_root = args.storage_root.resolve()
    attachment_root = (storage_root / "attachments").resolve()
    failures: list[dict] = []
    referenced: set[Path] = set()

    with SessionLocal() as db:
        database_query = (
            "select current_database()"
            if db.bind.dialect.name == "postgresql"
            else "select 'sqlite'"
        )
        database = str(db.execute(text(database_query)).scalar_one())
        if db.bind.dialect.name == "postgresql" and database != args.expected_database_name:
            raise SystemExit(f"database identity mismatch: {database}")

        attachments = list(db.scalars(select(Attachment)))
        documents = list(db.scalars(select(EngineeringDocument)))

        for item in attachments:
            path = safe_join(attachment_root, item.stored_name)
            if path is None:
                failures.append({"kind": "attachment", "id": item.id, "code": "unsafe_path"})
                continue
            referenced.add(path)
            if not path.is_file():
                failures.append({"kind": "attachment", "id": item.id, "code": "missing"})
                continue
            if path.stat().st_size != item.size:
                failures.append({"kind": "attachment", "id": item.id, "code": "size_mismatch"})
            if digest(path) != str(item.sha256 or "").lower():
                failures.append({"kind": "attachment", "id": item.id, "code": "sha256_mismatch"})

        managed_documents = [item for item in documents if str(item.storage_key or "").strip()]
        for item in managed_documents:
            path = safe_join(storage_root, item.storage_key)
            if path is None:
                failures.append(
                    {
                        "kind": "engineering_document",
                        "id": item.id,
                        "source_type": item.source_type,
                        "code": "unsafe_path",
                    }
                )
                continue
            referenced.add(path)
            if not path.is_file():
                failures.append(
                    {
                        "kind": "engineering_document",
                        "id": item.id,
                        "source_type": item.source_type,
                        "code": "missing",
                    }
                )
                continue
            if digest(path) != str(item.file_sha256 or "").lower():
                failures.append(
                    {
                        "kind": "engineering_document",
                        "id": item.id,
                        "source_type": item.source_type,
                        "code": "sha256_mismatch",
                    }
                )

    managed_roots = [attachment_root, (storage_root / "evidence" / "vendor").resolve()]
    orphans = []
    for root in managed_roots:
        if not root.is_dir():
            continue
        for path in sorted(item.resolve() for item in root.rglob("*") if item.is_file()):
            if path.name in {".gitkeep", ".keep"}:
                continue
            if path not in referenced:
                orphans.append(str(path.relative_to(storage_root)))

    status = audit_status(failures, orphans)
    payload = {
        **status,
        "database": database,
        "storage_root": str(storage_root),
        "attachment_rows": len(attachments),
        "managed_document_rows": len(managed_documents),
        "vendor_document_rows": sum(
            item.source_type == "vendor_upload" for item in managed_documents
        ),
        "referenced_file_count": len(referenced),
        "failures": failures,
        "orphans": orphans,
        "write_scope": "none",
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
