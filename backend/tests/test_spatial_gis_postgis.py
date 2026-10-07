"""PostGIS migration/query/index tests against the explicit public test database."""

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models import Location, WarehouseMap
from app.services.warehouse_maps import WarehouseMapService
from app.services.warehouse_routing import load_warehouse_map
from app.spatial.geometry import rectangle_polygon
from app.spatial.orm_geometry import register_legacy_geometry_hooks
from app.spatial.service import SpatialMapService
from app.spatial.snapshot import build_lab_snapshot


@pytest.fixture(scope="module")
def postgis_engine():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Public PostGIS test database not configured: TEST_POSTGRES_URL")
    parsed = make_url(url)
    if not (
        parsed.host == "127.0.0.1"
        and parsed.port == 55432
        and parsed.database == "materialbrain_public_v02_test"
    ):
        pytest.fail("Refusing PostGIS writes outside 127.0.0.1:55432/materialbrain_public_v02_test")
    parsed = parsed.update_query_dict({"connect_timeout": "5"})
    url = parsed.render_as_string(hide_password=False)
    environment = os.environ.copy()
    environment["DATABASE_URL"] = url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT PostGIS_Version()")).scalar_one()
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0016_spatial_hd_map"
        )
    # A migrated database contains no warehouse data. Create only the public
    # legacy map's empty locations so registration and ORM/backfill queries do
    # not depend on a pre-existing application database or synthetic inventory.
    register_legacy_geometry_hooks()
    with Session(engine) as db:
        definition = load_warehouse_map(
            Path(__file__).resolve().parents[1] / "sample_data/v2/warehouse_map_v4.json"
        )
        if db.scalar(select(WarehouseMap).where(WarehouseMap.code == definition.map_code)) is None:
            warehouse = db.scalar(
                select(Location).where(Location.code == definition.warehouse_code)
            )
            if warehouse is None:
                warehouse = Location(
                    code=definition.warehouse_code,
                    name="Public PostGIS test warehouse",
                    type="warehouse",
                    full_path="Public PostGIS test warehouse",
                )
                db.add(warehouse)
                db.flush()
            for binding in definition.bindings:
                location_id = db.scalar(
                    select(Location.id).where(Location.code == binding.location_code)
                )
                if location_id is None:
                    db.add(
                        Location(
                            code=binding.location_code,
                            name=binding.location_code,
                            type="box",
                            parent_id=warehouse.id,
                            full_path=warehouse.full_path + " / " + binding.location_code,
                        )
                    )
            db.flush()
            WarehouseMapService(db).seed_definition(definition, activate=False)
            db.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def postgis_db(postgis_engine):
    with Session(postgis_engine) as db:
        yield db
        db.rollback()


