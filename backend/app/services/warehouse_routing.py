"""Deterministic warehouse geometry and walking-route primitives.

The source of truth is a versioned traversable graph: nodes + edges + organizer
bindings.  Pairwise distance matrices are derived/cacheable artifacts and never
replace the graph.

A bin/drawer does not need its own graph node.  It inherits the nearest bound
organizer pick-face through the Location parent chain; local slot geometry is
used only for drawing/highlighting the bin inside the organizer.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class WarehouseMapNode:
    code: str
    node_type: str
    x_m: float
    y_m: float
    label: str = ""


@dataclass(frozen=True)
class WarehouseMapEdge:
    code: str
    from_node: str
    to_node: str
    distance_m: float
    bidirectional: bool = True
    enabled: bool = True


@dataclass(frozen=True)
class LocationMapBinding:
    location_code: str
    pick_node_code: str
    x_m: float
    y_m: float
    width_m: float
    depth_m: float
    rotation_deg: float = 0.0
    facing: str = "aisle"
    local_geometry_kind: str = "organizer"


@dataclass(frozen=True)
class WarehouseMapDefinition:
    map_code: str
    warehouse_code: str
    name: str
    version: str
    calibration_status: str
    coordinate_unit: str
    width_m: float
    height_m: float
    default_start_node: str
    default_end_node: str | None
    nodes: tuple[WarehouseMapNode, ...]
    edges: tuple[WarehouseMapEdge, ...]
    bindings: tuple[LocationMapBinding, ...]
    geometry_note: str = ""

    @property
    def graph_hash(self) -> str:
        canonical = {
            "map_code": self.map_code,
            "warehouse_code": self.warehouse_code,
            "version": self.version,
            "nodes": [node.__dict__ for node in self.nodes],
            "edges": [edge.__dict__ for edge in self.edges],
            "bindings": [binding.__dict__ for binding in self.bindings],
        }
        payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RouteSegment:
    from_node: str
    to_node: str
    distance_m: float
    path_nodes: tuple[str, ...]


@dataclass(frozen=True)
class OptimizedWarehouseRoute:
    strategy: str
    map_code: str
    graph_hash: str
    calibration_status: str
    start_node: str
    end_node: str | None
    ordered_stop_nodes: tuple[str, ...]
    total_distance_m: float
    segments: tuple[RouteSegment, ...]
    optimization_note: str


@dataclass(frozen=True)
class LocalSlotGeometry:
    x_norm: float
    y_norm: float
    width_norm: float
    height_norm: float


def _edge_distance(raw: Mapping[str, object], nodes: Mapping[str, WarehouseMapNode]) -> float:
    explicit = raw.get("distance_m")
    if explicit is not None:
        distance = float(explicit)
    else:
        first = nodes[str(raw["from_node"])]
        second = nodes[str(raw["to_node"])]
        distance = math.hypot(second.x_m - first.x_m, second.y_m - first.y_m)
    if distance <= 0:
        raise ValueError(f"edge {raw.get('code')} must have positive distance")
    return round(distance, 4)


def load_warehouse_map(path: str | Path) -> WarehouseMapDefinition:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes_list = [
        WarehouseMapNode(
            code=str(item["code"]),
            node_type=str(item["node_type"]),
            x_m=float(item["x_m"]),
            y_m=float(item["y_m"]),
            label=str(item.get("label") or ""),
        )
        for item in payload["nodes"]
    ]
    nodes = {item.code: item for item in nodes_list}
    if len(nodes) != len(nodes_list):
        raise ValueError("warehouse map node codes must be unique")

    edges: list[WarehouseMapEdge] = []
    edge_codes: set[str] = set()
    for raw in payload["edges"]:
        code = str(raw["code"])
        if code in edge_codes:
            raise ValueError(f"duplicate edge code: {code}")
        edge_codes.add(code)
        from_node = str(raw["from_node"])
        to_node = str(raw["to_node"])
        if from_node not in nodes or to_node not in nodes:
            raise ValueError(f"edge {code} references an unknown node")
        edges.append(
            WarehouseMapEdge(
                code=code,
                from_node=from_node,
                to_node=to_node,
                distance_m=_edge_distance(raw, nodes),
                bidirectional=bool(raw.get("bidirectional", True)),
                enabled=bool(raw.get("enabled", True)),
            )
        )

    bindings = tuple(
        LocationMapBinding(
            location_code=str(item["location_code"]),
            pick_node_code=str(item["pick_node_code"]),
            x_m=float(item["x_m"]),
            y_m=float(item["y_m"]),
            width_m=float(item["width_m"]),
            depth_m=float(item["depth_m"]),
            rotation_deg=float(item.get("rotation_deg") or 0),
            facing=str(item.get("facing") or "aisle"),
            local_geometry_kind=str(item.get("local_geometry_kind") or "organizer"),
        )
        for item in payload.get("organizer_bindings", [])
    )
    unknown_binding_nodes = sorted(
        {item.pick_node_code for item in bindings if item.pick_node_code not in nodes}
    )
    if unknown_binding_nodes:
        raise ValueError(f"bindings reference unknown pick nodes: {unknown_binding_nodes}")

    result = WarehouseMapDefinition(
        map_code=str(payload["map_code"]),
        warehouse_code=str(payload["warehouse_code"]),
        name=str(payload["name"]),
        version=str(payload["version"]),
        calibration_status=str(payload["calibration_status"]),
        coordinate_unit=str(payload.get("coordinate_unit") or "m"),
        width_m=float(payload["width_m"]),
        height_m=float(payload["height_m"]),
        default_start_node=str(payload["default_start_node"]),
        default_end_node=(
            str(payload["default_end_node"]) if payload.get("default_end_node") else None
        ),
        nodes=tuple(nodes_list),
        edges=tuple(edges),
        bindings=bindings,
        geometry_note=str(payload.get("geometry_note") or ""),
    )
    validate_warehouse_map(result)
    return result


def _adjacency(
    definition: WarehouseMapDefinition,
    *,
    closed_edge_codes: Iterable[str] = (),
) -> dict[str, list[tuple[str, float]]]:
    closed = set(closed_edge_codes)
    unknown = closed - {edge.code for edge in definition.edges}
    if unknown:
        raise ValueError(f"unknown closed edge codes: {', '.join(sorted(unknown))}")
    graph = {node.code: [] for node in definition.nodes}
    for edge in definition.edges:
        if not edge.enabled or edge.code in closed:
            continue
        graph[edge.from_node].append((edge.to_node, edge.distance_m))
        if edge.bidirectional:
            graph[edge.to_node].append((edge.from_node, edge.distance_m))
    for values in graph.values():
        values.sort(key=lambda item: item[0])
    return graph


def shortest_path(
    definition: WarehouseMapDefinition,
    start_node: str,
    end_node: str,
    *,
    closed_edge_codes: Iterable[str] = (),
) -> tuple[float, tuple[str, ...]]:
    graph = _adjacency(definition, closed_edge_codes=closed_edge_codes)
    if start_node not in graph or end_node not in graph:
        raise ValueError("unknown warehouse routing node")
    queue: list[tuple[float, str]] = [(0.0, start_node)]
    distance = {start_node: 0.0}
    previous: dict[str, str] = {}
    while queue:
        current_distance, node = heapq.heappop(queue)
        if current_distance > distance.get(node, math.inf):
            continue
        if node == end_node:
            break
        for next_node, weight in graph[node]:
            candidate = current_distance + weight
            if candidate + 1e-9 < distance.get(next_node, math.inf):
                distance[next_node] = candidate
                previous[next_node] = node
                heapq.heappush(queue, (candidate, next_node))
    if end_node not in distance:
        raise ValueError(f"no traversable path: {start_node} -> {end_node}")
    path = [end_node]
    while path[-1] != start_node:
        path.append(previous[path[-1]])
    path.reverse()
    return round(distance[end_node], 4), tuple(path)


def validate_warehouse_map(definition: WarehouseMapDefinition) -> None:
    nodes = {node.code: node for node in definition.nodes}
    if len(nodes) != len(definition.nodes):
        raise ValueError("duplicate node code")
    codes = [edge.code for edge in definition.edges]
    if len(codes) != len(set(codes)):
        raise ValueError("duplicate edge code")
    for edge in definition.edges:
        if (
            edge.from_node not in nodes
            or edge.to_node not in nodes
            or edge.from_node == edge.to_node
            or not math.isfinite(edge.distance_m)
            or edge.distance_m <= 0
        ):
            raise ValueError(f"invalid edge: {edge.code}")
    if (
        not math.isfinite(definition.width_m)
        or not math.isfinite(definition.height_m)
        or definition.width_m <= 0
        or definition.height_m <= 0
    ):
        raise ValueError("warehouse map dimensions must be positive")
    if definition.coordinate_unit != "m":
        raise ValueError("warehouse routing currently requires metre coordinates")
    if definition.default_start_node not in nodes:
        raise ValueError("default_start_node is missing")
    if definition.default_end_node and definition.default_end_node not in nodes:
        raise ValueError("default_end_node is missing")
    for node in definition.nodes:
        if not 0 <= node.x_m <= definition.width_m or not 0 <= node.y_m <= definition.height_m:
            raise ValueError(f"node outside map boundary: {node.code}")
    binding_codes = [binding.location_code for binding in definition.bindings]
    if len(binding_codes) != len(set(binding_codes)):
        raise ValueError("location organizer bindings must be unique")
    for binding in definition.bindings:
        if binding.width_m <= 0 or binding.depth_m <= 0:
            raise ValueError(f"binding footprint must be positive: {binding.location_code}")
        if binding.pick_node_code not in nodes:
            raise ValueError(f"binding references unknown pick node: {binding.location_code}")
        if nodes[binding.pick_node_code].node_type != "pick_face":
            raise ValueError(f"binding must reference a pick_face node: {binding.location_code}")
        if (
            not 0 <= binding.x_m <= definition.width_m
            or not 0 <= binding.y_m <= definition.height_m
        ):
            raise ValueError(f"binding origin outside map boundary: {binding.location_code}")
        if not all(
            math.isfinite(value)
            for value in (binding.width_m, binding.depth_m, binding.rotation_deg)
        ):
            raise ValueError(f"non-finite binding geometry: {binding.location_code}")
        angle = math.radians(binding.rotation_deg % 360)
        cx, cy = binding.x_m + binding.width_m / 2, binding.y_m + binding.depth_m / 2
        for dx in (-binding.width_m / 2, binding.width_m / 2):
            for dy in (-binding.depth_m / 2, binding.depth_m / 2):
                x = cx + dx * math.cos(angle) - dy * math.sin(angle)
                y = cy + dx * math.sin(angle) + dy * math.cos(angle)
                if not -1e-9 <= x <= definition.width_m + 1e-9:
                    raise ValueError(
                        f"binding footprint exceeds map width: {binding.location_code}"
                    )
                if not -1e-9 <= y <= definition.height_m + 1e-9:
                    raise ValueError(
                        f"binding footprint exceeds map height: {binding.location_code}"
                    )
    # Every active node must be reachable from the configured start on the baseline graph.
    for code in nodes:
        shortest_path(definition, definition.default_start_node, code)


def distance_matrix(
    definition: WarehouseMapDefinition,
    node_codes: Sequence[str],
    *,
    closed_edge_codes: Iterable[str] = (),
) -> dict[str, dict[str, float]]:
    unique = tuple(dict.fromkeys(node_codes))
    result: dict[str, dict[str, float]] = {}
    for first in unique:
        result[first] = {}
        for second in unique:
            if first == second:
                result[first][second] = 0.0
            else:
                result[first][second] = shortest_path(
                    definition,
                    first,
                    second,
                    closed_edge_codes=closed_edge_codes,
                )[0]
    return result


def _route_cost(
    order: Sequence[str], matrix: Mapping[str, Mapping[str, float]], start: str, end: str | None
) -> float:
    current = start
    total = 0.0
    for node in order:
        total += matrix[current][node]
        current = node
    if end is not None:
        total += matrix[current][end]
    return total


def _held_karp_order(
    stops: Sequence[str],
    matrix: Mapping[str, Mapping[str, float]],
    *,
    start: str,
    end: str | None,
) -> tuple[str, ...]:
    n = len(stops)
    if n == 0:
        return ()
    # (mask, last_index) -> (cost, previous_last_index)
    dp: dict[tuple[int, int], tuple[float, int | None]] = {}
    for index, stop in enumerate(stops):
        dp[(1 << index, index)] = (matrix[start][stop], None)
    for mask in range(1, 1 << n):
        for last in range(n):
            state = (mask, last)
            if state not in dp:
                continue
            cost, _ = dp[state]
            for nxt in range(n):
                if mask & (1 << nxt):
                    continue
                next_mask = mask | (1 << nxt)
                next_cost = cost + matrix[stops[last]][stops[nxt]]
                next_state = (next_mask, nxt)
                current = dp.get(next_state)
                if current is None or (next_cost, stops[last]) < (
                    current[0],
                    stops[current[1]] if current[1] is not None else "",
                ):
                    dp[next_state] = (next_cost, last)
    full = (1 << n) - 1
    best_last = min(
        range(n),
        key=lambda last: (
            dp[(full, last)][0] + (matrix[stops[last]][end] if end is not None else 0.0),
            stops[last],
        ),
    )
    order_indices = [best_last]
    mask = full
    last = best_last
    while True:
        _, previous = dp[(mask, last)]
        if previous is None:
            break
        mask ^= 1 << last
        last = previous
        order_indices.append(last)
    order_indices.reverse()
    return tuple(stops[index] for index in order_indices)


def _nearest_neighbor_order(
    stops: Sequence[str],
    matrix: Mapping[str, Mapping[str, float]],
    *,
    start: str,
) -> list[str]:
    remaining = set(stops)
    current = start
    order: list[str] = []
    while remaining:
        nxt = min(remaining, key=lambda code: (matrix[current][code], code))
        order.append(nxt)
        remaining.remove(nxt)
        current = nxt
    return order


def _two_opt(
    order: list[str],
    matrix: Mapping[str, Mapping[str, float]],
    *,
    start: str,
    end: str | None,
) -> tuple[str, ...]:
    best = list(order)
    best_cost = _route_cost(best, matrix, start, end)
    improved = True
    while improved:
        improved = False
        for first in range(len(best) - 1):
            for second in range(first + 2, len(best) + 1):
                candidate = best[:first] + list(reversed(best[first:second])) + best[second:]
                candidate_cost = _route_cost(candidate, matrix, start, end)
                if candidate_cost + 1e-9 < best_cost:
                    best = candidate
                    best_cost = candidate_cost
                    improved = True
                    break
            if improved:
                break
    return tuple(best)


def optimize_route(
    definition: WarehouseMapDefinition,
    stop_nodes: Iterable[str],
    *,
    start_node: str | None = None,
    end_node: str | None = None,
    closed_edge_codes: Iterable[str] = (),
    exact_stop_limit: int = 12,
) -> OptimizedWarehouseRoute:
    start = start_node or definition.default_start_node
    end = definition.default_end_node if end_node is None else end_node
    stops = tuple(sorted(set(stop_nodes)))
    if start in stops:
        stops = tuple(item for item in stops if item != start)
    if end is not None and end in stops:
        stops = tuple(item for item in stops if item != end)
    all_nodes = [start, *stops]
    if end is not None:
        all_nodes.append(end)
    matrix = distance_matrix(definition, all_nodes, closed_edge_codes=closed_edge_codes)
    if len(stops) <= exact_stop_limit:
        order = _held_karp_order(stops, matrix, start=start, end=end)
        strategy = "graph_v1_exact"
        optimization_note = (
            "exact shortest stop ordering on the configured traversable warehouse graph; "
            "physical accuracy depends on map calibration status"
        )
    else:
        seed = _nearest_neighbor_order(stops, matrix, start=start)
        order = _two_opt(seed, matrix, start=start, end=end)
        strategy = "graph_v1_nn_2opt"
        optimization_note = (
            "nearest-neighbor plus 2-opt on the configured traversable warehouse graph; "
            "not guaranteed globally optimal for more than the exact-stop limit"
        )

    route_nodes = [start, *order]
    if end is not None:
        route_nodes.append(end)
    segments: list[RouteSegment] = []
    total = 0.0
    for first, second in zip(route_nodes, route_nodes[1:], strict=False):
        segment_distance, path_nodes = shortest_path(
            definition,
            first,
            second,
            closed_edge_codes=closed_edge_codes,
        )
        total += segment_distance
        segments.append(
            RouteSegment(
                from_node=first,
                to_node=second,
                distance_m=segment_distance,
                path_nodes=path_nodes,
            )
        )
    return OptimizedWarehouseRoute(
        strategy=strategy,
        map_code=definition.map_code,
        graph_hash=definition.graph_hash,
        calibration_status=definition.calibration_status,
        start_node=start,
        end_node=end,
        ordered_stop_nodes=order,
        total_distance_m=round(total, 4),
        segments=tuple(segments),
        optimization_note=optimization_note,
    )


def local_slot_geometry(
    organizer_style: str,
    slot_name: str,
    *,
    left_module: str = "small",
    right_module: str = "small",
) -> LocalSlotGeometry:
    """Return normalized organizer-local slot geometry for map/detail highlighting."""

    name = slot_name.upper()
    if organizer_style == "drawer_rack_100":
        if len(name) < 2 or name[0] not in "ABCDE":
            raise ValueError(f"invalid 100-drawer slot: {slot_name}")
        column = ord(name[0]) - ord("A")
        row = int(name[1:]) - 1
        if not 0 <= row < 20:
            raise ValueError(f"invalid 100-drawer row: {slot_name}")
        return LocalSlotGeometry((column + 0.5) / 5, (row + 0.5) / 20, 1 / 5, 1 / 20)
    if organizer_style == "standard_56":
        if len(name) < 2 or name[0] not in "ABCDEFG":
            raise ValueError(f"invalid 56-bin slot: {slot_name}")
        row = ord(name[0]) - ord("A")
        column = int(name[1:]) - 1
        if not 0 <= column < 8:
            raise ValueError(f"invalid 56-bin column: {slot_name}")
        return LocalSlotGeometry((column + 0.5) / 8, (row + 0.5) / 7, 1 / 8, 1 / 7)
    if organizer_style == "shelf_rack_6":
        if not name.startswith("L"):
            raise ValueError(f"invalid shelf slot: {slot_name}")
        level = int(name[1:]) - 1
        if not 0 <= level < 6:
            raise ValueError(f"invalid shelf level: {slot_name}")
        return LocalSlotGeometry(0.5, (level + 0.5) / 6, 1.0, 1 / 6)
    if organizer_style == "split_configurable":
        parts = name.split("-")
        if len(parts) != 2 or parts[0] not in {"L", "R"} or len(parts[1]) < 2:
            raise ValueError(f"invalid split organizer slot: {slot_name}")
        side = parts[0]
        module = left_module if side == "L" else right_module
        expected_prefix = "S" if module == "small" else "L"
        if parts[1][0] != expected_prefix:
            raise ValueError(f"slot/module mismatch: {slot_name}")
        index = int(parts[1][1:]) - 1
        columns, rows = (4, 7) if module == "small" else (2, 4)
        if not 0 <= index < columns * rows:
            raise ValueError(f"invalid split organizer slot index: {slot_name}")
        column = index % columns
        row = index // columns
        half_x = 0.0 if side == "L" else 0.5
        return LocalSlotGeometry(
            half_x + (column + 0.5) / columns * 0.5,
            (row + 0.5) / rows,
            0.5 / columns,
            1 / rows,
        )
    raise ValueError(f"unsupported organizer style: {organizer_style}")
