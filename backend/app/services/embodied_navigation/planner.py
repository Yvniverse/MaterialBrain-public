"""Heading-grid A* + directed multi-stop ordering for a stop/turn differential base.

This is not Hybrid-A*, D* Lite, MPPI, certified collision avoidance, or a grasp planner.
Circumscribed-disc inflation is conservative for a rectangular robot at every yaw.
All returned driving segments are checked against rotated asset footprints.
"""

from __future__ import annotations

import copy
import hashlib
import heapq
import json
import math
import time
from pathlib import Path
from threading import RLock

from .geometry import (
    angle_delta,
    distance,
    obstacles_for,
    point_clearance,
    segment_clearance,
    segment_speed,
    validate_world,
)

DIRS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
STEP = math.pi / 4


def heading(yaw: float) -> int:
    return math.floor(yaw / STEP + 0.5) % 8


def canonical_hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def load_world() -> dict:
    world = json.loads(Path(__file__).with_name("world.v3.json").read_text(encoding="utf-8"))
    actual = canonical_hash({k: v for k, v in world.items() if k != "revision_sha256"})
    if actual != world["revision_sha256"]:
        raise ValueError("WORLD_DIGEST_MISMATCH")
    validate_world(world)
    return world


class MetricPlanner:
    def __init__(
        self,
        world: dict,
        *,
        profile: dict | None = None,
        obstacles: list | None = None,
        clearance_weight: float = 0.12,
    ):
        validate_world(world)
        self.world = copy.deepcopy(world)
        self.profile = copy.deepcopy(profile or world["robot"])
        self.extra = copy.deepcopy(obstacles or [])
        for k in ("width", "length", "max_speed", "angular_speed", "battery_wh"):
            if not math.isfinite(self.profile.get(k, math.nan)) or self.profile[k] <= 0:
                raise ValueError("INVALID_PROFILE:" + k)
        if not math.isfinite(self.profile.get("margin", math.nan)) or self.profile["margin"] < 0:
            raise ValueError("INVALID_PROFILE:margin")
        if not math.isfinite(clearance_weight) or clearance_weight < 0:
            raise ValueError("INVALID_CLEARANCE_WEIGHT")
        self.weight = clearance_weight
        self.obstacles = obstacles_for(world, self.extra)
        self.radius = math.hypot(self.profile["length"], self.profile["width"]) / 2
        self.r = self.radius + self.profile["margin"]
        self.res = world["resolution"]
        self.nx = math.floor(world["width"] / self.res) + 1
        self.ny = math.floor(world["height"] / self.res) + 1
        self.n = self.nx * self.ny
        if self.n > 250000:
            raise ValueError("GRID_TOO_LARGE")
        self.revision = (
            world["revision_sha256"]
            + ":"
            + canonical_hash(
                {"profile": self.profile, "obstacles": self.extra, "clearance_weight": self.weight}
            )[:16]
        )
        self.clear = [point_clearance(world, self.point(i), self.obstacles) for i in range(self.n)]
        self.mask = bytearray(self.n)
        self._edges = {}
        self._paths = {}
        self.lock = RLock()
        for i in range(self.n):
            if self.clear[i] <= self.r + 1e-9:
                continue
            a = self.point(i)
            ix, iy = i % self.nx, i // self.nx
            for h, (dx, dy) in enumerate(DIRS):
                x, y = ix + dx, iy + dy
                if x < 0 or y < 0 or x >= self.nx or y >= self.ny:
                    continue
                j = y * self.nx + x
                if self.clear[j] <= self.r + 1e-9:
                    continue
                if (
                    dx
                    and dy
                    and (self.clear[i + dx] <= self.r or self.clear[i + dy * self.nx] <= self.r)
                ):
                    continue
                b = self.point(j)
                d = distance(a, b)
                c = min(self.clear[i], self.clear[j]) - d
                if c <= self.r:
                    c = segment_clearance(world, a, b, self.obstacles)
                if c > self.r + 1e-9:
                    self.mask[i] |= 1 << h

    def point(self, i: int, h: int = 0) -> dict:
        return {"x": i % self.nx * self.res, "y": i // self.nx * self.res, "yaw": h * STEP}

    def connection(self, p: dict) -> int:
        if not all(
            isinstance(p.get(k, 0), (int, float)) and math.isfinite(p.get(k, 0))
            for k in ("x", "y", "yaw")
        ):
            raise ValueError("INVALID_POSE")
        if "x" not in p or "y" not in p:
            raise ValueError("INVALID_POSE")
        if point_clearance(self.world, p, self.obstacles) <= self.r + 1e-9:
            raise ValueError("POSE_BLOCKED")
        cx, cy = math.floor(p["x"] / self.res + 0.5), math.floor(p["y"] / self.res + 0.5)
        found = []
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                x, y = cx + dx, cy + dy
                if x < 0 or y < 0 or x >= self.nx or y >= self.ny:
                    continue
                i = y * self.nx + x
                q = self.point(i)
                if (
                    self.clear[i] > self.r
                    and segment_clearance(self.world, p, q, self.obstacles) > self.r + 1e-9
                ):
                    found.append((distance(p, q), i))
        if not found:
            raise ValueError("POSE_UNCONNECTED")
        return min(found)[1]

    def edge(self, i: int, h: int):
        key = i * 8 + h
        if key not in self._edges:
            dx, dy = DIRS[h]
            j = i + dy * self.nx + dx
            a, b = self.point(i), self.point(j)
            d = distance(a, b)
            c = segment_clearance(self.world, a, b, self.obstacles)
            t = d / segment_speed(self.world, a, b, self.profile)
            self._edges[key] = (j, t + self.weight * d * math.exp(-max(0, c - self.r) / 0.65))
        return self._edges[key]

    def connector(self, a: dict, b: dict) -> list:
        yaw = math.atan2(b["y"] - a["y"], b["x"] - a["x"])
        points = [{**a, "yaw": a.get("yaw", 0)}]
        if abs(angle_delta(a.get("yaw", 0), yaw)) > 1e-8:
            points.append({**a, "yaw": yaw})
        points.append({**b, "yaw": yaw})
        if abs(angle_delta(yaw, b.get("yaw", 0))) > 1e-8:
            points.append({**b, "yaw": b.get("yaw", 0)})
        return points

    def path(
        self,
        start: dict,
        goal: dict,
        *,
        max_expansions: int = 500000,
        heuristic_enabled: bool = True,
    ) -> dict:
        key = json.dumps([start, goal, heuristic_enabled], sort_keys=True)
        if key in self._paths:
            return copy.deepcopy(self._paths[key])
        si, gi = self.connection(start), self.connection(goal)
        sh, gh = heading(start.get("yaw", 0)), heading(goal.get("yaw", 0))
        s, target = si * 8 + sh, gi * 8 + gh
        dist = [math.inf] * (self.n * 8)
        dist[s] = 0.0
        parent = [-1] * (self.n * 8)
        queue = []

        def heuristic(i):
            return (
                distance(self.point(i), self.point(gi)) / self.profile["max_speed"]
                if heuristic_enabled
                else 0.0
            )

        heapq.heappush(queue, (heuristic(si), 0.0, s))
        expanded = 0
        while queue:
            _, cost, state = heapq.heappop(queue)
            if cost > dist[state] + 1e-9:
                continue
            if state == target:
                break
            expanded += 1
            if expanded > max_expansions:
                raise ValueError("SEARCH_BUDGET_EXCEEDED")
            i, h = state // 8, state % 8
            successors = [
                (i * 8 + (h + 7) % 8, STEP / self.profile["angular_speed"]),
                (i * 8 + (h + 1) % 8, STEP / self.profile["angular_speed"]),
            ]
            if self.mask[i] & (1 << h):
                j, c = self.edge(i, h)
                successors.append((j * 8 + h, c))
            for nxt, c in successors:
                nc = cost + c
                if nc + 1e-9 < dist[nxt]:
                    dist[nxt] = nc
                    parent[nxt] = state
                    heapq.heappush(queue, (nc + heuristic(nxt // 8), nc, nxt))
        if not math.isfinite(dist[target]):
            raise ValueError("NO_PATH")
        states = []
        state = target
        while state != -1:
            states.append(state)
            state = parent[state]
        points = [self.point(s // 8, s % 8) for s in reversed(states)]
        if distance(start, points[0]) > 1e-8:
            points = self.connector(start, points[0]) + points[1:]
        elif abs(angle_delta(start.get("yaw", 0), points[0]["yaw"])) > 1e-8:
            points.insert(0, {**start, "yaw": start.get("yaw", 0)})
        if distance(goal, points[-1]) > 1e-8:
            points += self.connector(points[-1], goal)[1:]
        elif abs(angle_delta(points[-1]["yaw"], goal.get("yaw", 0))) > 1e-8:
            points.append({**goal, "yaw": goal.get("yaw", 0)})
        metres = motion = total = 0.0
        clearance = math.inf
        actions = []
        for a, b in zip(points, points[1:], strict=False):
            d = distance(a, b)
            c = segment_clearance(self.world, a, b, self.obstacles)
            if c <= self.r + 1e-9:
                raise ValueError("COLLISION_IN_PATH")
            clearance = min(clearance, c - self.radius)
            t = (
                d / segment_speed(self.world, a, b, self.profile)
                if d > 1e-9
                else abs(angle_delta(a["yaw"], b["yaw"])) / self.profile["angular_speed"]
            )
            penalty = self.weight * d * math.exp(-max(0, c - self.r) / 0.65) if d > 1e-9 else 0.0
            metres += d
            motion += t
            total += t + penalty
            actions.append(
                {
                    "type": "drive" if d > 1e-9 else "rotate",
                    "from": a,
                    "to": b,
                    "distance_m": d,
                    "duration_s": t,
                }
            )
        if not math.isfinite(clearance):
            clearance = point_clearance(self.world, start, self.obstacles) - self.radius
        result = {
            "poses": points,
            "actions": actions,
            "distance_m": metres,
            "motion_s": motion,
            "cost_s": total,
            "min_clearance_m": clearance,
            "expanded": expanded,
            "revision": self.revision,
        }
        if len(self._paths) > 4096:
            self._paths.clear()
        self._paths[key] = result
        return copy.deepcopy(result)

    def plan(
        self,
        goal_ids: list[str],
        *,
        start: dict | None = None,
        end: dict | None = None,
        optimize: bool = True,
        battery_pct: float | None = None,
        payload_kg: float = 0.0,
    ) -> dict:
        begin = time.perf_counter()
        ids = list(dict.fromkeys(goal_ids))
        if len(ids) > 14:
            raise ValueError("TOO_MANY_GOALS")
        indexed = {g["id"]: g for g in self.world["goals"]}
        if any(g not in indexed for g in ids):
            raise ValueError("UNKNOWN_GOAL")
        goals = [indexed[g] for g in ids]
        start = start or self.world["home"]
        end = end or self.world["home"]
        unsupported = [
            g["id"]
            for g in goals
            if not set(g.get("require_capabilities", [])) <= set(self.profile["capabilities"])
        ]
        if unsupported:
            return {
                "status": "CAPABILITY_MISMATCH",
                "goals": unsupported,
                "revision": self.revision,
            }
        battery_pct = self.profile["battery_pct"] if battery_pct is None else battery_pct
        if (
            not math.isfinite(battery_pct)
            or not 0 <= battery_pct <= 100
            or not math.isfinite(payload_kg)
            or payload_kg < 0
        ):
            raise ValueError("INVALID_RESOURCE_STATE")
        payload = payload_kg + sum(g["payload_kg"] for g in goals)
        if payload > self.profile["payload_kg"]:
            return {
                "status": "SPLIT_REQUIRED",
                "payload_kg": payload,
                "capacity_kg": self.profile["payload_kg"],
                "batches": split_batches(goals, self.profile["payload_kg"] - payload_kg),
                "revision": self.revision,
            }
        nodes = [{"id": "__start", "pose": start}] + goals + [{"id": "__end", "pose": end}]
        n = len(goals)
        paths = [[None] * (n + 2) for _ in range(n + 2)]
        try:
            for i in range(n + 1):
                for j in range(1, n + 2):
                    if i != j:
                        paths[i][j] = self.path(nodes[i]["pose"], nodes[j]["pose"])
        except ValueError as exc:
            return {
                "status": "BLOCKED",
                "reason": str(exc),
                "revision": self.revision,
                "goal_ids": ids,
            }

        def cost(order):
            return sum(
                paths[a][b]["cost_s"] for a, b in zip([0] + order, order + [n + 1], strict=False)
            )

        baseline = list(range(1, n + 1))
        order = list(baseline)
        method = "input_order"
        if optimize and n > 1:
            if n <= 8:
                order = held_karp(n, paths)
                method = "held_karp_exact"
            else:
                order = nearest_two_opt(n, paths, cost)
                method = "nearest_2opt"
        segments = [
            {"from_id": nodes[a]["id"], "to_id": nodes[b]["id"], **paths[a][b]}
            for a, b in zip([0] + order, order + [n + 1], strict=False)
        ]
        totals = {
            key: sum(s[key] for s in segments)
            for key in ("distance_m", "motion_s", "cost_s", "expanded")
        }
        totals["min_clearance_m"] = min(s["min_clearance_m"] for s in segments)
        service = sum(g["service_s"] for g in goals)
        eta = totals["motion_s"] + service
        energy = (
            totals["distance_m"] * self.profile["wh_per_m"] + self.profile["idle_w"] * eta / 3600
        )
        after = battery_pct - 100 * energy / self.profile["battery_wh"]
        status = "NEEDS_CHARGE" if after < self.profile["reserve_pct"] else "READY"
        basecost = cost(baseline)
        return {
            "status": status,
            "world_id": self.world["id"],
            "revision": self.revision,
            "planner": "heading_grid_astar",
            "footprint_model": "circumscribed_disc",
            "order_method": method,
            "goal_ids": [nodes[i]["id"] for i in order],
            "requested_goal_ids": ids,
            "segments": segments,
            **totals,
            "service_s": service,
            "eta_s": eta,
            "energy_wh": energy,
            "battery_after_pct": after,
            "payload_kg": payload,
            "baseline": {
                "cost_s": basecost,
                "distance_m": sum(
                    paths[a][b]["distance_m"]
                    for a, b in zip([0] + baseline, baseline + [n + 1], strict=False)
                ),
            },
            "improvement_pct": (basecost - totals["cost_s"]) / basecost * 100 if basecost else 0.0,
            "cpu_ms": (time.perf_counter() - begin) * 1000,
            "calibration_status": self.world["provenance"],
            "reason": "低于返航电量阈值，先充电再执行" if status == "NEEDS_CHARGE" else None,
        }


def held_karp(n: int, paths: list) -> list[int]:
    dp = {(1 << (j - 1), j): (paths[0][j]["cost_s"], [j]) for j in range(1, n + 1)}
    for mask in range(1, 1 << n):
        for j in range(1, n + 1):
            if (mask, j) not in dp:
                continue
            value, order = dp[mask, j]
            for k in range(1, n + 1):
                if mask & (1 << (k - 1)):
                    continue
                key = (mask | (1 << (k - 1)), k)
                c = value + paths[j][k]["cost_s"]
                if key not in dp or c < dp[key][0] - 1e-9:
                    dp[key] = (c, order + [k])
    return min(
        (dp[(1 << n) - 1, j][0] + paths[j][n + 1]["cost_s"], dp[(1 << n) - 1, j][1])
        for j in range(1, n + 1)
    )[1]


def nearest_two_opt(n, paths, cost):
    remaining = set(range(1, n + 1))
    order = []
    previous = 0
    while remaining:
        nxt = min(remaining, key=lambda j: (paths[previous][j]["cost_s"], j))
        order.append(nxt)
        remaining.remove(nxt)
        previous = nxt
    best = cost(order)
    for _ in range(20):
        changed = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                candidate = order[:i] + list(reversed(order[i : j + 1])) + order[j + 1 :]
                c = cost(candidate)
                if c < best - 1e-9:
                    order = candidate
                    best = c
                    changed = True
        if not changed:
            break
    return order


def split_batches(goals: list, capacity: float):
    if capacity <= 0 or any(g["payload_kg"] > capacity for g in goals):
        return None
    batches = []
    for g in sorted(goals, key=lambda g: (-g["payload_kg"], g["id"])):
        b = next((b for b in batches if b["weight"] + g["payload_kg"] <= capacity), None)
        if b is None:
            b = {"ids": [], "weight": 0.0}
            batches.append(b)
        b["ids"].append(g["id"])
        b["weight"] += g["payload_kg"]
    return batches