def test_actual_postgis_roundtrip_predicates_knn_and_gist(postgis_db):
    service = SpatialMapService(postgis_db)
    before = postgis_db.execute(
        text(
            "SELECT id,code,status,graph_hash,calibration_status "
            "FROM warehouse_maps WHERE code='WH-RD-TWIN-V4'"
        )
    ).all()
    business_before = postgis_db.execute(
        text("SELECT COUNT(*),SUM(quantity),SUM(reserved_quantity) FROM materials")
    ).one()
    registration = service.register_lab()
    assert service.snapshot(registration["map_id"]) == build_lab_snapshot()
    statements = []

    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(postgis_db.get_bind(), "before_cursor_execute", capture)
    try:
        contains = service.query(
            registration["map_id"], {"kind": "contains", "point": {"x": 2.75, "y": 7}}
        )
        assert contains["engine"] == "postgis" and contains["predicate"] == "ST_Covers"
        assert "ZA" in {row["id"] for row in contains["results"]}
        nearest = service.query(
            registration["map_id"],
            {"kind": "nearest_dock", "point": {"x": 2.75, "y": 7}, "limit": 1},
        )
        assert (
            nearest["results"][0]["id"] == "P-SENSOR" and nearest["results"][0]["distance_m"] == 0
        )
        assert service.query(
            registration["map_id"],
            {"kind": "nearby", "entity": "assets", "point": {"x": 2.75, "y": 7}, "radius_m": 2},
        )["results"]
        crossed = service.query(
            registration["map_id"],
            {
                "kind": "intersects",
                "geometry": {"type": "LineString", "coordinates": [[7.75, 8.75], [9.75, 9.75]]},
            },
        )
        assert crossed["results"]
        assert service.query(
            registration["map_id"], {"kind": "edge_zones", "edge_id": crossed["results"][0]["id"]}
        )["results"]
    finally:
        event.remove(postgis_db.get_bind(), "before_cursor_execute", capture)
    for predicate in ("ST_Covers", "ST_DWithin", "ST_Intersects", "<->"):
        assert any(predicate in statement for statement in statements), predicate
    indexes = dict(
        postgis_db.execute(
            text(
                "SELECT indexname,indexdef FROM pg_indexes WHERE indexname IN "
                "('ix_wh_maps_geom_gist','ix_wh_nodes_geom_gist','ix_wh_edges_geom_gist',"
                "'ix_location_binding_footprint_gist','ix_wh_spatial_assets_geom_gist',"
                "'ix_wh_zones_geom_gist','ix_wh_docks_geom_gist','ix_wh_overlays_geom_gist')"
            )
        ).all()
    )
    assert len(indexes) == 8 and all("USING gist" in value for value in indexes.values())
    postgis_db.execute(text("SET LOCAL enable_seqscan=off"))
    explain = postgis_db.execute(
        text(
            "EXPLAIN (FORMAT JSON) SELECT code FROM warehouse_spatial_docks "
            "ORDER BY geom <-> ST_SetSRID(ST_MakePoint(2.75,7),0) LIMIT 1"
        )
    ).scalar_one()
    assert "ix_wh_docks_geom_gist" in str(explain)
    assert (
        postgis_db.execute(
            text(
                "SELECT id,code,status,graph_hash,calibration_status "
                "FROM warehouse_maps WHERE code='WH-RD-TWIN-V4'"
            )
        ).all()
        == before
    )
    assert (
        postgis_db.execute(
            text("SELECT COUNT(*),SUM(quantity),SUM(reserved_quantity) FROM materials")
        ).one()
        == business_before
    )


def test_postgis_backfill_uses_local_srid_and_valid_rotated_footprints(postgis_db):
    for table, column in (
        ("warehouse_maps", "geom"),
        ("warehouse_map_nodes", "geom"),
        ("warehouse_map_edges", "geom"),
        ("location_map_bindings", "footprint"),
    ):
        assert (
            postgis_db.execute(
                text(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL "
                    f"OR ST_SRID({column})<>0 OR NOT ST_IsValid({column})"
                )
            ).scalar_one()
            == 0
        )
    mismatch = postgis_db.execute(
        text(
            "SELECT COUNT(*) FROM warehouse_map_nodes "
            "WHERE NOT ST_Equals(geom,ST_SetSRID(ST_MakePoint(x_m,y_m),0))"
        )
    ).scalar_one()
    assert mismatch == 0
    assert (
        postgis_db.execute(
            text(
                "SELECT COUNT(*) FROM location_map_bindings "
                "WHERE abs(ST_Area(footprint)-width_m*depth_m)>0.000001"
            )
        ).scalar_one()
        == 0
    )


def test_postgis_overlay_is_a_temporal_join_without_inventory_write(postgis_db):
    service = SpatialMapService(postgis_db)
    service.register_lab()
    revision = service.snapshot("MB-EMB-LAB-03")["revision"]
    baseline = postgis_db.execute(
        text("SELECT COUNT(*),SUM(quantity),SUM(reserved_quantity) FROM materials")
    ).one()
    overlay = {
        "id": "PG-CROSSING",
        "kind": "closure",
        "geometry": rectangle_polygon({"x": 8.3, "y": 9.35, "width": 3.75, "depth": 0.85}),
        "source": "isolated_postgis_test",
        "reason": "Blocked-aisle test",
        "ttl_s": 60,
    }
    result = service.put_overlay("MB-EMB-LAB-03", overlay)
    assert result["created"]
    assert service.query("MB-EMB-LAB-03", {"kind": "affected_edges", "overlay_id": overlay["id"]})[
        "results"
    ]
    postgis_db.execute(
        text(
            "UPDATE warehouse_dynamic_overlays SET active_from=now()-interval '120 seconds',"
            "expires_at=now()-interval '1 second' WHERE code='PG-CROSSING'"
        )
    )
    assert not service.query("MB-EMB-LAB-03", {"kind": "affected_edges"})["results"]
    assert service.snapshot("MB-EMB-LAB-03")["dynamic_overlays"] == []
    assert service.snapshot("MB-EMB-LAB-03")["revision"] == revision
    assert (
        postgis_db.execute(
            text("SELECT COUNT(*),SUM(quantity),SUM(reserved_quantity) FROM materials")
        ).one()
        == baseline
    )


