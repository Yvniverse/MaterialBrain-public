"""Deterministic next-action oracle, separate from policy-visible observations."""

from __future__ import annotations

import math


def expert_action(environment) -> dict:
    observation = environment.observe()
    if observation["terminated"] or observation["truncated"]:
        raise RuntimeError("A closed episode has no next expert action")
    instruction = observation["instruction"]
    mission_args = {
        "mission_id": observation["mission_id"],
        "map_revision": observation["map_revision"],
    }
    context_args = {"map_id": observation["map_id"], "map_revision": observation["map_revision"]}
    request, business, execution = (
        observation["request"],
        observation["business"],
        observation["execution"],
    )

    def action(name, arguments):
        return {"name": name, "args": arguments}

    def wait(deadline=None):
        duration = (
            5 if deadline is None else max(1, math.ceil(deadline - observation["virtual_time_s"]))
        )
        return action(
            "wait_or_yield",
            {
                **mission_args,
                "reason": "等待已观察到的临时障碍、扫描器或人工交接恢复",
                "duration_s": min(300, duration),
            },
        )

    if request["ambiguity"] or not request["goal_ids"]:
        return {"decision": "clarify", "reason": "缺少已确认的工程版本或目标位置，请补全任务范围。"}
    if environment.scenario.get("terminal_skill") == "query_spatial_context":
        return action("query_spatial_context", {**context_args, "query": "affordances"})
    if not business["bom_resolved"]:
        return action("resolve_engineering_bom", {"instruction": instruction})
    if not business["inventory_checked"]:
        return action("check_inventory", {"instruction": instruction})
    if business["stock_status"] == "partial":
        return {
            "decision": "clarify",
            "reason": "部分需求缺少确定库存，请补充可执行范围，保持库存不变。",
        }
    if business["stock_status"] == "missing":
        return {"decision": "deny", "reason": "已核实库存不足，无法执行完整需求，保持库存不变。"}
    if not business["locations_resolved"]:
        return action("resolve_pick_locations", {"instruction": instruction})
    if not observation["context"]["queried"]:
        return action("query_spatial_context", {**context_args, "query": "affordances"})
    plan = execution["plan"]
    if plan is None:
        goal = execution["arrived_goal_id"]
        if (
            goal
            and "charge" in environment.docks[goal].get("capabilities", [])
            and observation["robot"]["battery_pct"] < 25
        ):
            return action("charge_robot", {**mission_args, "goal_id": goal})
        return action(
            "plan_mission",
            {**context_args, "goal_ids": request["goal_ids"], "profile": request["profile"]},
        )
    if plan["status"] != "READY":
        deadlines = [
            closure["reopen_at_s"]
            for closure in observation["active_closures"]
            if closure.get("reopen_at_s") is not None
        ]
        if deadlines:
            return wait(min(deadlines))
        if (
            observation["recovery"]
            and observation["recovery"].get("requires_replan")
            and not observation["active_closures"]
        ):
            return action(
                "replan_remaining",
                {
                    **mission_args,
                    "reason": "临时障碍已开放，按当前姿态恢复剩余任务",
                    "profile": request["profile"],
                },
            )
        return {
            "decision": "deny",
            "reason": "确定性规划器已确认资源或路径不可行，停止执行并保持库存不变。",
        }
    if observation["recovery"] and observation["recovery"].get("requires_replan"):
        return action(
            "replan_remaining",
            {
                **mission_args,
                "reason": "根据已观察到的封路或电量变化重规划剩余目标",
                "profile": request["profile"],
            },
        )
    if not execution["remaining_goal_ids"]:
        return action(
            "summarize_mission" if execution["returned_home"] else "return_home", mission_args
        )
    stop = execution["active_stop"]
    if stop is None:
        raise RuntimeError("Remaining goals have no accepted plan stop")
    goal_args = {**mission_args, "goal_id": stop["goal_id"]}
    if execution["arrived_goal_id"] != stop["goal_id"]:
        return action("navigate_mission", goal_args)
    if stop["kind"] == "charge":
        return action("charge_robot", goal_args)
    if observation["scanner"]["status"] == "wrong_reading":
        return wait()
    if not execution["scan_verified"]:
        return action(
            "confirm_scan", {**goal_args, "scan_code": observation["scanner"]["expected_code"]}
        )
    if not observation["handoff"]["available"]:
        return wait(observation["handoff"]["wait_until_s"])
    return action("verify_handoff", goal_args)
