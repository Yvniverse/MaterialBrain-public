from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools.common import ToolContext
from app.agent.tools.locations import find_material_locations
from app.core.exceptions import BusinessError
from app.models import InventoryLot, Location, Material, User
from app.schemas.agent import MaterialIdArgs
from app.services.audit import add_audit
from app.services.inventory import InventoryService
from app.services.location_organizers import LocationOrganizerService

DATASET_NAME = "sample_cables_v1"
LOCATION_ROOT_CODE = "WH-RD"
LOCATION_ZONE_CODE = "PF-CABLE"
LOCATION_ZONE_NAME = "线缆专区"
LEGACY_BIN_COUNT = 8
DRAWER_RACK_CODE = "CABLE-RACK-01"
DRAWER_RACK_NAME = "100抽线缆柜"
EXPECTED_CABLE_SKUS = 80
EXPECTED_CABLE_QUANTITY = Decimal("1186")
RECONCILE_REQUEST_ID = "sample-cables-v1-location-reconciliation"


@dataclass(frozen=True)
class CableLocationLayout:
    root: Location
    zone: Location
    rack: Location
    drawers: list[Location]
    legacy_bins: list[Location]
    created_locations: int


def ensure_cable_location_layout(db: Session) -> CableLocationLayout:
    """Ensure the stable synthetic cable area and its visible 100-drawer rack."""

    root = db.scalar(select(Location).where(Location.code == LOCATION_ROOT_CODE))
    if root is None or root.type != "warehouse":
        raise BusinessError(
            "SAMPLE_CABLE_ROOT_LOCATION_MISSING",
            "合成 Sample 仓库根库位不存在。",
            409,
        )
    created = 0
    zone = db.scalar(select(Location).where(Location.code == LOCATION_ZONE_CODE))
    zone_path = f"{root.full_path} / {LOCATION_ZONE_NAME}"
    if zone is None:
        zone = Location(
            code=LOCATION_ZONE_CODE,
            name=LOCATION_ZONE_NAME,
            parent_id=root.id,
            type="zone",
            full_path=zone_path,
            manager="仓库运营",
            notes="线缆专用存储区域。",
        )
        db.add(zone)
        db.flush()
        created += 1
    elif zone.parent_id != root.id or zone.type != "zone":
        raise BusinessError(
            "SAMPLE_CABLE_LOCATION_CONFLICT",
            "线缆区库位结构冲突。",
            409,
        )
    else:
        zone.name = LOCATION_ZONE_NAME
        zone.full_path = zone_path
        zone.manager = "仓库运营"
        zone.notes = "线缆专用存储区域。"
        zone.is_active = True

    # Keep the eight v2.5 source bins as empty historical locations so transfer
    # movements remain traceable and a fresh seed has the same stable shape.
    legacy_bins: list[Location] = []
    for number in range(1, LEGACY_BIN_COUNT + 1):
        code = f"{LOCATION_ZONE_CODE}-{number:02d}"
        name = f"历史线缆箱 {number:02d}"
        item = db.scalar(select(Location).where(Location.code == code))
        if item is None:
            item = Location(
                code=code,
                name=name,
                parent_id=zone.id,
                type="bin",
                full_path=f"{zone_path} / {name}",
                manager="仓库运营",
                notes="历史线缆来源库位；不应保留当前库存。",
            )
            db.add(item)
            db.flush()
            created += 1
        elif item.parent_id != zone.id or item.type != "bin":
            raise BusinessError(
                "SAMPLE_CABLE_LOCATION_CONFLICT",
                "历史线缆箱结构冲突。",
                409,
                details={"code": code},
            )
        else:
            item.name = name
            item.full_path = f"{zone_path} / {name}"
            item.manager = "仓库运营"
            item.notes = "历史线缆来源库位；不应保留当前库存。"
            item.is_active = True
        legacy_bins.append(item)

    rack, drawers, rack_created = LocationOrganizerService(db).ensure_drawer_rack(
        parent_id=zone.id,
        code=DRAWER_RACK_CODE,
        name=DRAWER_RACK_NAME,
        manager="仓库运营",
        notes="线缆货架：80 个已分配抽屉，20 个备用抽屉。",
    )
    created += rack_created
    db.commit()
    return CableLocationLayout(
        root=root,
        zone=zone,
        rack=rack,
        drawers=drawers,
        legacy_bins=legacy_bins,
        created_locations=created,
    )


def _organizer_ancestor(db: Session, location: Location) -> Location | None:
    current: Location | None = location
    visited: set[int] = set()
    while current is not None and current.id not in visited:
        visited.add(current.id)
        if current.type == "box" and current.organizer_style:
            return current
        current = db.get(Location, current.parent_id) if current.parent_id else None
    return None


