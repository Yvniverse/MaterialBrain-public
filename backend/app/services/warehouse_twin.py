"""Read-only projection of mapped locations, lots and organizer-local geometry."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models import InventoryLot, Location, LocationMapBinding, Material, WarehouseMap
from app.services.location_organizers import organizer_slot_names
from app.services.warehouse_maps import WarehouseMapService
from app.services.warehouse_routing import local_slot_geometry

DIMENSIONS = {
    "drawer_rack_100": {"width": 0.65, "height": 1.17, "depth": 0.22},
    "standard_56": {"width": 0.56, "height": 0.13, "depth": 0.42},
    "split_configurable": {"width": 0.60, "height": 0.14, "depth": 0.43},
    "shelf_rack_6": {"width": 1.05, "height": 1.85, "depth": 0.46},
}
# Display labels only. Persisted calibration status/hash/history are not changed.
MAP_LABELS = {
    "WH-RD-MAP-V1": "研发仓库布局 V1",
    "WH-RD-TWIN-V3": "研发仓 · 布局 V3",
    "WH-RD-TWIN-V4": "研发仓 · 布局 V4",
}


def warehouse_twin_snapshot(db: Session, map_id: int) -> dict:
    map_row = db.get(WarehouseMap, map_id)
    if map_row is None:
        raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "仓库地图不存在。", 404)
    definition = WarehouseMapService(db).definition(map_id)
    bindings = list(
        db.scalars(
            select(LocationMapBinding)
            .where(LocationMapBinding.warehouse_map_id == map_id)
            .order_by(LocationMapBinding.id)
        ).all()
    )
    locations = {loc.id: loc for loc in db.scalars(select(Location)).all()}
    children: dict[int, list[Location]] = defaultdict(list)
    for loc in locations.values():
        if loc.parent_id is not None:
            children[loc.parent_id].append(loc)

    def subtree(root_id: int) -> set[int]:
        seen: set[int] = set()
        stack = [root_id]
        while stack:
            loc_id = stack.pop()
            if loc_id in seen:
                continue
            seen.add(loc_id)
            stack.extend(child.id for child in children.get(loc_id, []))
        return seen

    root_ids = {b.location_id for b in bindings}
    descendants = {root_id: subtree(root_id) for root_id in root_ids}
    relevant_ids = set().union(*descendants.values()) if descendants else set()
    lots_by_location: dict[int, list[tuple[InventoryLot, Material]]] = defaultdict(list)
    if relevant_ids:
        rows = db.execute(
            select(InventoryLot, Material)
            .join(Material, Material.id == InventoryLot.material_id)
            .where(InventoryLot.location_id.in_(relevant_ids))
        ).all()
        for lot, material in rows:
            lots_by_location[lot.location_id].append((lot, material))

    def stock(location_ids: set[int]) -> dict:
        quantities: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        materials: dict[int, dict] = {}
        lot_count = 0
        for loc_id in sorted(location_ids):
            for lot, material in lots_by_location.get(loc_id, []):
                if lot.quantity == 0:
                    continue
                lot_count += 1
                quantities[material.unit] += lot.quantity
                item = materials.setdefault(
                    material.id,
                    {
                        "id": material.id,
                        "name": material.name,
                        "code": material.code,
                        "unit": material.unit,
                        "quantity": Decimal("0"),
                    },
                )
                item["quantity"] += lot.quantity
        return {
            "material_kind_count": len(materials),
            "lot_count": lot_count,
            "quantities_by_unit": {unit: str(qty) for unit, qty in sorted(quantities.items())},
            "materials": [
                dict(item, quantity=str(item["quantity"])) for item in materials.values()
            ],
        }

    assets = []
    by_binding = {b.location_code: b for b in definition.bindings}
    for binding in bindings:
        organizer = locations.get(binding.location_id)
        if organizer is None:
            continue
        spatial = by_binding[organizer.code]
        style = organizer.organizer_style or binding.local_geometry_kind
        dims = DIMENSIONS.get(
            style, {"width": float(binding.width_m), "height": 1.0, "depth": float(binding.depth_m)}
        )
        left = organizer.organizer_left_module or "small"
        right = organizer.organizer_right_module or "small"
        direct = children.get(organizer.id, [])
        by_name = {}
        for loc in direct:
            prefix = organizer.code + "-"
            suffix = loc.code[len(prefix) :] if loc.code.startswith(prefix) else loc.name.upper()
            by_name[suffix] = loc
        slots = []
        names = organizer_slot_names(style, left, right) if style in DIMENSIONS else []
        for name in names:
            loc = by_name.get(name)
            ids = subtree(loc.id) if loc else set()
            geometry = local_slot_geometry(style, name, left_module=left, right_module=right)
            slots.append(
                {
                    "name": name,
                    "location_id": loc.id if loc else None,
                    "descendant_ids": sorted(ids - {loc.id}) if loc else [],
                    "geometry": geometry.__dict__,
                    **stock(ids),
                }
            )
        boxes = []
        if style == "shelf_rack_6":
            for level in range(1, 7):
                shelf = by_name.get(f"L{level:02d}")
                if shelf:
                    boxes.extend(
                        {"level": level, "code": box.code, "name": box.name, "location_id": box.id}
                        for box in children.get(shelf.id, [])
                        if box.type in {"box", "container"}
                    )
        assets.append(
            {
                "code": organizer.code,
                "name": organizer.name,
                "location_id": organizer.id,
                "style": style,
                "x_m": float(binding.x_m) + float(binding.width_m) / 2,
                "y_m": float(binding.y_m) + float(binding.depth_m) / 2,
                "facing": binding.facing,
                "dimensions": dims,
                "footprint": {"width_m": float(binding.width_m), "depth_m": float(binding.depth_m)},
                "pick_node_code": spatial.pick_node_code,
                "full_path": organizer.full_path,
                "zone": "研发仓库",
                "slots": slots,
                "storage_boxes": boxes,
                **stock(descendants[organizer.id]),
            }
        )
    return {
        "schema_version": 1,
        "map": {
            "id": map_row.id,
            "code": map_row.code,
            "name": MAP_LABELS.get(map_row.code, map_row.name),
            "version": map_row.version,
            "graph_hash": map_row.graph_hash,
            "status": map_row.status,
            "calibration_status": map_row.calibration_status,
            "width_m": float(map_row.width_m),
            "height_m": float(map_row.height_m),
            "nodes": [node.__dict__ for node in definition.nodes],
            "edges": [edge.__dict__ for edge in definition.edges],
        },
        "assets": assets,
        "route": None,
        "task": None,
        "decorations": [
            {
                "kind": "pack",
                "x_m": next(
                    (
                        node.x_m
                        for node in definition.nodes
                        if node.code == definition.default_start_node
                    ),
                    definition.width_m / 2,
                ),
                "y_m": next(
                    (
                        node.y_m
                        for node in definition.nodes
                        if node.code == definition.default_start_node
                    ),
                    0.6,
                ),
            }
        ],
        "metadata": {"source": "database_snapshot", "stock_source": "InventoryLot", "units": "m"},
    }


def warehouse_twin_focus(db: Session, map_id: int, location_id: int) -> dict:
    data = warehouse_twin_snapshot(db, map_id)
    for asset in data["assets"]:
        if asset["location_id"] == location_id:
            return {
                "organizer_code": asset["code"],
                "location_id": location_id,
                "slot_name": None,
                "slot_geometry": None,
                "pick_node_code": asset["pick_node_code"],
            }
        for slot in asset["slots"]:
            if location_id == slot["location_id"] or location_id in slot["descendant_ids"]:
                return {
                    "organizer_code": asset["code"],
                    "organizer_style": asset["style"],
                    "location_id": location_id,
                    "slot_name": slot["name"],
                    "slot_geometry": slot["geometry"],
                    "pick_node_code": asset["pick_node_code"],
                }
    raise BusinessError("LOCATION_NOT_MAPPED_FOR_PICKING", "该库位尚未关联到当前地图。", 409)
