from __future__ import annotations

from typing import Any

from app.agent.task_contract import TaskContract

_PROCESS_ONLY_ANSWERS = {
    "已完成查询",
    "已完成查询。",
    "已核对物料",
    "已核对物料。",
    "已核对项目",
    "已核对项目。",
    "已核对产品",
    "已核对产品。",
}


def _has_business_answer(answer: str) -> bool:
    text = str(answer or "").strip()
    return bool(text) and text not in _PROCESS_ONLY_ANSWERS


def public_result_contract_violations(
    contract: TaskContract,
    *,
    entities: dict[str, Any],
    answer: str,
) -> list[str]:
    """Validate user-visible completeness after successful tool orchestration."""

    failures: list[str] = []
    cable = entities.get("cable_search") or entities.get("cable_detail") or {}
    if "cable_search" in contract.requested_facts and not cable:
        failures.append("cable intent has no public result entity")
    elif "cable_search" in contract.requested_facts:
        state = cable.get("result_state")
        items = cable.get("items") or ([cable] if entities.get("cable_detail") else [])
        if state not in {
            "awaiting_clarification",
            "exact_match",
            "near_match",
            "no_match",
        }:
            failures.append("cable result has no explicit public state")
        elif state == "awaiting_clarification" and not cable.get("clarification"):
            failures.append("cable clarification state has no question")
        elif state in {"exact_match", "near_match"} and not items:
            failures.append("completed cable result has no cards")
        elif state == "no_match" and items:
            failures.append("cable no-match state contains result cards")

    material_facts = {"material_identity", "inventory", "location"}.intersection(
        contract.requested_facts
    )
    material_candidates = entities.get("material_candidates") or {}
    resolved_material = bool(
        material_candidates.get("selected_material_id")
        or len(material_candidates.get("exact_match_ids") or []) == 1
        or len(material_candidates.get("items") or []) == 1
    )
    if material_facts and resolved_material:
        candidate = (material_candidates.get("items") or [{}])[0]
        has_inventory = bool(
            entities.get("inventory")
            or entities.get("locations")
            or candidate.get("available_quantity") is not None
        )
        if not has_inventory:
            failures.append("resolved material has no public inventory summary")
        if "location" in material_facts:
            location_result = entities.get("locations") or {}
            has_location_state = "distribution_status" in location_result or (
                "location" in candidate and candidate.get("location_truth_source") == "InventoryLot"
            )
            if not has_location_state:
                failures.append("resolved location query has no actual/unallocated state")

    product_candidates = entities.get("product_candidates") or {}
    resolved_product = bool(
        product_candidates.get("selected_product_id")
        or len(product_candidates.get("exact_match_ids") or []) == 1
        or len(product_candidates.get("items") or []) == 1
        or (entities.get("project_candidates") or {}).get("selected_project_id")
    )
    if (
        "build_readiness" in contract.requested_facts
        and resolved_product
        and not (entities.get("build_readiness") or entities.get("bom_analysis"))
        and not entities.get("cable_search")
    ):
        failures.append("build-readiness intent has no structured result")

    mandatory_entities: dict[str, tuple[str, ...]] = {
        "navigation_lab": ("navigation_lab",),
        "navigation_plan": ("navigation_plan",),
        "low_stock": ("low_stock",),
        "component_search": ("component_search",),
        "power_design": ("power_design",),
        "engineering_research": ("engineering_research",),
        "engineering_evidence": ("engineering_evidence",),
        "component_evidence_comparison": ("component_evidence_comparison",),
    }
    for fact, keys in mandatory_entities.items():
        if fact in contract.requested_facts and not any(entities.get(key) for key in keys):
            # Resolution ambiguity is a valid public state when it presents candidates.
            has_candidates = any(
                (entities.get(key) or {}).get("items")
                for key in ("material_candidates", "project_candidates", "product_candidates")
            )
            if not has_candidates:
                failures.append(f"{fact} intent has no structured public result")

    has_proposal_result = bool(entities.get("proposal") or entities.get("build_plan_proposal"))
    if (
        {"project_bom", "bom_stock"}.intersection(contract.requested_facts)
        and (entities.get("project_candidates") or {}).get("selected_project_id")
        and not (entities.get("project_bom") or entities.get("bom_analysis"))
        and not has_proposal_result
    ):
        failures.append("resolved Project BOM intent has no structured result")

    alternate_or_cable_result = bool(
        entities.get("product_bom_alternates")
        or entities.get("component_relations")
        or entities.get("cable_search")
    )
    if (
        "product_bom" in contract.requested_facts
        and resolved_product
        and not entities.get("product_bom")
        and not alternate_or_cable_result
        and not has_proposal_result
    ):
        failures.append("resolved Product BOM intent has no structured result")

    relation_requested = bool(
        {"component_relations", "product_alternates"}.intersection(contract.requested_facts)
    )
    relation_result = entities.get("component_relations") or entities.get(
        "product_bom_alternates"
    )
    if relation_requested and not relation_result and not _has_business_answer(answer):
        failures.append("relation/alternate intent has neither result nor clarification")

    if not _has_business_answer(answer) and not any(entities.values()):
        failures.append("public result is empty")
    return failures
