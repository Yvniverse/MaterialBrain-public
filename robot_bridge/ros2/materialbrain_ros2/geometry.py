"""Canonical metric OBB geometry used by raster, scanner and collision oracle."""

from __future__ import annotations

import json
import math
from pathlib import Path


def load_world(path: str | Path) -> dict:
    world = json.loads(Path(path).read_text(encoding="utf-8"))
    if world["id"] != "MB-EMB-LAB-03" or world["units"] != "m":
        raise ValueError("only the registered canonical metric laboratory is supported")
    return world


def corners(rect: dict, margin: float = 0.0) -> list[tuple[float, float]]:
    yaw = math.radians(rect.get("yaw_deg", 0))
    c, s = math.cos(yaw), math.sin(yaw)
    w, h = rect["width"] / 2 + margin, rect["depth"] / 2 + margin
    return [
        (rect["x"] + x * c - y * s, rect["y"] + x * s + y * c)
        for x, y in ((-w, -h), (w, -h), (w, h), (-w, h))
    ]


def point_rect_distance(x: float, y: float, rect: dict) -> float:
    a = math.radians(rect.get("yaw_deg", 0))
    c, s = math.cos(a), math.sin(a)
    dx, dy = x - rect["x"], y - rect["y"]
    return math.hypot(
        max(abs(dx * c + dy * s) - rect["width"] / 2, 0),
        max(abs(-dx * s + dy * c) - rect["depth"] / 2, 0),
    )


def polygons_overlap(a: list, b: list) -> bool:
    for poly in (a, b):
        for i, p in enumerate(poly):
            q = poly[(i + 1) % len(poly)]
            axis = (-(q[1] - p[1]), q[0] - p[0])
            ap = [v[0] * axis[0] + v[1] * axis[1] for v in a]
            bp = [v[0] * axis[0] + v[1] * axis[1] for v in b]
            if max(ap) < min(bp) - 1e-10 or max(bp) < min(ap) - 1e-10:
                return False
    return True


def point_segment_distance(p: tuple, a: tuple, b: tuple) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length)) if length else 0
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def polygon_clearance(a: list, b: list) -> float:
    if polygons_overlap(a, b):
        return 0.0
    return min(
        point_segment_distance(p, other[j], other[(j + 1) % len(other)])
        for source, other in ((a, b), (b, a))
        for p in source
        for j in range(len(other))
    )


def static_obstacles(world: dict) -> list[dict]:
    return [a for a in world["assets"] if a.get("collidable", True)] + [
        z for z in world["zones"] if z["kind"] == "keepout"
    ]


def footprint(world: dict, pose: dict, safety_margin: bool = False) -> list:
    robot = world["robot"]
    return corners(
        {
            "x": pose["x"],
            "y": pose["y"],
            "width": robot["length"],
            "depth": robot["width"],
            "yaw_deg": math.degrees(pose["yaw"]),
        },
        robot["margin"] if safety_margin else 0,
    )


def clearance(world: dict, pose: dict, obstacles: list, safety_margin: bool = False) -> float:
    body = footprint(world, pose, safety_margin)
    boundary = min(min(x, y, world["width"] - x, world["height"] - y) for x, y in body)
    if boundary <= 0:
        return 0.0
    return min([boundary] + [polygon_clearance(body, corners(r)) for r in obstacles])


def ray_ranges(world: dict, pose: dict, obstacles: list, beams: int = 360, max_range: float = 12.0):
    """Analytic OBB ray intersection; no fabricated ranges or random sensor noise."""
    import numpy as np

    angles = np.linspace(-math.pi, math.pi, beams, endpoint=False) + pose["yaw"]
    dx, dy = np.cos(angles), np.sin(angles)
    output = np.full(beams, max_range, dtype=np.float64)
    for axis, limit in (
        (dx, world["width"] - pose["x"]),
        (-dx, pose["x"]),
        (dy, world["height"] - pose["y"]),
        (-dy, pose["y"]),
    ):
        np.minimum(
            output, np.where(axis > 1e-10, limit / np.maximum(axis, 1e-10), max_range), out=output
        )
    for r in obstacles:
        a = math.radians(r.get("yaw_deg", 0))
        c, s = math.cos(a), math.sin(a)
        ox, oy = pose["x"] - r["x"], pose["y"] - r["y"]
        ox, oy = ox * c + oy * s, -ox * s + oy * c
        rx, ry = dx * c + dy * s, -dx * s + dy * c
        lower = np.full(beams, -np.inf)
        upper = np.full(beams, np.inf)
        valid = np.ones(beams, dtype=bool)
        for origin, direction, half in ((ox, rx, r["width"] / 2), (oy, ry, r["depth"] / 2)):
            parallel = np.abs(direction) < 1e-10
            valid &= ~(parallel & (abs(origin) > half))
            denom = np.where(parallel, 1.0, direction)
            t1, t2 = (-half - origin) / denom, (half - origin) / denom
            lower = np.maximum(lower, np.where(parallel, -np.inf, np.minimum(t1, t2)))
            upper = np.minimum(upper, np.where(parallel, np.inf, np.maximum(t1, t2)))
        hit = valid & (upper >= np.maximum(lower, 0))
        output = np.minimum(output, np.where(hit, np.maximum(lower, 0), max_range))
    return output.tolist()
