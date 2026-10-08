"""Actual ROS/Nav2 HTTP acceptance; reads measured topics/events, never a fake navigator."""

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


def request(url, body=None):
    req = Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=15) as response:
        return json.load(response)


def wait_ready(base, timeout=90):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            h = request(base + "/health")
            if h["ready"]:
                return h
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise AssertionError("installed Nav2 lifecycle/action/sensor readiness failed")


def make_plan(health, scenario):
    mid = f"p1-{scenario}-{int(time.time())}"
    goals = ["P-IC", "P-SENSOR", "P-LAB"]
    if scenario in ("cancel", "low-battery", "bt-recovery"):
        goals = ["P-IC"]
    if scenario == "charge":
        goals = ["CHARGER", "P-CABLE"]
    return {
        "schema_version": 1,
        "mission_id": mid,
        "map_id": health["map_id"],
        "map_revision": health["map_revision"],
        "profile": "fastest",
        "status": "READY",
        "ordered_goal_ids": goals,
        "completed_goal_ids": [],
        "stops": [],
        "constraints": {
            "battery_pct": 12 if scenario in ("low-battery", "charge") else 82,
            "battery_reserve_pct": 15,
            "payload_capacity_kg": 18,
            "robot_class": "MB-R01",
        },
        "solver": {
            "name": "registered_acceptance_order",
            "method": "scenario",
            "optimality_proven": False,
        },
        "metrics": {},
        "objective_terms": {},
        "segments": [{"to_goal_id": "HOME"}],
        "violations": [],
    }


