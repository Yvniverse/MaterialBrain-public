"""Zero-LLM, read-only smoke for restored Warehouse, Product Build and CI paths."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.tools.common import ToolContext
from app.agent.tools.inventory import get_inventory_availability
from app.agent.tools.locations import find_material_locations
from app.agent.tools.materials import search_materials
from app.component_intelligence.core import ComponentSearchCore
from app.core.database import SessionLocal
from app.models import (
    AgentActionProposal,
    AgentConversationContext,
    BuildPlan,
    Project,
    ProjectReservation,
    StockMovement,
    User,
)
from app.schemas.agent import MaterialIdArgs, SearchMaterialsArgs
from app.services.build_readiness import BuildReadinessService


def _write_snapshot(db) -> tuple[int, ...]:
    return tuple(
        int(db.scalar(select(func.count()).select_from(model)) or 0)
        for model in (
            StockMovement,
            ProjectReservation,
            AgentActionProposal,
            BuildPlan,
            AgentConversationContext,
        )
    )


def main() -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        before = _write_snapshot(db)
        context = ToolContext(db, user, "portfolio-restore-read-smoke")

        warehouse = search_materials(
            context,
            SearchMaterialsArgs(query="PORT-MCU-F405RGT6", limit=5),
        )
        material_id = warehouse["selected_material_id"]
        if material_id is None:
            raise SystemExit("Warehouse smoke did not resolve one exact material")
        inventory = get_inventory_availability(
            context,
            MaterialIdArgs(material_id=material_id),
        )
        locations = find_material_locations(
            context,
            MaterialIdArgs(material_id=material_id),
        )

        project = db.scalar(
            select(Project)
            .where(Project.product_revision_id.is_not(None))
            .order_by(Project.id)
        )
        readiness = BuildReadinessService(db).analyze(
            project.product_revision_id,
            1,
            project.id,
        )
        component = ComponentSearchCore(
            db,
            user,
            "portfolio-restore-ci-smoke",
        ).search("CAN FD 收发器", limit=3)
        after = _write_snapshot(db)

    result = {
        "warehouse_agent_read_path": (
            warehouse["count"] == 1
            and inventory["material_id"] == material_id
            and locations["material_id"] == material_id
        ),
        "product_build_read_path": (
            readiness["read_only"] is True
            and readiness["build_quantity"] == 1
            and len(readiness["items"]) > 0
        ),
        "component_search_core_read_path": (
            component.read_only is True
            and component.candidate_only is True
            and component.count > 0
        ),
        "model_calls": 0,
        "writes_before": before,
        "writes_after": after,
        "read_only_write_snapshot_unchanged": before == after,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(
        result[key]
        for key in (
            "warehouse_agent_read_path",
            "product_build_read_path",
            "component_search_core_read_path",
            "read_only_write_snapshot_unchanged",
        )
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
