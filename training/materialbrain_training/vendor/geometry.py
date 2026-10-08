"""Small metric GeoJSON predicates; no database or optional geometry dependency.

These conservative connector checks are not a low-level motion controller. Graph
edges remain the route source of truth; only the current pose gets a first mile.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

EPS = 1e-9


def geometry(value: dict | None) -> dict | None:
    if not value:
        return None
    if value.get("type") == "Feature":
        return geometry(value.get("geometry"))
    if value.get("type") in {"Polygon", "MultiPolygon", "LineString", "Point"}:
        return value
    return geometry(value.get("geometry") or value.get("polygon") or value.get("footprint"))


def point_segment_distance(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = dx * dx + dy * dy
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d)) if d else 0
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a, b, c, d) -> bool:
    if max(a[0], b[0]) < min(c[0], d[0]) - EPS:
        return False
    if max(c[0], d[0]) < min(a[0], b[0]) - EPS:
        return False
    if max(a[1], b[1]) < min(c[1], d[1]) - EPS:
        return False
    if max(c[1], d[1]) < min(a[1], b[1]) - EPS:
        return False
    return _cross(a, b, c) * _cross(a, b, d) <= EPS and _cross(c, d, a) * _cross(c, d, b) <= EPS


def segment_segment_distance(a, b, c, d) -> float:
    if segments_intersect(a, b, c, d):
        return 0.0
    return min(
        point_segment_distance(a, c, d),
        point_segment_distance(b, c, d),
        point_segment_distance(c, a, b),
        point_segment_distance(d, a, b),
    )


def _ring_contains(p, ring) -> bool:
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1], strict=False):
        if point_segment_distance(p, a, b) < EPS:
            return True
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if p[0] < x:
                inside = not inside
    return inside


def contains(p, value: dict) -> bool:
    g = geometry(value)
    if not g:
        return False
    if g["type"] == "MultiPolygon":
        return any(contains(p, {"type": "Polygon", "coordinates": c}) for c in g["coordinates"])
    if g["type"] != "Polygon":
        return False
    rings = g["coordinates"]
    return _ring_contains(p, rings[0]) and not any(_ring_contains(p, r) for r in rings[1:])


def boundary_segments(value: dict):
    g = geometry(value)
    if not g:
        return
    if g["type"] == "MultiPolygon":
        for c in g["coordinates"]:
            yield from boundary_segments({"type": "Polygon", "coordinates": c})
    elif g["type"] == "Polygon":
        for ring in g["coordinates"]:
            yield from zip(ring, ring[1:] + ring[:1], strict=False)
    elif g["type"] == "LineString":
        yield from zip(g["coordinates"], g["coordinates"][1:], strict=False)


def segment_geometry_distance(a, b, value: dict) -> float:
    g = geometry(value)
    if not g:
        return math.inf
    if contains(a, g) or contains(b, g):
        return 0.0
    if g["type"] == "Point":
        return point_segment_distance(g["coordinates"], a, b)
    return min(
        (segment_segment_distance(a, b, c, d) for c, d in boundary_segments(g)),
        default=math.inf,
    )


def line_coordinates(edge: dict, nodes: dict) -> list:
    g = geometry(edge.get("geometry"))
    if g and g["type"] == "LineString":
        return g["coordinates"]
    a, b = nodes[edge["from"]], nodes[edge["to"]]
    return [[a["x"], a["y"]], [b["x"], b["y"]]]


def line_distance(coords: list, value: dict) -> float:
    return min(
        (segment_geometry_distance(a, b, value) for a, b in zip(coords, coords[1:], strict=False)),
        default=segment_geometry_distance(coords[0], coords[0], value),
    )


def parse_time(value: str | datetime) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def active_overlays(overlays: list, now: datetime) -> tuple[list, list]:
    """Reject malformed expiry metadata rather than silently dropping a closure."""
    active, expired = [], []
    for overlay in overlays:
        deadlines = []
        if overlay.get("expires_at"):
            deadlines.append(parse_time(overlay["expires_at"]))
        if overlay.get("ttl_s") is not None:
            ttl = float(overlay["ttl_s"])
            if not math.isfinite(ttl) or ttl < 0 or not overlay.get("created_at"):
                raise ValueError("INVALID_OVERLAY_TTL")
            deadlines.append(parse_time(overlay["created_at"]) + timedelta(seconds=ttl))
        if deadlines and min(deadlines) <= now:
            expired.append(overlay.get("id", "unnamed"))
        else:
            active.append(overlay)
    return active, expired


def static_clearance(coords: list, snapshot: dict) -> float:
    """Minimum centreline clearance, including walls and the floor boundary."""
    data = snapshot.get("geometry", {})
    floor = geometry(data.get("floor"))
    distances = []
    if floor:
        if not all(contains(p, floor) for p in coords):
            return 0.0
        for a, b in zip(coords, coords[1:], strict=False):
            distances.extend(
                segment_segment_distance(a, b, c, d) for c, d in boundary_segments(floor)
            )
    for collection in ("assets", "walls", "columns"):
        for item in data.get(collection, []):
            if item.get("collidable", True):
                distances.append(line_distance(coords, item))
    return min(distances, default=math.inf)
