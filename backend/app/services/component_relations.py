from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import BusinessError
from app.models import (
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
    User,
)
from app.schemas.relations import (
    ComponentRelationCreate,
    ProductBomAlternateCreate,
    evidence_data,
)
from app.services.audit import add_audit
from app.services.engineering_evidence import citation_data


def canonical_material_pair(first_id: int, second_id: int) -> tuple[int, int]:
    if first_id == second_id:
        raise BusinessError(
            "COMPONENT_RELATION_SAME_MATERIAL",
            "器件关系不能引用同一个物料两次。",
            409,
        )
    return min(first_id, second_id), max(first_id, second_id)


def _material_data(material: Material) -> dict[str, Any]:
    return {
        "id": material.id,
        "code": material.code,
        "name": material.name,
        "mpn": material.mpn,
        "package": material.package,
        "manufacturer": material.manufacturer,
        "is_active": material.is_active,
        "is_deleted": material.is_deleted,
    }


def _material_unavailable_reasons(material: Material | None, label: str) -> list[str]:
    if material is None or material.is_deleted:
        return [f"{label}已删除"]
    if not material.is_active:
        return [f"{label}已停用"]
    return []


class ComponentRelationReviewService:
    """Audited human workflow; this service is never exposed as an Agent tool."""

    def __init__(self, db: Session, user: User, request_id: str):
        self.db = db
        self.user = user
        self.request_id = request_id

    def _require_any(self, *permissions: str) -> None:
        granted = set(self.user.role.permissions or [])
        if "*" not in granted and not granted.intersection(permissions):
            raise BusinessError(
                "COMPONENT_REVIEW_FORBIDDEN",
                "没有器件工程关系评审权限。",
                403,
                details={"required_any": list(permissions)},
            )

    def _require_validator(self) -> None:
        self._require_any("component:validate")

    def _material(self, material_id: int) -> Material:
        material = self.db.get(Material, material_id)
        if material is None or material.is_deleted:
            raise BusinessError("MATERIAL_NOT_FOUND", "物料不存在", 404)
        return material

    def _traceable_evidence_enabled(self) -> bool:
        return settings.traceable_engineering_evidence_required

    def _relation_evidence_material_ids(self, relation_id: int) -> set[int]:
        return {
            material_id
            for material_id in self.db.scalars(
                select(EngineeringDocument.material_id)
                .join(
                    EngineeringDocumentPage,
                    EngineeringDocumentPage.document_id == EngineeringDocument.id,
                )
                .join(
                    EvidenceAnchor,
                    EvidenceAnchor.document_page_id == EngineeringDocumentPage.id,
                )
                .join(
                    ComponentRelationEvidenceLink,
                    ComponentRelationEvidenceLink.evidence_anchor_id == EvidenceAnchor.id,
                )
                .where(
                    ComponentRelationEvidenceLink.component_relation_id == relation_id,
                    ComponentRelationEvidenceLink.role == "supporting",
                    EngineeringDocument.scope_type == "material",
                    EngineeringDocument.status == "current",
                    EngineeringDocument.ingest_status == "ready",
                )
            ).all()
            if material_id is not None
        }

    def _alternate_evidence_scopes(
        self, alternate_id: int
    ) -> tuple[set[int], set[int]]:
        rows = self.db.execute(
            select(
                EngineeringDocument.material_id,
                EngineeringDocument.product_revision_id,
            )
            .join(
                EngineeringDocumentPage,
                EngineeringDocumentPage.document_id == EngineeringDocument.id,
            )
            .join(
                EvidenceAnchor,
                EvidenceAnchor.document_page_id == EngineeringDocumentPage.id,
            )
            .join(
                ProductBomAlternateEvidenceLink,
                ProductBomAlternateEvidenceLink.evidence_anchor_id == EvidenceAnchor.id,
            )
            .where(
                ProductBomAlternateEvidenceLink.product_bom_alternate_id == alternate_id,
                ProductBomAlternateEvidenceLink.role == "supporting",
                EngineeringDocument.status == "current",
                EngineeringDocument.ingest_status == "ready",
            )
        ).all()
        return (
            {material_id for material_id, _ in rows if material_id is not None},
            {revision_id for _, revision_id in rows if revision_id is not None},
        )

    def _relation_evidence(self, relation_id: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            select(EvidenceAnchor, EngineeringDocumentPage, EngineeringDocument)
            .join(
                ComponentRelationEvidenceLink,
                ComponentRelationEvidenceLink.evidence_anchor_id == EvidenceAnchor.id,
            )
            .join(
                EngineeringDocumentPage,
                EvidenceAnchor.document_page_id == EngineeringDocumentPage.id,
            )
            .join(
                EngineeringDocument,
                EngineeringDocumentPage.document_id == EngineeringDocument.id,
            )
            .where(
                ComponentRelationEvidenceLink.component_relation_id == relation_id,
                EngineeringDocument.status == "current",
                EngineeringDocument.ingest_status == "ready",
            )
            .order_by(EngineeringDocument.document_key, EngineeringDocumentPage.page_number)
        ).all()
        return [citation_data(*row) for row in rows]

    def _alternate_evidence(self, alternate_id: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            select(EvidenceAnchor, EngineeringDocumentPage, EngineeringDocument)
            .join(
                ProductBomAlternateEvidenceLink,
                ProductBomAlternateEvidenceLink.evidence_anchor_id == EvidenceAnchor.id,
            )
            .join(
                EngineeringDocumentPage,
                EvidenceAnchor.document_page_id == EngineeringDocumentPage.id,
            )
            .join(
                EngineeringDocument,
                EngineeringDocumentPage.document_id == EngineeringDocument.id,
            )
            .where(
                ProductBomAlternateEvidenceLink.product_bom_alternate_id == alternate_id,
                EngineeringDocument.status == "current",
                EngineeringDocument.ingest_status == "ready",
            )
            .order_by(EngineeringDocument.document_key, EngineeringDocumentPage.page_number)
        ).all()
        return [citation_data(*row) for row in rows]

    def relation_data(self, relation: ComponentRelation) -> dict[str, Any]:
        source = self.db.get(Material, relation.source_material_id)
        target = self.db.get(Material, relation.target_material_id)
        unavailable_reasons = [
            *_material_unavailable_reasons(source, "源物料"),
            *_material_unavailable_reasons(target, "目标物料"),
        ]
        if relation.status == "revoked":
            unavailable_reasons.append("工程关系已撤销")
        evidence_material_ids = self._relation_evidence_material_ids(relation.id)
        current_evidence_complete = (
            not self._traceable_evidence_enabled()
            or relation.status not in {"validated", "revoked"}
            or {
                relation.source_material_id,
                relation.target_material_id,
            }.issubset(evidence_material_ids)
        )
        review_required = (
            relation.status in {"validated", "revoked"}
            and not current_evidence_complete
        )
        if review_required:
            unavailable_reasons.append("当前可追溯证据不完整，需要工程复核")
        return {
            "id": relation.id,
            "source_material": _material_data(source),
            "target_material": _material_data(target),
            "relation_type": relation.relation_type,
            "status": relation.status,
            "confidence_note": relation.confidence_note,
            "evidence_summary": relation.evidence_summary,
            "evidence_refs": list(relation.evidence_refs or []),
            "evidence_citations": self._relation_evidence(relation.id),
            "created_by_id": relation.created_by_id,
            "reviewed_by_id": relation.reviewed_by_id,
            "reviewed_at": relation.reviewed_at,
            "validated_by_id": relation.validated_by_id,
            "validated_at": relation.validated_at,
            "rejected_reason": relation.rejected_reason,
            "revoked_by_id": relation.revoked_by_id,
            "revoked_at": relation.revoked_at,
            "revoked_reason": relation.revoked_reason,
            "created_at": relation.created_at,
            "updated_at": relation.updated_at,
            "engineering_evidence_only": True,
            "global_replacement_approved": False,
            "historically_validated": relation.status in {"validated", "revoked"},
            "current_evidence_complete": current_evidence_complete,
            "review_required": review_required,
            "currently_usable": (
                relation.status == "validated"
                and current_evidence_complete
                and not unavailable_reasons
            ),
            "unavailable_reasons": unavailable_reasons,
            "pin_compatible_validated": (
                relation.status == "validated"
                and relation.relation_type == "pin_compatible"
                and current_evidence_complete
                and not unavailable_reasons
            ),
        }

    def list_relations(
        self,
        material_id: int,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        self._material(material_id)
        query = select(ComponentRelation).where(
            or_(
                ComponentRelation.source_material_id == material_id,
                ComponentRelation.target_material_id == material_id,
            )
        )
        if status:
            query = query.where(ComponentRelation.status == status)
        rows = list(
            self.db.scalars(
                query.order_by(ComponentRelation.status, ComponentRelation.relation_type)
            ).all()
        )
        return [self.relation_data(row) for row in rows]

    def create_relation(self, payload: ComponentRelationCreate) -> ComponentRelation:
        self._require_any("project:manage", "component:validate")
        source_id, target_id = canonical_material_pair(
            payload.source_material_id,
            payload.target_material_id,
        )
        self._material(source_id)
        self._material(target_id)
        if self.db.scalar(
            select(ComponentRelation.id).where(
                ComponentRelation.source_material_id == source_id,
                ComponentRelation.target_material_id == target_id,
                ComponentRelation.relation_type == payload.relation_type,
            )
        ):
            raise BusinessError(
                "COMPONENT_RELATION_EXISTS",
                "同一器件对的该类关系已经存在。",
                409,
            )
        relation = ComponentRelation(
            source_material_id=source_id,
            target_material_id=target_id,
            relation_type=payload.relation_type,
            status="candidate",
            confidence_note=payload.confidence_note.strip(),
            evidence_summary=payload.evidence_summary.strip(),
            evidence_refs=evidence_data(payload.evidence_refs),
            created_by_id=self.user.id,
            rejected_reason="",
        )
        self.db.add(relation)
        self.db.flush()
        add_audit(
            self.db,
            self.user.id,
            "component_relation.create",
            "component_relation",
            str(relation.id),
            self.request_id,
            after=jsonable_encoder(self.relation_data(relation)),
        )
        self.db.commit()
        self.db.refresh(relation)
        return relation

    def validate_relation(self, relation_id: int) -> ComponentRelation:
        self._require_validator()
        relation = self.db.scalar(
            select(ComponentRelation).where(ComponentRelation.id == relation_id).with_for_update()
        )
        if relation is None:
            raise BusinessError("COMPONENT_RELATION_NOT_FOUND", "器件关系不存在", 404)
        if relation.status != "candidate":
            raise BusinessError("COMPONENT_RELATION_NOT_CANDIDATE", "关系已处理", 409)
        materials = [
            self.db.get(Material, relation.source_material_id),
            self.db.get(Material, relation.target_material_id),
        ]
        if any(item is None or item.is_deleted or not item.is_active for item in materials):
            raise BusinessError(
                "COMPONENT_RELATION_MATERIAL_UNAVAILABLE",
                "关系包含停用或删除物料。",
                409,
            )
        if not relation.evidence_summary.strip():
            raise BusinessError(
                "COMPONENT_RELATION_EVIDENCE_REQUIRED",
                "验证关系需要工程证据摘要。",
                409,
            )
        if (
            self._traceable_evidence_enabled()
            and relation.relation_type
            in {"pin_compatible", "electrical_compatible", "same_footprint"}
            and not {
                relation.source_material_id,
                relation.target_material_id,
            }.issubset(self._relation_evidence_material_ids(relation.id))
        ):
            raise BusinessError(
                "COMPONENT_RELATION_TRACEABLE_EVIDENCE_REQUIRED",
                "验证该工程关系需要双方物料的当前可追溯证据。",
                409,
            )
        before = self.relation_data(relation)
        relation.status = "validated"
        relation.reviewed_by_id = self.user.id
        relation.reviewed_at = datetime.now(UTC)
        relation.validated_by_id = self.user.id
        relation.validated_at = relation.reviewed_at
        relation.rejected_reason = ""
        relation.revoked_reason = ""
        self.db.flush()
        add_audit(
            self.db,
            self.user.id,
            "component_relation.validate",
            "component_relation",
            str(relation.id),
            self.request_id,
            before=jsonable_encoder(before),
            after=jsonable_encoder(self.relation_data(relation)),
        )
        self.db.commit()
        self.db.refresh(relation)
        return relation

    def reject_relation(self, relation_id: int, reason: str) -> ComponentRelation:
        self._require_validator()
        relation = self.db.scalar(
            select(ComponentRelation).where(ComponentRelation.id == relation_id).with_for_update()
        )
        if relation is None:
            raise BusinessError("COMPONENT_RELATION_NOT_FOUND", "器件关系不存在", 404)
        if relation.status != "candidate":
            raise BusinessError("COMPONENT_RELATION_NOT_CANDIDATE", "关系已处理", 409)
        clean_reason = reason.strip()
        if not clean_reason:
            raise BusinessError("REJECT_REASON_REQUIRED", "请填写拒绝原因", 422)
        before = self.relation_data(relation)
        relation.status = "rejected"
        relation.rejected_reason = clean_reason
        relation.reviewed_by_id = self.user.id
        relation.reviewed_at = datetime.now(UTC)
        relation.validated_by_id = None
        relation.validated_at = None
        self.db.flush()
        add_audit(
            self.db,
            self.user.id,
            "component_relation.reject",
            "component_relation",
            str(relation.id),
            self.request_id,
            before=jsonable_encoder(before),
            after=jsonable_encoder(self.relation_data(relation)),
        )
        self.db.commit()
        self.db.refresh(relation)
        return relation

    def revoke_relation(self, relation_id: int, reason: str) -> ComponentRelation:
        self._require_validator()
        relation = self.db.scalar(
            select(ComponentRelation).where(ComponentRelation.id == relation_id).with_for_update()
        )
        if relation is None:
            raise BusinessError("COMPONENT_RELATION_NOT_FOUND", "器件关系不存在", 404)
        if relation.status != "validated":
            raise BusinessError(
                "COMPONENT_RELATION_NOT_VALIDATED",
                "只有已验证关系可以撤销。",
                409,
            )
        clean_reason = reason.strip()
        if not clean_reason:
            raise BusinessError("REVOKE_REASON_REQUIRED", "请填写撤销原因", 422)
        before = self.relation_data(relation)
        relation.status = "revoked"
        relation.revoked_by_id = self.user.id
        relation.revoked_at = datetime.now(UTC)
        relation.revoked_reason = clean_reason
        self.db.flush()
        add_audit(
            self.db,
            self.user.id,
            "component_relation.revoke",
            "component_relation",
            str(relation.id),
            self.request_id,
            before=jsonable_encoder(before),
            after=jsonable_encoder(self.relation_data(relation)),
        )
        self.db.commit()
        self.db.refresh(relation)
        return relation

    def alternate_data(self, alternate: ProductBomAlternate) -> dict[str, Any]:
        bom_item = self.db.get(ProductBomItem, alternate.product_bom_item_id)
        revision = self.db.get(ProductRevision, bom_item.product_revision_id)
        product = self.db.get(Product, revision.product_id)
        primary = self.db.get(Material, bom_item.material_id)
        candidate = self.db.get(Material, alternate.alternate_material_id)
        unavailable_reasons = [
            *_material_unavailable_reasons(primary, "主料"),
            *_material_unavailable_reasons(candidate, "备选料"),
        ]
        if revision is None or revision.status != "released":
            unavailable_reasons.append("产品版本不是已发布状态")
        if alternate.status == "revoked":
            unavailable_reasons.append("产品备选批准已撤销")
        evidence_material_ids, evidence_revision_ids = self._alternate_evidence_scopes(
            alternate.id
        )
        evidence_scopes_complete = (
            primary is not None
            and candidate is not None
            and {primary.id, candidate.id}.issubset(evidence_material_ids)
            and (
                not alternate.usage_condition.strip()
                or (revision is not None and revision.id in evidence_revision_ids)
            )
        )
        current_evidence_complete = (
            not self._traceable_evidence_enabled()
            or alternate.status not in {"approved", "revoked"}
            or evidence_scopes_complete
        )
        review_required = (
            alternate.status in {"approved", "revoked"}
            and not current_evidence_complete
        )
        if review_required:
            unavailable_reasons.append("当前可追溯证据不完整，需要工程复核")
        return {
            "id": alternate.id,
            "product": {
                "id": product.id,
                "code": product.code,
                "name": product.name,
            },
            "revision": {
                "id": revision.id,
                "revision": revision.revision,
                "status": revision.status,
            },
            "product_bom_item_id": bom_item.id,
            "primary_material": _material_data(primary),
            "alternate_material": _material_data(candidate),
            "status": alternate.status,
            "priority": alternate.priority,
            "usage_condition": alternate.usage_condition,
            "engineering_note": alternate.engineering_note,
            "evidence_refs": list(alternate.evidence_refs or []),
            "evidence_citations": self._alternate_evidence(alternate.id),
            "source_component_relation_id": alternate.source_component_relation_id,
            "created_by_id": alternate.created_by_id,
            "reviewed_by_id": alternate.reviewed_by_id,
            "reviewed_at": alternate.reviewed_at,
            "approved_by_id": alternate.approved_by_id,
            "approved_at": alternate.approved_at,
            "rejected_reason": alternate.rejected_reason,
            "revoked_by_id": alternate.revoked_by_id,
            "revoked_at": alternate.revoked_at,
            "revoked_reason": alternate.revoked_reason,
            "created_at": alternate.created_at,
            "updated_at": alternate.updated_at,
            "scope": "product_revision_bom_position",
            "automatic_substitution": False,
            "historically_approved": alternate.status in {"approved", "revoked"},
            "current_evidence_complete": current_evidence_complete,
            "review_required": review_required,
            "currently_usable": (
                alternate.status == "approved"
                and current_evidence_complete
                and not unavailable_reasons
            ),
            "unavailable_reasons": unavailable_reasons,
            "approval_statement": (
                f"此产品版本 {product.code} {revision.revision} 已批准备选"
                if alternate.status == "approved"
                else "候选备选，尚未批准"
            ),
        }

    def list_alternates(self, product_bom_item_id: int) -> list[dict[str, Any]]:
        if self.db.get(ProductBomItem, product_bom_item_id) is None:
            raise BusinessError("PRODUCT_BOM_ITEM_NOT_FOUND", "单台 BOM 项不存在", 404)
        rows = list(
            self.db.scalars(
                select(ProductBomAlternate)
                .where(ProductBomAlternate.product_bom_item_id == product_bom_item_id)
                .order_by(ProductBomAlternate.priority, ProductBomAlternate.id)
            ).all()
        )
        return [self.alternate_data(row) for row in rows]

    def create_alternate(
        self,
        product_bom_item_id: int,
        payload: ProductBomAlternateCreate,
    ) -> ProductBomAlternate:
        self._require_any("project:manage", "component:validate")
        bom_item = self.db.get(ProductBomItem, product_bom_item_id)
        if bom_item is None:
            raise BusinessError("PRODUCT_BOM_ITEM_NOT_FOUND", "单台 BOM 项不存在", 404)
        self._material(bom_item.material_id)
        self._material(payload.alternate_material_id)
        if bom_item.material_id == payload.alternate_material_id:
            raise BusinessError(
                "PRODUCT_BOM_ALTERNATE_SAME_MATERIAL",
                "备选料不能与主料相同。",
                409,
            )
        if self.db.scalar(
            select(ProductBomAlternate.id).where(
                ProductBomAlternate.product_bom_item_id == product_bom_item_id,
                ProductBomAlternate.alternate_material_id == payload.alternate_material_id,
            )
        ):
            raise BusinessError("PRODUCT_BOM_ALTERNATE_EXISTS", "该 BOM 位已经登记此备选。", 409)
        if payload.source_component_relation_id is not None:
            relation = self.db.get(ComponentRelation, payload.source_component_relation_id)
            pair = canonical_material_pair(bom_item.material_id, payload.alternate_material_id)
            if (
                relation is None
                or relation.status != "validated"
                or (relation.source_material_id, relation.target_material_id) != pair
            ):
                raise BusinessError(
                    "PRODUCT_BOM_ALTERNATE_SOURCE_RELATION_INVALID",
                    "来源关系必须是同一主料/备选料之间的已验证工程关系。",
                    409,
                )
        alternate = ProductBomAlternate(
            product_bom_item_id=product_bom_item_id,
            alternate_material_id=payload.alternate_material_id,
            status="candidate",
            priority=payload.priority,
            usage_condition=payload.usage_condition.strip(),
            engineering_note=payload.engineering_note.strip(),
            evidence_refs=evidence_data(payload.evidence_refs),
            source_component_relation_id=payload.source_component_relation_id,
            created_by_id=self.user.id,
            rejected_reason="",
        )
        self.db.add(alternate)
        self.db.flush()
        add_audit(
            self.db,
            self.user.id,
            "product_bom_alternate.create",
            "product_bom_alternate",
            str(alternate.id),
            self.request_id,
            after=jsonable_encoder(self.alternate_data(alternate)),
        )
        self.db.commit()
        self.db.refresh(alternate)
        return alternate

    def approve_alternate(self, alternate_id: int) -> ProductBomAlternate:
        self._require_validator()
        alternate = self.db.scalar(
            select(ProductBomAlternate)
            .where(ProductBomAlternate.id == alternate_id)
            .with_for_update()
        )
        if alternate is None:
            raise BusinessError("PRODUCT_BOM_ALTERNATE_NOT_FOUND", "产品备选不存在", 404)
        if alternate.status != "candidate":
            raise BusinessError("PRODUCT_BOM_ALTERNATE_NOT_CANDIDATE", "产品备选已处理", 409)
        bom_item = self.db.get(ProductBomItem, alternate.product_bom_item_id)
        revision = self.db.get(ProductRevision, bom_item.product_revision_id) if bom_item else None
        primary = self.db.get(Material, bom_item.material_id) if bom_item else None
        candidate = self.db.get(Material, alternate.alternate_material_id)
        if revision is None or revision.status != "released":
            raise BusinessError(
                "PRODUCT_REVISION_NOT_RELEASED",
                "只有已发布产品版本可以批准备选。",
                409,
            )
        if any(
            item is None or item.is_deleted or not item.is_active for item in (primary, candidate)
        ):
            raise BusinessError(
                "PRODUCT_BOM_ALTERNATE_MATERIAL_UNAVAILABLE",
                "主料或备选料不可用。",
                409,
            )
        if primary.id == candidate.id:
            raise BusinessError(
                "PRODUCT_BOM_ALTERNATE_SAME_MATERIAL",
                "备选料不能与主料相同。",
                409,
            )
        if not alternate.engineering_note.strip() or not alternate.evidence_refs:
            raise BusinessError(
                "PRODUCT_BOM_ALTERNATE_EVIDENCE_REQUIRED",
                "批准产品备选需要工程说明和证据。",
                409,
            )
        if self._traceable_evidence_enabled():
            material_ids, revision_ids = self._alternate_evidence_scopes(alternate.id)
            if not {primary.id, candidate.id}.issubset(material_ids):
                raise BusinessError(
                    "PRODUCT_BOM_ALTERNATE_TRACEABLE_EVIDENCE_REQUIRED",
                    "批准备选需要主料和备选料双方的当前可追溯证据。",
                    409,
                )
            if alternate.usage_condition.strip() and revision.id not in revision_ids:
                raise BusinessError(
                    "PRODUCT_BOM_ALTERNATE_PRODUCT_EVIDENCE_REQUIRED",
                    "产品特定使用条件需要该产品版本的工程说明证据。",
                    409,
                )
        before = self.alternate_data(alternate)
        alternate.status = "approved"
        alternate.reviewed_by_id = self.user.id
        alternate.reviewed_at = datetime.now(UTC)
        alternate.approved_by_id = self.user.id
        alternate.approved_at = alternate.reviewed_at
        alternate.rejected_reason = ""
        alternate.revoked_reason = ""
        self.db.flush()
        add_audit(
            self.db,
            self.user.id,
            "product_bom_alternate.approve",
            "product_bom_alternate",
            str(alternate.id),
            self.request_id,
            before=jsonable_encoder(before),
            after=jsonable_encoder(self.alternate_data(alternate)),
        )
        self.db.commit()
        self.db.refresh(alternate)
        return alternate

    def reject_alternate(self, alternate_id: int, reason: str) -> ProductBomAlternate:
        self._require_validator()
        alternate = self.db.scalar(
            select(ProductBomAlternate)
            .where(ProductBomAlternate.id == alternate_id)
            .with_for_update()
        )
        if alternate is None:
            raise BusinessError("PRODUCT_BOM_ALTERNATE_NOT_FOUND", "产品备选不存在", 404)
        if alternate.status != "candidate":
            raise BusinessError("PRODUCT_BOM_ALTERNATE_NOT_CANDIDATE", "产品备选已处理", 409)
        clean_reason = reason.strip()
        if not clean_reason:
            raise BusinessError("REJECT_REASON_REQUIRED", "请填写拒绝原因", 422)
        before = self.alternate_data(alternate)
        alternate.status = "rejected"
        alternate.rejected_reason = clean_reason
        alternate.reviewed_by_id = self.user.id
        alternate.reviewed_at = datetime.now(UTC)
        alternate.approved_by_id = None
        alternate.approved_at = None
        self.db.flush()
        add_audit(
            self.db,
            self.user.id,
            "product_bom_alternate.reject",
            "product_bom_alternate",
            str(alternate.id),
            self.request_id,
            before=jsonable_encoder(before),
            after=jsonable_encoder(self.alternate_data(alternate)),
        )
        self.db.commit()
        self.db.refresh(alternate)
        return alternate

    def revoke_alternate(self, alternate_id: int, reason: str) -> ProductBomAlternate:
        self._require_validator()
        alternate = self.db.scalar(
            select(ProductBomAlternate)
            .where(ProductBomAlternate.id == alternate_id)
            .with_for_update()
        )
        if alternate is None:
            raise BusinessError("PRODUCT_BOM_ALTERNATE_NOT_FOUND", "产品备选不存在", 404)
        if alternate.status != "approved":
            raise BusinessError(
                "PRODUCT_BOM_ALTERNATE_NOT_APPROVED",
                "只有已批准备选可以撤销。",
                409,
            )
        clean_reason = reason.strip()
        if not clean_reason:
            raise BusinessError("REVOKE_REASON_REQUIRED", "请填写撤销原因", 422)
        before = self.alternate_data(alternate)
        alternate.status = "revoked"
        alternate.revoked_by_id = self.user.id
        alternate.revoked_at = datetime.now(UTC)
        alternate.revoked_reason = clean_reason
        self.db.flush()
        add_audit(
            self.db,
            self.user.id,
            "product_bom_alternate.revoke",
            "product_bom_alternate",
            str(alternate.id),
            self.request_id,
            before=jsonable_encoder(before),
            after=jsonable_encoder(self.alternate_data(alternate)),
        )
        self.db.commit()
        self.db.refresh(alternate)
        return alternate

    def link_relation_evidence(
        self,
        relation_id: int,
        anchor_id: int,
        *,
        role: str,
        review_note: str,
    ) -> ComponentRelationEvidenceLink:
        self._require_validator()
        relation = self.db.get(ComponentRelation, relation_id)
        anchor = self.db.get(EvidenceAnchor, anchor_id)
        if relation is None:
            raise BusinessError("COMPONENT_RELATION_NOT_FOUND", "器件关系不存在", 404)
        if anchor is None:
            raise BusinessError("EVIDENCE_ANCHOR_NOT_FOUND", "证据锚点不存在", 404)
        link = self.db.scalar(
            select(ComponentRelationEvidenceLink).where(
                ComponentRelationEvidenceLink.component_relation_id == relation_id,
                ComponentRelationEvidenceLink.evidence_anchor_id == anchor_id,
            )
        )
        if link is None:
            link = ComponentRelationEvidenceLink(
                component_relation_id=relation_id,
                evidence_anchor_id=anchor_id,
                role=role,
                review_note=review_note.strip(),
            )
            self.db.add(link)
            self.db.flush()
            add_audit(
                self.db,
                self.user.id,
                "component_relation.evidence_link",
                "component_relation",
                str(relation_id),
                self.request_id,
                after={"anchor_id": anchor_id, "role": role},
            )
            self.db.commit()
            self.db.refresh(link)
        return link

    def link_alternate_evidence(
        self,
        alternate_id: int,
        anchor_id: int,
        *,
        role: str,
        review_note: str,
    ) -> ProductBomAlternateEvidenceLink:
        self._require_validator()
        alternate = self.db.get(ProductBomAlternate, alternate_id)
        anchor = self.db.get(EvidenceAnchor, anchor_id)
        if alternate is None:
            raise BusinessError("PRODUCT_BOM_ALTERNATE_NOT_FOUND", "产品备选不存在", 404)
        if anchor is None:
            raise BusinessError("EVIDENCE_ANCHOR_NOT_FOUND", "证据锚点不存在", 404)
        link = self.db.scalar(
            select(ProductBomAlternateEvidenceLink).where(
                ProductBomAlternateEvidenceLink.product_bom_alternate_id == alternate_id,
                ProductBomAlternateEvidenceLink.evidence_anchor_id == anchor_id,
            )
        )
        if link is None:
            link = ProductBomAlternateEvidenceLink(
                product_bom_alternate_id=alternate_id,
                evidence_anchor_id=anchor_id,
                role=role,
                review_note=review_note.strip(),
            )
            self.db.add(link)
            self.db.flush()
            add_audit(
                self.db,
                self.user.id,
                "product_bom_alternate.evidence_link",
                "product_bom_alternate",
                str(alternate_id),
                self.request_id,
                after={"anchor_id": anchor_id, "role": role},
            )
            self.db.commit()
            self.db.refresh(link)
        return link
