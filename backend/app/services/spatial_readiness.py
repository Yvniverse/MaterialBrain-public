"""Executable adapter boundaries for future mapping and fleet integrations."""

import math
from typing import Protocol

from app.schemas.spatial_readiness import MapDeltaProposal, ObservationFrame


class PerceptionAdapter(Protocol):
    def observe(self, map_id: str, map_revision: str) -> ObservationFrame: ...


class FleetReservationAdapter(Protocol):
    def reserve(self, robot_id: str, edge_ids: list[str], start_s: float, end_s: float) -> dict: ...
    def release(self, reservation_id: str) -> None: ...


class LearnedSemanticCostPlugin(Protocol):
    def estimate(self, edge: dict, observation: ObservationFrame | None) -> float: ...


def bounded_semantic_cost(
    edge: dict, estimate: float, *, legal: bool, maximum: float = 50
) -> float:
    """A learned estimate can add bounded cost but cannot legalize an illegal edge."""
    if not legal:
        return math.inf
    if not math.isfinite(estimate) or estimate < 0 or not math.isfinite(maximum) or maximum < 0:
        raise ValueError("INVALID_LEARNED_COST")
    return min(estimate, maximum)


class MapUpdateValidator:
    def validate(self, proposal: MapDeltaProposal, snapshot: dict) -> dict:
        from app.spatial.geometry import intersects, validate_geometry

        reasons = []
        if proposal.map_id != snapshot["map_id"] or proposal.base_revision != snapshot["revision"]:
            reasons.append("STALE_MAP_REVISION")
        if proposal.confidence < 0.8:
            reasons.append("LOW_CONFIDENCE")
        registered = {node["id"] for node in snapshot["route_graph"]["nodes"]}
        for change in proposal.topology_changes:
            if change.get("from") not in registered or change.get("to") not in registered:
                reasons.append("UNREGISTERED_TOPOLOGY_ENDPOINT")
            elif change.get("from") == change.get("to"):
                reasons.append("SELF_LOOP_TOPOLOGY")
            else:
                nodes = {node["id"]: node for node in snapshot["route_graph"]["nodes"]}
                first, second = nodes[change["from"]], nodes[change["to"]]
                line = {
                    "type": "LineString",
                    "coordinates": [[first["x"], first["y"]], [second["x"], second["y"]]],
                }
                for asset in snapshot.get("geometry", {}).get("assets", []):
                    if asset.get("collidable", True) and intersects(line, asset["footprint"]):
                        reasons.append("TOPOLOGY_CROSSES_OBSTACLE")
                for zone in snapshot.get("zones", []):
                    if zone["kind"] == "keepout" and intersects(line, zone["polygon"]):
                        reasons.append("TOPOLOGY_CROSSES_KEEPOUT")
        for change in proposal.geometry_changes:
            geometry = change.get("geometry") or {}
            if geometry.get("type") not in {"Polygon", "LineString", "Point"}:
                reasons.append("INVALID_GEOMETRY")
                continue
            try:
                validate_geometry(geometry)
            except (TypeError, ValueError):
                reasons.append("INVALID_GEOMETRY_COORDINATES")
        return {
            "valid": not reasons,
            "reasons": sorted(set(reasons)),
            "requires_review": True,
            "automatic_write": False,
            "new_version_required": True,
            "base_revision": snapshot["revision"],
        }
