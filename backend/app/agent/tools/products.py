import re

from sqlalchemy import or_, select

from app.core.exceptions import BusinessError
from app.models import Material, Product, ProductBomItem, ProductRevision
from app.schemas.agent import ProductBomArgs, ProductBuildReadinessArgs, SearchProductsArgs
from app.services.build_readiness import BuildReadinessService

from .common import ToolContext

_NOISE = re.compile(
    r"(?:帮我|请|找|看看|看|查询|产品|单台|的|是什么|BOM|物料清单|够不够|够料吗|料够吗)",
    re.I,
)
_BUILD_PHRASE = re.compile(r"(?:生产|再生产|做|再做|按)\s*-?\d+(?:\.\d+)?\s*(?:台|套|个)")


def _fold(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _search_needle(query: str) -> str:
    return _fold(_NOISE.sub("", _BUILD_PHRASE.sub("", query)))


def _revision_dict(revision: ProductRevision) -> dict:
    return {
        "id": revision.id,
        "revision": revision.revision,
        "status": revision.status,
        "is_default": revision.is_default,
    }


def search_products(ctx: ToolContext, args: SearchProductsArgs) -> dict:
    term = args.query.strip()
    pattern = f"%{term}%"
    products = list(
        ctx.db.scalars(
            select(Product)
            .where(
                Product.lifecycle_status == "active",
                or_(Product.code.ilike(pattern), Product.name.ilike(pattern)),
            )
            .order_by(Product.code)
            .limit(args.limit)
        ).all()
    )
    if not products:
        needle = _search_needle(term)
        scored: list[tuple[int, Product]] = []
        for product in ctx.db.scalars(
            select(Product)
            .where(Product.lifecycle_status == "active")
            .order_by(Product.code)
            .limit(2000)
        ).all():
            code = _fold(product.code)
            name = _fold(product.name)
            score = 0
            if needle and needle in {code, name}:
                score = 10000
            elif needle and (needle in code or needle in name or code in needle or name in needle):
                score = max(len(needle), min(len(code), len(name))) * 100
            else:
                query_tokens = {
                    _fold(token)
                    for token in re.findall(r"[A-Za-z0-9-]+|[\u3400-\u9fff]{2,}", term)
                    if _fold(token) and _fold(token) not in {"bom", "产品", "单台", "够不够"}
                }
                score = sum(token in code or token in name for token in query_tokens) * 10
                folded_term = _fold(term)
                mention_positions = [
                    folded_term.find(token)
                    for token in {
                        _fold(token)
                        for token in re.findall(
                            r"[A-Za-z0-9-]+|[\u3400-\u9fff]{2,}",
                            f"{product.code} {product.name}",
                        )
                        if len(_fold(token)) >= 3
                        and _fold(token) not in {"prod", "robot", "机器人", "产品"}
                    }
                    if folded_term.find(token) >= 0
                ]
                if mention_positions:
                    score += max(1, 30 - min(mention_positions))
            if score:
                scored.append((score, product))
        if scored:
            best = max(score for score, _product in scored)
            products = [product for score, product in scored if score == best][: args.limit]

    product_ids = [product.id for product in products]
    revisions_by_product: dict[int, list[ProductRevision]] = {
        product_id: [] for product_id in product_ids
    }
    if product_ids:
        for revision in ctx.db.scalars(
            select(ProductRevision)
            .where(
                ProductRevision.product_id.in_(product_ids),
                ProductRevision.status == "released",
            )
            .order_by(ProductRevision.product_id, ProductRevision.revision)
        ).all():
            revisions_by_product[revision.product_id].append(revision)
    lowered = _search_needle(term)
    exact = [
        product for product in products if lowered in {_fold(product.code), _fold(product.name)}
    ]
    selected = exact or products
    return {
        "query": term,
        "items": [
            {
                "id": product.id,
                "code": product.code,
                "name": product.name,
                "description": product.description,
                "lifecycle_status": product.lifecycle_status,
                "released_revisions": [
                    _revision_dict(revision) for revision in revisions_by_product[product.id]
                ],
                "default_revision": next(
                    (
                        _revision_dict(revision)
                        for revision in revisions_by_product[product.id]
                        if revision.is_default
                    ),
                    None,
                ),
            }
            for product in selected
        ],
        "count": len(selected),
        "exact_match_ids": [product.id for product in exact],
    }


def _resolve_revision(ctx: ToolContext, args: ProductBomArgs) -> tuple[Product, ProductRevision]:
    if args.product_revision_id is not None:
        revision = ctx.db.get(ProductRevision, args.product_revision_id)
        if revision is None or revision.status == "obsolete":
            raise BusinessError("PRODUCT_REVISION_NOT_FOUND", "产品版本不存在或已停用", 404)
        product = ctx.db.get(Product, revision.product_id)
        if args.product_id is not None and args.product_id != product.id:
            raise BusinessError("PRODUCT_REVISION_MISMATCH", "产品版本不属于所选产品", 409)
        return product, revision

    product = ctx.db.get(Product, args.product_id)
    if product is None or product.lifecycle_status == "archived":
        raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或已归档", 404)
    if args.revision:
        revision = ctx.db.scalar(
            select(ProductRevision).where(
                ProductRevision.product_id == product.id,
                ProductRevision.revision.ilike(args.revision),
                ProductRevision.status != "obsolete",
            )
        )
        if revision is None:
            raise BusinessError("PRODUCT_REVISION_NOT_FOUND", "产品版本不存在", 404)
        return product, revision

    defaults = list(
        ctx.db.scalars(
            select(ProductRevision).where(
                ProductRevision.product_id == product.id,
                ProductRevision.status == "released",
                ProductRevision.is_default.is_(True),
            )
        ).all()
    )
    if len(defaults) != 1:
        revisions = list(
            ctx.db.scalars(
                select(ProductRevision).where(
                    ProductRevision.product_id == product.id,
                    ProductRevision.status == "released",
                )
            ).all()
        )
        raise BusinessError(
            "PRODUCT_REVISION_REQUIRED",
            "产品没有唯一的已发布默认版本，请先选择产品版本",
            409,
            details={"revisions": [_revision_dict(item) for item in revisions]},
        )
    return product, defaults[0]


def get_product_bom(ctx: ToolContext, args: ProductBomArgs) -> dict:
    product, revision = _resolve_revision(ctx, args)
    rows = list(
        ctx.db.execute(
            select(ProductBomItem, Material)
            .join(Material, Material.id == ProductBomItem.material_id)
            .where(ProductBomItem.product_revision_id == revision.id)
            .order_by(Material.code)
        ).all()
    )
    if not rows:
        raise BusinessError("PRODUCT_BOM_EMPTY", "该产品版本还没有单台 BOM", 409)
    return {
        "product": {"id": product.id, "code": product.code, "name": product.name},
        "revision": _revision_dict(revision),
        "items": [
            {
                "id": item.id,
                "material_id": material.id,
                "code": material.code,
                "name": material.name,
                "mpn": material.mpn,
                "unit": material.unit,
                "quantity_per_unit": str(item.quantity_per_unit),
                "available_quantity": str(material.available_quantity),
                "safety_stock": str(material.safety_stock),
            }
            for item, material in rows
        ],
        "count": len(rows),
        "quantity_semantics": "单台用量；未使用 Project BomItem.required_quantity",
    }


def analyze_product_build_readiness(
    ctx: ToolContext,
    args: ProductBuildReadinessArgs,
) -> dict:
    _product, revision = _resolve_revision(ctx, args)
    return BuildReadinessService(ctx.db).analyze(
        revision.id,
        args.build_quantity,
        args.project_id,
    )
