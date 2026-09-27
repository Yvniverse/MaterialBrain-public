from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import BusinessError
from app.models import (
    AgentActionProposal,
    ComponentRelation,
    ComponentRelationEvidenceLink,
    EngineeringDocument,
    EngineeringDocumentPage,
    EvidenceAnchor,
    Material,
    Product,
    ProductBomAlternate,
    ProductBomAlternateEvidenceLink,
    ProductBomItem,
    ProductRevision,
    StockMovement,
    User,
)
from app.services.component_relations import (
    ComponentRelationReviewService,
    canonical_material_pair,
)
from app.services.engineering_evidence import EngineeringEvidenceIngestionService

DATA_DIR = Path(__file__).resolve().parents[2] / "portfolio_demo_data" / "v2_2"
FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "evals" / "evidence" / "fixtures" / "synthetic_datasheets"
)


class PortfolioEvidenceSeeder:
    """Explicit, idempotent synthetic evidence seed with zero inventory or model writes."""

    def __init__(self, db: Session, user: User, settings: Settings):
        self.db = db
        self.user = user
        self.settings = settings

    def _snapshot(self) -> tuple[int, int, int]:
        return (
            self.db.scalar(select(func.count(StockMovement.id))) or 0,
            self.db.scalar(select(func.count(AgentActionProposal.id))) or 0,
            self.db.scalar(select(func.count(ProductBomItem.id))) or 0,
        )

    @staticmethod
    def _load(name: str) -> dict[str, Any]:
        return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))

    def _anchor(self, document_key: str, page_number: int) -> EvidenceAnchor:
        anchor = self.db.scalar(
            select(EvidenceAnchor)
            .join(
                EngineeringDocumentPage,
                EvidenceAnchor.document_page_id == EngineeringDocumentPage.id,
            )
            .join(
                EngineeringDocument,
                EngineeringDocumentPage.document_id == EngineeringDocument.id,
            )
            .where(
                EngineeringDocument.document_key == document_key,
                EngineeringDocumentPage.page_number == page_number,
                EngineeringDocument.ingest_status == "ready",
            )
        )
        if anchor is None:
            raise BusinessError(
                "PORTFOLIO_EVIDENCE_ANCHOR_NOT_FOUND",
                f"合成证据锚点不存在：{document_key} p.{page_number}",
                409,
            )
        return anchor

    def _relation(self, definition: dict[str, Any]) -> ComponentRelation:
        materials = {
            item.code: item.id
            for item in self.db.scalars(
                select(Material).where(Material.code.in_(definition["materials"]))
            ).all()
        }
        if set(materials) != set(definition["materials"]):
            raise BusinessError(
                "PORTFOLIO_EVIDENCE_RELATION_SCOPE_MISSING",
                "合成关系证据的物料范围不完整。",
                409,
            )
        source_id, target_id = canonical_material_pair(*materials.values())
        relation = self.db.scalar(
            select(ComponentRelation).where(
                ComponentRelation.source_material_id == source_id,
                ComponentRelation.target_material_id == target_id,
                ComponentRelation.relation_type == definition["relation_type"],
            )
        )
        if relation is None:
            raise BusinessError(
                "PORTFOLIO_EVIDENCE_RELATION_NOT_FOUND",
                "合成关系证据找不到目标关系。",
                409,
            )
        return relation

    def _alternate(self, definition: dict[str, Any]) -> ProductBomAlternate:
        alternate_material_id = self.db.scalar(
            select(Material.id).where(Material.code == definition["alternate_material_code"])
        )
        if alternate_material_id is None:
            raise BusinessError(
                "PORTFOLIO_EVIDENCE_ALTERNATE_MATERIAL_NOT_FOUND",
                "合成备选证据的备选物料不存在。",
                409,
            )
        alternate = self.db.scalar(
            select(ProductBomAlternate)
            .join(
                ProductBomItem,
                ProductBomAlternate.product_bom_item_id == ProductBomItem.id,
            )
            .join(
                ProductRevision,
                ProductBomItem.product_revision_id == ProductRevision.id,
            )
            .join(Product, ProductRevision.product_id == Product.id)
            .join(Material, ProductBomItem.material_id == Material.id)
            .where(
                Product.code == definition["product_code"],
                ProductRevision.revision == definition["revision"],
                Material.code == definition["primary_material_code"],
                ProductBomAlternate.alternate_material_id == alternate_material_id,
            )
        )
        if alternate is None:
            raise BusinessError(
                "PORTFOLIO_EVIDENCE_ALTERNATE_NOT_FOUND",
                "合成备选证据找不到目标 BOM 位批准。",
                409,
            )
        return alternate

    def seed(self) -> dict[str, Any]:
        if not self.settings.portfolio_evidence_seed_enabled:
            raise BusinessError(
                "PORTFOLIO_EVIDENCE_SEED_DISABLED",
                "Portfolio 合成证据导入未显式启用。",
                409,
            )
        before = self._snapshot()
        manifest = self._load("synthetic_datasheet_manifest_v1.json")
        links = self._load("portfolio_evidence_links_v1.json")
        ingestion = EngineeringEvidenceIngestionService(
            self.db, self.user, "portfolio-evidence-seed"
        )
        documents: dict[str, EngineeringDocument] = {}
        created_documents = 0
        for definition in manifest["documents"]:
            supersedes_id = None
            supersedes_key = definition.get("supersedes_document_key")
            if supersedes_key:
                previous = documents.get(supersedes_key) or self.db.scalar(
                    select(EngineeringDocument).where(
                        EngineeringDocument.document_key == supersedes_key
                    )
                )
                if previous is None:
                    raise BusinessError(
                        "PORTFOLIO_EVIDENCE_SUPERSEDES_MISSING",
                        "合成证据修订链不完整。",
                        409,
                    )
                supersedes_id = previous.id
            document, created = ingestion.ingest_fixture(
                definition,
                FIXTURE_DIR / definition["filename"],
                supersedes_document_id=supersedes_id,
            )
            documents[definition["document_key"]] = document
            created_documents += int(created)

        review = ComponentRelationReviewService(self.db, self.user, "portfolio-evidence-links")
        created_relation_links = 0
        for definition in links["component_relation_links"]:
            relation = self._relation(definition)
            for link_definition in definition["links"]:
                anchor = self._anchor(link_definition["document_key"], int(link_definition["page"]))
                existed = self.db.scalar(
                    select(ComponentRelationEvidenceLink.id).where(
                        ComponentRelationEvidenceLink.component_relation_id == relation.id,
                        ComponentRelationEvidenceLink.evidence_anchor_id == anchor.id,
                    )
                )
                review.link_relation_evidence(
                    relation.id,
                    anchor.id,
                    role=link_definition["role"],
                    review_note=f"工程证据链接：{link_definition['fact']}",
                )
                created_relation_links += int(existed is None)

        created_alternate_links = 0
        for definition in links["product_bom_alternate_links"]:
            alternate = self._alternate(definition)
            for link_definition in definition["links"]:
                anchor = self._anchor(link_definition["document_key"], int(link_definition["page"]))
                existed = self.db.scalar(
                    select(ProductBomAlternateEvidenceLink.id).where(
                        ProductBomAlternateEvidenceLink.product_bom_alternate_id == alternate.id,
                        ProductBomAlternateEvidenceLink.evidence_anchor_id == anchor.id,
                    )
                )
                review.link_alternate_evidence(
                    alternate.id,
                    anchor.id,
                    role=link_definition["role"],
                    review_note=f"工程证据链接：{link_definition['fact']}",
                )
                created_alternate_links += int(existed is None)

        after = self._snapshot()
        if before != after:
            self.db.rollback()
            raise BusinessError(
                "PORTFOLIO_EVIDENCE_WRITE_SAFETY_VIOLATION",
                "合成证据导入触碰了库存、提案或产品 BOM，事务已回滚。",
                409,
            )
        # Persist provenance normalization for an already seeded fixture set.
        # Idempotent evidence-link helpers do not commit when every link exists.
        self.db.commit()
        return {
            "dataset": manifest["dataset"],
            "document_count": self.db.scalar(select(func.count(EngineeringDocument.id))) or 0,
            "page_count": self.db.scalar(select(func.count(EngineeringDocumentPage.id))) or 0,
            "anchor_count": self.db.scalar(select(func.count(EvidenceAnchor.id))) or 0,
            "relation_link_count": self.db.scalar(
                select(func.count(ComponentRelationEvidenceLink.id))
            )
            or 0,
            "alternate_link_count": self.db.scalar(
                select(func.count(ProductBomAlternateEvidenceLink.id))
            )
            or 0,
            "created_documents": created_documents,
            "created_relation_links": created_relation_links,
            "created_alternate_links": created_alternate_links,
            "write_sensitive_unchanged": before == after,
            "model_calls": 0,
            "ocr_calls": 0,
        }
