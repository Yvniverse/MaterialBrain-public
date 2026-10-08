"""Pure canonical V3 world -> semantic, collision-checked WarehouseMap export."""

from __future__ import annotations

import copy
import math
from functools import lru_cache

from app.services.embodied_navigation.geometry import (
    obstacles_for,
    point_clearance,
    segment_clearance,
)
from app.services.embodied_navigation.planner import canonical_hash, load_world

from .geometry import intersects, line_geometry, rectangle_polygon

ROBOT_CLASSES = ["MB-R01"]
GRAPH_STEP_M = 1.0
SNAPSHOT_VERSION = "spatial-v4.1"


def static_revision(snapshot: dict) -> str:
    """Overlays have their own lifetime; they never rewrite map/graph identity."""
    return canonical_hash(
        {k: v for k, v in snapshot.items() if k not in ("revision", "dynamic_overlays")}
    )


def _zones(world):
    policies = {
        "ZA": ("esd", 0.6, 0.01, ["esd_protected_payload"]),
        "ZB": ("area", 0.8, 0.04, []),
        "ZC": ("receiving", 0.35, 0.25, ["yield_to_receiving_staff", "receiving_slow"]),
        "ZD": ("handoff", 0.45, 0.12, ["human_handoff_only", "handoff_slow"]),
        "ZQ": ("keepout", None, 1.0, ["no_robot_entry"]),
    }
    result = []
    for zone in world["zones"]:
        kind, speed, risk, rules = policies[zone["id"]]
        result.append(
            {
                "id": zone["id"],
                "name": zone["name"],
                "kind": kind,
                "polygon": rectangle_polygon(zone),
                "speed_limit_mps": speed,
                "risk_level": risk,
                "rule_refs": rules,
                "source": "canonical_v3_zone_with_synthetic_policy",
            }
        )
    # Semantic policy annotations are explicit synthetic metadata, not moved assets.
    for code, name, kind, rect, speed, risk, rules in (
        (
            "Z-HUMAN-CROSSING",
            "中央人机混行通道",
            "human_mixed",
            {"x": 8.75, "y": 9.25, "width": 5.25, "depth": 1.4},
            0.8,
            0.75,
            ["yield_to_humans", "esd_exposure"],
        ),
        (
            "Z-CHARGING",
            "充电停靠区",
            "charging",
            {"x": world["charger"]["x"], "y": world["charger"]["y"], "width": 1.5, "depth": 1.3},
            0.25,
            0.02,
            ["charging_dock_slow"],
        ),
        (
            "Z-ESD-QC",
            "防静电检验交接",
            "esd",
            {"x": 10.25, "y": 3.4, "width": 3.5, "depth": 1.6},
            0.45,
            0.01,
            ["esd_protected_payload", "human_handoff_only"],
        ),
    ):
        result.append(
            {
                "id": code,
                "name": name,
                "kind": kind,
                "polygon": rectangle_polygon(rect),
                "speed_limit_mps": speed,
                "risk_level": risk,
                "rule_refs": rules,
                "source": "synthetic_lab_policy",
            }
        )
    return sorted(result, key=lambda item: item["id"])


def _docks(world):
    result = []
    for goal in world["goals"]:
        result.append(
            {
                **copy.deepcopy(goal),
                "pose": {key: float(goal["pose"][key]) for key in ("x", "y", "yaw")},
                "node_id": goal["id"],
                "kind": "human_handoff",
                "robot_classes": ROBOT_CLASSES[:],
                "capabilities": list(goal["require_capabilities"]),
                "human_handoff_only": True,
            }
        )
    for code, label, pose, kind, asset, capabilities in (
        ("HOME", "机器人待命点", world["home"], "home", None, ["navigate", "wait_or_yield"]),
        ("CHARGER", "充电停靠点", world["charger"], "charging", "D05", ["navigate", "charge"]),
    ):
        result.append(
            {
                "id": code,
                "label": label,
                "pose": {key: float(pose[key]) for key in ("x", "y", "yaw")},
                "asset_id": asset,
                "node_id": code,
                "kind": kind,
                "robot_classes": ROBOT_CLASSES[:],
                "capabilities": capabilities,
                "service_s": 0,
                "payload_kg": 0,
                "human_handoff_only": False,
            }
        )
    return sorted(result, key=lambda item: item["id"])