def test_postgis_concurrent_overlay_retry_preserves_first_expiry(postgis_engine):
    code = "PG-IDEMPOTENT-" + uuid.uuid4().hex[:12]
    body = {
        "id": code,
        "kind": "obstacle",
        "geometry": rectangle_polygon({"x": 8.3, "y": 9.35, "width": 1, "depth": 0.8}),
        "source": "isolated_postgis_test",
        "reason": "Concurrent retry",
        "ttl_s": 60,
    }
    with Session(postgis_engine) as db:
        SpatialMapService(db).register_lab()
        db.commit()

    def put(_):
        with Session(postgis_engine) as db:
            result = SpatialMapService(db).put_overlay("MB-EMB-LAB-03", body)
            db.commit()
            return result

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = list(executor.map(put, range(2)))
        assert first["overlay"] == second["overlay"]
        assert sorted([first["created"], second["created"]]) == [False, True]
        with Session(postgis_engine) as db:
            assert (
                db.execute(
                    text("SELECT COUNT(*) FROM warehouse_dynamic_overlays WHERE code=:code"),
                    {"code": code},
                ).scalar_one()
                == 1
            )
    finally:
        with Session(postgis_engine) as db:
            db.execute(
                text("DELETE FROM warehouse_dynamic_overlays WHERE code=:code"), {"code": code}
            )
            db.commit()


def test_postgis_legacy_orm_clone_derives_indexed_geometry(postgis_db):
    from sqlalchemy import select

    from app.models import WarehouseMap
    from app.services.warehouse_maps import WarehouseMapService

    register_legacy_geometry_hooks()
    active = postgis_db.scalar(select(WarehouseMap).where(WarehouseMap.code == "WH-RD-TWIN-V4"))
    before = postgis_db.execute(
        text("SELECT COUNT(*),SUM(quantity),SUM(reserved_quantity) FROM materials")
    ).one()
    suffix = uuid.uuid4().hex[:12]
    legacy = WarehouseMapService(postgis_db)
    draft = legacy.clone_to_draft(
        active.id, code="GIS-ORM-" + suffix, version="test-" + suffix, name="Isolated ORM test"
    )
    definition = legacy.definition(draft.id)
    start = next(node for node in definition.nodes if node.code == definition.default_start_node)
    result = SpatialMapService(postgis_db).query(
        draft.code,
        {
            "kind": "nearby",
            "entity": "nodes",
            "point": {"x": start.x_m, "y": start.y_m},
            "radius_m": 0.1,
            "limit": 1,
        },
    )
    assert result["results"][0]["id"] == start.code and result["engine"] == "postgis"
    assert (
        postgis_db.execute(
            text(
                "SELECT COUNT(*) FROM warehouse_map_edges WHERE warehouse_map_id=:id "
                "AND (geom IS NULL OR ST_SRID(geom)<>0)"
            ),
            {"id": draft.id},
        ).scalar_one()
        == 0
    )
    assert (
        postgis_db.execute(
            text(
                "SELECT COUNT(*) FROM location_map_bindings WHERE warehouse_map_id=:id "
                "AND (footprint IS NULL OR ST_SRID(footprint)<>0)"
            ),
            {"id": draft.id},
        ).scalar_one()
        == 0
    )
    assert (
        postgis_db.execute(
            text("SELECT COUNT(*),SUM(quantity),SUM(reserved_quantity) FROM materials")
        ).one()
        == before
    )
