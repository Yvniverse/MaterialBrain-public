"""Small, finite local-metre GeoJSON helpers (no fabricated EPSG reference)."""

from __future__ import annotations

import math

EPS = 1e-9


def rectangle_polygon(rect: dict) -> dict:
    angle = math.radians(rect.get("yaw_deg", 0))
    c, s = math.cos(angle), math.sin(angle)
    w, h = rect["width"] / 2, rect["depth"] / 2
    corners = [
        [round(rect["x"] + x * c - y * s, 9), round(rect["y"] + x * s + y * c, 9)]
        for x, y in ((-w, -h), (w, -h), (w, h), (-w, h))
    ]
    return {"type": "Polygon", "coordinates": [[*corners, corners[0][:]]]}


def point_geometry(pose: dict) -> dict:
    return {"type": "Point", "coordinates": [pose["x"], pose["y"]]}


def line_geometry(first: dict, second: dict) -> dict:
    return {
        "type": "LineString",
        "coordinates": [[first["x"], first["y"]], [second["x"], second["y"]]],
    }


def _point_segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    squared = dx * dx + dy * dy
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / squared)) if squared else 0
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def covers_point(geometry: dict, point: tuple | list) -> bool:
    kind, coordinates = geometry["type"], geometry["coordinates"]
    if kind == "Point":
        return math.dist(coordinates, point) <= EPS
    if kind == "LineString":
        return any(
            _point_segment_distance(point, a, b) <= EPS
            for a, b in zip(coordinates, coordinates[1:], strict=False)
        )

    def ring_contains(ring):
        if any(
            _point_segment_distance(point, a, b) <= EPS
            for a, b in zip(ring, ring[1:], strict=False)
        ):
            return 2  # boundary is covered, including a hole boundary
        inside = False
        for a, b in zip(ring, ring[1:], strict=False):
            if (a[1] > point[1]) != (b[1] > point[1]):
                cross_x = a[0] + (point[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                if point[0] < cross_x:
                    inside = not inside
        return int(inside)

    outer = ring_contains(coordinates[0])
    return bool(outer) and not any(ring_contains(hole) == 1 for hole in coordinates[1:])


def geometry_segments(geometry: dict) -> list:
    if geometry["type"] == "Point":
        return []
    rings = (
        [geometry["coordinates"]] if geometry["type"] == "LineString" else geometry["coordinates"]
    )
    return [(a, b) for ring in rings for a, b in zip(ring, ring[1:], strict=False)]


def _segments_intersect(a, b, c, d):
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    if (
        min(a[0], b[0]) > max(c[0], d[0]) + EPS
        or min(c[0], d[0]) > max(a[0], b[0]) + EPS
        or min(a[1], b[1]) > max(c[1], d[1]) + EPS
        or min(c[1], d[1]) > max(a[1], b[1]) + EPS
    ):
        return False
    return cross(a, b, c) * cross(a, b, d) <= EPS and cross(c, d, a) * cross(c, d, b) <= EPS


def geometry_vertices(geometry: dict) -> list:
    if geometry["type"] == "Point":
        return [geometry["coordinates"]]
    if geometry["type"] == "LineString":
        return geometry["coordinates"]
    return [point for ring in geometry["coordinates"] for point in ring]


def intersects(first: dict, second: dict) -> bool:
    if any(covers_point(first, p) for p in geometry_vertices(second)):
        return True
    if any(covers_point(second, p) for p in geometry_vertices(first)):
        return True
    return any(
        _segments_intersect(a, b, c, d)
        for a, b in geometry_segments(first)
        for c, d in geometry_segments(second)
    )


def geometry_distance(first: dict, second: dict) -> float:
    if intersects(first, second):
        return 0.0
    distances = [
        math.dist(a, b) for a in geometry_vertices(first) for b in geometry_vertices(second)
    ]
    distances.extend(
        _point_segment_distance(p, a, b)
        for points, segments in (
            (geometry_vertices(first), geometry_segments(second)),
            (geometry_vertices(second), geometry_segments(first)),
        )
        for p in points
        for a, b in segments
    )
    return min(distances)


def validate_geometry(value: dict, *, polygon_only: bool = False) -> dict:
    """Reject malformed/nonfinite/huge geometry before SQL or overlay mutation."""
    if not isinstance(value, dict) or set(value) != {"type", "coordinates"}:
        raise ValueError("INVALID_LOCAL_GEOMETRY")
    kind = value["type"]
    if kind not in (("Polygon",) if polygon_only else ("Point", "LineString", "Polygon")):
        raise ValueError("INVALID_LOCAL_GEOMETRY")
    coords = value["coordinates"]
    if not isinstance(coords, list):
        raise ValueError("INVALID_LOCAL_GEOMETRY")
    if kind == "Point":
        sequences = [[coords]]
    elif kind == "LineString":
        if len(coords) < 2:
            raise ValueError("INVALID_LOCAL_GEOMETRY")
        sequences = [coords]
    else:
        if not coords or any(not isinstance(r, list) or len(r) < 4 for r in coords):
            raise ValueError("INVALID_LOCAL_GEOMETRY")
        sequences = coords
    if sum(len(r) for r in sequences) > 2048:
        raise ValueError("LOCAL_GEOMETRY_TOO_LARGE")
    for ring in sequences:
        for point in ring:
            if (
                not isinstance(point, list)
                or len(point) != 2
                or any(
                    isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n)
                    for n in point
                )
            ):
                raise ValueError("INVALID_LOCAL_GEOMETRY")
        if kind != "Point" and any(a == b for a, b in zip(ring, ring[1:], strict=False)):
            raise ValueError("DEGENERATE_LOCAL_GEOMETRY")
        if kind == "Polygon":
            if ring[0] != ring[-1] or len({tuple(p) for p in ring[:-1]}) < 3:
                raise ValueError("INVALID_LOCAL_POLYGON")
            segments = list(zip(ring, ring[1:], strict=False))
            for i, (a, b) in enumerate(segments):
                for j, (c, d) in enumerate(segments):
                    if j <= i + 1 or (i == 0 and j == len(segments) - 1):
                        continue
                    if _segments_intersect(a, b, c, d):
                        raise ValueError("SELF_INTERSECTING_LOCAL_POLYGON")
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in segments) / 2
            if abs(area) <= EPS:
                raise ValueError("DEGENERATE_LOCAL_GEOMETRY")
    if kind == "Polygon":
        for hole in coords[1:]:
            if not all(
                covers_point({"type": "Polygon", "coordinates": [coords[0]]}, p) for p in hole
            ):
                raise ValueError("INVALID_LOCAL_POLYGON_HOLE")
    return value
