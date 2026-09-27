from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models import Material, Product, ProductBomItem, ProductRevision
from app.services.data_provenance import material_provenance
from app.services.peripheral_bom import material_match

_VALID_MATCH_STATUSES = {"exact", "compatible"}
_BLOCKING_SELECTION_STATUSES = {
    "needs_selection",
    "needs_design_selection",
    "no_matching_material",
    "shortage",
    "ambiguous_candidates",
    "stocked_location_unassigned",
    "candidate_found",
}


def _decimal_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        return format(Decimal(str(value)), "f")
    except (InvalidOperation, ValueError):
        return None


def _positive_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return quantity if quantity > 0 else None


def _material_identity(material: Material) -> dict[str, Any]:
    return {
        "id": material.id,
        "code": material.code,
        "name": material.name,
        "mpn": material.mpn,
        "unit": material.unit,
    }


def _source_requirement_fingerprint(row: dict[str, Any]) -> str:
    payload = {
        key: row.get(key)
        for key in (
            "requirement_id",
            "role",
            "value",
            "unit",
            "rated_voltage_v",
            "dielectric",
            "tolerance",
            "package",
            "connection",
            "constraint_value",
            "required_quantity",
            "quantity_per_unit",
            "constraints",
        )
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ProductBomPreviewService:
    """Build a read-only diff from a persisted Engineering Research Draft.

    This service intentionally has no write-capable dependency.  The only
    database operations are reads, so a preview cannot accidentally create or
    modify ProductBomItem, inventory, reservations, picking, or audit truth.
    """

    def __init__(self, db: Session):
        self.db = db

    def build(
        self,
        *,
        product_id: int,
        revision_id: int,
        engineering_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        product = self.db.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.lifecycle_status == "active",
            )
        )
        if product is None:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或已归档", 404)
        revision = self.db.scalar(
            select(ProductRevision).where(
                ProductRevision.id == revision_id,
                ProductRevision.product_id == product.id,
                ProductRevision.status != "obsolete",
            )
        )
        if revision is None:
            raise BusinessError("PRODUCT_REVISION_NOT_FOUND", "产品版本不存在或已停用", 404)

        context = engineering_context if isinstance(engineering_context, dict) else {}
        draft = context.get("engineering_bom_draft") or {}
        source_rows = draft.get("rows") or context.get("peripheral_requirements") or []
        rows = [row for row in source_rows if isinstance(row, dict)]
        draft_status = str(draft.get("status") or context.get("draft_status") or "not_available")
        existing_rows = self.db.execute(
            select(ProductBomItem, Material)
            .join(Material, Material.id == ProductBomItem.material_id)
            .where(ProductBomItem.product_revision_id == revision.id)
        ).all()
        existing_by_material = {material.id: (item, material) for item, material in existing_rows}
        current_bom_fingerprint = self._bom_fingerprint(existing_rows)
        target_revision_fingerprint = self._revision_fingerprint(revision)

        lines: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for row in rows:
            line, unresolved_item = self._preview_row(
                row,
                existing_by_material,
                draft_status=draft_status,
            )
            lines.append(line)
            if unresolved_item is not None:
                unresolved.append(unresolved_item)

        if not rows:
            unresolved_item = {
                "requirement_id": "engineering-draft",
                "role": "工程草案",
                "reason": "当前对话没有可用于预览的 Engineering BOM Draft。",
            }
            unresolved.append(unresolved_item)
            lines.append(
                {
                    "action": "unresolved",
                    "role": "工程草案",
                    "rail_id": None,
                    "stage_id": None,
                    "requirement_id": "engineering-draft",
                    "selected_material_id": None,
                    "material_code": None,
                    "mpn": None,
                    "draft_quantity": None,
                    "existing_bom_item_id": None,
                    "existing_quantity_per_unit": None,
                    "proposed_quantity_per_unit": None,
                    "reason": unresolved_item["reason"],
                    "reason_code": "DRAFT_INCOMPLETE",
                    "selected_candidate_match_status": None,
                    "selected_candidate_still_valid": False,
                    "material_active": None,
                    "requirement_satisfied": False,
                    "source_requirement_id": "engineering-draft",
                    "source_requirement_fingerprint": _source_requirement_fingerprint(
                        {"requirement_id": "engineering-draft"}
                    ),
                    "warnings": [],
                    "evidence_refs": [],
                }
            )

        warnings: list[str] = []
        if revision.status == "released":
            warnings.append("目标 ProductRevision 已 released；本次仍仅生成预览，未执行 Apply。")
        if draft_status not in {"complete_draft", "reviewable"}:
            warnings.append(
                f"源工程草案状态为 {draft_status}；预览允许继续，但尚未达到可应用就绪状态。"
            )
        blocking_reasons = list(
            dict.fromkeys([item.get("reason_code") or "DRAFT_INCOMPLETE" for item in unresolved])
        )
        summary = {
            "add_count": sum(line["action"] == "add" for line in lines),
            "update_quantity_count": sum(line["action"] == "update_quantity" for line in lines),
            "no_change_count": sum(line["action"] == "no_change" for line in lines),
            "unresolved_count": len(unresolved),
            "complete_for_apply_preview": not unresolved,
            "blocking_reasons": list(dict.fromkeys(item["reason"] for item in unresolved)),
            "readiness": "ready" if not unresolved and not warnings else "blocked",
            "readiness_for_apply": not unresolved and revision.status != "released",
            "ready_for_confirmation_preview": (
                not unresolved and draft_status in {"complete_draft", "reviewable"}
            ),
            "blocking_reason_codes": blocking_reasons,
            "warnings": warnings,
            "selected_count": sum(line.get("selected_material_id") is not None for line in lines),
            "valid_selected_count": sum(
                line.get("selected_candidate_still_valid") is True for line in lines
            ),
            "selected_line_count": sum(
                line.get("selected_material_id") is not None for line in lines
            ),
            "valid_selected_line_count": sum(
                line.get("selected_candidate_still_valid") is True for line in lines
            ),
            "target_revision_status": revision.status,
        }
        source_draft = {
            "status": draft_status,
            "focus_scope": draft.get("focus_scope") or context.get("focus_scope"),
            "row_count": len(rows),
            "completeness": dict(draft.get("completeness") or context.get("completeness") or {}),
            "incomplete": draft_status not in {"complete_draft", "reviewable"},
        }
        preview_fingerprint = _json_fingerprint(
            {
                "product_id": product.id,
                "revision_id": revision.id,
                "target_revision_fingerprint": target_revision_fingerprint,
                "current_bom_fingerprint": current_bom_fingerprint,
                "draft_status": draft_status,
                "lines": lines,
                "source_draft": source_draft,
            }
        )
        preview = {
            "workflow": "product_bom_preview",
            "target_product": {
                "id": product.id,
                "code": product.code,
                "name": product.name,
            },
            "target_revision": {
                "id": revision.id,
                "revision": revision.revision,
                "status": revision.status,
                "is_default": revision.is_default,
            },
            "source_engineering_draft": source_draft,
            "lines": lines,
            "summary": summary,
            "unresolved": unresolved,
            "preview_fingerprint": preview_fingerprint,
            "target_revision_fingerprint": target_revision_fingerprint,
            "current_bom_fingerprint": current_bom_fingerprint,
            "read_only": True,
            "automatic_write": False,
            "formal_product_bom_modified": False,
            "quantity_semantics": "ProductBomItem.quantity_per_unit；工程草案数量必须是单台用量",
        }
        # Phase 3.3.7 is intentionally an embedded read-only projection.  It
        # describes the future mutation set and its preconditions, but never
        # exposes or invokes a write endpoint.
        preview["apply_readiness_dry_run"] = self.build_apply_dry_run(preview=preview)
        return preview

    @staticmethod
    def _bom_fingerprint(rows: list[tuple[ProductBomItem, Material]]) -> str:
        return _json_fingerprint(
            [
                {
                    "id": item.id,
                    "material_id": material.id,
                    "quantity_per_unit": _decimal_text(item.quantity_per_unit),
                }
                for item, material in sorted(rows, key=lambda value: (value[1].id, value[0].id))
            ]
        )

    @staticmethod
    def _revision_fingerprint(revision: ProductRevision) -> str:
        return _json_fingerprint(
            {
                "id": revision.id,
                "product_id": revision.product_id,
                "revision": revision.revision,
                "status": revision.status,
                "is_default": revision.is_default,
                "bom_hash": revision.bom_hash,
            }
        )

    def build_apply_dry_run(
        self,
        *,
        preview: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Produce a deterministic future-Apply plan without any write path."""

        target = preview.get("target_revision") or {}
        revision_id = int(target.get("id") or 0)
        revision = self.db.get(ProductRevision, revision_id) if revision_id else None
        current_rows: list[tuple[ProductBomItem, Material]] = []
        if revision is not None:
            current_rows = self.db.execute(
                select(ProductBomItem, Material)
                .join(Material, Material.id == ProductBomItem.material_id)
                .where(ProductBomItem.product_revision_id == revision.id)
            ).all()
        current_bom_fingerprint = self._bom_fingerprint(current_rows)
        target_revision_fingerprint = (
            self._revision_fingerprint(revision) if revision is not None else ""
        )
        preview_bom_fingerprint = str(preview.get("current_bom_fingerprint") or "")
        preview_revision_fingerprint = str(preview.get("target_revision_fingerprint") or "")
        stale_preview = bool(
            preview_bom_fingerprint
            and preview_bom_fingerprint != current_bom_fingerprint
        ) or bool(
            preview_revision_fingerprint
            and preview_revision_fingerprint != target_revision_fingerprint
        )

        preconditions: list[str] = []
        if not revision:
            preconditions.append("target_revision_not_found")
        elif revision.status != "draft":
            preconditions.append("target_revision_not_editable")
        if preview.get("source_engineering_draft", {}).get("status") not in {
            "complete_draft",
            "reviewable",
        }:
            preconditions.append("engineering_draft_incomplete")
        if int((preview.get("summary") or {}).get("unresolved_count") or 0) != 0:
            preconditions.append("unresolved_preview_items")
        if any(
            line.get("selected_candidate_still_valid") is not True
            or line.get("material_active") is not True
            for line in preview.get("lines") or []
            if line.get("action") != "unresolved"
        ):
            preconditions.append("selected_candidate_or_material_invalid")
        if stale_preview:
            preconditions.append("stale_preview")

        planned_mutations = [
            {
                "action": str(line.get("action") or "").upper(),
                "material_id": line.get("selected_material_id"),
                "before": line.get("existing_quantity_per_unit"),
                "after": line.get("proposed_quantity_per_unit"),
            }
            for line in preview.get("lines") or []
            if line.get("action") in {"add", "update_quantity"}
        ]
        blocked_items = list(preview.get("unresolved") or [])
        if stale_preview:
            blocked_items.append(
                {"reason_code": "STALE_PREVIEW", "reason": "当前 BOM 或版本已变化"}
            )
        fingerprint = str(preview.get("preview_fingerprint") or _json_fingerprint(preview))
        return {
            "dry_run": True,
            "apply_allowed": False,
            "requires_explicit_user_confirmation": True,
            "preconditions": list(dict.fromkeys(preconditions)),
            "planned_mutations": planned_mutations,
            "blocked_items": blocked_items,
            "stale_preview": stale_preview,
            "idempotency_key": idempotency_key or f"apply-readiness:{fingerprint}",
            "preview_fingerprint": fingerprint,
            "target_revision_fingerprint": target_revision_fingerprint,
            "current_bom_fingerprint": current_bom_fingerprint,
            "read_only": True,
            "automatic_write": False,
            "formal_product_bom_modified": False,
        }

    def _preview_row(
        self,
        row: dict[str, Any],
        existing_by_material: dict[int, tuple[ProductBomItem, Material]],
        *,
        draft_status: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        selected_id = self._selected_material_id(row)
        quantity = _positive_decimal(
            row.get("quantity_per_unit")
            if row.get("quantity_per_unit") is not None
            else row.get("required_quantity")
        )
        reason: str | None = None
        reason_code: str | None = None
        material: Material | None = None
        existing: ProductBomItem | None = None
        selected_candidate_match_status: str | None = None
        component_class: str = "unknown"
        class_source: str = "unknown"
        class_match: str = "unknown"
        rejection_reason: str | None = None
        provenance: dict[str, Any] = {}
        selected_candidate_still_valid: bool | None = None
        material_active: bool | None = None
        requirement_satisfied = False
        warnings: list[str] = []
        if selected_id is None:
            reason = "该工程草案行没有 explicit_user 物料选择，不能自动映射为 ADD。"
            reason_code = "NO_EXPLICIT_SELECTION"
        elif row.get("selection_conflict"):
            reason = str(row["selection_conflict"])
            reason_code = "SELECTION_CONFLICT"
        elif str(row.get("selection_status") or "") in _BLOCKING_SELECTION_STATUSES:
            reason = f"工程草案选择状态为 {row.get('selection_status')}，尚未形成可应用的明确选择。"
            reason_code = "DRAFT_INCOMPLETE"
        elif not quantity:
            reason = "缺少明确的单台用量；不能使用库存数量或构建数量代替。"
            reason_code = "MISSING_UNIT_QUANTITY"

        if reason is None and selected_id is not None:
            material = self.db.get(Material, selected_id)
            material_active = bool(
                material is not None
                and not material.is_deleted
                and material.is_active
                and str(material.lifecycle_status or "active") == "active"
            )
            if not material_active:
                reason = "显式选择的物料不存在、已停用或已删除。"
                reason_code = "SELECTED_MATERIAL_INACTIVE"
            else:
                material_payload = {
                    "material_id": material.id,
                    "code": material.code,
                    "name": material.name,
                    "mpn": material.mpn,
                    "specification": material.specification,
                    "package": material.package,
                    "manufacturer": material.manufacturer,
                    "unit": material.unit,
                    "attributes": dict(material.attributes or {}),
                    "category": (
                        {"name": material.category.name, "code": material.category.code}
                        if material.category is not None
                        else None
                    ),
                }
                match = material_match(
                    {
                        **row,
                        "expected_component_classes": list(
                            row.get("expected_component_classes") or []
                        ),
                    },
                    material_payload,
                )
                selected_candidate_match_status = match.get("match_status") or match.get("status")
                component_class = str(match.get("component_class") or "unknown")
                class_source = str(match.get("class_source") or "unknown")
                class_match = str(match.get("class_match") or "unknown")
                rejection_reason = match.get("rejection_reason")
                provenance = material_provenance(material)
                selected_candidate_still_valid = (
                    selected_candidate_match_status in _VALID_MATCH_STATUSES
                )
                if not selected_candidate_still_valid:
                    reason = "显式选择的候选已不再满足当前确定性工程约束。"
                    reason_code = "SELECTED_CANDIDATE_NO_LONGER_MATCHES"
                elif not quantity:
                    reason = "缺少明确的单台用量；不能使用库存数量或构建数量代替。"
                    reason_code = "MISSING_UNIT_QUANTITY"
                else:
                    requirement_satisfied = True
                existing = existing_by_material.get(selected_id, (None, None))[0]

        if reason is None and draft_status not in {"complete_draft", "reviewable"}:
            warnings.append(f"源工程草案状态为 {draft_status}，本行仍只做只读预览。")

        evidence_refs = self._evidence_refs(row)
        resolved_reason_code = reason_code
        action = "unresolved" if reason else self._diff_action(existing, quantity)
        if resolved_reason_code is None:
            resolved_reason_code = {
                "add": "ADD_NEW_MATERIAL",
                "no_change": "SAME_MATERIAL_SAME_QTY",
                "update_quantity": "SAME_MATERIAL_QTY_CHANGE",
            }.get(action, "DRAFT_INCOMPLETE")
        line = {
            "action": action,
            "role": row.get("role"),
            "rail_id": row.get("rail_id") or row.get("rail"),
            "stage_id": row.get("stage_id") or row.get("stage"),
            "requirement_id": row.get("requirement_id"),
            "selected_material_id": selected_id,
            "material_code": material.code if material else None,
            "mpn": material.mpn if material else None,
            "draft_quantity": _decimal_text(quantity),
            "existing_bom_item_id": existing.id if existing else None,
            "existing_quantity_per_unit": (
                _decimal_text(existing.quantity_per_unit) if existing else None
            ),
            "proposed_quantity_per_unit": _decimal_text(quantity) if not reason else None,
            "reason": reason or self._action_reason(existing, quantity),
            "reason_code": resolved_reason_code,
            "selected_candidate_match_status": selected_candidate_match_status,
            "selected_candidate_still_valid": selected_candidate_still_valid,
            "component_class": component_class,
            "class_source": class_source,
            "class_match": class_match,
            "rejection_reason": rejection_reason,
            "provenance": provenance,
            "material_active": material_active,
            "requirement_satisfied": requirement_satisfied,
            "source_requirement_id": row.get("requirement_id"),
            "source_requirement_fingerprint": _source_requirement_fingerprint(row),
            "warnings": warnings,
            "evidence_refs": evidence_refs,
        }
        unresolved_item = None
        if reason:
            unresolved_item = {
                "requirement_id": row.get("requirement_id"),
                "role": row.get("role"),
                "selected_material_id": selected_id,
                "reason": reason,
                "reason_code": resolved_reason_code,
            }
        return line, unresolved_item

    @staticmethod
    def _selected_material_id(row: dict[str, Any]) -> int | None:
        raw = row.get("selected_material_id")
        try:
            selected_id = int(raw) if raw is not None and raw != "" else None
        except (TypeError, ValueError):
            return None
        if selected_id is None:
            return None
        basis = row.get("selection_basis")
        if basis != "explicit_user" or row.get("selection_status") != "selected":
            return None
        return selected_id

    @staticmethod
    def _diff_action(existing: ProductBomItem | None, quantity: Decimal | None) -> str:
        if existing is None:
            return "add"
        return (
            "no_change"
            if quantity is not None and Decimal(existing.quantity_per_unit) == quantity
            else "update_quantity"
        )

    @staticmethod
    def _action_reason(existing: ProductBomItem | None, quantity: Decimal | None) -> str:
        if existing is None:
            return "目标 ProductRevision 没有该物料，预览为 ADD。"
        if quantity is not None and Decimal(existing.quantity_per_unit) == quantity:
            return "目标 ProductRevision 已有相同单台用量，预览为 NO_CHANGE。"
        return "目标 ProductRevision 已有该物料但单台用量不同，预览为 UPDATE_QUANTITY。"

    @staticmethod
    def _evidence_refs(row: dict[str, Any]) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        anchor = row.get("source_anchor")
        if isinstance(anchor, dict) and anchor:
            refs.append(dict(anchor))
        for citation in row.get("evidence_refs") or row.get("citations") or []:
            if isinstance(citation, dict):
                refs.append(dict(citation))
        return refs[:8]
