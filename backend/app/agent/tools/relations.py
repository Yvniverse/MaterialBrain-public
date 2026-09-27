from __future__ import annotations

from sqlalchemy import and_, or_, select

from app.core.exceptions import BusinessError
from app.models import (
    ComponentRelation,
    Material,
    Product,
    ProductBomAlternate,
    ProductBomItem,
    ProductRevision,
)
from app.schemas.agent import ComponentRelationsArgs, ProductBomAlternatesArgs
from app.services.component_relations import ComponentRelationReviewService

from .common import ToolContext


def _material(material: Material) -> dict:
    return {
        "id": material.id,
        "code": material.code,
        "name": material.name,
        "mpn": material.mpn,
        "package": material.package,
        "manufacturer": material.manufacturer,
    }


def get_component_relations(ctx: ToolContext, args: ComponentRelationsArgs) -> dict:
    review_service = ComponentRelationReviewService(ctx.db, ctx.user, ctx.request_id)
    scope_ids = list(args.material_ids)
    if args.material_id is not None:
        scope_ids.append(args.material_id)
    if args.related_material_id is not None:
        scope_ids.append(args.related_material_id)
    materials = {
        item.id: item
        for item in ctx.db.scalars(
            select(Material).where(
                Material.id.in_(scope_ids),
                Material.is_deleted.is_(False),
            )
        ).all()
    }
    missing = sorted(set(scope_ids) - set(materials))
    if missing:
        raise BusinessError(
            "MATERIAL_NOT_FOUND",
            "关系查询包含不存在的物料。",
            404,
            details={"material_ids": missing},
        )
    visible_statuses = [args.status] if args.status else ["candidate", "validated"]
    query = select(ComponentRelation).where(ComponentRelation.status.in_(visible_statuses))
    if len(scope_ids) == 1:
        material_id = scope_ids[0]
        query = query.where(
            or_(
                ComponentRelation.source_material_id == material_id,
                ComponentRelation.target_material_id == material_id,
            )
        )
    else:
        query = query.where(
            and_(
                ComponentRelation.source_material_id.in_(scope_ids),
                ComponentRelation.target_material_id.in_(scope_ids),
            )
        )
    relations = list(
        ctx.db.scalars(
            query.order_by(ComponentRelation.status, ComponentRelation.relation_type)
        ).all()
    )
    related_ids = {
        material_id
        for relation in relations
        for material_id in (
            relation.source_material_id,
            relation.target_material_id,
        )
    }
    related_materials = {
        item.id: item
        for item in ctx.db.scalars(select(Material).where(Material.id.in_(related_ids))).all()
    }
    items = []
    for relation in relations:
        source = related_materials[relation.source_material_id]
        target = related_materials[relation.target_material_id]
        review_state = review_service.relation_data(relation)
        citations = review_state["evidence_citations"]
        items.append({
            "id": relation.id,
            "source_material": _material(source),
            "target_material": _material(target),
            "relation_type": relation.relation_type,
            "status": relation.status,
            "language": {
                "validated": "已验证工程关系",
                "candidate": "候选关系",
                "rejected": "已拒绝的历史关系",
                "revoked": "已撤销的历史关系",
            }[relation.status],
            "confidence_note": relation.confidence_note,
            "evidence_summary": relation.evidence_summary,
            "evidence_refs": list(relation.evidence_refs or []),
            "evidence_citations": citations,
            "historically_validated": review_state["historically_validated"],
            "current_evidence_complete": review_state["current_evidence_complete"],
            "review_required": review_state["review_required"],
            "currently_usable": review_state["currently_usable"],
            "unavailable_reasons": review_state["unavailable_reasons"],
            "global_replacement_approved": False,
        })
    validated_types = sorted(
        {
            item["relation_type"]
            for item in items
            if item["status"] == "validated"
            and item["currently_usable"]
            and item["evidence_citations"]
        }
    )
    return {
        "material_scope": [_material(materials[item]) for item in scope_ids],
        "items": items,
        "count": len(items),
        "validated_relation_types": validated_types,
        "pin_compatible_validated": "pin_compatible" in validated_types,
        "global_replacement_approved": False,
        "safety_statement": (
            "器件关系是工程证据；similar_to 不代表引脚兼容，"
            "也不能据此作为替代料使用。"
            "只有显式 validated pin_compatible 记录才可称为已验证引脚兼容。"
        ),
        "read_only": True,
    }


