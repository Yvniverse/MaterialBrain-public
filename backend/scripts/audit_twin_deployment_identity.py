#!/usr/bin/env python3
"""Verify the deployed database uses the exact warehouse-map identity qualified for the twin."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import func, select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.models import LocationMapBinding, WarehouseMap
from app.services.warehouse_maps import WarehouseMapService


def current_database_name(db) -> str:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return str(db.execute(text("SELECT current_database()")).scalar_one())
    return str(db.bind.dialect.name if db.bind is not None else "unknown")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--map-code", required=True)
    parser.add_argument("--map-version", required=True)
    parser.add_argument("--graph-hash", required=True)
    parser.add_argument("--binding-count", type=int, required=True)
    parser.add_argument("--require-active", action="store_true")
    args = parser.parse_args()
    failures: list[str] = []
    with SessionLocal() as db:
        database = current_database_name(db)
        if database != args.expected_database_name:
            failures.append(f"database {database!r} != {args.expected_database_name!r}")
        maps = list(
            db.scalars(select(WarehouseMap).where(WarehouseMap.code == args.map_code)).all()
        )
        if len(maps) != 1:
            failures.append(f"expected exactly one map {args.map_code}, got {len(maps)}")
            map_row = maps[0] if maps else None
        else:
            map_row = maps[0]
        binding_count = None
        reconstructed_hash = None
        if map_row is not None:
            reconstructed_hash = WarehouseMapService(db).definition(map_row.id).graph_hash
            if reconstructed_hash != map_row.graph_hash:
                failures.append("persisted graph differs from recorded graph hash")
            if map_row.version != args.map_version:
                failures.append(f"map version {map_row.version!r} != {args.map_version!r}")
            if map_row.graph_hash != args.graph_hash.lower():
                failures.append("graph hash mismatch")
            if args.require_active and map_row.status != "active":
                failures.append(f"map status {map_row.status!r} != 'active'")
            binding_count = int(
                db.scalar(
                    select(func.count(LocationMapBinding.id)).where(
                        LocationMapBinding.warehouse_map_id == map_row.id
                    )
                )
                or 0
            )
            if binding_count != args.binding_count:
                failures.append(f"binding count {binding_count} != {args.binding_count}")
        result = {
            "status": "PASS" if not failures else "FAIL",
            "database": database,
            "map_code": map_row.code if map_row else None,
            "map_id": map_row.id if map_row else None,
            "map_version": map_row.version if map_row else None,
            "map_status": map_row.status if map_row else None,
            "graph_hash": map_row.graph_hash if map_row else None,
            "binding_count": binding_count,
            "reconstructed_graph_hash": reconstructed_hash,
            "failures": failures,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
