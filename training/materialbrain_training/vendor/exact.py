"""Independent, exact small-N order oracle over directed graph-derived costs."""

from __future__ import annotations

import math


def held_karp_exact(costs: list[list[float]], goal_indices: list[int], end_index: int) -> dict:
    """Prove the unconstrained ordering bound, never a resource-constrained optimum.

    The matrix can be asymmetric and unreachable entries must be infinity. All
    service costs should be included on departure, matching the routing objective.
    """
    n = len(goal_indices)
    if n > 8:
        raise ValueError("EXACT_BASELINE_LIMIT_8")
    if not n:
        value = costs[0][end_index]
        return {
            "cost": value,
            "order": [],
            "feasible": math.isfinite(value),
            "optimality_proven": True,
        }
    labels = {}
    for position, node in enumerate(goal_indices):
        if math.isfinite(costs[0][node]):
            labels[(1 << position, position)] = (costs[0][node], (node,))
    for mask in range(1, 1 << n):
        for last in range(n):
            if (mask, last) not in labels:
                continue
            value, order = labels[(mask, last)]
            for nxt, node in enumerate(goal_indices):
                if mask & (1 << nxt) or not math.isfinite(costs[goal_indices[last]][node]):
                    continue
                candidate = value + costs[goal_indices[last]][node], order + (node,)
                key = mask | (1 << nxt), nxt
                if key not in labels or candidate < labels[key]:
                    labels[key] = candidate
    endings = [
        (value + costs[goal_indices[last]][end_index], order)
        for (mask, last), (value, order) in labels.items()
        if mask == (1 << n) - 1 and math.isfinite(costs[goal_indices[last]][end_index])
    ]
    if not endings:
        return {"cost": math.inf, "order": [], "feasible": False, "optimality_proven": True}
    value, order = min(endings)
    return {"cost": value, "order": list(order), "feasible": True, "optimality_proven": True}