def run(base, scenario, output, reset=True, timeout=900, plan_path=None):
    health = wait_ready(base)
    started_at = datetime.now(timezone.utc).isoformat()
    if reset:
        result = subprocess.run(
            ["ros2", "service", "call", "/simulation/reset", "std_srvs/srv/Trigger", "{}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode or "success=True" not in result.stdout:
            raise AssertionError(
                "isolated simulator reset failed: " + result.stdout + result.stderr
            )
        time.sleep(0.5)
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8")) if plan_path else make_plan(health, scenario)
    mid = plan["mission_id"]
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    request(base + "/missions", plan)
    assert request(base + "/missions", plan)["mission_id"] == mid, (
        "idempotent mission submission duplicated"
    )
    injected = False
    removed = False
    wrong_checked = False
    cancelled = False
    recoveries = []
    velocities = []
    before_block_completed = None
    begin = time.monotonic()
    last_print = 0.0
    while time.monotonic() - begin < timeout:
        state = request(base + "/missions/" + mid)
        pose = state["current_pose"]
        robot = state["robot_state"]
        if (
            scenario == "bt-recovery"
            and not injected
            and state["status"] == "NAVIGATING"
            and state["metrics"]["action_feedback_messages"] >= 5
            and robot.get("distance_m", 0) > 0.25
        ):
            request(base + "/obstacles", {"operation": "add", "scenario_id": "isolated-dock"})
            injected = True
            injected_at = time.monotonic()
        if (
            scenario == "blocked"
            and not injected
            and "P-SENSOR" in state["completed_goal_ids"]
            and state["status"] == "NAVIGATING"
            and pose["y"] > 8
            and pose["x"] < 6.4
        ):
            before_block_completed = state["completed_goal_ids"][:]
            request(base + "/obstacles", {"operation": "add", "scenario_id": "blocked-crossing"})
            injected = True
            injected_at = time.monotonic()
        if injected and scenario == "blocked":
            velocities.append(
                {"at": time.monotonic() - injected_at, "v_mps": robot.get("v_mps", 0), "pose": pose}
            )
            if not removed and time.monotonic() - injected_at > 12:
                request(base + "/obstacles", {"operation": "remove", "id": "OB-PALLET-01"})
                removed = True
        if scenario == "bt-recovery" and not removed:
            recoveries = [
                e
                for e in state["events"]
                if e["type"] == "recovery"
                and e["details"].get("source") == "nav2_behavior_tree_log"
                and e["details"].get("node") in ("Wait", "BackUp", "Spin")
            ]
            if recoveries:
                request(base + "/obstacles", {"operation": "remove", "id": "OB-GOAL"})
                removed = True
        if (
            scenario == "cancel"
            and not cancelled
            and state["metrics"]["action_feedback_messages"] >= 5
            and robot.get("distance_m", 0) > 0.25
        ):
            state = request(base + "/missions/" + mid + "/cancel", {})
            cancelled = True
            before = dict(state["current_pose"])
            time.sleep(1)
            after = request(base + "/missions/" + mid)
            assert abs(after["robot_state"]["v_mps"]) < 0.001
            assert (
                abs(after["current_pose"]["x"] - before["x"])
                + abs(after["current_pose"]["y"] - before["y"])
                < 0.02
            )
            state = after
            break
        if state["status"] == "AWAITING_HANDOFF":
            goal = state["current_goal_id"]
            if not wrong_checked:
                rejected = request(
                    base + "/missions/" + mid + "/handoff",
                    {"goal_id": goal, "scan_code": "WRONG-SLOT"},
                )
                assert rejected["completed_goal_ids"] == state["completed_goal_ids"], (
                    "wrong scan mutated completion"
                )
                wrong_checked = True
            verified = request(
                base + "/missions/" + mid + "/handoff", {"goal_id": goal, "scan_code": goal}
            )
            assert verified["completed_goal_ids"].count(goal) == 1
            duplicate = request(
                base + "/missions/" + mid + "/handoff", {"goal_id": goal, "scan_code": goal}
            )
            assert (
                duplicate["metrics"]["completed_handoffs"]
                == verified["metrics"]["completed_handoffs"]
            ), "handoff retry duplicated"
        if state["status"] in (
            "COMPLETED",
            "CANCELLED",
            "BLOCKED_LOW_BATTERY",
            "FAILED",
            "TRANSPORT_PAUSED",
        ):
            break
        if time.monotonic() - last_print > 10:
            print(
                json.dumps(
                    {
                        "scenario": scenario,
                        "status": state["status"],
                        "goal": state["current_goal_id"],
                        "pose": pose,
                        "completed": state["completed_goal_ids"],
                        "feedback": state["metrics"]["action_feedback_messages"],
                    }
                ),
                flush=True,
            )
            last_print = time.monotonic()
        time.sleep(0.25)
    else:
        request(base + "/missions/" + mid + "/cancel", {})
        raise AssertionError("real navigation acceptance timed out")
    assertions = {
        "no_collision": state["robot_state"].get("collision_count") == 0,
        "monotonic_events": [e["sequence"] for e in state["events"]]
        == list(range(1, state["last_sequence"] + 1)),
        "unique_event_ids": len({e["event_id"] for e in state["events"]}) == len(state["events"]),
    }
    if scenario in ("nominal", "semantic", "blocked", "bt-recovery", "charge"):
        assertions.update(
            completed=state["status"] == "COMPLETED",
            registered_handoffs=all(
                g in state["completed_goal_ids"] for g in plan["ordered_goal_ids"] if g != "CHARGER"
            ),
            returned_home="HOME" in state["completed_goal_ids"],
            actual_nav2_feedback=state["metrics"]["action_feedback_messages"] > 10,
        )
    if scenario == "blocked":
        assertions.update(
            canonical_central_obstacle_injected=injected,
            current_pose_replan=state["metrics"]["replan_count"] > 0,
            completed_handoffs_preserved=before_block_completed
            and all(state["completed_goal_ids"].count(g) == 1 for g in before_block_completed),
            slowed_or_stopped=bool(velocities) and min(abs(v["v_mps"]) for v in velocities) < 0.12,
        )
    if scenario == "bt-recovery":
        assertions["actual_bt_recovery"] = bool(recoveries)
        assertions["canonical_dock_obstacle_injected_during_navigation"] = injected
    if scenario == "cancel":
        assertions["cancelled_and_stationary"] = cancelled and state["status"] == "CANCELLED"
    if scenario == "low-battery":
        assertions.update(
            low_battery_blocked=state["status"] == "BLOCKED_LOW_BATTERY",
            zero_nav2_actions=state["metrics"]["nav2_action_count"] == 0,
        )
    if scenario == "charge":
        assertions["simulated_charging_after_nav2_arrival"] = any(
            e["type"] == "charging" and e["details"].get("state") == "complete"
            for e in state["events"]
        )
    if scenario == "semantic":
        dispatched = [
            e
            for e in state["events"]
            if e["type"] == "feedback"
            and e["details"].get("state") == "dispatching"
            and e.get("goal_id") != "HOME"
        ]
        assertions["canonical_graph_route_dispatched"] = bool(dispatched) and all(
            e["details"].get("semantic_route_mode") == "registered_graph_waypoints"
            and len(e["details"].get("semantic_waypoint_ids", [])) > 1
            for e in dispatched
        )
    final_health = request(base + "/health")
    summary = {
        "schema_version": 1,
        "execution_boundary": "ros2_nav2_simulation",
        "hardware_control": False,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at,
        "build_sha": final_health["build_sha"],
        "map_id": health["map_id"],
        "map_revision": health["map_revision"],
        "ros_distro": health["ros_distro"],
        "nav2_version": health["nav2_version"],
        "active_plugins": final_health["active_plugins"],
        "name": scenario,
        "status": "PASS" if all(assertions.values()) else "FAIL",
        "mission_id": mid,
        "completed_goal_ids": state["completed_goal_ids"],
        "metrics": state["metrics"],
        "assertions": assertions,
        "state": state,
        "obstacle_velocity_samples": velocities,
    }
    path = directory / f"{scenario}.json"
    path.write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: {name: value for name, value in v.items() if name != "global_path_lengths_m"}
                if k == "metrics"
                else v
                for k, v in summary.items()
                if k not in ("state", "obstacle_velocity_samples", "active_plugins")
            },
            indent=2,
        ),
        flush=True,
    )
    if summary["status"] != "PASS":
        raise AssertionError("real runtime scenario assertions failed: " + json.dumps(assertions))
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8766")
    p.add_argument(
        "--scenario",
        choices=(
            "nominal",
            "semantic",
            "blocked",
            "bt-recovery",
            "cancel",
            "low-battery",
            "charge",
            "all",
        ),
        required=True,
    )
    p.add_argument("--output", default="/opt/materialbrain/.evidence/results")
    p.add_argument("--no-reset", action="store_true")
    p.add_argument("--timeout", type=float, default=900)
    p.add_argument("--plan")
    a = p.parse_args()
    if a.scenario == "semantic" and not a.plan:
        p.error("semantic acceptance requires a server-generated canonical graph plan")
    if a.scenario == "all":
        if a.plan or a.no_reset:
            p.error("all scenarios use independent registered plans and simulator resets")
        for scenario in ("nominal", "blocked", "bt-recovery", "cancel", "low-battery", "charge"):
            run(a.base_url, scenario, a.output, timeout=a.timeout)
    else:
        run(a.base_url, a.scenario, a.output, not a.no_reset, a.timeout, a.plan)