def get_product_bom_alternates(
    ctx: ToolContext,
    args: ProductBomAlternatesArgs,
) -> dict:
    review_service = ComponentRelationReviewService(ctx.db, ctx.user, ctx.request_id)
    query = (
        select(
            ProductBomAlternate,
            ProductBomItem,
            ProductRevision,
            Product,
            Material,
        )
        .join(
            ProductBomItem,
            ProductBomItem.id == ProductBomAlternate.product_bom_item_id,
        )
        .join(
            ProductRevision,
            ProductRevision.id == ProductBomItem.product_revision_id,
        )
        .join(Product, Product.id == ProductRevision.product_id)
        .join(Material, Material.id == ProductBomItem.material_id)
        .where(
            ProductBomAlternate.status.in_(
                [args.status] if args.status else ["candidate", "approved"]
            )
        )
    )
    has_scope = any(
        value is not None
        for value in (
            args.product_bom_item_id,
            args.product_revision_id,
            args.product_id,
            args.primary_material_id,
        )
    )
    if args.product_bom_item_id is not None:
        query = query.where(ProductBomItem.id == args.product_bom_item_id)
    if args.product_revision_id is not None:
        query = query.where(ProductRevision.id == args.product_revision_id)
    if args.product_id is not None:
        query = query.where(Product.id == args.product_id)
    if args.primary_material_id is not None:
        query = query.where(ProductBomItem.material_id == args.primary_material_id)
    rows = (
        list(
            ctx.db.execute(
                query.order_by(
                    Product.code,
                    ProductRevision.revision,
                    ProductBomItem.id,
                    ProductBomAlternate.priority,
                )
            ).all()
        )
        if has_scope
        else []
    )
    alternate_ids = {row[0].alternate_material_id for row in rows}
    alternate_materials = {
        item.id: item
        for item in ctx.db.scalars(select(Material).where(Material.id.in_(alternate_ids))).all()
    }
    items = []
    for alternate, bom_item, revision, product, primary in rows:
        candidate = alternate_materials[alternate.alternate_material_id]
        review_state = review_service.alternate_data(alternate)
        citations = review_state["evidence_citations"]
        items.append(
            {
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
                "primary_material": _material(primary),
                "alternate_material": _material(candidate),
                "status": alternate.status,
                "language": (
                    "此产品版本已批准备选"
                    if alternate.status == "approved"
                    else "候选备选，尚未批准"
                ),
                "priority": alternate.priority,
                "usage_condition": alternate.usage_condition,
                "engineering_note": alternate.engineering_note,
                "evidence_refs": list(alternate.evidence_refs or []),
                "evidence_citations": citations,
                "historically_approved": review_state["historically_approved"],
                "current_evidence_complete": review_state["current_evidence_complete"],
                "review_required": review_state["review_required"],
                "currently_usable": review_state["currently_usable"],
                "unavailable_reasons": review_state["unavailable_reasons"],
                "scope": "product_revision_bom_position",
                "automatic_substitution": False,
            }
        )
    approved = [item for item in items if item["status"] == "approved"]
    usable = [item for item in approved if item["currently_usable"]]
    selected_material_id = (
        int(usable[0]["alternate_material"]["id"])
        if len(usable) == 1
        else None
    )
    return {
        "items": items,
        "count": len(items),
        "approved_count": len(approved),
        "currently_usable_count": len(usable),
        "candidate_count": sum(item["status"] == "candidate" for item in items),
        "selected_alternate_material_id": selected_material_id,
        "scope_required": not has_scope,
        "scope": "product_revision_bom_position",
        "primary_bom_arithmetic_only": True,
        "automatic_substitution": False,
        "safety_statement": (
            "备选批准只适用于返回记录中的产品版本和 BOM 位；"
            "Phase 2.3 不把备选库存计入 Build Readiness 或 BuildPlan。"
        ),
        "read_only": True,
    }
