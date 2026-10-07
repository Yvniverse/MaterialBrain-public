"""Pure geometry/graph and SQLite compatibility; no production database access."""

import copy
import heapq
import importlib.util
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models import (
    Location,
    LocationMapBinding,
    Material,
    WarehouseMap,
    WarehouseMapEdge,
    WarehouseMapNode,
)
from app.services.embodied_navigation.geometry import obstacles_for, segment_clearance
from app.services.embodied_navigation.planner import canonical_hash, load_world
from app.services.warehouse_maps import WarehouseMapService
from app.spatial.geometry import covers_point, intersects, rectangle_polygon, validate_geometry
from app.spatial.orm_geometry import register_legacy_geometry_hooks
from app.spatial.service import SpatialMapService
from app.spatial.snapshot import build_lab_snapshot, static_revision


def _migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0016_spatial_hd_map.py"
    spec = importlib.util.spec_from_file_location("spatial_hd_map_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def spatial_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        root = Location(code="GIS-WH", name="Test warehouse", type="warehouse", full_path="Test")
        db.add(root)
        db.flush()
        slot = Location(
            code="GIS-SHELF",
            name="Shelf",
            type="organizer",
            full_path="Test/Shelf",
            parent_id=root.id,
        )
        active = WarehouseMap(
            code="WH-RD-TWIN-V4",
            name="Existing business map",
            version="4.0.0",
            status="active",
            warehouse_location_id=root.id,
            calibration_status="verified",
            width_m=24,
            height_m=18,
            graph_hash="f" * 64,
            geometry_note="existing measured truth",
        )
        db.add_all(
            [
                slot,
                active,
                Material(code="GIS-MAT", name="Existing stock", quantity=13, reserved_quantity=2),
            ]
        )
        db.flush()
        first = WarehouseMapNode(
            warehouse_map_id=active.id, code="PACK", node_type="packing", x_m=2, y_m=2
        )
        second = WarehouseMapNode(
            warehouse_map_id=active.id, code="FACE", node_type="pick_face", x_m=5, y_m=2
        )
        db.add_all([first, second])
        db.flush()
        db.add_all(
            [
                WarehouseMapEdge(
                    warehouse_map_id=active.id,
                    code="OLD-EDGE",
                    from_node_id=first.id,
                    to_node_id=second.id,
                    distance_m=3,
                ),
                LocationMapBinding(
                    warehouse_map_id=active.id,
                    location_id=slot.id,
                    pick_node_id=second.id,
                    x_m=5,
                    y_m=3,
                    width_m=2,
                    depth_m=1,
                    rotation_deg=90,
                ),
            ]
        )
        db.commit()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            _migration().upgrade()
    with Session(engine) as db:
        yield db
    engine.dispose()


def _business_truth(db):
    return {
        "stock": db.execute(
            text("SELECT code,quantity,reserved_quantity FROM materials ORDER BY id")
        ).all(),
        "locations": db.execute(
            text("SELECT id,code,parent_id,type FROM locations ORDER BY id")
        ).all(),
        "active": db.execute(
            text(
                "SELECT code,status,graph_hash,calibration_status "
                "FROM warehouse_maps WHERE code='WH-RD-TWIN-V4'"
            )
        ).all(),
        "bindings": db.execute(
            text(
                "SELECT warehouse_map_id,location_id,pick_node_id,x_m,y_m,width_m,depth_m,"
                "rotation_deg FROM location_map_bindings ORDER BY id"
            )
        ).all(),
    }


def test_snapshot_preserves_canonical_geometry_docks_and_traceable_policy():
    world, snapshot = load_world(), build_lab_snapshot()
    assert snapshot["map_id"] == world["id"]
    assert snapshot["frame"] == {
        "name": "warehouse_map",
        "unit": "m",
        "srid": 0,
        "site_georef": None,
    }
    assert snapshot["provenance"]["source_world_revision"] == world["revision_sha256"]
    assert snapshot["provenance"]["calibration_status"] == "demo_synthetic"
    assert snapshot["provenance"]["map_status"] == "draft"
    assert snapshot["provenance"]["graph_hash"] == canonical_hash(snapshot["route_graph"])
    assert snapshot["revision"] == static_revision(snapshot)
    exported = {asset["id"]: asset for asset in snapshot["geometry"]["assets"]}
    assert len(exported) == 30
    for asset in world["assets"]:
        assert {k: v for k, v in exported[asset["id"]].items() if k != "footprint"} == asset
        assert exported[asset["id"]]["footprint"] == rectangle_polygon(asset)
    docks = {dock["id"]: dock for dock in snapshot["docks"]}
    assert docks["HOME"]["pose"] == world["home"]
    assert docks["CHARGER"]["pose"] == world["charger"]
    for goal in world["goals"]:
        assert docks[goal["id"]]["pose"] == goal["pose"]
        assert docks[goal["id"]]["asset_id"] == goal["asset_id"]
        assert docks[goal["id"]]["human_handoff_only"]
    assert {z["kind"] for z in snapshot["zones"]} >= {
        "esd",
        "receiving",
        "handoff",
        "human_mixed",
        "keepout",
        "charging",
    }
    assert all(
        not a["inventory_write"] and not a["can_open_drawer"] and not a["can_grasp"]
        for a in snapshot["affordances"]
    )


def test_every_directed_edge_is_robot_clear_and_docks_connected():
    world, snapshot = load_world(), build_lab_snapshot()
    obstacles = obstacles_for(world)
    robot = world["robot"]
    radius = math.hypot(robot["length"], robot["width"]) / 2 + robot["margin"]
    nodes = {node["id"]: node for node in snapshot["route_graph"]["nodes"]}
    edges = snapshot["route_graph"]["edges"]
    reached, queue, adjacency = {"HOME"}, ["HOME"], {}
    for edge in edges:
        a, b = nodes[edge["from"]], nodes[edge["to"]]
        clearance = segment_clearance(world, a, b, obstacles)
        assert clearance > radius
        assert edge["min_clearance_m"] == pytest.approx(clearance - radius, abs=1e-6)
        assert edge["width_m"] == pytest.approx(clearance * 2, abs=1e-6)
        assert edge["length_m"] == pytest.approx(
            math.hypot(b["x"] - a["x"], b["y"] - a["y"]), abs=1e-4
        )
        assert edge["allowed_robot_classes"] == ["MB-R01"]
        assert edge["geometry"]["coordinates"] == [[a["x"], a["y"]], [b["x"], b["y"]]]
        assert edge["energy_wh"] == pytest.approx(
            edge["length_m"] * robot["wh_per_m"]
            + robot["idle_w"] * edge["length_m"] / edge["speed_limit_mps"] / 3600,
            abs=1e-6,
        )
        adjacency.setdefault(edge["from"], []).append(edge["to"])
    while queue:
        for neighbor in adjacency.get(queue.pop(), []):
            if neighbor not in reached:
                reached.add(neighbor)
                queue.append(neighbor)
    assert reached == set(nodes)
    assert {dock["node_id"] for dock in snapshot["docks"]} <= reached


def _path(snapshot, start, target, risk_weight):
    adjacent = {}
    for edge in snapshot["route_graph"]["edges"]:
        adjacent.setdefault(edge["from"], []).append(edge)
    queue, best = [(0, start, [])], {start: 0}
    while queue:
        cost, node, path = heapq.heappop(queue)
        if node == target:
            return path
        if cost > best[node]:
            continue
        for edge in adjacent.get(node, []):
            next_cost = (
                cost
                + edge["length_m"] / edge["speed_limit_mps"]
                + risk_weight * edge["risk_level"] * edge["length_m"]
            )
            if next_cost < best.get(edge["to"], math.inf):
                best[edge["to"]] = next_cost
                heapq.heappush(queue, (next_cost, edge["to"], [*path, edge]))
    raise AssertionError("Disconnected graph")


def test_semantics_provide_a_real_longer_lower_risk_alternative():
    snapshot = build_lab_snapshot()
    differences = []
    ids = [dock["id"] for dock in snapshot["docks"]]
    for first in ids:
        for second in ids:
            if first == second:
                continue
            fast, safe = _path(snapshot, first, second, 0), _path(snapshot, first, second, 30)
            fast_risk = sum(e["risk_level"] * e["length_m"] for e in fast)
            safe_risk = sum(e["risk_level"] * e["length_m"] for e in safe)
            if (
                safe_risk < fast_risk - 0.1
                and sum(e["length_m"] for e in safe) > sum(e["length_m"] for e in fast) + 0.1
            ):
                differences.append((first, second))
    assert differences, "Policy metadata must allow actual alternative paths"


def test_snapshot_returns_independent_copies_and_overlay_does_not_revise_map():
    snapshot = build_lab_snapshot()
    original = snapshot["revision"]
    snapshot["dynamic_overlays"].append({"id": "temp"})
    assert static_revision(snapshot) == original
    snapshot["route_graph"]["nodes"].clear()
    assert build_lab_snapshot()["revision"] == original
    assert build_lab_snapshot()["route_graph"]["nodes"]


def test_registration_roundtrip_and_queries_preserve_business_truth(spatial_db):
    service = SpatialMapService(spatial_db)
    before = _business_truth(spatial_db)
    registered = service.register_lab()
    assert registered["created"] and registered["status"] == "draft"
    assert service.snapshot(registered["map_id"]) == build_lab_snapshot()
    assert not service.register_lab()["created"]
    assert _business_truth(spatial_db) == before
    point = {"x": 2.75, "y": 7}
    contains = service.query(registered["map_id"], {"kind": "contains", "point": point})
    assert contains["engine"] == "sqlite_geometry_compat"
    assert "ZA" in {r["id"] for r in contains["results"]}
    assert (
        service.query(registered["map_id"], {"kind": "nearest_dock", "point": point, "limit": 1})[
            "results"
        ][0]["id"]
        == "P-SENSOR"
    )
    near = service.query(
        registered["map_id"], {"kind": "nearby", "entity": "assets", "point": point, "radius_m": 2}
    )
    assert "A02" in {r["id"] for r in near["results"]}
    crossed = service.query(
        registered["map_id"],
        {
            "kind": "intersects",
            "geometry": {"type": "LineString", "coordinates": [[7.75, 8.75], [9.75, 9.75]]},
        },
    )
    assert crossed["results"]
    zones = service.query(
        registered["map_id"], {"kind": "edge_zones", "edge_id": crossed["results"][0]["id"]}
    )
    assert zones["results"]
    assert _business_truth(spatial_db) == before


def test_legacy_rotation_backfill_preserves_unknown_semantics(spatial_db):
    footprint = spatial_db.execute(text("SELECT footprint FROM location_map_bindings")).scalar_one()
    import json

    geometry = json.loads(footprint)
    xs, ys = zip(*geometry["coordinates"][0], strict=True)
    assert min(xs) == pytest.approx(4.5) and max(xs) == pytest.approx(5.5)
    assert min(ys) == pytest.approx(2) and max(ys) == pytest.approx(4)
    legacy = spatial_db.execute(
        text("SELECT width_m,speed_limit_mps,risk_level,semantic_rules FROM warehouse_map_edges")
    ).one()
    assert legacy[0] is None and legacy[1] is None and legacy[2] == 0
    assert (
        spatial_db.execute(text("SELECT spatial_revision FROM warehouse_maps")).scalar_one() is None
    )


def test_legacy_clone_and_draft_replacement_keep_spatial_queries_working(spatial_db):
    register_legacy_geometry_hooks()
    before = _business_truth(spatial_db)
    legacy = WarehouseMapService(spatial_db)
    active = spatial_db.scalar(select(WarehouseMap).where(WarehouseMap.code == "WH-RD-TWIN-V4"))
    draft = legacy.clone_to_draft(active.id, code="GIS-DRAFT", version="5.0.0", name="New draft")
    spatial = SpatialMapService(spatial_db)
    query = {"kind": "nearby", "entity": "nodes", "point": {"x": 5, "y": 2}, "radius_m": 0.1}
    assert spatial.query(draft.code, query)["results"][0]["id"] == "FACE"
    definition = legacy.definition(draft.id)
    updated = replace(
        definition,
        width_m=30,
        height_m=20,
        nodes=tuple(replace(node, x_m=node.x_m + 1) for node in definition.nodes),
        bindings=tuple(replace(binding, x_m=binding.x_m + 1) for binding in definition.bindings),
    )
    legacy.replace_draft_definition(draft.id, updated)
    query["point"]["x"] = 6
    assert spatial.query(draft.code, query)["results"][0]["id"] == "FACE"
    snapshot = spatial.snapshot(draft.code)
    assert max(p[0] for p in snapshot["geometry"]["floor"]["coordinates"][0]) == 30
    assert all(edge["geometry"] for edge in snapshot["route_graph"]["edges"])
    assert all(
        edge["width_m"] is None and edge["speed_limit_mps"] is None
        for edge in snapshot["route_graph"]["edges"]
    )
    after = _business_truth(spatial_db)
    assert after["stock"] == before["stock"] and after["locations"] == before["locations"]
    assert after["active"] == before["active"]


def test_ttl_overlay_retry_is_idempotent_and_expiry_is_read_only(spatial_db):
    now = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
    service = SpatialMapService(spatial_db, clock=lambda: now)
    service.register_lab()
    original = service.snapshot("MB-EMB-LAB-03")["revision"]
    before = _business_truth(spatial_db)
    body = {
        "id": "OB-CROSSING",
        "kind": "closure",
        "geometry": rectangle_polygon({"x": 8.3, "y": 9.35, "width": 3.75, "depth": 0.85}),
        "source": "simulation",
        "reason": "Temporary blocked crossing",
        "ttl_s": 60,
    }
    created = service.put_overlay("MB-EMB-LAB-03", body)
    assert created["created"]
    now += timedelta(seconds=10)
    retried = service.put_overlay("MB-EMB-LAB-03", copy.deepcopy(body))
    assert not retried["created"] and retried["overlay"] == created["overlay"]
    assert service.query("MB-EMB-LAB-03", {"kind": "affected_edges"})["results"]
    assert service.snapshot("MB-EMB-LAB-03")["revision"] == original
    assert len(service.snapshot("MB-EMB-LAB-03")["dynamic_overlays"]) == 1
    with pytest.raises(BusinessError, match="动态限制编码"):
        service.put_overlay("MB-EMB-LAB-03", {**body, "reason": "changed"})
    now += timedelta(seconds=51)
    assert service.snapshot("MB-EMB-LAB-03")["dynamic_overlays"] == []
    assert service.query("MB-EMB-LAB-03", {"kind": "affected_edges"})["results"] == []
    assert (
        spatial_db.execute(text("SELECT COUNT(*) FROM warehouse_dynamic_overlays")).scalar_one()
        == 1
    )
    assert _business_truth(spatial_db) == before


@pytest.mark.parametrize(
    "change",
    [
        {"ttl_s": 0},
        {"ttl_s": math.inf},
        {"ttl_s": True},
        {"ttl_s": 86401},
        {"created_at": "2000-01-01T00:00:00Z"},
        {"source": ""},
        {"reason": ""},
        {"kind": "speed_override"},
        {"kind": "risk_override", "risk_level": 2},
        {"node_ids": ["INVENTED"]},
        {"edge_ids": ["INVENTED"]},
        {"geometry": {"type": "Point", "coordinates": [8, 9]}},
        {
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[1, 1], [3, 3], [1, 3], [3, 1], [1, 1]]],
            }
        },
    ],
)
def test_invalid_overlay_creates_zero_rows(spatial_db, change):
    service = SpatialMapService(spatial_db)
    service.register_lab()
    body = {
        "id": "INVALID",
        "kind": "closure",
        "geometry": rectangle_polygon({"x": 8, "y": 9, "width": 2, "depth": 1}),
        "source": "test",
        "reason": "test",
        "ttl_s": 60,
        **change,
    }
    with pytest.raises(BusinessError) as error:
        service.put_overlay("MB-EMB-LAB-03", body)
    assert error.value.code == "SPATIAL_OVERLAY_INVALID"
    assert (
        spatial_db.execute(text("SELECT COUNT(*) FROM warehouse_dynamic_overlays")).scalar_one()
        == 0
    )


