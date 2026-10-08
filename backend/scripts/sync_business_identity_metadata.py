"""Audit or narrowly sync legacy business identity metadata before release capture.

This script intentionally does NOT rewrite MPNs or cable internal identifiers. It only
repairs exact legacy metadata values that contradict the canonical sample data:
- Demo Robotics -> MATERIALBRAIN Robotics for canonical internally designed materials.
- Demo Warehouse -> 仓库运营 for locations carrying the exact legacy manager value.

The location transition is an exact-value migration rather than a broad ``Demo``
replacement. The release API serializes all location managers, including generated
cable-rack children whose seed definition inherits the canonical warehouse manager, so
leaving any exact legacy value behind would leak stale identity in packaged evidence.

Default mode is dry-run. Apply mode requires an exact database-name assertion and an
explicit environment enable flag.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Location, Material

CANONICAL_MATERIALS = (
    Path(__file__).resolve().parents[1] / "sample_data/v2/v1_demo_materials.json"
)
ENABLE_ENV = "MATERIALBRAIN_BUSINESS_IDENTITY_SYNC_ENABLED"


def _canonical_manufacturers() -> dict[str, str]:
    payload = json.loads(CANONICAL_MATERIALS.read_text(encoding="utf-8"))
    return {
        str(item["code"]): str(item.get("manufacturer") or "").strip()
        for item in payload
        if str(item.get("code") or "").strip()
    }


def _audit(db) -> dict[str, Any]:
    canonical = _canonical_manufacturers()
    material_rows = list(
        db.scalars(select(Material).where(Material.code.in_(list(canonical)))).all()
    )
    legacy_manufacturers = [
        material.code
        for material in material_rows
        if material.manufacturer == "Demo Robotics"
        and canonical.get(material.code) == "MATERIALBRAIN Robotics"
    ]
    manufacturer_mismatches = [
        {
            "code": material.code,
            "current": material.manufacturer,
            "canonical": canonical.get(material.code),
        }
        for material in material_rows
        if canonical.get(material.code)
        and material.manufacturer != canonical.get(material.code)
        and material.manufacturer != "Demo Robotics"
    ]
    legacy_locations = list(
        db.scalars(select(Location).where(Location.manager == "Demo Warehouse")).all()
    )
    internal_demo_parts = list(
        db.scalars(
            select(Material).where(
                Material.manufacturer == "MATERIALBRAIN Robotics",
                Material.mpn.ilike("DEMO-%"),
            )
        ).all()
    )
    sample_cable_fallbacks = list(
        db.scalars(select(Material).where(Material.mpn.ilike("SAMPLE-CBL-%"))).all()
    )
    return {
        "legacy_demo_manufacturer_count": len(legacy_manufacturers),
        "legacy_demo_manufacturer_codes": sorted(legacy_manufacturers),
        "legacy_demo_warehouse_count": len(legacy_locations),
        "legacy_demo_warehouse_codes": sorted(location.code for location in legacy_locations),
        "canonical_manufacturer_mismatch_count": len(manufacturer_mismatches),
        "canonical_manufacturer_mismatches": manufacturer_mismatches,
        "internal_demo_part_number_count": len(internal_demo_parts),
        "internal_demo_part_number_codes": sorted(
            material.code for material in internal_demo_parts
        ),
        "sample_cable_fallback_mpn_count": len(sample_cable_fallbacks),
        "notes": [
            "DEMO-* part numbers are audited as internal identifiers, not automatically rewritten.",
            "SAMPLE-CBL-* fallback MPNs are audited as internal uniqueness identifiers, "
            "not automatically rewritten.",
        ],
    }


def _apply(db) -> dict[str, int]:
    canonical = _canonical_manufacturers()
    manufacturer_changes = 0
    for material in db.scalars(
        select(Material).where(
            Material.code.in_(list(canonical)),
            Material.manufacturer == "Demo Robotics",
        )
    ):
        if canonical.get(material.code) != "MATERIALBRAIN Robotics":
            continue
        material.manufacturer = "MATERIALBRAIN Robotics"
        manufacturer_changes += 1

    manager_changes = 0
    for location in db.scalars(
        select(Location).where(Location.manager == "Demo Warehouse")
    ):
        location.manager = "仓库运营"
        manager_changes += 1

    return {
        "manufacturer_changes": manufacturer_changes,
        "location_manager_changes": manager_changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    actual_database = make_url(settings.database_url).database
    if actual_database != args.expected_database_name:
        raise RuntimeError(
            f"Refusing unexpected database: expected={args.expected_database_name!r} "
            f"actual={actual_database!r}"
        )
    if args.apply and os.getenv(ENABLE_ENV, "false").casefold() != "true":
        raise RuntimeError(f"Set {ENABLE_ENV}=true for --apply")

    with SessionLocal() as db:
        before = _audit(db)
        if not args.apply:
            print(json.dumps({"dry_run": True, "before": before}, ensure_ascii=False, indent=2))
            return 0
        changes = _apply(db)
        db.commit()
        after = _audit(db)
        print(
            json.dumps(
                {"dry_run": False, "before": before, "changes": changes, "after": after},
                ensure_ascii=False,
                indent=2,
            )
        )
        return (
            0
            if not after["legacy_demo_manufacturer_count"]
            and not after["legacy_demo_warehouse_count"]
            else 1
        )


if __name__ == "__main__":
    raise SystemExit(main())
