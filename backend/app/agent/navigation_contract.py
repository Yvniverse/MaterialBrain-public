"""Translate explicit lab intent into registered IDs, never LLM coordinates."""

from __future__ import annotations

import re

from pydantic import ValidationError

from app.agent.task_contract import TaskContract
from app.services.embodied_navigation.schemas import NavigationExecutionContext


def navigation_plan_arguments(
    manifest: dict, contract: TaskContract, message: str, context: dict | None
) -> tuple[dict | None, dict | None]:
    def clarification(reason: str):
        return None, {
            "status": "CLARIFICATION",
            "reason": reason,
            "world_id": manifest["world_id"],
            "world_revision": manifest["world_revision"],
            "mode": "simulation",
            "inventory_written": False,
        }

    goals = {goal["id"]: goal for goal in manifest["goals"]}
    scenario = "baseline"
    for scenario_id, markers in (
        ("blocked-crossing", ("占用", "占道", "堵塞", "中央通道", "blocked-crossing")),
        ("isolated-dock", ("隔离", "isolated-dock")),
        ("low-battery", ("低电量", "电量不足", "low-battery")),
    ):
        if any(marker in message.casefold() for marker in markers):
            scenario = scenario_id
    if contract.navigation_operation == "replan":
        if context is None:
            return clarification(
                "请先打开并暂停当前实验仓任务，再重规划剩余站点；需要当前位姿、已完成站点、载荷和电量。"
            )
        try:
            execution = NavigationExecutionContext.model_validate(context)
        except ValidationError:
            return clarification("当前仿真状态不完整，请重新打开实验仓任务。")
        if execution.world_revision != manifest["world_revision"]:
            return clarification("实验地图版本已变化，请在当前地图重新建立任务。")
        if execution.state in {"WAITING_HANDOFF", "COMPLETED", "CANCELLED"}:
            return clarification("请先完成当前扫码交接，或为已结束的任务新建路线。")
        ids, completed = execution.goal_ids, execution.completed_goal_ids
        if (
            len(ids) != len(set(ids))
            or len(completed) != len(set(completed))
            or not set(ids).issubset(goals)
            or not set(completed).issubset(ids)
        ):
            return clarification("当前任务包含未注册或重复的停靠点，请重新选择实验任务。")
        expected_load = sum(goals[goal]["payload_kg"] for goal in completed)
        if abs(execution.payload_kg - expected_load) > 1e-6:
            return clarification("已交接站点与当前载荷不一致，请在实验仓核对任务状态。")
        if scenario == "baseline":
            scenario = execution.scenario_id
        return {
            "world_id": manifest["world_id"],
            "world_revision": manifest["world_revision"],
            "goal_ids": [goal for goal in ids if goal not in completed],
            "scenario_id": scenario,
            "start": execution.pose.model_dump(),
            "payload_kg": execution.payload_kg,
            "battery_pct": execution.battery_pct,
            "mode": "simulation",
        }, None
    explicit_ids = re.findall(r"\bP-[A-Z]+\b", message.upper())
    if any(goal not in goals for goal in explicit_ids):
        return clarification("该停靠点没有注册，请选择实验仓已有站点。")
    if any(marker in message.casefold() for marker in ("坐标", "x=", "y=")):
        return clarification("请使用已注册停靠点 ID 选择目标；路线由地图与机器人外廓计算。")
    return {
        "world_id": manifest["world_id"],
        "world_revision": manifest["world_revision"],
        "goal_ids": list(dict.fromkeys(explicit_ids)) or manifest["default_goal_ids"],
        "scenario_id": scenario,
        "mode": "simulation",
    }, None
