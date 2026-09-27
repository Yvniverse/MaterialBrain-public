"""Zero-model, read-only page-citation smoke for a restored Phase 2.4 baseline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.tools.common import ToolContext
from app.agent.tools.relations import get_component_relations, get_product_bom_alternates
from app.core.database import SessionLocal
from app.models import (
    AgentActionProposal,
    BuildPlan,
    ComponentRelation,
    Material,
    Product,
    ProductBomAlternate,
    ProductRevision,
    ProjectReservation,
    StockMovement,
    User,
)
from app.schemas.agent import ComponentRelationsArgs, ProductBomAlternatesArgs
from app.services.engineering_evidence import EvidenceRetrievalService


def _write_snapshot(db) -> tuple[int, ...]:
    return tuple(
        int(db.scalar(select(func.count()).select_from(model)) or 0)
        for model in (
            StockMovement,
            ProjectReservation,
            AgentActionProposal,
            BuildPlan,
            ComponentRelation,
            ProductBomAlternate,
        )
    )


def _material_id(db, code: str) -> int:
    value = db.scalar(select(Material.id).where(Material.code == code))
    if value is None:
        raise SystemExit(f"Evidence smoke material is missing: {code}")
    return int(value)


def main() -> int:
    with SessionLocal() as db:
        user = db.scalar(
            select(User).where(
                User.username == "admin",
                User.is_active.is_(True),
                User.is_deleted.is_(False),
            )
        )
        if user is None:
            raise SystemExit("Evidence smoke administrator identity is missing")
        first_id = _material_id(db, "PORT-CAN-TCAN1044")
        second_id = _material_id(db, "PORT-CAN-MCP2562FD")
        atlas_revision_id = db.scalar(
            select(ProductRevision.id)
            .join(Product, ProductRevision.product_id == Product.id)
            .where(
                Product.code == "PROD-ATLAS-AMR",
                ProductRevision.revision == "EVT-R2",
            )
        )
        if atlas_revision_id is None:
            raise SystemExit("Atlas EVT-R2 evidence scope is missing")

        before = _write_snapshot(db)
        retrieval = EvidenceRetrievalService(db)
        current = retrieval.search_material_evidence(
            material_ids=[second_id],
            query="Current Pin 5 function",
            limit=6,
        )
        context = ToolContext(db, user, "portfolio-evidence-restore-smoke")
        relation = get_component_relations(
            context,
            ComponentRelationsArgs(material_ids=[first_id, second_id]),
        )
        alternate = get_product_bom_alternates(
            context,
            ProductBomAlternatesArgs(product_revision_id=int(atlas_revision_id)),
        )
        after = _write_snapshot(db)

    current_keys = {item["document_key"] for item in current["citations"]}
    current_pages = {int(item["page"]) for item in current["citations"]}
    current_fact_names = {
        str(item.get("name"))
        for item in current["facts"]
        if isinstance(item, dict) and item.get("name")
    }
    approved = [item for item in alternate["items"] if item["status"] == "approved"]
    result = {
        "current_revision_only": (
            current_keys == {"SYN-CAN-B-RB"} and "SYN-CAN-B-RA" not in current_keys
        ),
        "page_citation_verified": 3 in current_pages,
        "current_pin_fact_verified": "VIO" in current_fact_names
        and "VREF" not in current_fact_names,
        "relation_evidence_linked": (
            relation["validated_relation_types"] == ["similar_to"]
            and len(relation["items"][0]["evidence_citations"]) >= 2
        ),
        "alternate_evidence_linked": (
            len(approved) == 1
            and approved[0]["currently_usable"] is True
            and len(approved[0]["evidence_citations"]) >= 3
        ),
        "synthetic_fixture_documents": int(
            sum(1 for item in current["citations"] if item.get("synthetic_fixture"))
        ),
        "synthetic_fixture_identity_verified": bool(current["citations"])
        and all(item.get("synthetic_fixture") is True for item in current["citations"]),
        "model_calls": 0,
        "ocr_calls": 0,
        "read_only_write_snapshot_unchanged": before == after,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    required = (
        "current_revision_only",
        "page_citation_verified",
        "current_pin_fact_verified",
        "relation_evidence_linked",
        "alternate_evidence_linked",
        "synthetic_fixture_identity_verified",
        "read_only_write_snapshot_unchanged",
    )
    return 0 if all(result[key] for key in required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
