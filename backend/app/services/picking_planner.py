"""Deterministic location allocation primitives for Picking Core.

This module deliberately does not mutate inventory.  It answers one narrow
question: given a required quantity and concrete InventoryLot candidates, which
lots should a pick plan use and in what stable warehouse-hierarchy order?

The v1 strategy minimizes the number of stops for one material by consuming the
largest free lots first, then orders the selected stops by a natural sort of the
known Location.full_path.  Because MaterialBrain does not yet persist physical
coordinates, this MUST NOT be described as a mathematically shortest walking
route.  It is a deterministic hierarchy route ordering suitable for Phase 2.7.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

_NATURAL_PART = re.compile(r"(\d+)")


@dataclass(frozen=True)
class PickLotCandidate:
    inventory_lot_id: int
    location_id: int
    full_path: str
    quantity: Decimal
    active_allocated_quantity: Decimal = Decimal("0")

    @property
    def free_quantity(self) -> Decimal:
        return max(self.quantity - self.active_allocated_quantity, Decimal("0"))


@dataclass(frozen=True)
class PlannedPickAllocation:
    inventory_lot_id: int
    location_id: int
    full_path: str
    planned_quantity: Decimal
    route_sequence: int
    route_key: str


@dataclass(frozen=True)
class PickAllocationPlan:
    required_quantity: Decimal
    locatable_free_quantity: Decimal
    allocated_quantity: Decimal
    shortage_quantity: Decimal
    stop_count: int
    route_strategy: str
    optimization_note: str
    allocations: tuple[PlannedPickAllocation, ...]

    @property
    def fully_allocated(self) -> bool:
        return self.shortage_quantity == 0


def _natural_key(value: str) -> tuple[tuple[int, object], ...]:
    """Return a stable natural-sort key without assuming warehouse coordinates."""

    normalized = " / ".join(part.strip() for part in value.split("/") if part.strip())
    parts = _NATURAL_PART.split(normalized.casefold())
    key: list[tuple[int, object]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((1, int(part)))
        else:
            key.append((0, part))
    return tuple(key)


def _route_label(candidate: PickLotCandidate) -> str:
    return candidate.full_path.strip() or f"location:{candidate.location_id}"


def plan_pick_allocations(
    required_quantity: Decimal,
    lots: Iterable[PickLotCandidate],
) -> PickAllocationPlan:
    if required_quantity <= 0:
        raise ValueError("required_quantity must be positive")

    candidates = [candidate for candidate in lots if candidate.free_quantity > 0]
    locatable_free = sum((item.free_quantity for item in candidates), Decimal("0"))

    # Largest-free-lot first minimizes the number of lot stops needed to satisfy
    # one material requirement.  Natural location order is only a deterministic
    # tie-break during selection, not a claim of physical distance optimality.
    selection_order = sorted(
        candidates,
        key=lambda item: (
            -item.free_quantity,
            _natural_key(_route_label(item)),
            item.location_id,
            item.inventory_lot_id,
        ),
    )

    remaining = required_quantity
    selected: list[tuple[PickLotCandidate, Decimal]] = []
    for candidate in selection_order:
        if remaining <= 0:
            break
        quantity = min(candidate.free_quantity, remaining)
        if quantity <= 0:
            continue
        selected.append((candidate, quantity))
        remaining -= quantity

    # Once the minimal stop set is chosen, visit it in stable warehouse hierarchy
    # order.  Physical-coordinate route optimization belongs to a later phase.
    route_order = sorted(
        selected,
        key=lambda item: (
            _natural_key(_route_label(item[0])),
            item[0].location_id,
            item[0].inventory_lot_id,
        ),
    )
    allocations = tuple(
        PlannedPickAllocation(
            inventory_lot_id=candidate.inventory_lot_id,
            location_id=candidate.location_id,
            full_path=candidate.full_path,
            planned_quantity=quantity,
            route_sequence=index,
            route_key=_route_label(candidate),
        )
        for index, (candidate, quantity) in enumerate(route_order, 1)
    )
    allocated = required_quantity - max(remaining, Decimal("0"))
    shortage = max(required_quantity - allocated, Decimal("0"))
    return PickAllocationPlan(
        required_quantity=required_quantity,
        locatable_free_quantity=locatable_free,
        allocated_quantity=allocated,
        shortage_quantity=shortage,
        stop_count=len(allocations),
        route_strategy="hierarchy_v1",
        optimization_note=(
            "minimize material lot stops, then stable Location.full_path hierarchy order; "
            "not physical shortest-path optimization"
        ),
        allocations=allocations,
    )
