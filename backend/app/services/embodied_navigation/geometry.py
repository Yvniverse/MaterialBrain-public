"""Metric OBB geometry shared in meaning with the browser planner; standard-library only."""

from __future__ import annotations

import math

EPS = 1e-9


def distance(a: dict, b: dict) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def angle_delta(a: float, b: float) -> float:
    return math.atan2(math.sin(b - a), math.cos(b - a))


def local_point(p: dict, r: dict) -> tuple[float, float]:
    a = math.radians(r.get("yaw_deg", 0))
    c, s = math.cos(a), math.sin(a)
    x, y = p["x"] - r["x"], p["y"] - r["y"]
    return x * c + y * s, -x * s + y * c


def point_rect_distance(p: dict, r: dict) -> float:
    x, y = local_point(p, r)
    return math.hypot(max(abs(x) - r["width"] / 2, 0), max(abs(y) - r["depth"] / 2, 0))


def _point_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = min(1.0, max(0.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length)) if length else 0
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def segment_rect_distance(a: dict, b: dict, r: dict) -> float:
    a, b = local_point(a, r), local_point(b, r)
    w, h = r["width"] / 2, r["depth"] / 2
    lo, hi = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    hit = True
    for p, q in ((-dx, a[0] + w), (dx, w - a[0]), (-dy, a[1] + h), (dy, h - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                hit = False
                break
            continue
        t = q / p
        if p < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            hit = False
            break
    if hit:
        return 0.0
    corners = ((-w, -h), (w, -h), (w, h), (-w, h))
    best = math.inf
    for i, c in enumerate(corners):
        d = corners[(i + 1) % 4]
        best = min(
            best,
            _point_segment(a, c, d),
            _point_segment(b, c, d),
            _point_segment(c, a, b),
            _point_segment(d, a, b),
        )
    return best


def obstacles_for(world: dict, extra: list | None = None) -> list:
    return (
        [a for a in world["assets"] if a.get("collidable", True)]
        + [z for z in world["zones"] if z["kind"] == "keepout"]
        + list(extra or [])
    )


def point_clearance(world: dict, p: dict, obstacles: list) -> float:
    return min(
        p["x"],
        p["y"],
        world["width"] - p["x"],
        world["height"] - p["y"],
        *(point_rect_distance(p, r) for r in obstacles),
    )


def segment_clearance(world: dict, a: dict, b: dict, obstacles: list) -> float:
    return min(
        a["x"],
        a["y"],
        world["width"] - a["x"],
        world["height"] - a["y"],
        b["x"],
        b["y"],
        world["width"] - b["x"],
        world["height"] - b["y"],
        *(segment_rect_distance(a, b, r) for r in obstacles),
    )


def segment_speed(world: dict, a: dict, b: dict, profile: dict) -> float:
    result = profile["max_speed"]
    for z in world["zones"]:
        if z["kind"] == "slow" and segment_rect_distance(a, b, z) < 1e-10:
            result = min(result, z["speed"])
    return result


def validate_world(world: dict) -> None:
    for k in ("width", "height", "resolution"):
        if (
            not isinstance(world.get(k), (int, float))
            or not math.isfinite(world[k])
            or world[k] <= 0
        ):
            raise ValueError("INVALID_WORLD:" + k)
    ids = set()
    for a in world["assets"]:
        if a["id"] in ids:
            raise ValueError("DUPLICATE_ASSET:" + a["id"])
        ids.add(a["id"])
        if not all(
            math.isfinite(a.get(k, math.nan)) for k in ("x", "y", "width", "depth", "height")
        ):
            raise ValueError("INVALID_GEOMETRY")
        if min(a["width"], a["depth"], a["height"]) <= 0:
            raise ValueError("INVALID_DIMENSION")
        angle = math.radians(a.get("yaw_deg", 0))
        c, s = abs(math.cos(angle)), abs(math.sin(angle))
        ex, ey = (c * a["width"] + s * a["depth"]) / 2, (s * a["width"] + c * a["depth"]) / 2
        if (
            a["x"] - ex < 0
            or a["y"] - ey < 0
            or a["x"] + ex > world["width"]
            or a["y"] + ey > world["height"]
        ):
            raise ValueError("ASSET_OUT_OF_MAP:" + a["id"])
    for goal in world["goals"]:
        if goal["asset_id"] not in ids:
            raise ValueError("UNKNOWN_ASSET:" + goal["asset_id"])
