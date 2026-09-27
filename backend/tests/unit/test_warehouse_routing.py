from __future__ import annotations

import json
from pathlib import Path

from app.services.warehouse_routing import (
    distance_matrix,
    load_warehouse_map,
    local_slot_geometry,
    optimize_route,
    shortest_path,
)

ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = ROOT / "portfolio_demo_data" / "v2" / "warehouse_map_v1.json"
MATRIX_PATH = ROOT / "portfolio_demo_data" / "v2" / "warehouse_distance_matrix_v1.json"


def test_demo_warehouse_map_is_connected_and_binds_all_portfolio_organizers():
    definition = load_warehouse_map(MAP_PATH)
    expected = {
        "PORT-IC-100",
        "PORT-SENSOR-56",
        "PORT-PASSIVE-56",
        "PORT-CABLE-MIX",
        "PORT-PWR-6",
        "PORT-LCSC-IC-100",
        "PORT-LCSC-PASSIVE-100",
        "PORT-LCSC-CONN-100",
        "PORT-LCSC-SENSOR-56",
        "PORT-LCSC-MODULE-56",
        "PORT-LCSC-CABLE-56",
        "PORT-LCSC-LAB-6",
    }
    assert {item.location_code for item in definition.bindings} == expected
    assert definition.calibration_status == "demo_synthetic"
    assert definition.default_start_node == "PACK"
    assert definition.default_end_node == "PACK"
    for binding in definition.bindings:
        distance, path = shortest_path(definition, "PACK", binding.pick_node_code)
        assert distance > 0
        assert path[0] == "PACK"
        assert path[-1] == binding.pick_node_code


def test_checked_in_distance_matrix_is_derived_from_current_graph_hash():
    definition = load_warehouse_map(MAP_PATH)
    stored = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    assert stored["graph_hash"] == definition.graph_hash
    node_codes = stored["node_codes"]
    assert stored["matrix_m"] == distance_matrix(definition, node_codes)
    for first in node_codes:
        assert stored["matrix_m"][first][first] == 0
        for second in node_codes:
            assert stored["matrix_m"][first][second] == stored["matrix_m"][second][first]


def test_exact_route_optimizer_returns_shortest_configured_graph_order():
    definition = load_warehouse_map(MAP_PATH)
    stops = [
        "PF-PORT-IC-100",
        "PF-PORT-PWR-6",
        "PF-PORT-LCSC-CONN-100",
        "PF-PORT-LCSC-CABLE-56",
    ]
    result = optimize_route(definition, stops)
    assert result.strategy == "graph_v1_exact"
    assert set(result.ordered_stop_nodes) == set(stops)
    assert result.start_node == "PACK"
    assert result.end_node == "PACK"
    assert result.total_distance_m > 0
    # Independent brute force on four stops keeps the optimizer honest.
    import itertools

    matrix = distance_matrix(definition, ["PACK", *stops])
    brute = min(
        sum(
            matrix[a][b]
            for a, b in zip(
                ["PACK", *order],
                [*order, "PACK"],
                strict=False,
            )
        )
        for order in itertools.permutations(stops)
    )
    assert result.total_distance_m == round(brute, 4)


def test_closed_cross_aisle_reroutes_without_using_closed_edge():
    definition = load_warehouse_map(MAP_PATH)
    baseline = optimize_route(
        definition,
        ["PF-PORT-CABLE-MIX", "PF-PORT-LCSC-MODULE-56"],
    )
    rerouted = optimize_route(
        definition,
        ["PF-PORT-CABLE-MIX", "PF-PORT-LCSC-MODULE-56"],
        closed_edge_codes={"W40-C40", "C40-E40"},
    )
    assert rerouted.total_distance_m > baseline.total_distance_m
    assert all("C-40" not in segment.path_nodes for segment in rerouted.segments)


def test_local_slot_geometry_supports_all_current_organizer_styles():
    drawer = local_slot_geometry("drawer_rack_100", "A01")
    standard = local_slot_geometry("standard_56", "G08")
    shelf = local_slot_geometry("shelf_rack_6", "L06")
    split_small = local_slot_geometry(
        "split_configurable", "L-S01", left_module="small", right_module="large"
    )
    split_large = local_slot_geometry(
        "split_configurable", "R-L08", left_module="small", right_module="large"
    )
    for item in (drawer, standard, shelf, split_small, split_large):
        assert 0 < item.x_norm <= 1
        assert 0 < item.y_norm <= 1
        assert 0 < item.width_norm <= 1
        assert 0 < item.height_norm <= 1


def test_rotated_footprint_and_nonfinite_dimensions_are_validated():
    from dataclasses import replace

    import pytest

    from app.services.warehouse_routing import validate_warehouse_map

    definition = load_warehouse_map(MAP_PATH)
    binding = replace(definition.bindings[0], x_m=0, y_m=0, width_m=2, depth_m=1, rotation_deg=90)
    with pytest.raises(ValueError, match="footprint exceeds"):
        validate_warehouse_map(replace(definition, bindings=(binding,)))
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="dimensions"):
            validate_warehouse_map(replace(definition, width_m=value))
