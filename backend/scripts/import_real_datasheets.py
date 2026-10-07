"""Validate or idempotently ingest the Phase 2.5.4 vendor datasheet archive."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import EngineeringDocument, EngineeringDocumentPage, EvidenceAnchor, Material, User
from app.services.engineering_evidence import (
    EngineeringEvidenceIngestionService,
    extract_pdf_pages,
    normalize_page_text,
    sha256_bytes,
)

DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "sample_data"
    / "v2_4"
    / "real_datasheet_manifest_v1.json"
)


def _safe_archive_path(input_root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"Unsafe manifest archive path: {value}")
    resolved_root = input_root.resolve()
    resolved = input_root.joinpath(*relative.parts).resolve()
    if resolved_root not in resolved.parents:
        raise RuntimeError(f"Manifest path escaped input root: {value}")
    return resolved


def validate_document(definition: dict, path: Path) -> dict:
    if not path.is_file():
        raise RuntimeError(f"Datasheet not found: {path}")
    actual_sha = sha256_bytes(path.read_bytes())
    if actual_sha != definition["sha256"]:
        raise RuntimeError(f"SHA mismatch: {path.name}")
    pages = extract_pdf_pages(path)
    if len(pages) != int(definition["page_count"]):
        raise RuntimeError(f"Page-count mismatch: {path.name}")
    for anchor in definition["anchors"]:
        text = normalize_page_text(pages[int(anchor["page"]) - 1]["text_content"])
        folded = " ".join(text.split()).casefold()
        missing = [
            phrase
            for phrase in anchor["expected_phrases"]
            if " ".join(phrase.split()).casefold() not in folded
        ]
        if missing:
            raise RuntimeError(
                f"Content verification failed: {path.name} page={anchor['page']} missing={missing}"
            )
    return {
        "document_key": definition["document_key"],
        "material_code": definition["material_code"],
        "filename": path.name,
        "sha256": actual_sha,
        "page_count": len(pages),
        "anchor_count": len(definition["anchors"]),
        "content_verified": True,
    }


def _counts(db) -> dict[str, int]:
    return {
        "documents": int(db.scalar(select(func.count()).select_from(EngineeringDocument)) or 0),
        "pages": int(db.scalar(select(func.count()).select_from(EngineeringDocumentPage)) or 0),
        "anchors": int(db.scalar(select(func.count()).select_from(EvidenceAnchor)) or 0),
    }


def _apply_material_updates(db, definitions: list[dict]) -> int:
    changed = 0
    for definition in definitions:
        material = db.scalar(
            select(Material).where(
                Material.code == definition["material_code"],
                Material.is_deleted.is_(False),
            )
        )
        if material is None:
            raise RuntimeError(
                f"Vendor-evidence material update target is missing: "
                f"{definition['material_code']}"
            )
        before = (material.specification, material.tags, material.attributes)
        material.specification = definition.get("specification", material.specification)
        material.tags = list(definition.get("tags", material.tags or []))
        material.attributes = {
            **dict(material.attributes or {}),
            **dict(definition.get("attributes") or {}),
        }
        after = (material.specification, material.tags, material.attributes)
        changed += int(before != after)
    db.commit()
    return changed


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
    if args.apply and os.getenv("REAL_DATASHEET_IMPORT_ENABLED", "false").casefold() != "true":
        raise RuntimeError("Set REAL_DATASHEET_IMPORT_ENABLED=true for --apply")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("content_verified") is not True or manifest.get("pdf_count") != len(
        manifest.get("documents") or []
    ):
        raise RuntimeError("Datasheet manifest is incomplete")
    validated = []
    paths: dict[str, Path] = {}
    for definition in manifest["documents"]:
        path = _safe_archive_path(args.input_root, definition["archive_path"])
        paths[definition["document_key"]] = path
        validated.append(validate_document(definition, path))

    if not args.apply:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "archive": manifest["archive"],
                    "documents": validated,
                    "missing_priority_targets": manifest["missing_priority_targets"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    with SessionLocal() as db:
        operator = db.scalar(
            select(User).where(
                User.username == args.operator,
                User.is_active.is_(True),
                User.is_deleted.is_(False),
            )
        )
        if operator is None:
            raise RuntimeError("Evidence import operator is unavailable")
        before = _counts(db)
        created = 0
        reused = 0
        for definition in manifest["documents"]:
            document, was_created = EngineeringEvidenceIngestionService(
                db,
                operator,
                "phase2-5-4-real-datasheet-import",
            ).ingest_datasheet(
                definition,
                paths[definition["document_key"]],
                storage_key=f"imports/phase2_5_4/{definition['archive_path']}",
            )
            del document
            created += int(was_created)
            reused += int(not was_created)
        updated_materials = _apply_material_updates(db, manifest.get("material_updates") or [])
        after = _counts(db)
    print(
        json.dumps(
            {
                "dry_run": False,
                "validated_documents": len(validated),
                "created_documents": created,
                "reused_documents": reused,
                "updated_materials": updated_materials,
                "before": before,
                "after": after,
                "delta": {key: after[key] - before[key] for key in before},
                "missing_priority_targets": manifest["missing_priority_targets"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
