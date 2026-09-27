"""Synchronize manifest-backed facts onto existing verified vendor anchors.

This is a narrow metadata migration for an already-ingested, byte-identical
vendor PDF corpus. It never creates documents/pages/anchors and never changes
excerpts. Dry-run is the default. Apply requires an explicit environment gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import (
    EngineeringDocument,
    EngineeringDocumentPage,
    EvidenceAnchor,
    Material,
    User,
)
from app.services.audit import add_audit
from app.services.engineering_evidence import (
    STRUCTURED_FACT_FIELDS,
    _verified_excerpt,
    sha256_bytes,
)
from scripts.import_real_datasheets import DEFAULT_MANIFEST


def _document_scope_matches(db, document: EngineeringDocument, definition: dict) -> bool:
    if definition["scope_type"] != document.scope_type:
        return False
    if document.scope_type == "material":
        material = db.get(Material, document.material_id) if document.material_id else None
        return bool(material and material.code == definition["material_code"])
    return False


def _managed_storage_key(document: EngineeringDocument) -> str:
    return f"evidence/vendor/{str(document.file_sha256).lower()}.pdf"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
    if args.apply and os.getenv("REAL_DATASHEET_ANCHOR_SYNC_ENABLED", "false").casefold() != "true":
        raise RuntimeError("Set REAL_DATASHEET_ANCHOR_SYNC_ENABLED=true for --apply")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    plans: list[dict] = []

    with SessionLocal() as db:
        operator = db.scalar(
            select(User).where(
                User.username == args.operator,
                User.is_active.is_(True),
                User.is_deleted.is_(False),
            )
        )
        if operator is None:
            raise RuntimeError("Evidence manifest-sync operator is unavailable")

        for definition in manifest["documents"]:
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
                    f"Verified vendor document is not present: {definition['document_key']}"
                )
            if not _document_scope_matches(db, document, definition):
                raise RuntimeError(f"Evidence scope mismatch: {definition['document_key']}")
            if document.storage_key != _managed_storage_key(document):
                raise RuntimeError(
                    "Vendor document is not in managed evidence storage: "
                    f"{definition['document_key']}"
                )

            for anchor_definition in definition["anchors"]:
                page_number = int(anchor_definition["page"])
                page = db.scalar(
                    select(EngineeringDocumentPage).where(
                        EngineeringDocumentPage.document_id == document.id,
                        EngineeringDocumentPage.page_number == page_number,
                    )
                )
                if page is None:
                    raise RuntimeError(
                        f"Evidence page missing: {definition['document_key']} p.{page_number}"
                    )
                if sha256_bytes(page.text_content.encode("utf-8")) != page.text_sha256:
                    raise RuntimeError(
                        "Stored evidence page text SHA mismatch: "
                        f"{definition['document_key']} p.{page_number}"
                    )

                _verified_excerpt(page.text_content, list(anchor_definition["expected_phrases"]))
                anchors = list(
                    db.scalars(
                        select(EvidenceAnchor).where(
                            EvidenceAnchor.document_page_id == page.id,
                            EvidenceAnchor.section_title == anchor_definition["section_title"],
                        )
                    ).all()
                )
                if len(anchors) != 1:
                    raise RuntimeError(
                        f"Expected exactly one manifest anchor for {definition['document_key']} "
                        f"p.{page_number} section={anchor_definition['section_title']!r}; "
                        f"found={len(anchors)}"
                    )
                anchor = anchors[0]
                if sha256_bytes(anchor.excerpt_text.encode("utf-8")) != anchor.excerpt_sha256:
                    raise RuntimeError(f"Stored evidence excerpt SHA mismatch: anchor={anchor.id}")
                if anchor.anchor_source == "human":
                    raise RuntimeError(f"Refusing to overwrite human evidence anchor: {anchor.id}")

                facts = list(anchor_definition.get("facts") or [])
                unknown_fields = {
                    fact.get("field")
                    for fact in facts
                    if fact.get("field") not in STRUCTURED_FACT_FIELDS
                }
                if unknown_fields:
                    raise RuntimeError(
                        f"Unsupported manifest fact fields for anchor {anchor.id}: "
                        f"{sorted(str(item) for item in unknown_fields)}"
                    )
                desired_structured = {"facts": facts} if facts else None
                desired_source = "deterministic_parser" if facts else "extracted"
                changed = (
                    anchor.structured_fact != desired_structured
                    or anchor.anchor_source != desired_source
                )
                plans.append(
                    {
                        "document_key": document.document_key,
                        "page": page_number,
                        "anchor_id": anchor.id,
                        "changed": changed,
                        "before_fact_sha256": sha256_bytes(
                            json.dumps(
                                anchor.structured_fact,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ),
                        "after_fact_sha256": sha256_bytes(
                            json.dumps(
                                desired_structured,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ),
                    }
                )
                if args.apply and changed:
                    before = {
                        "structured_fact": anchor.structured_fact,
                        "anchor_source": anchor.anchor_source,
                    }
                    anchor.structured_fact = desired_structured
                    anchor.anchor_source = desired_source
                    add_audit(
                        db,
                        operator.id,
                        "engineering_evidence.sync_manifest_anchor",
                        "evidence_anchor",
                        str(anchor.id),
                        "phase2-5-5-real-datasheet-anchor-sync",
                        before=before,
                        after={
                            "structured_fact": desired_structured,
                            "anchor_source": desired_source,
                            "document_key": document.document_key,
                            "page": page_number,
                            "file_sha256": document.file_sha256,
                        },
                    )
        if args.apply:
            db.commit()

    print(
        json.dumps(
            {
                "dry_run": not args.apply,
                "anchors_checked": len(plans),
                "anchors_changed": sum(int(item["changed"]) for item in plans),
                "plans": plans,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
