"""Directed Dijkstra routes with observable semantic costs and hard exclusions."""

from __future__ import annotations

import copy
import heapq
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from .geometry import (
    active_overlays,
    line_coordinates,
    line_distance,
    parse_time,
    static_clearance,
)

PROFILES = {
    "fastest": {"clearance": 0.25, "risk": 0.35, "energy": 0.10, "semantic": 0.25},
    "safest": {"clearance": 0.85, "risk": 2.50, "energy": 0.20, "semantic": 1.20},
    "esd_safe": {"clearance": 0.85, "risk": 2.25, "energy": 0.20, "semantic": 1.60},
}


@dataclass(frozen=True)
class RobotProfile:
    robot_class: str = "MB-R01"
    width_m: float = 0.58
    length_m: float = 0.76
    margin_m: float = 0.09
    cruise_mps: float = 0.8
    angular_speed: float = 1.0
    battery_wh: float = 320
    idle_w: float = 12
    wh_per_m: float = 0.035
    charge_w: float = 160

    @property
    def radius_m(self):
        return math.hypot(self.width_m, self.length_m) / 2 + self.margin_m

    @classmethod
    def from_snapshot(cls, snapshot: dict, robot_class: str):
        provenance = snapshot.get("provenance", {})
        raw = provenance.get("robot", {}) if isinstance(provenance, dict) else {}
        aliases = {
            "width_m": "width",
            "length_m": "length",
            "margin_m": "margin",
            "cruise_mps": "max_speed",
        }
        values = {"robot_class": robot_class}
        for key in cls.__dataclass_fields__:
            if key == "robot_class":
                continue
            value = raw.get(key, raw.get(aliases.get(key, key), getattr(cls(), key)))
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("INVALID_ROBOT_PROFILE:" + key)
            if value < 0 or (key not in {"margin_m", "idle_w", "wh_per_m"} and value == 0):
                raise ValueError("INVALID_ROBOT_PROFILE:" + key)
            values[key] = float(value)
        return cls(**values)


