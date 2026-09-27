"""Copy verified vendor datasheets from intake staging into managed evidence storage.

Dry-run is the default. The operation is idempotent and only updates an existing
EngineeringDocument when document_key, SHA-256, scope, readiness and source
provenance already match the manifest-bound vendor document.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import EngineeringDocument, Material, User
from app.services.audit import add_audit
from app.services.engineering_evidence import sha256_bytes
from scripts.import_real_datasheets import DEFAULT_MANIFEST, _safe_archive_path, validate_document

MANAGED_SUBDIR = PurePosixPath("evidence/vendor")


def _managed_target(storage_root: Path, sha256: str) -> tuple[str, Path]:
    normalized_sha = str(sha256).lower()
    if len(normalized_sha) != 64 or any(char not in "0123456789abcdef" for char in normalized_sha):
        raise RuntimeError(f"Invalid manifest SHA-256: {sha256}")
    relative = MANAGED_SUBDIR / f"{normalized_sha}.pdf"
    root = storage_root.resolve()
    target = root.joinpath(*relative.parts).resolve()
    if root not in target.parents:
        raise RuntimeError("Managed evidence path escaped storage root")
    return relative.as_posix(), target


def _copy_verified(source: Path, target: Path, expected_sha: str) -> bool:
    if target.exists():
        if not target.is_file() or sha256_bytes(target.read_bytes()) != expected_sha:
            raise RuntimeError(f"Managed evidence target has unexpected content: {target}")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".pdf.tmp")
    if temporary.exists():
        temporary.unlink()
    shutil.copyfile(source, temporary)
    if sha256_bytes(temporary.read_bytes()) != expected_sha:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Managed evidence copy SHA mismatch: {source.name}")
    os.replace(temporary, target)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--operator", default="admin")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    actual_database = make_url(settings.database_url).database
    if actual_database != args.expected_database_name:
        raise RuntimeError(
            f"Refusing unexpected database: expected={args.expected_database_name!r} "
            f"actual={actual_database!r}"
        )
    if args.apply and (
        os.getenv("REAL_DATASHEET_STORAGE_REHOME_ENABLED", "false").casefold() != "true"
    ):
        raise RuntimeError("Set REAL_DATASHEET_STORAGE_REHOME_ENABLED=true for --apply")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    storage_root = settings.engineering_evidence_storage_dir
    plans = []
    for definition in manifest["documents"]:
        source = _safe_archive_path(args.input_root, definition["archive_path"])
        validated = validate_document(definition, source)
        storage_key, target = _managed_target(storage_root, validated["sha256"])
        plans.append((definition, source, validated, storage_key, target))

    if not args.apply:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "storage_root": str(storage_root),
                    "documents": [
                        {
                            "document_key": definition["document_key"],
                            "source": str(source),
                            "source_sha256": validated["sha256"],
                            "target_storage_key": storage_key,
                            "target_exists": target.exists(),
                            "target_sha256": (
                                sha256_bytes(target.read_bytes()) if target.is_file() else None
                            ),
                        }
                        for definition, source, validated, storage_key, target in plans
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    copied = 0
    updated = 0
    with SessionLocal() as db:
        operator = db.scalar(
            select(User).where(
                User.username == args.operator,
                User.is_active.is_(True),
                User.is_deleted.is_(False),
            )
        )
        if operator is None:
            raise RuntimeError("Evidence storage migration operator is unavailable")
        for definition, source, _validated, storage_key, target in plans:
            document = db.scalar(
                select(EngineeringDocument).where(
                    EngineeringDocument.document_key == definition["document_key"],
                    EngineeringDocument.file_sha256 == definition["sha256"],
                    EngineeringDocument.source_type == "vendor_upload",
                    EngineeringDocument.document_type == "datasheet",
                    EngineeringDocument.ingest_status == "ready",
                )
            )
            if document is None:
                raise RuntimeError(
                    "Manifest-bound ready vendor document is not present: "
                    f"{definition['document_key']}"
                )
            material = db.get(Material, document.material_id) if document.material_id else None
            if definition["scope_type"] != document.scope_type or (
                definition["scope_type"] == "material"
                and (material is None or material.code != definition["material_code"])
            ):
                raise RuntimeError(f"Evidence scope mismatch: {definition['document_key']}")
            copied += int(_copy_verified(source, target, definition["sha256"]))
            if document.storage_key != storage_key:
                before = document.storage_key
                document.storage_key = storage_key
                updated += 1
                add_audit(
                    db,
                    operator.id,
                    "engineering_evidence.rehome_vendor_file",
                    "engineering_document",
                    str(document.id),
                    "phase2-5-5-evidence-storage-rehome",
                    before={"storage_key": before},
                    after={"storage_key": storage_key, "file_sha256": document.file_sha256},
                )
        db.commit()

    print(
        json.dumps(
            {
                "dry_run": False,
                "documents": len(plans),
                "copied_files": copied,
                "updated_storage_keys": updated,
                "managed_subdir": MANAGED_SUBDIR.as_posix(),
                "sha256": [validated["sha256"] for _d, _s, validated, _k, _t in plans],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
