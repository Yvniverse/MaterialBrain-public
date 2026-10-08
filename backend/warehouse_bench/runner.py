"""Execute actual planners against seeded canonical scenarios and persist evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from app.schemas.spatial import MissionRequest
from app.services.embodied_navigation.planner import MetricPlanner, load_world
from app.services.spatial_mission import plan_mission
from app.services.spatial_mission.geometry import active_overlays, geometry, line_coordinates

from .catalog import scenario_catalog
from .metrics import summarize
from .verify import audit_route, graph_distance_bound

ALGORITHMS = ("heading_grid_astar", "semantic_graph_ortools")


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _recovery_request(snapshot, request):
    initial = plan_mission(snapshot, request)
    if initial["status"] != "READY":
        raise ValueError("BENCH_RECOVERY_INITIAL_PLAN_INFEASIBLE")
    completed = next(s for s in initial["stops"] if s["kind"] == "task")
    used = set(e for s in initial["segments"][1:] for e in s["edge_ids"])
    edges = {e["id"]: e for e in snapshot["route_graph"]["edges"]}
    candidates = sorted(e for e in used if "Z-HUMAN-CROSSING" in edges[e].get("zone_ids", []))
    selected = edges[
        candidates[len(candidates) // 2] if candidates else sorted(used)[len(used) // 2]
    ]
    nodes = {n["id"]: n for n in snapshot["route_graph"]["nodes"]}
    coords = line_coordinates(selected, nodes)
    x, y = (coords[0][0] + coords[-1][0]) / 2, (coords[0][1] + coords[-1][1]) / 2
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [
                [x - 0.25, y - 0.25],
                [x + 0.25, y - 0.25],
                [x + 0.25, y + 0.25],
                [x - 0.25, y + 0.25],
                [x - 0.25, y - 0.25],
            ]
        ],
    }
    blocked = [
        e["id"]
        for e in edges.values()
        if {e["from"], e["to"]} == {selected["from"], selected["to"]}
    ]
    return {
        **copy.deepcopy(request),
        "start_pose": copy.deepcopy(completed["pose"]),
        "completed_goal_ids": [completed["goal_id"]],
        "constraints": {**request["constraints"], "battery_pct": completed["battery_arrival_pct"]},
        "dynamic_overlays": [
            {
                "id": "BENCH-BLOCKED-AISLE",
                "kind": "closure",
                "geometry": polygon,
                "edge_ids": blocked,
                "source": "WarehouseBench seeded scenario",
                "reason": "central aisle obstacle after first completed handoff",
                "created_at": "2026-10-04T12:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        ],
    }


def _v3_obstacles(request):
    active, _ = active_overlays(request.get("dynamic_overlays", []), datetime.now(UTC))
    rectangles = []
    for overlay in active:
        g = geometry(overlay)
        if not g:
            if overlay.get("edge_ids"):
                raise ValueError("V3_BASELINE_CANNOT_REPRESENT_EDGE_ONLY_CLOSURE")
            continue
        if g["type"] != "Polygon":
            raise ValueError("V3_BASELINE_REQUIRES_POLYGON_OBSTACLE")
        points = [p for ring in g["coordinates"] for p in ring]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        rectangles.append(
            {
                "id": overlay["id"],
                "x": (min(xs) + max(xs)) / 2,
                "y": (min(ys) + max(ys)) / 2,
                "width": max(xs) - min(xs),
                "depth": max(ys) - min(ys),
                "yaw_deg": 0,
            }
        )
    return rectangles


def _baseline_plan(snapshot, request, cache):
    world = load_world()
    constraints = request["constraints"]
    profile = {
        **world["robot"],
        "payload_kg": constraints["payload_capacity_kg"],
        "reserve_pct": constraints["battery_reserve_pct"],
    }
    obstacles = _v3_obstacles(request)
    key = _hash({"profile": profile, "obstacles": obstacles})
    if key not in cache:
        cache[key] = MetricPlanner(world, profile=profile, obstacles=obstacles)
    planner = cache[key]
    indexed = {g["id"]: g for g in world["goals"]}
    completed = request.get("completed_goal_ids", [])
    selected = [g for g in request["goal_ids"] if g not in completed]
    result = planner.plan(
        selected,
        start=request["start_pose"],
        payload_kg=sum(indexed[g]["payload_kg"] for g in completed),
        battery_pct=constraints["battery_pct"],
    )
    ready = result["status"] == "READY"
    plan = {
        "status": "READY" if ready else "INFEASIBLE",
        "native_status": result["status"],
        "ordered_goal_ids": result.get("goal_ids", []),
        "completed_goal_ids": completed,
        "segments": [],
        "stops": [],
        "metrics": {
            **result,
            "travel_s": result.get("motion_s"),
            "mission_completion_time_s": result.get("eta_s"),
            "turn_count": 0,
        },
        "solver": {
            "name": "heading_grid_astar",
            "method": result.get("order_method"),
            "optimality_proven": result.get("order_method") == "held_karp_exact",
            "scope": "fixed V3 heading-grid pairwise stop ordering",
        },
    }
    arrival, turns = 0.0, 0
    for segment in result.get("segments", []):
        target = "HOME" if segment["to_id"] == "__end" else segment["to_id"]
        arrival += segment["motion_s"]
        turns += sum(a["type"] == "rotate" for a in segment["actions"])
        plan["segments"].append(
            {
                **segment,
                "from_goal_id": "__start__"
                if segment["from_id"] == "__start"
                else segment["from_id"],
                "to_goal_id": target,
            }
        )
        if target != "HOME":
            goal = indexed[target]
            plan["stops"].append(
                {
                    "goal_id": target,
                    "pose": goal["pose"],
                    "arrival_s": arrival,
                    "service_s": goal["service_s"],
                    "time_window_s": None,
                    "kind": "task",
                }
            )
            arrival += goal["service_s"]
    plan["metrics"]["turn_count"] = turns
    return plan


def _episode(snapshot, case, algorithm, plan, planning_ms, distance_bound, seed, repetition):
    request = case["request"]
    audit = audit_route(snapshot, request, plan)
    metrics = plan["metrics"]
    success = (
        plan["status"] == "READY"
        and audit["collisions"] == 0
        and audit["constraint_violations"] == 0
    )
    status = (
        plan["status"]
        if audit["collisions"] == 0 and audit["constraint_violations"] == 0
        else "INVALID_PLAN"
    )
    distance = metrics.get("distance_m")
    cost = metrics.get("cost_s")
    if algorithm == "semantic_graph_ortools":
        reference_cost = plan["solver"].get("exact_baseline", {}).get("cost_s")
        cost_reference = "held_karp_unconstrained_graph_semantic_cost_bound"
    else:
        reference_cost = metrics.get("baseline", {}).get("cost_s")
        cost_reference = "V3_same_algorithm_input_order_cost"
    reference_distance = distance_bound.get("distance_m")
    return {
        "schema_version": 1,
        "algorithm": algorithm,
        "profile": request.get("profile", "fastest"),
        "scenario_id": case["id"],
        "tier": case["tier"],
        "seed": seed,
        "repetition": repetition,
        "map_id": snapshot["map_id"],
        "map_revision": snapshot["revision"],
        "world_revision": snapshot["provenance"]["source_world_revision"],
        "configuration_hash": _hash(request),
        "expected_status": case["expected_status"],
        "status": status,
        "success": success,
        "decision_correct": status == case["expected_status"],
        "path_length_m": distance,
        "path_ratio": distance / reference_distance
        if distance is not None and reference_distance
        else None,
        "path_reference": distance_bound,
        "route_cost_s": cost,
        "route_cost_ratio": cost / reference_cost if cost is not None and reference_cost else None,
        "route_cost_reference": {"method": cost_reference, "cost_s": reference_cost},
        "min_clearance_m": audit["min_clearance_m"],
        "turn_count": metrics.get("turn_count"),
        "planning_ms": planning_ms,
        "replan_ms": planning_ms if case["id"] == "recovery_blocked_aisle" else None,
        "collisions": audit["collisions"],
        "constraint_violations": audit["constraint_violations"],
        "violations": audit["violations"],
        "mission_completion_time_s": metrics.get("mission_completion_time_s"),
        "mission_completion_time_source": "planned_estimate",
        "energy_wh": metrics.get("energy_wh"),
        "energy_source": "deterministic_drive_service_wait_estimate",
        "recovery_attempted": case["id"] == "recovery_blocked_aisle",
        "recovery_success": success if case["id"] == "recovery_blocked_aisle" else None,
        "task_goals_requested": len(request["goal_ids"]),
        "task_goals_completed_before_replan": len(request.get("completed_goal_ids", [])),
        "task_goals_planned": len(set(plan["ordered_goal_ids"]) & set(request["goal_ids"])),
        "charging_stops": metrics.get("charging_stops", 0),
        "solver": plan["solver"],
        "execution_boundary": "deterministic_plan_verification",
        "inventory_written": False,
        "request": request,
        "plan": plan,
    }


def run_benchmark(
    snapshot=None, *, seed=17, repetitions=3, algorithms=ALGORITHMS, case_ids=None, source_sha=None
):
    if not isinstance(seed, int) or not isinstance(repetitions, int) or not 1 <= repetitions <= 100:
        raise ValueError("INVALID_BENCH_SEED_OR_REPETITIONS")
    if not algorithms or set(algorithms) - set(ALGORITHMS):
        raise ValueError("UNKNOWN_BENCH_ALGORITHM")
    if snapshot is None:
        from app.spatial.snapshot import build_lab_snapshot

        snapshot = build_lab_snapshot()
    cases = scenario_catalog(snapshot, seed)
    if case_ids is not None:
        unknown = set(case_ids) - {c["id"] for c in cases}
        if unknown:
            raise ValueError("UNKNOWN_BENCH_SCENARIO:" + ",".join(sorted(unknown)))
        cases = [c for c in cases if c["id"] in case_ids]
    for case in cases:
        if case["id"] == "recovery_blocked_aisle":
            case["request"] = _recovery_request(snapshot, case["request"])
        case["request"] = MissionRequest.model_validate(case["request"]).model_dump(mode="json")
    episodes, baseline_cache, bounds = [], {}, {}
    for repetition in range(repetitions):
        for case in cases:
            if case["id"] not in bounds:
                bounds[case["id"]] = graph_distance_bound(snapshot, case["request"])
            for algorithm in algorithms:
                if algorithm == "heading_grid_astar" and not case["baseline_supported"]:
                    episodes.append(
                        {
                            "schema_version": 1,
                            "algorithm": algorithm,
                            "scenario_id": case["id"],
                            "profile": case["request"]["profile"],
                            "seed": seed,
                            "repetition": repetition,
                            "map_id": snapshot["map_id"],
                            "map_revision": snapshot["revision"],
                            "world_revision": snapshot["provenance"]["source_world_revision"],
                            "status": "SKIP",
                            "skip_reason": (
                                "Preserved V3 baseline does not implement priority/time-window "
                                "contracts; compare its supported cases only."
                            ),
                            "success": False,
                            "expected_status": case["expected_status"],
                        }
                    )
                    continue
                begin = time.perf_counter()
                plan = (
                    plan_mission(snapshot, case["request"])
                    if algorithm == "semantic_graph_ortools"
                    else _baseline_plan(snapshot, case["request"], baseline_cache)
                )
                elapsed = (time.perf_counter() - begin) * 1000
                episodes.append(
                    _episode(
                        snapshot,
                        case,
                        algorithm,
                        plan,
                        elapsed,
                        bounds[case["id"]],
                        seed,
                        repetition,
                    )
                )
    groups = sorted({(e["algorithm"], e["profile"]) for e in episodes})
    report = {
        "schema_version": 1,
        "benchmark": "WarehouseBench",
        "seed": seed,
        "repetitions": repetitions,
        "map_id": snapshot["map_id"],
        "map_revision": snapshot["revision"],
        "world_revision": snapshot["provenance"]["source_world_revision"],
        "source_sha": source_sha,
        "captured_at": datetime.now(UTC).isoformat(),
        "execution_boundary": "deterministic_plan_verification",
        "hardware_control": False,
        "provider_calls": 0,
        "inventory_written": False,
        "measurement": {
            "planning_latency": (
                "perf_counter wall time including request-local graph build / first baseline "
                "grid build; later baseline runs use cache"
            ),
            "reproducibility": (
                "seed, world/map revision, request hash, routes and metrics except clock/latency "
                "are deterministic"
            ),
            "success": (
                "READY plan passes independent geometry and resource audit; infeasibility "
                "is a decision, not task completion"
            ),
            "path_ratio": (
                "path length / exact filtered graph-distance ordering bound for <=8; finer "
                "V3 grid may be shorter than coarse graph"
            ),
            "mission_time": "planner estimate; observed Nav2 evidence is imported separately",
        },
        "algorithm_availability": [
            {"algorithm": algorithm, "status": "EXECUTED"} for algorithm in algorithms
        ]
        + [
            {
                "algorithm": "nav2_state_lattice",
                "status": "NOT_RUN",
                "reason": (
                    "Import the separate real ROS2/Nav2 acceptance summary and matching "
                    "topic/event artifacts; never substitute a planner estimate."
                ),
            }
        ],
        "episodes": episodes,
        "summaries": {
            f"{algorithm}:{profile}": summarize(
                [e for e in episodes if (e["algorithm"], e["profile"]) == (algorithm, profile)]
            )
            for algorithm, profile in groups
        },
    }
    json.dumps(report, allow_nan=False)
    return report


def write_results(report: dict, output: str | Path) -> dict:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    json_path, jsonl_path = output / "warehouse-bench.json", output / "warehouse-bench.jsonl"
    lines = "".join(
        json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
        for e in report["episodes"]
    )
    line_bytes = lines.encode("utf-8")
    jsonl_path.write_bytes(line_bytes)
    saved = {
        **report,
        "artifacts": {"episodes_jsonl_sha256": hashlib.sha256(line_bytes).hexdigest()},
    }
    json_bytes = (
        json.dumps(saved, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    json_path.write_bytes(json_bytes)
    manifest = {
        "json": str(json_path.resolve()),
        "jsonl": str(jsonl_path.resolve()),
        "json_sha256": hashlib.sha256(json_bytes).hexdigest(),
        "jsonl_sha256": hashlib.sha256(line_bytes).hexdigest(),
    }
    (output / "warehouse-bench.sha256.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