class SemanticGraph:
    """A request-local graph; no cache can survive a different closure or profile."""

    def __init__(
        self, snapshot: dict, *, profile: str, robot: RobotProfile, overlays: list | None = None
    ):
        self.snapshot = snapshot
        self.profile = profile
        self.weights = PROFILES[profile]
        self.robot = robot
        self.nodes = {n["id"]: copy.deepcopy(n) for n in snapshot["route_graph"]["nodes"]}
        if len(self.nodes) != len(snapshot["route_graph"]["nodes"]):
            raise ValueError("DUPLICATE_GRAPH_NODE")
        for node in self.nodes.values():
            if not all(math.isfinite(float(node[k])) for k in ("x", "y")):
                raise ValueError("INVALID_GRAPH_COORDINATE")
        self.zones = {z["id"]: z for z in snapshot.get("zones", [])}
        provenance = snapshot.get("provenance", {})
        reference_time = provenance.get("evaluation_time") if isinstance(provenance, dict) else None
        self.now = parse_time(reference_time) if reference_time else datetime.now(UTC)
        # A request may add restrictions; it cannot remove persisted restrictions
        # by submitting an expired overlay with the same identifier.
        merged = list(snapshot.get("dynamic_overlays", [])) + list(overlays or [])
        self.overlays, self.expired_overlay_ids = active_overlays(merged, self.now)
        self.edges, self.terms, self.excluded = {}, {}, []
        self.adjacency = {n: [] for n in self.nodes}
        self._source_cache = {}
        for original in snapshot["route_graph"]["edges"]:
            edge = copy.deepcopy(original)
            if edge["id"] in self.edges or any(e["edge_id"] == edge["id"] for e in self.excluded):
                raise ValueError("DUPLICATE_GRAPH_EDGE")
            if edge["from"] not in self.nodes or edge["to"] not in self.nodes:
                raise ValueError("UNKNOWN_EDGE_NODE")
            self._insert(edge)
        for neighbours in self.adjacency.values():
            neighbours.sort(key=lambda e: e["id"])

    def _insert(self, edge):
        terms, reason = self.edge_terms(edge)
        if reason:
            self.excluded.append({"edge_id": edge["id"], "reason": reason})
            return
        self.edges[edge["id"]] = edge
        self.terms[edge["id"]] = terms
        self.adjacency[edge["from"]].append(edge)

    def edge_terms(self, edge: dict) -> tuple[dict | None, str | None]:
        length = float(edge.get("length_m", edge.get("distance_m", math.nan)))
        width = float(edge.get("width_m", math.nan))
        speed = min(
            self.robot.cruise_mps, float(edge.get("speed_limit_mps", self.robot.cruise_mps))
        )
        risk_level = float(edge.get("risk_level", 0))
        if not all(math.isfinite(v) for v in (length, width, speed, risk_level)):
            raise ValueError("INVALID_EDGE_METRIC:" + edge["id"])
        if length < 0 or width <= 0 or speed <= 0 or risk_level < 0:
            raise ValueError("INVALID_EDGE_METRIC:" + edge["id"])
        allowed = set(edge.get("allowed_robot_classes") or [])
        if allowed and self.robot.robot_class not in allowed:
            return None, "ROBOT_CLASS"
        if width + 1e-9 < self.robot.width_m + 2 * self.robot.margin_m:
            return None, "FOOTPRINT_WIDTH"
        if edge.get("blocked") or edge.get("keepout"):
            return None, "BLOCKED_EDGE"
        coords = line_coordinates(edge, self.nodes)
        zones = set(edge.get("zone_ids") or [])
        for zone in self.zones.values():
            buffer = self.robot.radius_m if zone.get("kind") in {"keepout", "restricted"} else 1e-9
            if line_distance(coords, zone) <= buffer:
                zones.add(zone["id"])
        semantic, rules = 0.0, set(edge.get("rule_refs") or [])
        human_mixed = bool(edge.get("human_mixed"))
        esd_sensitive = bool(edge.get("crosses_esd_sensitive")) or "esd_exposure" in rules
        esd_preferred = bool({"esd_preferred", "esd_protected_payload"} & rules)
        for zone_id in zones:
            zone = self.zones.get(zone_id, {})
            kind = zone.get("kind", "")
            if kind in {"keepout", "closure", "restricted"}:
                return None, "KEEPOUT_ZONE"
            if zone.get("speed_limit_mps") is not None:
                limit = float(zone["speed_limit_mps"])
                if not math.isfinite(limit) or limit <= 0:
                    raise ValueError("INVALID_ZONE_SPEED")
                speed = min(speed, limit)
            risk_level = max(risk_level, float(zone.get("risk_level") or 0))
            human_mixed |= kind in {"human_mixed", "human-mixed"}
            zone_rules = set(zone.get("rule_refs") or [])
            protected = "esd_protected_payload" in zone_rules
            esd_sensitive |= (
                kind in {"esd", "esd_sensitive", "precision"} and not protected
            ) or "esd_exposure" in zone_rules
            esd_preferred |= (
                kind in {"esd_preferred", "esd_safe"}
                or bool(zone.get("esd_preferred"))
                or protected
            )
        for overlay in self.overlays:
            if edge["id"] in overlay.get("edge_ids", []):
                hit = True
            elif edge["from"] in overlay.get("node_ids", []) or edge["to"] in overlay.get(
                "node_ids", []
            ):
                hit = True
            else:
                hit = line_distance(coords, overlay) <= self.robot.radius_m
            if not hit:
                continue
            if overlay.get("kind") in {
                "closure",
                "obstacle",
                "keepout",
                "blocked",
                "aisle_closure",
            }:
                return None, "DYNAMIC_CLOSURE:" + overlay["id"]
            if overlay.get("speed_limit_mps") is not None:
                speed = min(speed, float(overlay["speed_limit_mps"]))
                if speed <= 0:
                    return None, "DYNAMIC_ZERO_SPEED"
        travel = length / speed
        clearance = max(0.0, float(edge.get("min_clearance_m", width / 2 - self.robot.radius_m)))
        clearance_penalty = length * math.exp(
            -max(0, width - self.robot.width_m - 2 * self.robot.margin_m) / 0.65
        )
        energy = float(
            edge.get("energy_wh", length * self.robot.wh_per_m + self.robot.idle_w * travel / 3600)
        )
        # A slowdown adds idle energy even if a canonical edge already carried its cruise estimate.
        base_speed = min(
            self.robot.cruise_mps, float(edge.get("speed_limit_mps", self.robot.cruise_mps))
        )
        energy += self.robot.idle_w * max(0, travel - length / base_speed) / 3600
        if not math.isfinite(energy) or energy < 0:
            raise ValueError("INVALID_EDGE_ENERGY")
        if human_mixed:
            semantic += 0.6 * travel
        if self.profile == "esd_safe" and esd_sensitive and not esd_preferred:
            semantic += 3.0 + 0.6 * travel
        risk = risk_level * travel
        result = {
            "travel_time_s": travel,
            "clearance_penalty": clearance_penalty,
            "risk": risk,
            "energy_wh": energy,
            "semantic_penalty": semantic,
            "min_clearance_m": clearance,
        }
        result["cost_s"] = travel + sum(
            self.weights[key] * result[term]
            for key, term in (
                ("clearance", "clearance_penalty"),
                ("risk", "risk"),
                ("energy", "energy_wh"),
                ("semantic", "semantic_penalty"),
            )
        )
        return result, None

    def attach_start(self, pose: dict) -> str:
        exact = [
            n
            for n, p in self.nodes.items()
            if math.hypot(p["x"] - pose["x"], p["y"] - pose["y"]) <= 1e-8
        ]
        if exact:
            return min(exact)
        identifier = "__current_pose__"
        self.nodes[identifier] = {"id": identifier, **pose, "kind": "current_pose"}
        self.adjacency[identifier] = []
        nearest = sorted(
            (math.hypot(p["x"] - pose["x"], p["y"] - pose["y"]), n)
            for n, p in self.nodes.items()
            if n != identifier
        )
        limit = float(self.snapshot.get("route_graph", {}).get("connector_max_m", 2.0))
        for distance, node_id in nearest[:12]:
            if distance > limit:
                continue
            p = self.nodes[node_id]
            coords = [[pose["x"], pose["y"]], [p["x"], p["y"]]]
            clearance = static_clearance(coords, self.snapshot)
            if clearance <= self.robot.radius_m + 1e-9:
                continue
            if not math.isfinite(clearance):
                # Missing metric geometry cannot authorize a free first-mile connector.
                continue
            self._insert(
                {
                    "id": f"__first_mile__:{node_id}",
                    "from": identifier,
                    "to": node_id,
                    "length_m": distance,
                    "width_m": clearance * 2,
                    "min_clearance_m": clearance - self.robot.radius_m,
                    "speed_limit_mps": self.robot.cruise_mps,
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            )
        if not self.adjacency[identifier]:
            raise ValueError("CURRENT_POSE_UNCONNECTED")
        return identifier

    def path(
        self,
        source: str,
        target: str,
        *,
        start_pose: dict | None = None,
        end_pose: dict | None = None,
    ) -> dict | None:
        if source not in self._source_cache:
            distances, previous = {source: 0.0}, {}
            queue = [(0.0, source)]
            while queue:
                cost, node = heapq.heappop(queue)
                if cost > distances[node] + 1e-9:
                    continue
                for edge in self.adjacency[node]:
                    candidate = cost + self.terms[edge["id"]]["cost_s"]
                    nxt = edge["to"]
                    if candidate < distances.get(nxt, math.inf) - 1e-9:
                        distances[nxt], previous[nxt] = candidate, edge["id"]
                        heapq.heappush(queue, (candidate, nxt))
            self._source_cache[source] = distances, previous
        distances, previous = self._source_cache[source]
        if target not in distances:
            return None
        node_ids, edge_ids = [target], []
        node = target
        while node != source:
            edge_id = previous[node]
            edge_ids.append(edge_id)
            node = self.edges[edge_id]["from"]
            node_ids.append(node)
        edge_ids.reverse()
        node_ids.reverse()
        coords = []
        for edge_id in edge_ids:
            part = line_coordinates(self.edges[edge_id], self.nodes)
            coords.extend(part if not coords else part[1:])
        a, b = start_pose or self.nodes[source], end_pose or self.nodes[target]
        if not coords:
            coords = [[a["x"], a["y"]]]
        poses, yaw, turn_count, rotation_s = (
            [{"x": a["x"], "y": a["y"], "yaw": a.get("yaw", 0)}],
            a.get("yaw", 0),
            0,
            0.0,
        )
        for p, q in zip(coords, coords[1:], strict=False):
            if math.dist(p, q) <= 1e-9:
                continue
            heading = math.atan2(q[1] - p[1], q[0] - p[0])
            rotation = abs(math.atan2(math.sin(heading - yaw), math.cos(heading - yaw)))
            if rotation > 1e-6:
                poses.append({"x": p[0], "y": p[1], "yaw": heading})
                turn_count += 1
                rotation_s += rotation / self.robot.angular_speed
            poses.append({"x": q[0], "y": q[1], "yaw": heading})
            yaw = heading
        final_rotation = abs(
            math.atan2(math.sin(b.get("yaw", 0) - yaw), math.cos(b.get("yaw", 0) - yaw))
        )
        if final_rotation > 1e-6:
            poses.append({"x": b["x"], "y": b["y"], "yaw": b.get("yaw", 0)})
            turn_count += 1
            rotation_s += final_rotation / self.robot.angular_speed
        terms = {
            key: sum(self.terms[e][key] for e in edge_ids)
            for key in (
                "travel_time_s",
                "clearance_penalty",
                "risk",
                "energy_wh",
                "semantic_penalty",
                "cost_s",
            )
        }
        rotation_energy = self.robot.idle_w * rotation_s / 3600
        terms["rotation_time_s"] = rotation_s
        terms["travel_time_s"] += rotation_s
        terms["energy_wh"] += rotation_energy
        terms["cost_s"] += rotation_s + self.weights["energy"] * rotation_energy
        clearance = min((self.terms[e]["min_clearance_m"] for e in edge_ids), default=None)
        return {
            "node_ids": node_ids,
            "edge_ids": edge_ids,
            "poses": poses,
            "cost_terms": terms,
            "distance_m": sum(float(self.edges[e].get("length_m", 0)) for e in edge_ids),
            "travel_s": terms["travel_time_s"],
            "energy_wh": terms["energy_wh"],
            "min_clearance_m": clearance,
            "turn_count": turn_count,
        }