def _route_graph(world, zones, docks):
    robot = world["robot"]
    radius = math.hypot(robot["length"], robot["width"]) / 2 + robot["margin"]
    obstacles = obstacles_for(world)
    nodes, edges, grid = {}, [], {}
    nx, ny = math.floor(world["width"] / GRAPH_STEP_M), math.floor(world["height"] / GRAPH_STEP_M)
    for ix in range(nx):
        for iy in range(ny):
            pose = {"x": ix * GRAPH_STEP_M + 0.75, "y": iy * GRAPH_STEP_M + 0.75, "yaw": 0.0}
            if point_clearance(world, pose, obstacles) <= radius + 1e-9:
                continue
            code = f"N{ix:02d}-{iy:02d}"
            nodes[code] = {"id": code, **pose, "kind": "intersection"}
            grid[ix, iy] = code

    def add_connection(first, second):
        a, b = nodes[first], nodes[second]
        clearance = segment_clearance(world, a, b, obstacles)
        if clearance <= radius + 1e-9:
            return False
        geometry = line_geometry(a, b)
        overlaps = [zone for zone in zones if intersects(zone["polygon"], geometry)]
        if any(zone["kind"] == "keepout" for zone in overlaps):
            return False
        speed = min(
            [robot["max_speed"], *(z["speed_limit_mps"] or robot["max_speed"] for z in overlaps)]
        )
        length = round(math.hypot(b["x"] - a["x"], b["y"] - a["y"]), 4)
        common = {
            "length_m": length,
            "width_m": round(clearance * 2, 6),
            "speed_limit_mps": speed,
            "risk_level": max((z["risk_level"] for z in overlaps), default=0.0),
            "energy_wh": round(
                length * robot["wh_per_m"] + robot["idle_w"] * length / speed / 3600, 6
            ),
            "min_clearance_m": round(clearance - radius, 6),
            "zone_ids": [z["id"] for z in overlaps],
            "rule_refs": sorted({rule for z in overlaps for rule in z["rule_refs"]}),
            "allowed_robot_classes": ROBOT_CLASSES[:],
            "bidirectional": False,
        }
        for origin, target in ((first, second), (second, first)):
            edges.append(
                {
                    "id": f"E-{origin}-{target}",
                    "from": origin,
                    "to": target,
                    **copy.deepcopy(common),
                    "geometry": line_geometry(nodes[origin], nodes[target]),
                }
            )
        return True

    for (ix, iy), first in sorted(grid.items()):
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
            second = grid.get((ix + dx, iy + dy))
            if second:
                add_connection(first, second)
    for dock in docks:
        code = dock["node_id"]
        pose = dock["pose"]
        if point_clearance(world, pose, obstacles) <= radius + 1e-9:
            raise ValueError("CANONICAL_DOCK_BLOCKED:" + code)
        nodes[code] = {"id": code, **pose, "kind": "dock"}
        nearby = sorted(
            (math.hypot(pose["x"] - n["x"], pose["y"] - n["y"]), n["id"])
            for n in nodes.values()
            if n["kind"] == "intersection"
        )
        connected = 0
        for distance, target in nearby:
            if distance > 2.25:
                break
            if distance > 1e-8 and add_connection(code, target):
                connected += 1
                if connected == 4:
                    break
        if not connected:
            raise ValueError("CANONICAL_DOCK_UNCONNECTED:" + code)
    # Drop any inaccessible islands; all registered docks must remain reachable.
    adjacent = {code: [] for code in nodes}
    for edge in edges:
        adjacent[edge["from"]].append(edge["to"])
    reached, queue = {"HOME"}, ["HOME"]
    while queue:
        code = queue.pop()
        for target in adjacent[code]:
            if target not in reached:
                reached.add(target)
                queue.append(target)
    for dock in docks:
        if dock["node_id"] not in reached:
            raise ValueError("CANONICAL_DOCK_UNREACHABLE:" + dock["id"])
    return {
        "nodes": sorted(
            (node for code, node in nodes.items() if code in reached), key=lambda n: n["id"]
        ),
        "edges": sorted((edge for edge in edges if edge["from"] in reached), key=lambda e: e["id"]),
    }


