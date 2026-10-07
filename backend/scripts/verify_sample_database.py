"""Verify that the normal business database is a clean synthetic Sample baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.models import (
    AgentActionProposal,
    AgentConversationContext,
    Attachment,
    BomItem,
    BuildPlan,
    InventoryLot,
    Loan,
    Location,
    Material,
    Product,
    ProductBomItem,
    ProductRevision,
    Project,
    ProjectReservation,
    PurchaseOrder,
    Session,
    StockMovement,
    Stocktake,
    Supplier,
    User,
)
from app.sample_data.cable_locations import CableLocationReconciliationService
from app.services.product_revisions import calculate_bom_hash

DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "sample_data"
    / "v2_1"
    / "sample_products_v1.json"
)
EXTENDED_PRODUCTS_PATH = (
    Path(__file__).resolve().parents[1]
    / "sample_data"
    / "v2_3"
    / "sample_cable_products_v1.json"
)
PROJECTS_PATH = (
    Path(__file__).resolve().parents[1]
    / "sample_data"
    / "v2"
    / "v1_demo_projects_bom.json"
)
EXTENDED_PROJECTS_PATH = (
    Path(__file__).resolve().parents[1]
    / "sample_data"
    / "v2"
    / "demo_projects_extension.json"
)
EXPECTED_COUNTS = {
    "materials": 222,
    "projects": 12,
    "project_bom_items": 161,
    "project_reservations": 10,
    # The supplied v2 seeder creates 746 locations after the one default warehouse.
    # The handoff's 998 figure was explicitly approximate and came from the former mixed DB.
    "locations": 857,
    "products": 8,
    "product_revisions": 12,
    "product_bom_items": 133,
}


def _count(db, model) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def verify(expected_admin: str) -> dict:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    expected_product_codes = {item["code"] for item in dataset["products"]}
    expected_product_codes.update(
        item["code"]
        for item in json.loads(EXTENDED_PRODUCTS_PATH.read_text(encoding="utf-8"))["new_products"]
    )
    expected_project_codes = {
        item["code"]
        for path in (PROJECTS_PATH, EXTENDED_PROJECTS_PATH)
        for item in json.loads(path.read_text(encoding="utf-8"))["projects"]
    }

    with SessionLocal() as db:
        counts = {
            "materials": _count(db, Material),
            "projects": _count(db, Project),
            "project_bom_items": _count(db, BomItem),
            "project_reservations": _count(db, ProjectReservation),
            "locations": _count(db, Location),
            "products": _count(db, Product),
            "product_revisions": _count(db, ProductRevision),
            "product_bom_items": _count(db, ProductBomItem),
        }
        users = list(db.scalars(select(User).order_by(User.id)).all())
        materials = list(db.scalars(select(Material)).all())
        projects = list(db.scalars(select(Project)).all())
        products = list(db.scalars(select(Product)).all())
        revisions = list(db.scalars(select(ProductRevision)).all())

        non_sample_materials = [
            item.code
            for item in materials
            if not (
                ((item.attributes or {}).get("sample_data") or {}).get("stock_is_synthetic")
                is True
                or (item.attributes or {}).get("stock_is_synthetic") is True
            )
        ]
        non_sample_projects = [
            item.code for item in projects if item.code not in expected_project_codes
        ]
        missing_projects = sorted(expected_project_codes - {item.code for item in projects})
        unexpected_products = sorted({item.code for item in products} - expected_product_codes)
        missing_products = sorted(expected_product_codes - {item.code for item in products})

        revision_hash_failures: list[int] = []
        for revision in revisions:
            items = list(
                db.scalars(
                    select(ProductBomItem).where(ProductBomItem.product_revision_id == revision.id)
                ).all()
            )
            if (
                revision.status != "released"
                or not revision.bom_hash
                or revision.bom_hash != calculate_bom_hash(items)
            ):
                revision_hash_failures.append(revision.id)

        default_counts = dict(
            db.execute(
                select(ProductRevision.product_id, func.count(ProductRevision.id))
                .where(ProductRevision.is_default.is_(True))
                .group_by(ProductRevision.product_id)
            ).all()
        )
        default_failures = [
            product.id for product in products if default_counts.get(product.id, 0) != 1
        ]

        prohibited_history = {
            "suppliers": _count(db, Supplier),
            "purchase_orders": _count(db, PurchaseOrder),
            "loans": _count(db, Loan),
            "stocktakes": _count(db, Stocktake),
            "attachments": _count(db, Attachment),
            "sessions": _count(db, Session),
            "agent_action_proposals": _count(db, AgentActionProposal),
            "agent_conversation_contexts": _count(db, AgentConversationContext),
            "build_plans": _count(db, BuildPlan),
        }
        synthetic_stock_history_failures = int(
            db.scalar(
                select(func.count())
                .select_from(StockMovement)
                .where(
                    ~StockMovement.idempotency_key.startswith("sample-v2-"),
                    ~StockMovement.idempotency_key.startswith("sample-cables-v1-"),
                )
            )
            or 0
        )
        invalid_lots = sum(
            not (
                ((material.attributes or {}).get("sample_data") or {}).get("stock_is_synthetic")
                is True
                or (material.attributes or {}).get("stock_is_synthetic") is True
            )
            for _lot, material in db.execute(
                select(InventoryLot, Material).join(
                    Material, Material.id == InventoryLot.material_id
                )
            ).all()
        )
        cable_location_audit = CableLocationReconciliationService(db).audit()
        cable_location_summary = cable_location_audit["summary"]

        checks = {
            "exact_expected_counts": counts == EXPECTED_COUNTS,
            "single_selected_admin_preserved": (
                len(users) == 1
                and users[0].username == expected_admin
                and users[0].is_active
                and not users[0].is_deleted
                and bool(users[0].password_hash)
            ),
            "all_material_stock_is_synthetic": not non_sample_materials,
            "exact_project_dataset": not non_sample_projects and not missing_projects,
            "exact_product_dataset": not unexpected_products and not missing_products,
            "released_revision_hashes_valid": not revision_hash_failures,
            "one_default_revision_per_product": not default_failures,
            "no_preexisting_business_history": not any(prohibited_history.values()),
            "stock_history_is_sample_seed_only": synthetic_stock_history_failures == 0,
            "inventory_lots_reference_synthetic_materials": invalid_lots == 0,
            "all_positive_cable_stock_has_visible_physical_drawers": bool(
                cable_location_summary["release_gate_passed"]
                and cable_location_summary["cable_sku_total"] == 80
                and cable_location_summary["exact_located_skus"] == 80
                and cable_location_summary["positive_quantity"] == "1186.0000"
            ),
        }
        failures = [name for name, passed in checks.items() if not passed]
        return {
            "sample_database_verified": not failures,
            "real_history_business_rows_remaining": (
                len(non_sample_materials)
                + len(non_sample_projects)
                + len(unexpected_products)
                + sum(prohibited_history.values())
                + synthetic_stock_history_failures
                + invalid_lots
            ),
            "counts": counts,
            "expected_counts": EXPECTED_COUNTS,
            "checks": checks,
            "failures": failures,
            "details": {
                "non_sample_material_codes": non_sample_materials,
                "non_sample_project_codes": non_sample_projects,
                "missing_project_codes": missing_projects,
                "unexpected_product_codes": unexpected_products,
                "missing_product_codes": missing_products,
                "revision_hash_failure_ids": revision_hash_failures,
                "default_revision_failure_product_ids": default_failures,
                "prohibited_history_counts": prohibited_history,
                "synthetic_stock_history_failures": synthetic_stock_history_failures,
                "invalid_inventory_lots": invalid_lots,
                "cable_location_summary": cable_location_summary,
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-admin", required=True)
    args = parser.parse_args()
    result = verify(args.expected_admin)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["sample_database_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
