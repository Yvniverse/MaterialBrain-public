import json
import re

from sqlalchemy import String, cast, or_, select

from app.core.exceptions import BusinessError
from app.models import (
    EngineeringDocument,
    EngineeringDocumentPage,
    EvidenceAnchor,
    Location,
    Material,
)
from app.schemas.agent import MaterialIdArgs, SearchMaterialsArgs
from app.services.data_provenance import material_provenance

from .common import ToolContext, location_dict, material_dict


def _material_location(ctx: ToolContext, material: Material) -> dict | None:
    if not material.location_id:
        return None
    location = ctx.db.get(Location, material.location_id)
    return location_dict(location)


def _candidate_dict(material: Material) -> dict:
    return {
        "id": material.id,
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
        "provenance": material_provenance(material),
    }


_ALNUM_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_IDENTIFIER_STOP_TOKENS = {"pdf", "datasheet"}
_READ_INTENT_SUFFIX = re.compile(
    r"\s*(?:现在|当前)?\s*(?:库存)?\s*"
    r"(?:还有|还剩|剩下|剩余|可用|有)?\s*"
    r"(?:多少|够不够|够吗)(?:个|件|条|只|pcs)?[？?。！!]*$",
    re.I,
)
_LOCATION_CLAIM_SUFFIX = re.compile(
    r"\s*在\s*[A-Za-z][A-Za-z0-9._+-]*\s*(?:对吧|是吧|吗|么)?[？?。！!]*$",
    re.I,
)


