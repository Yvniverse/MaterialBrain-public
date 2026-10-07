"""Zero-model, read-only smoke for restored Phase 2.5 cable paths."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.models import (
    AgentActionProposal,
    BuildPlan,
    Product,
    ProductRevision,
    ProjectReservation,
    StockMovement,
    User,
)
from app.schemas.agent import CableSearchArgs
from app.services.build_readiness import BuildReadinessService
from app.services.cable_intelligence import CableSearchService


def _write_snapshot(db) -> tuple[int, ...]:
    return tuple(
        int(db.scalar(select(func.count()).select_from(model)) or 0)
        for model in (
            StockMovement,
            ProjectReservation,
            AgentActionProposal,
            BuildPlan,
        )
    )


def main() -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        if user is None:
            raise SystemExit("Preserved admin identity is missing")
        before = _write_snapshot(db)
        search = CableSearchService(db).search(CableSearchArgs(query="Atlas Camera FFC", limit=10))
        revision_id = db.scalar(
            select(ProductRevision.id)
            .join(Product, Product.id == ProductRevision.product_id)
            .where(
                Product.code == "PROD-ATLAS-AMR",
                ProductRevision.revision == "DVT-CABLE-R1",
                ProductRevision.status == "released",
            )
        )
        readiness = BuildReadinessService(db).analyze(revision_id, 3)
        after = _write_snapshot(db)

    result = {
        "cable_search_resolved": search["count"] == 1,
        "inventory_lot_location_grounded": bool(
            search["items"] and search["items"][0]["locations"]
        ),
        "product_cable_build_readiness": bool(
            readiness.get("items") and readiness.get("read_only") is True
        ),
        "automatic_substitution": search["automatic_substitution"],
        "model_calls": 0,
        "read_only_write_snapshot_unchanged": before == after,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return (
        0
        if result["cable_search_resolved"]
        and result["inventory_lot_location_grounded"]
        and result["product_cable_build_readiness"]
        and result["automatic_substitution"] is False
        and result["read_only_write_snapshot_unchanged"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