@lru_cache(maxsize=1)
def _build_lab_snapshot():
    world = load_world()
    zones, docks = _zones(world), _docks(world)
    graph = _route_graph(world, zones, docks)
    width, height = world["width"], world["height"]
    assets = [
        {**copy.deepcopy(asset), "footprint": rectangle_polygon(asset)} for asset in world["assets"]
    ]
    floor = rectangle_polygon({"x": width / 2, "y": height / 2, "width": width, "depth": height})
    walls = [
        {
            "id": code,
            "geometry": {"type": "LineString", "coordinates": [a, b]},
            "source": "canonical_floor_boundary",
        }
        for code, a, b in (
            ("W-WEST", [0, 0], [0, height]),
            ("W-EAST", [width, 0], [width, height]),
            ("W-SOUTH", [0, 0], [width, 0]),
            ("W-NORTH", [0, height], [width, height]),
        )
    ]
    snapshot = {
        "schema_version": 1,
        "map_id": world["id"],
        "frame": {"name": "warehouse_map", "unit": "m", "srid": 0, "site_georef": None},
        "geometry": {
            "floor": floor,
            "assets": sorted(assets, key=lambda a: a["id"]),
            "walls": walls,
            "columns": sorted((a for a in assets if a["kind"] == "column"), key=lambda a: a["id"]),
        },
        "zones": zones,
        "route_graph": graph,
        "docks": docks,
        "affordances": [
            {
                "entity_id": dock["id"],
                "asset_id": dock["asset_id"],
                "capabilities": dock["capabilities"][:],
                "human_handoff_only": dock["human_handoff_only"],
                "execution_boundary": "simulation",
                "inventory_write": False,
                "can_open_drawer": False,
                "can_grasp": False,
            }
            for dock in docks
        ],
        "dynamic_overlays": [],
        "provenance": {
            "source": "synthetic_lab",
            "source_world_id": world["id"],
            "source_world_revision": world["revision_sha256"],
            "source_world_version": world["revision"],
            "snapshot_builder": SNAPSHOT_VERSION,
            "graph_step_m": GRAPH_STEP_M,
            "geometry_source": "unchanged_canonical_v3_world",
            "policy_source": "synthetic_lab_policy",
            "calibration_status": "demo_synthetic",
            "map_status": "draft",
            "robot": {
                **copy.deepcopy(world["robot"]),
                "charge_w": 160,
                "charge_source": "synthetic_lab_simulation",
            },
            "home_dock_id": "HOME",
            "charger_dock_id": "CHARGER",
            "default_goal_ids": world["default_goal_ids"][:],
            "energy_model": (
                "edge drive Wh = distance*wh_per_m + idle_w*travel_s/3600; excludes stop/wait/yaw"
            ),
            "clearance_model": (
                "exact segment-to-rotated-footprint distance minus "
                "circumscribed robot radius and margin"
            ),
            "graph_hash": canonical_hash(graph),
            "regulations": [
                {"id": "yield_to_humans", "kind": "risk", "enforcement": "profile_cost"},
                {"id": "no_robot_entry", "kind": "keepout", "enforcement": "hard"},
                {"id": "human_handoff_only", "kind": "affordance", "enforcement": "hard"},
                {"id": "esd_exposure", "kind": "esd", "enforcement": "profile_cost"},
            ],
            "inventory_written": False,
        },
    }
    snapshot["revision"] = static_revision(snapshot)
    return snapshot


def build_lab_snapshot() -> dict:
    """Return a deterministic copy of canonical geometry, with no external writes."""
    return copy.deepcopy(_build_lab_snapshot())