@pytest.mark.parametrize(
    "query",
    [
        {"kind": "contains", "point": {"x": math.nan, "y": 2}},
        {"kind": "contains", "point": {"x": True, "y": 2}},
        {"kind": "nearby", "point": {"x": 2, "y": 2}, "radius_m": -1},
        {"kind": "nearest_dock", "point": {"x": 2, "y": 2}, "limit": 101},
        {"kind": "intersects", "geometry": {"type": "Point", "coordinates": [math.inf, 0]}},
        {"kind": "edge_zones"},
        {"kind": "sql"},
        {"kind": []},
    ],
)
def test_queries_reject_invalid_metric_inputs(spatial_db, query):
    service = SpatialMapService(spatial_db)
    service.register_lab()
    with pytest.raises(BusinessError) as error:
        service.query("MB-EMB-LAB-03", query)
    assert error.value.code == "SPATIAL_QUERY_INVALID"


def test_changed_persisted_graph_fails_closed(spatial_db):
    service = SpatialMapService(spatial_db)
    service.register_lab()
    spatial_db.execute(
        text(
            "UPDATE warehouse_map_edges SET risk_level=0.99 WHERE warehouse_map_id="
            "(SELECT id FROM warehouse_maps WHERE code='MB-EMB-LAB-03')"
        )
    )
    with pytest.raises(BusinessError) as error:
        service.snapshot("MB-EMB-LAB-03")
    assert error.value.code == "SPATIAL_MAP_REVISION_MISMATCH"


def test_geojson_boundary_holes_and_crossing_semantics():
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]],
            [[1, 1], [1, 3], [3, 3], [3, 1], [1, 1]],
        ],
    }
    validate_geometry(polygon)
    assert covers_point(polygon, (0, 2)) and covers_point(polygon, (1, 2))
    assert not covers_point(polygon, (2, 2))
    assert intersects(polygon, {"type": "LineString", "coordinates": [[-1, 2], [5, 2]]})
    assert not intersects(polygon, {"type": "Point", "coordinates": [2, 2]})


def test_missing_root_does_not_create_a_business_location():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            _migration().upgrade()
    with Session(engine) as db:
        with pytest.raises(BusinessError) as error:
            SpatialMapService(db).register_lab()
        assert error.value.code == "SPATIAL_WAREHOUSE_ROOT_REQUIRED"
        assert db.scalar(select(Location.id)) is None
