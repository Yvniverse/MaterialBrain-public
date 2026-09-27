#!/usr/bin/env python3
"""Strict static audit for WH-RD-TWIN-V4 spatial and route properties."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from app.services.warehouse_routing import distance_matrix, load_warehouse_map, optimize_route

EXPECTED = {
    "map_code": "WH-RD-TWIN-V4",
    "version": "4.0.0",
    "nodes": 30,
    "edges": 39,
    "bindings": 13,
    "cyclomatic_loops": 10,
    "graph_hash": "c36ec0e36c85b42daa23ee7154469b62413423901c493d15dbc941737c11145d",
}
DEMO_STOPS = [
    "PF-PORT-IC-100",
    "PF-PORT-LCSC-CONN-100",
    "PF-PORT-PWR-6",
    "PF-PORT-LCSC-CABLE-56",
    "PF-PORT-PASSIVE-56",
    "PF-PORT-LCSC-MODULE-56",
]
CLOSED_EDGE = "G31--G41"


def rectangles_overlap(a, b, margin=0.05):
    return not (
        a[2] + margin <= b[0]
        or b[2] + margin <= a[0]
        or a[3] + margin <= b[1]
        or b[3] + margin <= a[1]
    )


def point_in_rect(x, y, rect, margin=0.08):
    return rect[0] - margin <= x <= rect[2] + margin and rect[1] - margin <= y <= rect[3] + margin


def orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a, b, c, d):
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return (o1 == 0 or o2 == 0 or o1 * o2 <= 0) and (o3 == 0 or o4 == 0 or o3 * o4 <= 0)


def segment_hits_rect(a, b, rect, margin=0.05):
    x0, y0, x1, y1 = rect
    r = (x0 - margin, y0 - margin, x1 + margin, y1 + margin)
    if point_in_rect(a[0], a[1], r, 0) or point_in_rect(b[0], b[1], r, 0):
        return True
    corners = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
    return any(segments_intersect(a, b, corners[i], corners[(i + 1) % 4]) for i in range(4))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    definition = load_warehouse_map(args.fixture)
    failures: list[str] = []

    metrics = {
        "map_code": definition.map_code,
        "version": definition.version,
        "nodes": len(definition.nodes),
        "edges": len(definition.edges),
        "bindings": len(definition.bindings),
        "cyclomatic_loops": len(definition.edges) - len(definition.nodes) + 1,
        "graph_hash": definition.graph_hash,
    }
    for key, expected in EXPECTED.items():
        if metrics[key] != expected:
            failures.append(f"{key}: {metrics[key]!r} != {expected!r}")

    node_by_code = {node.code: node for node in definition.nodes}
    boxes = {}
    for binding in definition.bindings:
        boxes[binding.location_code] = (
            binding.x_m,
            binding.y_m,
            binding.x_m + binding.width_m,
            binding.y_m + binding.depth_m,
        )
        box = boxes[binding.location_code]
        if box[0] < 0 or box[1] < 0 or box[2] > definition.width_m or box[3] > definition.height_m:
            failures.append(f"binding outside room: {binding.location_code} {box}")

    items = list(boxes.items())
    for i, (code_a, box_a) in enumerate(items):
        for code_b, box_b in items[i + 1 :]:
            if rectangles_overlap(box_a, box_b):
                failures.append(f"binding overlap: {code_a} / {code_b}")

    pick_nodes = {binding.pick_node_code for binding in definition.bindings}
    for node in definition.nodes:
        if node.code in pick_nodes:
            continue
        for code, box in boxes.items():
            if point_in_rect(node.x_m, node.y_m, box):
                failures.append(f"transit node {node.code} too close to {code}")

    for edge in definition.edges:
        first = node_by_code[edge.from_node]
        second = node_by_code[edge.to_node]
        for code, box in boxes.items():
            if segment_hits_rect((first.x_m, first.y_m), (second.x_m, second.y_m), box):
                failures.append(f"edge {edge.code} crosses {code}")

    # Connectivity from PACK.
    adjacency = {node.code: set() for node in definition.nodes}
    for edge in definition.edges:
        if not edge.enabled:
            continue
        adjacency[edge.from_node].add(edge.to_node)
        if edge.bidirectional:
            adjacency[edge.to_node].add(edge.from_node)
    seen = {definition.default_start_node}
    stack = [definition.default_start_node]
    while stack:
        node = stack.pop()
        for nxt in adjacency[node] - seen:
            seen.add(nxt)
            stack.append(nxt)
    if len(seen) != len(definition.nodes):
        failures.append(f"graph disconnected: reached {len(seen)}/{len(definition.nodes)}")

    optimized = optimize_route(definition, DEMO_STOPS)
    matrix = distance_matrix(definition, [definition.default_start_node, *DEMO_STOPS])
    sequence = [definition.default_start_node, *DEMO_STOPS, definition.default_start_node]
    input_order_distance = round(
        sum(matrix[a][b] for a, b in zip(sequence, sequence[1:], strict=False)), 4
    )
    saving = round(input_order_distance - optimized.total_distance_m, 4)
    saving_pct = round(saving * 100 / input_order_distance, 2)
    if optimized.strategy != "graph_v1_exact":
        failures.append(f"unexpected route strategy {optimized.strategy}")
    if not math.isclose(optimized.total_distance_m, 31.16, abs_tol=0.001):
        failures.append(f"optimized route {optimized.total_distance_m} != 31.16")
    if saving_pct < 35:
        failures.append(f"route optimization saving too small: {saving_pct}%")

    replanned = optimize_route(definition, DEMO_STOPS, closed_edge_codes={CLOSED_EDGE})
    detour = round(replanned.total_distance_m - optimized.total_distance_m, 4)
    if detour < 3:
        failures.append(f"closure detour too small: {detour}")

    report = {
        "status": "PASS" if not failures else "FAIL",
        **metrics,
        "input_order_distance_m": input_order_distance,
        "optimized_distance_m": optimized.total_distance_m,
        "saving_m": saving,
        "saving_pct": saving_pct,
        "optimized_order": list(optimized.ordered_stop_nodes),
        "closed_edge": CLOSED_EDGE,
        "replanned_distance_m": replanned.total_distance_m,
        "detour_m": detour,
        "replanned_order": list(replanned.ordered_stop_nodes),
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