class CableLocationReconciliationService:
    """Audit and safely relocate synthetic Cable lots without changing inventory."""

    def __init__(self, db: Session, operator: User | None = None):
        self.db = db
        self.operator = operator

    def _materials(self) -> list[Material]:
        return list(
            self.db.scalars(
                select(Material)
                .where(
                    Material.is_deleted.is_(False),
                    Material.attributes["material_kind"].as_string() == "cable",
                )
                .order_by(Material.code)
            ).all()
        )

    def audit(self, *, verify_agent: bool = False) -> dict[str, Any]:
        materials = self._materials()
        child_parent_ids = set(
            self.db.scalars(
                select(Location.parent_id).where(Location.parent_id.is_not(None)).distinct()
            ).all()
        )
        rows: list[dict[str, Any]] = []
        totals = {
            "positive_quantity": Decimal("0"),
            "leaf_container_quantity": Decimal("0"),
            "unallocated_quantity": Decimal("0"),
            "root_only_quantity": Decimal("0"),
            "non_navigable_quantity": Decimal("0"),
        }
        exact_skus = partial_skus = unallocated_skus = root_only_skus = 0
        agent_mismatches: list[str] = []
        for material in materials:
            lot_rows = list(
                self.db.execute(
                    select(InventoryLot, Location)
                    .join(Location, Location.id == InventoryLot.location_id)
                    .where(
                        InventoryLot.material_id == material.id,
                        InventoryLot.quantity > 0,
                    )
                    .order_by(Location.full_path)
                ).all()
            )
            positive_lot_quantity = sum(
                (Decimal(lot.quantity) for lot, _location in lot_rows), Decimal("0")
            )
            book_quantity = Decimal(material.quantity)
            unallocated = max(book_quantity - positive_lot_quantity, Decimal("0"))
            root_only = sum(
                (
                    Decimal(lot.quantity)
                    for lot, location in lot_rows
                    if location.type in {"warehouse", "zone"} or location.id in child_parent_ids
                ),
                Decimal("0"),
            )
            navigable = sum(
                (
                    Decimal(lot.quantity)
                    for lot, location in lot_rows
                    if location.id not in child_parent_ids
                    and _organizer_ancestor(self.db, location) is not None
                ),
                Decimal("0"),
            )
            non_navigable = sum(
                (
                    Decimal(lot.quantity)
                    for lot, location in lot_rows
                    if _organizer_ancestor(self.db, location) is None
                ),
                Decimal("0"),
            )
            if positive_lot_quantity > book_quantity:
                status = "inconsistent"
            elif book_quantity > 0 and not lot_rows:
                status = "unallocated"
                unallocated_skus += 1
            elif root_only > 0:
                status = "root_only"
                root_only_skus += 1
            elif non_navigable > 0:
                status = "non_navigable"
                partial_skus += 1
            elif unallocated > 0:
                status = "partial"
                partial_skus += 1
            else:
                status = "exact"
                if book_quantity > 0:
                    exact_skus += 1

            locations = [
                {
                    "location_id": location.id,
                    "code": location.code,
                    "full_path": location.full_path,
                    "quantity": str(lot.quantity),
                    "organizer_id": (
                        organizer.id
                        if (organizer := _organizer_ancestor(self.db, location)) is not None
                        else None
                    ),
                    "organizer_style": organizer.organizer_style if organizer else None,
                }
                for lot, location in lot_rows
            ]
            if verify_agent and self.operator is not None and book_quantity > 0:
                agent_result = find_material_locations(
                    ToolContext(self.db, self.operator, "cable-location-audit"),
                    MaterialIdArgs(material_id=material.id),
                )
                expected = {(item["location_id"], Decimal(item["quantity"])) for item in locations}
                observed = {
                    (item["location_id"], Decimal(item["quantity_at_location"]))
                    for item in agent_result["locations"]
                    if item["quantity_is_exact"]
                }
                if expected != observed or agent_result["distribution_status"] != "complete":
                    agent_mismatches.append(material.code)

            totals["positive_quantity"] += book_quantity
            totals["leaf_container_quantity"] += navigable
            totals["unallocated_quantity"] += unallocated
            totals["root_only_quantity"] += root_only
            totals["non_navigable_quantity"] += non_navigable
            rows.append(
                {
                    "material_id": material.id,
                    "code": material.code,
                    "mpn": material.mpn,
                    "name": material.name,
                    "dataset": (material.attributes or {}).get("sample_cable_dataset"),
                    "legacy_storage_location": (material.attributes or {}).get(
                        "storage_location", ""
                    ),
                    "book_quantity": str(book_quantity),
                    "positive_lot_quantity": str(positive_lot_quantity),
                    "positive_lot_count": len(lot_rows),
                    "unallocated_quantity": str(unallocated),
                    "root_only_quantity": str(root_only),
                    "non_navigable_quantity": str(non_navigable),
                    "leaf_container_quantity": str(navigable),
                    "locations": locations,
                    "status": status,
                }
            )

        stocked = [item for item in rows if Decimal(item["book_quantity"]) > 0]
        summary = {
            "cable_sku_total": len(rows),
            "cable_skus_with_stock": len(stocked),
            "exact_located_skus": exact_skus,
            "partial_skus": partial_skus,
            "unallocated_skus": unallocated_skus,
            "root_only_skus": root_only_skus,
            **{key: str(value) for key, value in totals.items()},
            "agent_location_mismatch_count": len(agent_mismatches),
            "agent_location_mismatch_codes": agent_mismatches,
        }
        summary["release_gate_passed"] = bool(
            len(stocked) == exact_skus
            and totals["positive_quantity"] == totals["leaf_container_quantity"]
            and totals["unallocated_quantity"] == 0
            and totals["root_only_quantity"] == 0
            and totals["non_navigable_quantity"] == 0
            and not agent_mismatches
        )
        return {"summary": summary, "items": rows}

    def reconcile(self) -> dict[str, Any]:
        if self.operator is None:
            raise RuntimeError("A selected operator is required for an audited reconciliation")
        before = self.audit()
        materials = [
            item
            for item in self._materials()
            if Decimal(item.quantity) > 0
            and (item.attributes or {}).get("sample_cable_dataset") == DATASET_NAME
        ]
        if len(materials) != EXPECTED_CABLE_SKUS:
            raise RuntimeError("Expected exactly 80 positive-stock synthetic Sample Cable SKUs")
        if Decimal(before["summary"]["positive_quantity"]) != EXPECTED_CABLE_QUANTITY:
            raise RuntimeError("Cable inventory total is not the fixed 1,186 synthetic quantity")
        if Decimal(before["summary"]["unallocated_quantity"]) != 0:
            raise RuntimeError("Refusing relocation while Cable book stock and lot totals differ")

        layout = ensure_cable_location_layout(self.db)
        transfers = 0
        replayed = 0
        for material, target in zip(
            materials,
            layout.drawers[: len(materials)],
            strict=True,
        ):
            source_lots = list(
                self.db.scalars(
                    select(InventoryLot)
                    .where(
                        InventoryLot.material_id == material.id,
                        InventoryLot.quantity > 0,
                    )
                    .order_by(InventoryLot.location_id)
                ).all()
            )
            for source_lot in source_lots:
                if source_lot.location_id == target.id:
                    continue
                source = self.db.get(Location, source_lot.location_id)
                result = InventoryService(
                    self.db,
                    self.operator.id,
                    RECONCILE_REQUEST_ID,
                ).transfer(
                    material.id,
                    Decimal(source_lot.quantity),
                    source_lot.location_id,
                    target.id,
                    (
                        f"sample-cables-v1-relocate-{material.code}-"
                        f"{source.code if source else source_lot.location_id}"
                    )[:96],
                    "线缆物理库位校准",
                    "Quantity unchanged; existing synthetic stock moved to a visible drawer.",
                )
                if result.get("idempotent_replay"):
                    replayed += 1
                else:
                    transfers += 1
            previous_location_id = material.location_id
            if previous_location_id != target.id:
                material.location_id = target.id
                add_audit(
                    self.db,
                    self.operator.id,
                    "sample_cable.location_reconcile",
                    "material",
                    str(material.id),
                    RECONCILE_REQUEST_ID,
                    before={"primary_location_id": previous_location_id},
                    after={
                        "primary_location_id": target.id,
                        "actual_location_path": target.full_path,
                        "legacy_storage_location_unchanged": (material.attributes or {}).get(
                            "storage_location", ""
                        ),
                    },
                )
                self.db.commit()

        after = self.audit(verify_agent=True)
        if not after["summary"]["release_gate_passed"]:
            raise RuntimeError("Cable physical-location release gate did not pass")
        if Decimal(after["summary"]["positive_quantity"]) != Decimal(
            before["summary"]["positive_quantity"]
        ):
            raise RuntimeError("Cable inventory changed during location reconciliation")
        return {
            "before": before["summary"],
            "after": after["summary"],
            "layout": {
                "root": layout.root.full_path,
                "zone": layout.zone.full_path,
                "rack": layout.rack.full_path,
                "drawer_count": len(layout.drawers),
                "assigned_drawers": len(materials),
                "spare_drawers": len(layout.drawers) - len(materials),
                "created_locations": layout.created_locations,
            },
            "transfers": transfers,
            "idempotent_replays": replayed,
            "inventory_quantity_before": before["summary"]["positive_quantity"],
            "inventory_quantity_after": after["summary"]["positive_quantity"],
            "items": after["items"],
        }


def audit_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"] if "summary" in report else report["after"]
    lines = [
        "# Cable physical location audit",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in summary.items():
        if key == "agent_location_mismatch_codes":
            continue
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "| Code | Quantity | Status | Actual location | Legacy note |",
            "|---|---:|---|---|---|",
        ]
    )
    items = report.get("items") or []
    for item in items:
        paths = "<br>".join(location["full_path"] for location in item["locations"]) or "—"
        lines.append(
            f"| {item['code']} | {item['book_quantity']} | {item['status']} | "
            f"{paths} | {item['legacy_storage_location'] or '—'} |"
        )
    return "\n".join(lines) + "\n"
