"""Measured planner metrics; failure and skipped evidence retain their semantics."""

from __future__ import annotations

import math
from statistics import mean


def percentile(values, q):
    if not 0 <= q <= 1:
        raise ValueError("PERCENTILE_OUT_OF_RANGE")
    ordered = sorted(float(v) for v in values if v is not None)
    if not all(math.isfinite(v) for v in ordered):
        raise ValueError("NONFINITE_METRIC")
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _average(episodes, key):
    values = [e[key] for e in episodes if e.get(key) is not None]
    return mean(values) if values else None


def summarize(episodes):
    executed = [e for e in episodes if e.get("status") != "SKIP"]
    successes = [e for e in executed if e.get("success")]
    recoveries = [e for e in executed if e.get("recovery_attempted")]
    planning = [
        value
        for episode in executed
        for value in episode.get("planning_latencies_ms", [episode.get("planning_ms")])
    ]
    replanning = [
        value
        for episode in recoveries
        for value in episode.get("replanning_latencies_ms", [episode.get("replan_ms")])
    ]
    count = len(executed)
    return {
        "episodes": len(episodes),
        "executed_episodes": count,
        "skipped_episodes": len(episodes) - count,
        "success_rate": len(successes) / count if count else None,
        "decision_correct_rate": sum(e.get("decision_correct", False) for e in executed) / count
        if count
        else None,
        "collision_rate": sum(e.get("collisions", 0) > 0 for e in executed) / count
        if count
        else None,
        "constraint_violation_rate": sum(e.get("constraint_violations", 0) > 0 for e in executed)
        / count
        if count
        else None,
        "planning_p50_ms": percentile(planning, 0.5),
        "planning_p95_ms": percentile(planning, 0.95),
        "replan_p50_ms": percentile(replanning, 0.5),
        "replan_p95_ms": percentile(replanning, 0.95),
        "recovery_success_rate": sum(e.get("recovery_success", False) for e in recoveries)
        / len(recoveries)
        if recoveries
        else None,
        "mean_path_length_m": _average(successes, "path_length_m"),
        "mean_path_ratio": _average(successes, "path_ratio"),
        "mean_route_cost_ratio": _average(successes, "route_cost_ratio"),
        "min_clearance_m": min(
            (e["min_clearance_m"] for e in successes if e.get("min_clearance_m") is not None),
            default=None,
        ),
        "mean_turn_count": _average(successes, "turn_count"),
        "mean_mission_completion_time_s": _average(successes, "mission_completion_time_s"),
        "mean_energy_wh": _average(successes, "energy_wh"),
    }
