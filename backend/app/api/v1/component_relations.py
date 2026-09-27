from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import DB, CurrentUser, require, require_any
from app.schemas.relations import (
    ComponentRelationCreate,
    EvidenceLinkRequest,
    ProductBomAlternateCreate,
    RelationRejectRequest,
    RelationRevokeRequest,
)
from app.services.component_relations import ComponentRelationReviewService

router = APIRouter(tags=["器件工程关系"])


@router.get(
    "/materials/{material_id}/relations",
    dependencies=[Depends(require("material:view"))],
)
def list_material_relations(
    material_id: int,
    db: DB,
    user: CurrentUser,
    status: Literal["candidate", "validated", "rejected", "revoked"] | None = Query(
        default=None
    ),
):
    items = ComponentRelationReviewService(db, user, "read").list_relations(
        material_id,
        status=status,
    )
    return {"items": items, "count": len(items)}


@router.post(
    "/component-relations",
    status_code=201,
    dependencies=[Depends(require_any("project:manage", "component:validate"))],
)
def create_component_relation(
    payload: ComponentRelationCreate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    service = ComponentRelationReviewService(db, user, request.state.request_id)
    return service.relation_data(service.create_relation(payload))


@router.post(
    "/component-relations/{relation_id}/validate",
    dependencies=[Depends(require("component:validate"))],
)
def validate_component_relation(
    relation_id: int,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    service = ComponentRelationReviewService(db, user, request.state.request_id)
    return service.relation_data(service.validate_relation(relation_id))


@router.post(
    "/component-relations/{relation_id}/reject",
    dependencies=[Depends(require("component:validate"))],
)
def reject_component_relation(
    relation_id: int,
    payload: RelationRejectRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    service = ComponentRelationReviewService(db, user, request.state.request_id)
    return service.relation_data(service.reject_relation(relation_id, payload.reason))


@router.post(
    "/component-relations/{relation_id}/revoke",
    dependencies=[Depends(require("component:validate"))],
)
def revoke_component_relation(
    relation_id: int,
    payload: RelationRevokeRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    service = ComponentRelationReviewService(db, user, request.state.request_id)
    return service.relation_data(service.revoke_relation(relation_id, payload.reason))


@router.post(
    "/component-relations/{relation_id}/evidence-links",
    status_code=201,
    dependencies=[Depends(require("component:validate"))],
)
def link_component_relation_evidence(
    relation_id: int,
    payload: EvidenceLinkRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    link = ComponentRelationReviewService(
        db, user, request.state.request_id
    ).link_relation_evidence(
        relation_id,
        payload.evidence_anchor_id,
        role=payload.role,
        review_note=payload.review_note,
    )
    return {
        "id": link.id,
        "component_relation_id": link.component_relation_id,
        "evidence_anchor_id": link.evidence_anchor_id,
        "role": link.role,
        "review_note": link.review_note,
    }


@router.get(
    "/product-bom-items/{item_id}/alternates",
    dependencies=[Depends(require("material:view"))],
)
def list_product_bom_alternates(item_id: int, db: DB, user: CurrentUser):
    items = ComponentRelationReviewService(db, user, "read").list_alternates(item_id)
    return {"items": items, "count": len(items)}


@router.post(
    "/product-bom-items/{item_id}/alternates",
    status_code=201,
    dependencies=[Depends(require_any("project:manage", "component:validate"))],
)
def create_product_bom_alternate(
    item_id: int,
    payload: ProductBomAlternateCreate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    service = ComponentRelationReviewService(db, user, request.state.request_id)
    return service.alternate_data(service.create_alternate(item_id, payload))


@router.post(
    "/product-bom-alternates/{alternate_id}/approve",
    dependencies=[Depends(require("component:validate"))],
)
def approve_product_bom_alternate(
    alternate_id: int,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    service = ComponentRelationReviewService(db, user, request.state.request_id)
    return service.alternate_data(service.approve_alternate(alternate_id))


@router.post(
    "/product-bom-alternates/{alternate_id}/reject",
    dependencies=[Depends(require("component:validate"))],
)
def reject_product_bom_alternate(
    alternate_id: int,
    payload: RelationRejectRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    service = ComponentRelationReviewService(db, user, request.state.request_id)
    return service.alternate_data(service.reject_alternate(alternate_id, payload.reason))


@router.post(
    "/product-bom-alternates/{alternate_id}/revoke",
    dependencies=[Depends(require("component:validate"))],
)
def revoke_product_bom_alternate(
    alternate_id: int,
    payload: RelationRevokeRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    service = ComponentRelationReviewService(db, user, request.state.request_id)
    return service.alternate_data(service.revoke_alternate(alternate_id, payload.reason))


@router.post(
    "/product-bom-alternates/{alternate_id}/evidence-links",
    status_code=201,
    dependencies=[Depends(require("component:validate"))],
)
def link_product_bom_alternate_evidence(
    alternate_id: int,
    payload: EvidenceLinkRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    link = ComponentRelationReviewService(
        db, user, request.state.request_id
    ).link_alternate_evidence(
        alternate_id,
        payload.evidence_anchor_id,
        role=payload.role,
        review_note=payload.review_note,
    )
    return {
        "id": link.id,
        "product_bom_alternate_id": link.product_bom_alternate_id,
        "evidence_anchor_id": link.evidence_anchor_id,
        "role": link.role,
        "review_note": link.review_note,
    }
