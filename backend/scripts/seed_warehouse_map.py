#!/usr/bin/env python3
"""Safely import only a warehouse-map fixture without running the full demo seeder.

Default behavior is read-only dry-run. `--apply --mode draft` creates the map as
an inactive draft. `--mode demo-active` exists only for sample/demo stacks
and requires an explicit acknowledgement that the geometry is synthetic and not
measured. Production/real-warehouse activation must still go through the normal
measured -> verified -> active lifecycle.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.core.exceptions import BusinessError
from app.models import Location, WarehouseMap
from app.services.warehouse_maps import WarehouseMapService, validate_definition
from app.services.warehouse_routing import load_warehouse_map

DEFAULT_FIXTURE = Path.cwd() / "sample_data" / "v2" / "warehouse_map_v1.json"


def current_database_name(db) -> str:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return str(db.execute(text("SELECT current_database()")).scalar_one())
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        return "sqlite"
    return str(db.bind.dialect.name if db.bind is not None else "unknown")


def inspect_fixture(db, fixture: Path, *, require_complete: bool = False) -> dict:
    definition = load_warehouse_map(str(fixture))
    validate_definition(definition)
    warehouse = db.scalar(select(Location).where(Location.code == definition.warehouse_code))
    missing = []
    for binding in definition.bindings:
        if db.scalar(select(Location.id).where(Location.code == binding.location_code)) is None:
            missing.append(binding.location_code)
    existing = db.scalar(select(WarehouseMap).where(WarehouseMap.code == definition.map_code))
    if warehouse is not None and warehouse.type == "warehouse" and not missing:
        for binding in definition.bindings:
            current = db.scalar(select(Location).where(Location.code == binding.location_code))
            visited = set()
            while current is not None and current.id != warehouse.id and current.id not in visited:
                visited.add(current.id)
                current = db.get(Location, current.parent_id) if current.parent_id else None
            if current is None or current.id != warehouse.id:
                raise BusinessError(
                    "WAREHOUSE_MAP_LOCATION_OUTSIDE_WAREHOUSE", "Binding outside warehouse", 409
                )
        if require_complete:
            WarehouseMapService(db)._validate_location_bindings(warehouse.id, definition)
    return {
        "map_code": definition.map_code,
        "map_name": definition.name,
        "map_version": definition.version,
        "warehouse_code": definition.warehouse_code,
        "calibration_status": definition.calibration_status,
        "graph_hash": definition.graph_hash,
        "node_count": len(definition.nodes),
        "edge_count": len(definition.edges),
        "binding_count": len(definition.bindings),
        "warehouse_found": bool(warehouse and warehouse.type == "warehouse"),
        "missing_binding_locations": sorted(missing),
        "existing": (
            {
                "id": existing.id,
                "status": existing.status,
                "calibration_status": existing.calibration_status,
                "graph_hash": existing.graph_hash,
            }
            if existing is not None
            else None
        ),
        "definition": definition,
        "warehouse": warehouse,
    }


def activate_demo_synthetic(db, service: WarehouseMapService, map_row: WarehouseMap) -> None:
    if map_row.calibration_status != "demo_synthetic":
        raise BusinessError(
            "WAREHOUSE_MAP_DEMO_ACTIVATION_INVALID",
            "demo-active 只允许显式启用 demo_synthetic 地图。",
            409,
        )
    for item in service.list_for_warehouse(map_row.warehouse_location_id):
        if item.id != map_row.id and item.status == "active":
            item.status = "archived"
    map_row.status = "active"
    # Keep calibration provenance in the structured status field. Activation should not
    # inject explanatory/demo copy into the user-facing geometry note.
    db.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--apply", action="store_true", help="Actually write. Default is dry-run.")
    parser.add_argument(
        "--mode",
        choices=("draft", "demo-active"),
        default="draft",
        help="draft is the default; demo-active requires a synthetic demo deployment.",
    )
    parser.add_argument(
        "--confirm-demo-is-not-measured",
        action="store_true",
        help="Required with --mode demo-active.",
    )
    args = parser.parse_args()

    fixture = args.fixture.resolve()
    if not fixture.exists():
        print(
            json.dumps(
                {"status": "FAIL", "reason": f"fixture not found: {fixture}"}, ensure_ascii=False
            )
        )
        return 2

    with SessionLocal() as db:
        actual_db = current_database_name(db)
        if actual_db != args.expected_database_name:
            print(
                json.dumps(
                    {
                        "status": "FAIL",
                        "reason": "database identity mismatch",
                        "expected_database_name": args.expected_database_name,
                        "actual_database_name": actual_db,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 3
        try:
            inspection = inspect_fixture(db, fixture, require_complete=args.mode == "demo-active")
        except BusinessError as exc:
            print(
                json.dumps(
                    {
                        "status": "FAIL",
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 4
        except ValueError as exc:
            print(json.dumps({"status": "FAIL", "reason": str(exc)}, ensure_ascii=False, indent=2))
            return 4

        public = {k: v for k, v in inspection.items() if k not in {"definition", "warehouse"}}
        public.update(
            {
                "database_name": actual_db,
                "fixture": str(fixture),
                "requested_mode": args.mode,
                "apply": args.apply,
            }
        )
        if not inspection["warehouse_found"] or inspection["missing_binding_locations"]:
            public["status"] = "FAIL"
            public["reason"] = (
                "warehouse root/bindings are incomplete; map-only seed refuses to invent locations"
            )
            print(json.dumps(public, ensure_ascii=False, indent=2))
            return 5

        if not args.apply:
            public["status"] = "DRY_RUN_PASS"
            print(json.dumps(public, ensure_ascii=False, indent=2))
            return 0

        if args.mode == "demo-active" and not args.confirm_demo_is_not_measured:
            public["status"] = "FAIL"
            public["reason"] = "--confirm-demo-is-not-measured is required for demo-active"
            print(json.dumps(public, ensure_ascii=False, indent=2))
            return 6

        if inspection["existing"]:
            public["status"] = "FAIL"
            public["reason"] = "existing managed map: use deployment map mode skip"
            print(json.dumps(public, ensure_ascii=False, indent=2))
            return 7

        definition = inspection["definition"]
        service = WarehouseMapService(db)
        map_row = service.seed_definition(definition, activate=False)
        if args.mode == "demo-active":
            activate_demo_synthetic(db, service, map_row)
        db.commit()
        db.refresh(map_row)
        public.update(
            {
                "status": "APPLY_PASS",
                "map_id": map_row.id,
                "map_status": map_row.status,
                "map_calibration_status": map_row.calibration_status,
                "map_graph_hash": map_row.graph_hash,
                "note": (
                    "configured map activated"
                    if map_row.status == "active"
                    else "draft imported"
                ),
            }
        )
        print(json.dumps(public, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
