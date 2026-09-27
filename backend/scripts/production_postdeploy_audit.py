#!/usr/bin/env python3
"""Read-only production smoke/invariant audit after a MaterialBrain deployment."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, or_, select, text

from alembic.config import Config
from alembic.script import ScriptDirectory
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import (
    InventoryLot,
    LocationMapBinding,
    Material,
    PickAllocation,
    PickTask,
    ProjectReservation,
    StockMovement,
    WarehouseMap,
)


def count(db, model) -> int:
    return int(db.scalar(select(func.count(model.id))) or 0)


def main() -> int:
    alembic_ini = Path.cwd() / "alembic.ini"
    if not alembic_ini.is_file():
        alembic_ini = Path.cwd() / "alembic.ini"
    if not alembic_ini.is_file():
        alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    script_head = ScriptDirectory.from_config(Config(str(alembic_ini))).get_current_head()
    failures: list[str] = []
    with SessionLocal() as db:
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ ONLY"))
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ ONLY"))
        revision = db.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
        if revision != script_head:
            failures.append(f"database revision {revision!r} != script head {script_head!r}")

        material_invalid = int(
            db.scalar(
                select(func.count(Material.id)).where(
                    or_(
                        Material.quantity < 0,
                        Material.reserved_quantity < 0,
                        Material.reserved_quantity > Material.quantity,
                    )
                )
            )
            or 0
        )
        lot_invalid = int(
            db.scalar(select(func.count(InventoryLot.id)).where(InventoryLot.quantity < 0)) or 0
        )
        reservation_invalid = int(
            db.scalar(
                select(func.count(ProjectReservation.id)).where(
                    or_(ProjectReservation.quantity < 0, ProjectReservation.consumed_quantity < 0)
                )
            )
            or 0
        )
        allocation_invalid = int(
            db.scalar(
                select(func.count(PickAllocation.id)).where(
                    or_(
                        PickAllocation.planned_quantity <= 0,
                        PickAllocation.picked_quantity < 0,
                        PickAllocation.picked_quantity > PickAllocation.planned_quantity,
                        PickAllocation.route_sequence <= 0,
                    )
                )
            )
            or 0
        )
        for name, value in (
            ("material_invalid", material_invalid),
            ("inventory_lot_invalid", lot_invalid),
            ("project_reservation_invalid", reservation_invalid),
            ("pick_allocation_invalid", allocation_invalid),
        ):
            if value:
                failures.append(f"{name}={value}")

        maps = list(db.scalars(select(WarehouseMap).order_by(WarehouseMap.id)).all())
        active_maps = [item for item in maps if item.status == "active"]
        map_rows = [
            {
                "id": item.id,
                "code": item.code,
                "version": item.version,
                "status": item.status,
                "calibration_status": item.calibration_status,
                "graph_hash": item.graph_hash,
                "binding_count": int(
                    db.scalar(
                        select(func.count(LocationMapBinding.id)).where(
                            LocationMapBinding.warehouse_map_id == item.id
                        )
                    )
                    or 0
                ),
            }
            for item in maps
        ]

        result = {
            "status": "PASS" if not failures else "FAIL",
            "backend_build_sha": settings.materialbrain_build_sha,
            "database_revision": revision,
            "script_head": script_head,
            "counts": {
                "materials": count(db, Material),
                "inventory_lots": count(db, InventoryLot),
                "project_reservations": count(db, ProjectReservation),
                "stock_movements": count(db, StockMovement),
                "pick_tasks": count(db, PickTask),
                "pick_allocations": count(db, PickAllocation),
                "warehouse_maps": len(maps),
                "active_warehouse_maps": len(active_maps),
            },
            "invariants": {
                "material_invalid": material_invalid,
                "inventory_lot_invalid": lot_invalid,
                "project_reservation_invalid": reservation_invalid,
                "pick_allocation_invalid": allocation_invalid,
            },
            "warehouse_maps": map_rows,
            "failures": failures,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