def _normalized(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _search_identifier_tokens(term: str, *, minimum_length: int = 4) -> list[str]:
    """Extract meaningful part identifiers while ignoring document-type words."""

    tokens: list[str] = []
    for raw_token in _ALNUM_TOKEN.findall(term):
        normalized = _normalized(raw_token)
        if (
            len(normalized) < minimum_length
            or normalized in _IDENTIFIER_STOP_TOKENS
            or (normalized.isdigit() and len(normalized) < 4)
        ):
            continue
        if normalized not in tokens:
            tokens.append(normalized)
        # Vendor orderable aliases commonly spell a device as e.g.
        # TCAN1044A-Q1 while the warehouse MPN includes a package suffix
        # (TCAN1044AVDRQ1).  The stable device stem is still an identity
        # signal, unlike generic words such as PDF.
        stem = _normalized(re.split(r"[-_.+]+", raw_token, maxsplit=1)[0])
        if len(stem) >= 4 and stem not in tokens:
            tokens.append(stem)
    return tokens


def _fallback_material_candidates(
    ctx: ToolContext,
    term: str,
    *,
    limit: int,
    identity_only: bool = False,
) -> list[Material]:
    """Resolve useful identifiers from a natural-language search sentence.

    The LLM may pass the whole question instead of one clean identifier. The fallback
    ranks only deterministic identifier-token matches; it keeps every top-scoring row
    so an ambiguous result can never be silently reduced to one material.
    """

    tokens = _search_identifier_tokens(term, minimum_length=2)
    if not tokens:
        return []
    materials = list(
        ctx.db.scalars(
            select(Material)
            .where(Material.is_deleted.is_(False), Material.is_active.is_(True))
            .order_by(Material.code)
            .limit(2000)
        ).all()
    )
    identity_scored: list[tuple[int, Material]] = []
    scored: list[tuple[int, Material]] = []
    for material in materials:
        identity = _normalized(" ".join((material.code, material.mpn)))
        searchable = _normalized(
            " ".join(
                str(value or "")
                for value in (
                    material.code,
                    material.name,
                    material.mpn,
                    material.specification,
                    material.package,
                    material.manufacturer,
                    json.dumps(material.attributes or {}, ensure_ascii=False, default=str),
                )
            )
        )
        identity_hits = [
            token
            for token in tokens
            if len(token) >= 4 and not token.isdigit() and token in identity
        ]
        if identity_hits:
            identity_scored.append((len(identity_hits) * 100, material))
        score = sum(1 for token in tokens if token in searchable)
        if score:
            scored.append((score, material))
    if identity_scored:
        best_score = max(score for score, _ in identity_scored)
        return [material for score, material in identity_scored if score == best_score][: limit * 3]
    if identity_only:
        return []
    if not scored:
        return []
    best_score = max(score for score, _ in scored)
    return [material for score, material in scored if score == best_score][: limit * 3]


def _evidence_family_candidates(ctx: ToolContext, term: str, *, limit: int) -> list[Material]:
    """Resolve a datasheet-only family variant to its owning material.

    Some vendor PDFs cover sibling orderable variants that are not separate
    warehouse materials.  An evidence question for that explicit sibling must
    still resolve the document's owning material so the evidence service can
    apply its variant scope rules.  This is identification only; it does not
    turn the sibling into an inventory item or assert material identity.
    """

    identifier_tokens = _search_identifier_tokens(term)
    if not identifier_tokens:
        return []
    rows = ctx.db.execute(
        select(Material, EngineeringDocument, EvidenceAnchor)
        .join(EngineeringDocument, EngineeringDocument.material_id == Material.id)
        .join(
            EngineeringDocumentPage,
            EngineeringDocumentPage.document_id == EngineeringDocument.id,
        )
        .join(
            EvidenceAnchor,
            EvidenceAnchor.document_page_id == EngineeringDocumentPage.id,
        )
        .where(
            Material.is_deleted.is_(False),
            Material.is_active.is_(True),
            EngineeringDocument.scope_type == "material",
            EngineeringDocument.status == "current",
            EngineeringDocument.ingest_status == "ready",
        )
    ).all()
    scored: dict[int, tuple[int, Material]] = {}
    for material, document, anchor in rows:
        structured = json.dumps(anchor.structured_fact or {}, ensure_ascii=False, default=str)
        searchable = _normalized(
            " ".join(
                (
                    document.document_key,
                    document.title,
                    document.original_filename,
                    structured,
                )
            )
        )
        matched_tokens = [token for token in identifier_tokens if token in searchable]
        if not matched_tokens:
            continue
        score = max(len(token) for token in matched_tokens)
        if any(token in _normalized(structured) for token in matched_tokens):
            score += 100
        if any(token in _normalized(document.document_key) for token in matched_tokens):
            score += 50
        previous = scored.get(material.id)
        if previous is None or score > previous[0]:
            scored[material.id] = (score, material)
    ordered = sorted(scored.values(), key=lambda item: (-item[0], item[1].code))
    return [material for _score, material in ordered[:limit]]


def search_materials(ctx: ToolContext, args: SearchMaterialsArgs) -> dict:
    original_term = args.query.strip()
    term = _READ_INTENT_SUFFIX.sub("", original_term).strip(" ，,？?。！!")
    term = _LOCATION_CLAIM_SUFFIX.sub("", term).strip(" ，,？?。！!")
    term = term or original_term
    pattern = f"%{term}%"
    candidates = list(
        ctx.db.scalars(
            select(Material)
            .where(
                Material.is_deleted.is_(False),
                Material.is_active.is_(True),
                or_(
                    Material.code.ilike(pattern),
                    Material.name.ilike(pattern),
                    Material.mpn.ilike(pattern),
                    Material.specification.ilike(pattern),
                    Material.manufacturer.ilike(pattern),
                    cast(Material.attributes, String).ilike(pattern),
                ),
            )
            .limit(args.limit * 3)
        ).all()
    )
    if not candidates:
        candidates = _fallback_material_candidates(ctx, term, limit=args.limit, identity_only=True)
    if not candidates:
        candidates = _evidence_family_candidates(ctx, term, limit=args.limit)
    if not candidates:
        candidates = _fallback_material_candidates(ctx, term, limit=args.limit)
    lowered = term.casefold()

    def rank(material: Material) -> tuple[int, str]:
        values = [material.code, material.name, material.mpn]
        if any(value.casefold() == lowered for value in values if value):
            return 0, material.code
        if any(value.casefold().startswith(lowered) for value in values if value):
            return 1, material.code
        return 2, material.code

    candidates.sort(key=rank)
    limited_candidates = candidates[: args.limit]
    exact_candidates = [
        material
        for material in limited_candidates
        if any(
            value and value.casefold() == lowered
            for value in (material.code, material.name, material.mpn)
        )
    ]
    selected_candidates = exact_candidates or limited_candidates
    items = [_candidate_dict(material) for material in selected_candidates]
    exact_match_ids = [material.id for material in exact_candidates]
    selected_material_id = items[0]["id"] if len(items) == 1 else None
    return {
        "query": term,
        "items": items,
        "count": len(items),
        "exact_match_ids": exact_match_ids,
        "selected_material_id": selected_material_id,
        "next_step": (
            "Use selected_material_id with the live inventory/location/detail tool required "
            "by the user; search results are identification data, not live facts."
            if selected_material_id
            else "Ask the user to choose one candidate before calling material fact tools."
        ),
    }


def get_material_detail(ctx: ToolContext, args: MaterialIdArgs) -> dict:
    material = ctx.db.scalar(
        select(Material).where(
            Material.id == args.material_id,
            Material.is_deleted.is_(False),
        )
    )
    if not material:
        raise BusinessError("MATERIAL_NOT_FOUND", "物料不存在", 404)
    return {
        **material_dict(material, location=_material_location(ctx, material)),
        "footprint": material.footprint,
        "supplier_part_number": material.supplier_part_number,
        "safety_stock": str(material.safety_stock),
        "target_stock": str(material.target_stock),
        "barcode": material.barcode,
        "lifecycle_status": material.lifecycle_status,
        "rohs_status": material.rohs_status,
        "tags": list(material.tags or []),
        "attributes": dict(material.attributes or {}),
    }
