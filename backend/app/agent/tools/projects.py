import re
from decimal import Decimal

from sqlalchemy import or_, select

from app.core.exceptions import BusinessError
from app.models import BomItem, Material, Project, ProjectReservation
from app.schemas.agent import MaterialIdArgs, ProjectBomArgs, SearchProjectsArgs

from .common import ToolContext
from .locations import find_material_locations

_ALNUM_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_CJK = re.compile(r"[\u3400-\u9fff]")


def _normalized(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _cjk_ngrams(value: str, size: int = 3) -> set[str]:
    text = "".join(_CJK.findall(value))
    if len(text) < size:
        return {text} if len(text) >= 2 else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _fallback_project_candidates(ctx: ToolContext, term: str, *, limit: int) -> list[Project]:
    query_normalized = _normalized(term)
    alnum_tokens = {
        _normalized(token) for token in _ALNUM_TOKEN.findall(term) if len(_normalized(token)) >= 2
    }
    query_ngrams = _cjk_ngrams(term)
    projects = list(ctx.db.scalars(select(Project).order_by(Project.code).limit(2000)).all())
    scored: list[tuple[int, Project]] = []
    for project in projects:
        code = _normalized(project.code)
        name = _normalized(project.name)
        searchable = code + name
        phrase_score = max(
            (len(value) for value in (code, name) if value and value in query_normalized),
            default=0,
        )
        token_score = sum(token in searchable for token in alnum_tokens)
        ngram_score = len(query_ngrams.intersection(_cjk_ngrams(project.name)))
        score = phrase_score * 100 + token_score * 10 + ngram_score
        if score:
            scored.append((score, project))
    if not scored:
        return []
    best_score = max(score for score, _ in scored)
    return [project for score, project in scored if score == best_score][: limit * 3]


def search_projects(ctx: ToolContext, args: SearchProjectsArgs) -> dict:
    term = args.query.strip()
    terms = {term}
    normalized_term = _normalized(term)
    if "机器人" in term or "robot" in normalized_term:
        terms.update({"机器人", "robot"})
    predicates = []
    for search_term in terms:
        pattern = f"%{search_term}%"
        predicates.extend((Project.code.ilike(pattern), Project.name.ilike(pattern)))
    projects = list(
        ctx.db.scalars(
            select(Project)
            .where(or_(*predicates))
            .order_by(Project.updated_at.desc())
            .limit(args.limit)
        ).all()
    )
    if not projects:
        projects = _fallback_project_candidates(ctx, term, limit=args.limit)
    lowered = term.casefold()
    exact_projects = [
        project
        for project in projects
        if project.code.casefold() == lowered or project.name.casefold() == lowered
    ]
    selected_projects = exact_projects or projects
    project_ids = [project.id for project in selected_projects]
    versions_by_project: dict[int, list[str]] = {project_id: [] for project_id in project_ids}
    if project_ids:
        version_rows = ctx.db.execute(
            select(BomItem.project_id, BomItem.version)
            .where(BomItem.project_id.in_(project_ids))
            .distinct()
            .order_by(BomItem.project_id, BomItem.version)
        ).all()
        for project_id, version in version_rows:
            versions_by_project[project_id].append(version)
    return {
        "query": term,
        "items": [
            {
                "id": project.id,
                "code": project.code,
                "name": project.name,
                "status": project.status,
                "product_revision_id": project.product_revision_id,
                "available_versions": versions_by_project[project.id],
            }
            for project in selected_projects
        ],
        "count": len(selected_projects),
        "exact_match_ids": [project.id for project in exact_projects],
    }


def _bom_rows(ctx: ToolContext, args: ProjectBomArgs):
    project = ctx.db.get(Project, args.project_id)
    if not project:
        raise BusinessError("PROJECT_NOT_FOUND", "项目不存在", 404)
    available_versions = list(
        ctx.db.scalars(
            select(BomItem.version)
            .where(BomItem.project_id == project.id)
            .distinct()
            .order_by(BomItem.version)
        ).all()
    )
    if not args.version and len(available_versions) > 1:
        raise BusinessError(
            "BOM_VERSION_REQUIRED",
            "项目存在多个 BOM 版本，请先选择版本",
            409,
            details={
                "project_id": project.id,
                "project_code": project.code,
                "available_versions": available_versions,
            },
        )
    effective_version = args.version or (
        available_versions[0] if len(available_versions) == 1 else None
    )
    if args.version and args.version not in available_versions:
        raise BusinessError(
            "BOM_VERSION_NOT_FOUND",
            "指定的 BOM 版本不存在",
            404,
            details={"available_versions": available_versions},
        )
    stmt = (
        select(BomItem, Material)
        .join(Material, Material.id == BomItem.material_id)
        .where(
            BomItem.project_id == project.id,
            Material.is_deleted.is_(False),
        )
        .order_by(BomItem.version, Material.code)
    )
    if effective_version:
        stmt = stmt.where(BomItem.version == effective_version)
    rows = list(ctx.db.execute(stmt).all())
    material_ids = [material.id for _, material in rows]
    reservations = (
        list(
            ctx.db.scalars(
                select(ProjectReservation).where(
                    ProjectReservation.project_id == project.id,
                    ProjectReservation.material_id.in_(material_ids),
                )
            ).all()
        )
        if material_ids
        else []
    )
    reservation_by_material = {item.material_id: item for item in reservations}
    return project, effective_version, rows, reservation_by_material


def get_project_bom(ctx: ToolContext, args: ProjectBomArgs) -> dict:
    project, version, rows, reservation_by_material = _bom_rows(ctx, args)
    return {
        "project": {
            "id": project.id,
            "code": project.code,
            "name": project.name,
            "status": project.status,
        },
        "version": version,
        "items": [
            {
                "bom_item_id": bom.id,
                "version": bom.version,
                "material_id": material.id,
                "code": material.code,
                "name": material.name,
                "mpn": material.mpn,
                "attributes": dict(material.attributes or {}),
                "unit": material.unit,
                "required_quantity": str(bom.required_quantity),
                "available_quantity": str(material.available_quantity),
                "reserved_for_project": str(
                    reservation_by_material.get(material.id).quantity
                    if material.id in reservation_by_material
                    else Decimal("0")
                ),
                "notes": bom.notes,
            }
            for bom, material in rows
        ],
        "count": len(rows),
        "quantity_semantics": "按数据库现有 required_quantity 作为当前项目 BOM 总需求分析",
    }


def analyze_project_bom_stock(ctx: ToolContext, args: ProjectBomArgs) -> dict:
    project, version, rows, reservation_by_material = _bom_rows(ctx, args)
    items = []
    for bom, material in rows:
        reservation = reservation_by_material.get(material.id)
        reserved_for_project = reservation.quantity if reservation else Decimal("0")
        coverage = material.available_quantity + reserved_for_project
        shortage = max(Decimal("0"), bom.required_quantity - coverage)
        location_result = find_material_locations(
            ctx,
            MaterialIdArgs(material_id=material.id),
        )
        items.append(
            {
                "bom_item_id": bom.id,
                "version": bom.version,
                "material_id": material.id,
                "code": material.code,
                "name": material.name,
                "mpn": material.mpn,
                "attributes": dict(material.attributes or {}),
                "unit": material.unit,
                "required_quantity": str(bom.required_quantity),
                "available_quantity": str(material.available_quantity),
                "reserved_for_project": str(reserved_for_project),
                "shortage": str(shortage),
                "sufficient": shortage == 0,
                # These are physical InventoryLot locations, not a project-specific
                # picking allocation. Show them for every BOM row so "not queried"
                # is never misrepresented as "unallocated".
                "locations": location_result["locations"],
                "distribution_status": location_result["distribution_status"],
                "unallocated_quantity": location_result["unallocated_quantity"],
            }
        )
    return {
        "project": {
            "id": project.id,
            "code": project.code,
            "name": project.name,
            "status": project.status,
        },
        "version": version,
        "items": items,
        "shortage_count": sum(not item["sufficient"] for item in items),
        "sufficient": all(item["sufficient"] for item in items),
        "quantity_semantics": (
            "未乘以构建台数；按数据库当前 Project BOM 的 required_quantity 分析；"
            "库存库位来自 InventoryLot 物理分布，不代表项目拣料分配；"
            "当前不会生成项目级 PickAllocation、PickTask 或路线；"
            "逐站 Picking 需要后续 Picking Core"
        ),
    }
