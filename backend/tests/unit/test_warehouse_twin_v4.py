from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import Location
from app.services.warehouse_maps import WarehouseMapService
from app.services.warehouse_routing import distance_matrix, load_warehouse_map, optimize_route

FIXTURE = Path(__file__).resolve().parents[2] / "sample_data/v2/warehouse_map_v4.json"


def test_v4_persisted_graph_keeps_exact_identity_and_route_after_reload():
    definition = load_warehouse_map(FIXTURE)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        warehouse = Location(code="WH-RD", name="研发仓", type="warehouse", full_path="研发仓")
        db.add(warehouse)
        db.flush()
        for binding in definition.bindings:
            db.add(
                Location(
                    code=binding.location_code,
                    name=binding.location_code,
                    type="box",
                    parent_id=warehouse.id,
                    full_path="研发仓 / " + binding.location_code,
                )
            )
        db.flush()
        service = WarehouseMapService(db)
        row = service.seed_definition(definition, activate=False)
        map_id = row.id
        db.commit()
        db.expire_all()
        restored = service.definition(map_id)
        assert restored.graph_hash == definition.graph_hash
        route = optimize_route(restored, [item.pick_node_code for item in restored.bindings])
        assert route.graph_hash == definition.graph_hash
        assert route.total_distance_m == 39.9092
    engine.dispose()


def test_v4_has_complete_sample_and_legacy_cable_rack_binding():
    definition = load_warehouse_map(FIXTURE)
    codes = {item.location_code for item in definition.bindings}
    assert "CABLE-RACK-01" in codes
    assert len(definition.bindings) == 13
    assert len(definition.nodes) == 30
    assert len(definition.edges) == 39
    assert len(definition.edges) - len(definition.nodes) + 1 == 10
    assert (
        definition.graph_hash == "c36ec0e36c85b42daa23ee7154469b62413423901c493d15dbc941737c11145d"
    )


def test_v4_six_stop_route_shows_real_optimization_and_replan_delta():
    definition = load_warehouse_map(FIXTURE)
    stops = [
        "PF-PORT-IC-100",
        "PF-PORT-LCSC-CONN-100",
        "PF-PORT-PWR-6",
        "PF-PORT-LCSC-CABLE-56",
        "PF-PORT-PASSIVE-56",
        "PF-PORT-LCSC-MODULE-56",
    ]
    route = optimize_route(definition, stops)
    assert route.strategy == "graph_v1_exact"
    assert route.total_distance_m == 31.16
    matrix = distance_matrix(definition, ["PACK", *stops])
    input_order = ["PACK", *stops, "PACK"]
    input_distance = sum(matrix[a][b] for a, b in zip(input_order, input_order[1:], strict=False))
    assert round(input_distance, 2) == 57.66
    assert round((input_distance - route.total_distance_m) / input_distance * 100, 2) == 45.96

    replanned = optimize_route(definition, stops, closed_edge_codes={"G31--G41"})
    assert replanned.total_distance_m == 35.86
    assert round(replanned.total_distance_m - route.total_distance_m, 2) == 4.70


def test_v4_all_thirteen_devices_exercise_existing_nn_2opt_boundary():
    definition = load_warehouse_map(FIXTURE)
    stops = [binding.pick_node_code for binding in definition.bindings]
    route = optimize_route(definition, stops)
    assert len(route.ordered_stop_nodes) == 13
    assert route.strategy == "graph_v1_nn_2opt"
    assert route.total_distance_m == 39.9092
