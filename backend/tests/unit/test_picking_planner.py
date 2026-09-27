from decimal import Decimal

from app.services.picking_planner import PickLotCandidate, plan_pick_allocations


def lot(lot_id: int, location_id: int, path: str, quantity: str, active: str = "0"):
    return PickLotCandidate(
        inventory_lot_id=lot_id,
        location_id=location_id,
        full_path=path,
        quantity=Decimal(quantity),
        active_allocated_quantity=Decimal(active),
    )


def test_planner_minimizes_material_stops_then_orders_selected_route():
    result = plan_pick_allocations(
        Decimal("7"),
        [
            lot(1, 31, "研发仓库 / 线缆柜 / C10", "3"),
            lot(2, 11, "研发仓库 / IC柜 / A02", "5"),
            lot(3, 21, "研发仓库 / IC柜 / A10", "4"),
        ],
    )

    assert result.fully_allocated is True
    assert result.stop_count == 2
    assert result.allocated_quantity == Decimal("7")
    # Largest two lots are selected (5 + 4), then route-sorted by path.
    assert {item.inventory_lot_id for item in result.allocations} == {2, 3}
    assert [item.route_sequence for item in result.allocations] == [1, 2]
    assert [item.location_id for item in result.allocations] == [11, 21]
    assert [item.planned_quantity for item in result.allocations] == [Decimal("5"), Decimal("2")]


def test_planner_never_counts_unlocated_or_already_allocated_quantity():
    result = plan_pick_allocations(
        Decimal("10"),
        [
            lot(1, 1, "研发仓库 / A01", "6", "2"),
            lot(2, 2, "研发仓库 / A02", "3", "3"),
        ],
    )

    assert result.locatable_free_quantity == Decimal("4")
    assert result.allocated_quantity == Decimal("4")
    assert result.shortage_quantity == Decimal("6")
    assert result.fully_allocated is False
    assert result.stop_count == 1


def test_route_strategy_does_not_claim_physical_shortest_path():
    result = plan_pick_allocations(Decimal("1"), [lot(1, 1, "研发仓库 / A01", "1")])
    assert result.route_strategy == "hierarchy_v1"
    assert "not physical shortest-path" in result.optimization_note
