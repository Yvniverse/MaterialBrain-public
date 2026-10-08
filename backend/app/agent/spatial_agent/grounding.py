"""Cheap intent boundary and deterministic grounding to registered warehouse IDs."""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable

from pydantic import ValidationError

from app.schemas.spatial import MissionRequest

_EXPLICIT = re.compile(
    r"空间|任务图|ROS\s*2|(?<![A-Za-z0-9])V4(?![A-Za-z0-9])|spatial|task\s*graph", re.I
)
_BUSINESS = re.compile(
    r"项目|产品|工程\s*BOM|(?<![A-Za-z0-9])BOM(?![A-Za-z0-9])|备料|生产|build|material|物料", re.I
)
_PHYSICAL = re.compile(
    r"导航|运送|搬运|送到|送去|交接|运输|取料|取货|返回|回到|巡检|"
    r"navigate|deliver|handoff|transport|return\s+home",
    re.I,
)
_COMMAND = re.compile(
    r"^(?:请|帮我|机器人|V4|空间|任务|现在|继续|先|再|\s|，|,)*"
    r"(?:取消|停止|执行|启动|开始|重规划|重新规划|绕行|查询进度|查看进度|"
    r"状态|进度|扫描|扫码|确认交接|核验交接|加入|移除|注入|去掉|撤掉|"
    r"cancel|start|execute|replan|scan|status|resume)",
    re.I,
)
_ENGINEERING_ESD = re.compile(r"器件|二极管|保护管|芯片|电容|电阻|TVS|封装|datasheet", re.I)
_COORDINATES = re.compile(r"坐标|\b[xy]\s*=|(?:navigate|导航|前往).{0,12}\(\s*-?\d", re.I)
_NO_EXECUTION = re.compile(
    r"不要执行|不执行|别执行|先不执行|无需执行|不启动|别启动|只规划|仅规划|"
    r"只查询|仅查询|只看|query\s*only|do\s*not\s*(?:execute|start)",
    re.I,
)
_BATTERY_LABEL = re.compile(r"电量(?:百分比)?|battery(?:_pct|\s+(?:percentage|level))?", re.I)
_BATTERY_PREFIX = re.compile(
    r"^\s*(?:(?:为|是|剩余|还剩|只有|仅剩|假设为|假定为|设为|is|at)\s*)?[:：=]?\s*", re.I
)
_BATTERY_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?"
_BATTERY_VALUE = re.compile(
    rf"^({_BATTERY_NUMBER})\s*(%|％|percent(?![A-Za-z]))?(?![A-Za-z0-9_.])", re.I
)
_NEW_PLAN = re.compile(r"规划|(?<![A-Za-z])plan(?:ning)?(?![A-Za-z])", re.I)
_REPLAN = re.compile(r"重规划|重新规划|replan|剩余.{0,8}(?:路线|站点|任务)", re.I)


def should_handle_instruction(message: str, previous_graph: dict | None = None) -> bool:
    """Do not touch GIS/DB for ordinary engineering or the established V3 route."""
    text = message.strip()
    if _EXPLICIT.search(text):
        return True
    if re.search(r"(?<![A-Za-z0-9])esd(?![A-Za-z0-9])", text, re.I) and not _ENGINEERING_ESD.search(
        text
    ):
        return True
    if _BUSINESS.search(text) and _PHYSICAL.search(text):
        return True
    if previous_graph and (
        _COMMAND.search(text)
        or re.search(r"重规划|重新规划", text)
        or re.search(
            r"剩余.{0,8}(?:路线|任务|站点)|(?:通道|路径).{0,8}(?:堵塞|占用|封闭)|"
            r"电量不足|低电量|地图.{0,8}(?:变化|过期)|wrong\s+scan|battery\s+low",
            text,
            re.I,
        )
    ):
        return True
    return False


def _response(action: str, **kwargs) -> dict:
    return {
        "handled": True,
        "action": action,
        "args": {},
        "business_grounding": {},
        "clarification": "",
        **kwargs,
    }


def _clarify(message: str, *, business: dict | None = None, code: str = "SCOPE_REQUIRED") -> dict:
    return _response(
        "clarify",
        clarification=message,
        args={"code": code},
        business_grounding=copy.deepcopy(business or {}),
    )


def _profile(message: str, current: str = "fastest") -> str:
    if re.search(r"ESD|静电|防静电", message, re.I):
        return "esd_safe"
    if re.search(r"安全|最稳|低风险|safest|safe\b", message, re.I):
        return "safest"
    if re.search(r"最快|最短|fastest|fast\b", message, re.I):
        return "fastest"
    return current


def _execution_state(previous: dict | None) -> dict:
    robot = copy.deepcopy((previous or {}).get("robot_state") or {})
    execution = robot.get("execution") or {}
    observed = bool(
        execution.get("robot_state")
        or execution.get("current_pose")
        or robot.get("current_pose")
        or robot.get("last_event_sequence", -1) >= 0
    )
    if not observed:
        # A TaskGraph starts with planning constraints, which may be hypothetical.
        # They are not telemetry and must not leak into the next new mission.
        robot.pop("battery_pct", None)
        robot.pop("payload_kg", None)
    robot.update(execution.get("robot_state") or {})
    if execution.get("current_pose"):
        robot["current_pose"] = execution["current_pose"]
    if execution.get("current_goal_id"):
        robot["current_goal_id"] = execution["current_goal_id"]
    return robot


def _planning_battery(message: str) -> tuple[float | None, str | None]:
    values = []
    labels = list(_BATTERY_LABEL.finditer(message))
    for index, label in enumerate(labels):
        end = labels[index + 1].start() if index + 1 < len(labels) else len(message)
        tail = message[label.end() : end]
        clause = re.split(r"[,，;；。\n]", tail, maxsplit=1)[0]
        supplied = _BATTERY_PREFIX.sub("", clause, count=1)
        match = _BATTERY_VALUE.match(supplied)
        implied_percentage = bool(re.search(r"百分比|_pct|percentage", label[0], re.I))
        percentage_claim = bool(re.search(r"%|％|percent", clause, re.I))
        assignment = bool(re.match(r"\s*(?:为|是|设为|[:：=])", clause))
        if match is None:
            if percentage_claim or implied_percentage or assignment:
                return None, "BATTERY_PERCENTAGE_INVALID"
            continue  # Descriptions such as '低电量/电量不足' are recovery hints.
        if not match[2] and not implied_percentage:
            return None, "BATTERY_PERCENTAGE_INVALID"
        if re.match(r"\s*[%％]", supplied[match.end() :]):
            return None, "BATTERY_PERCENTAGE_INVALID"
        if len(re.findall(rf"{_BATTERY_NUMBER}\s*(?:%|％|percent(?![A-Za-z]))", clause, re.I)) > 1:
            return None, "BATTERY_PERCENTAGE_AMBIGUOUS"
        value = float(match[1])
        if not math.isfinite(value) or not 0 <= value <= 100:
            return None, "BATTERY_PERCENTAGE_INVALID"
        values.append(value)
    if len(values) > 1:
        return None, "BATTERY_PERCENTAGE_AMBIGUOUS"
    return (values[0] if values else None), None


def ground_instruction(
    message: str,
    snapshot: dict,
    previous_graph: dict | None = None,
    business_resolver: Callable[[str], dict] | None = None,
) -> dict:
    """Return a typed operation; feasibility and dispatch remain server services.

    Business requests use the injected read-only BOM/inventory/Location resolver.
    No material quantity, measured mass or replacement is inferred here.
    """
    if not should_handle_instruction(message, previous_graph):
        return {
            "handled": False,
            "action": "query",
            "args": {},
            "business_grounding": {},
            "clarification": "",
        }
    text = message.strip()
    if not snapshot.get("revision") or not snapshot.get("docks"):
        return _clarify("请先读取已注册的空间地图，再选择任务站点。", code="MAP_NOT_REGISTERED")
    docks = {dock["id"]: dock for dock in snapshot["docks"]}
    robot = _execution_state(previous_graph)
    if _COORDINATES.search(text):
        return _clarify(
            "请使用已注册的站点或真实库位，导航坐标由服务器地图解析。",
            code="REGISTERED_GOAL_REQUIRED",
        )
    if re.search(
        r"(?:直接|自动).{0,8}(?:扣库存|出库|开抽屉|抓取)|physical\s*execute|硬件执行", text, re.I
    ):
        return _clarify(
            "空间任务支持导航、扫码与人工交接；库存确认沿用已有拣货流程。",
            code="EXECUTION_BOUNDARY",
        )
    if re.search(r"取消|停止任务|终止任务|cancel", text, re.I):
        if not previous_graph:
            return _clarify("当前会话没有可取消的空间任务。", code="MISSION_CONTEXT_REQUIRED")
        return _response("cancel")
    if previous_graph and previous_graph.get("map_revision") != snapshot["revision"]:
        return _clarify(
            "地图版本已变化，已完成交接保留；请在当前地图重新核验任务。", code="STALE_MAP"
        )
    planning_battery, battery_error = _planning_battery(text)
    if battery_error:
        return _clarify(
            "请指定唯一的 0–100% 规划电量值；运行电量仍从导航反馈读取。", code=battery_error
        )
    new_plan_requested = bool(_NEW_PLAN.search(text) and not _REPLAN.search(text))
    current_profile = ((previous_graph or {}).get("last_valid_plan") or {}).get(
        "profile", "fastest"
    )
    profile = _profile(text, current_profile)
    if not new_plan_requested and re.search(
        r"(?:加入|添加|注入|移除|去掉|撤掉|remove|inject|add).{0,20}"
        r"(?:障碍|占道|料车|封闭|block|obstacle)|blocked-crossing|isolated-dock",
        text,
        re.I,
    ):
        if not previous_graph:
            return _clarify(
                "请先建立空间任务，再添加或移除已注册的障碍场景。", code="MISSION_CONTEXT_REQUIRED"
            )
        scenario = (
            "isolated-dock" if re.search(r"隔离|isolated-dock", text, re.I) else "blocked-crossing"
        )
        operation = "remove" if re.search(r"移除|去掉|撤掉|remove", text, re.I) else "add"
        return _response("obstacles", args={"scenario_id": scenario, "operation": operation})
    if not new_plan_requested and re.search(
        r"重规划|重新规划|绕行|replan|剩余.{0,8}(?:路线|站点)|"
        r"(?:通道|路径).{0,8}(?:堵塞|占用|封闭)|电量不足|低电量",
        text,
        re.I,
    ):
        if not previous_graph:
            return _clarify(
                "请先建立空间任务；重规划需要当前位姿与已完成站点。",
                code="MISSION_CONTEXT_REQUIRED",
            )
        if previous_graph.get("status") in {"COMPLETED", "CANCELLED"}:
            return _clarify("当前任务已结束，请为新的需求建立空间任务。", code="MISSION_FINISHED")
        return _response("replan", args={"profile": profile})
    if (
        previous_graph
        and not new_plan_requested
        and re.search(r"扫码|扫描|确认交接|核验交接|handoff|\bscan\b", text, re.I)
    ):
        goal = robot.get("current_goal_id")
        if not goal:
            scan_node = next(
                (
                    node
                    for node in previous_graph["nodes"]
                    if node["skill"] in {"confirm_scan", "verify_handoff"}
                    and node["status"] == "RUNNING"
                ),
                None,
            )
            goal = (scan_node or {}).get("args", {}).get("goal_id")
        if not goal or goal not in docks:
            return _clarify("机器人尚未到达可交接站点，请等待导航到站。", code="ARRIVAL_REQUIRED")
        if previous_graph.get("status") not in {
            "AWAITING_HANDOFF",
            "WAITING_HANDOFF",
            "SCAN_REJECTED",
        }:
            return _clarify("当前没有等待扫码的到站交接。", code="ARRIVAL_REQUIRED")
        match = re.search(
            r"(?:扫码|扫描|scan(?:_code)?|槽位码|库位码)\s*[:：=]?\s*([A-Za-z0-9_-]+)", text, re.I
        )
        if not match:
            return _clarify(
                "请提供实际扫描的库位码，扫码结果由服务器核验。", code="SCAN_CODE_REQUIRED"
            )
        return _response("handoff", args={"goal_id": goal, "scan_code": match[1]})
    execute = bool(
        re.search(r"执行|启动|开始|出发|跑起来|\bexecute\b|\bstart\b|\bresume\b", text, re.I)
    )
    execute = (
        execute
        and not _NO_EXECUTION.search(text)
        and not re.search(r"开始规划|start planning", text, re.I)
    )
    if previous_graph and execute and not re.search(r"新任务|重新建立|new\s+mission", text, re.I):
        if previous_graph.get("status") in {
            "COMPLETED",
            "CANCELLED",
            "BLOCKED",
            "CLARIFICATION",
            "INFEASIBLE",
        } or not previous_graph.get("last_valid_plan"):
            return _clarify(
                "当前任务不可直接启动，请先取得可执行的最新规划。", code="READY_PLAN_REQUIRED"
            )
        request = robot.get("request")
        if not request:
            return _clarify(
                "任务缺少原始规划请求，请重新建立空间任务。", code="MISSION_REQUEST_REQUIRED"
            )
        return _response(
            "execute",
            request=copy.deepcopy(request),
            args={"existing_mission": True},
            business_grounding=copy.deepcopy(robot.get("business_grounding") or {}),
        )
    if (
        re.search(
            r"状态|进度|查询|查看|为什么|有哪些|是什么|如何|在哪|哪里|附近|所属|属于|\bstatus\b|\bquery\b|\bwhere\b",
            text,
            re.I,
        )
        and not re.search(r"规划|plan\b|备料|导航|运送|搬运", text, re.I)
        and not execute
    ):
        args = {
            "map_id": snapshot["map_id"],
            "map_revision": snapshot["revision"],
            "profile": profile,
        }
        if previous_graph:
            args.update(
                status=previous_graph["status"],
                completed_goal_ids=previous_graph["completed_goal_ids"],
                remaining_goal_ids=previous_graph["remaining_goal_ids"],
            )
        if re.search(
            r"附近|最近|所属|属于|所在区域|所在分区|在哪|哪里|near|nearest|zone", text, re.I
        ):
            named_goals = re.findall(r"(?<![A-Za-z0-9_-])P-[A-Za-z0-9-]+", text.upper())
            if any(goal not in docks for goal in named_goals):
                return _clarify("查询目标没有注册，请指定已注册站点。", code="UNREGISTERED_GOAL")
            if len(set(named_goals)) > 1:
                return _clarify("请指定一个查询参考站点。", code="QUERY_POINT_AMBIGUOUS")
            point = (
                docks[named_goals[0]].get("pose")
                if named_goals
                else robot.get("current_pose") or (docks.get("HOME") or {}).get("pose")
            )
            if point is None:
                return _clarify("请先读取机器人位姿或指定有停靠位姿的站点。", code="POSE_REQUIRED")
            membership = bool(re.search(r"所属|属于|所在区域|所在分区|membership", text, re.I))
            nearest = bool(re.search(r"最近|nearest", text, re.I))
            kind = "contains" if membership else "nearest_dock" if nearest else "nearby"
            args["query"] = {
                "kind": kind,
                "point": {"x": point["x"], "y": point["y"]},
                "entity": "zones"
                if membership or re.search(r"区域|分区|zone", text, re.I)
                else "docks",
                "limit": 10,
            }
            if kind == "nearby":
                args["query"]["radius_m"] = 5
            args["query_point_source"] = (
                "registered_dock"
                if named_goals
                else "observed_pose"
                if robot.get("current_pose")
                else "registered_home"
            )
        return _response("query", args=args)
    explicit = list(dict.fromkeys(re.findall(r"(?<![A-Za-z0-9_-])P-[A-Za-z0-9-]+", text.upper())))
    unknown = set(explicit) - set(docks)
    if unknown:
        return _clarify("目标站点没有注册，请选择地图已有站点。", code="UNREGISTERED_GOAL")
    business = {}
    if _BUSINESS.search(text) and (_PHYSICAL.search(text) or not explicit):
        if business_resolver is None:
            return _clarify(
                "请先指定项目或产品版本与台数，再读取工程 BOM、库存和库位。",
                code="BUSINESS_RESOLVER_REQUIRED",
            )
        business = copy.deepcopy(business_resolver(text))
        if business.get("status") != "GROUNDED":
            message = (
                business.get("clarification")
                or "需求存在缺料或未映射库位，请先核验工程 BOM 与库存。"
            )
            return _clarify(
                message, business=business, code="BUSINESS_" + business.get("status", "UNKNOWN")
            )
        goals = list(dict.fromkeys(business.get("goal_ids", [])))
        if explicit and set(explicit) != set(goals):
            return _clarify(
                "所选站点与该工程需求的真实库位不一致，请按已核验库位规划。",
                business=business,
                code="BUSINESS_GOALS_MISMATCH",
            )
        if business.get("inventory_written") or business.get("automatic_substitution"):
            return _clarify(
                "需求解析违反只读或替代审批边界，请重新核验。",
                business=business,
                code="BUSINESS_BOUNDARY",
            )
    else:
        goals = explicit
        if not goals:
            goals = [
                dock["id"]
                for dock in docks.values()
                if dock.get("label")
                and dock["label"] in text
                and dock["id"] not in {"HOME", "CHARGER"}
            ]
        if not goals and re.search(r"默认|六站|全部站点|default|all\s+stops", text, re.I):
            provenance = snapshot.get("provenance") or {}
            goals = provenance.get("default_goal_ids", []) if isinstance(provenance, dict) else []
    if not goals or not set(goals).issubset(docks):
        return _clarify(
            "请选择已注册的站点编号，或提供项目 / 产品版本与备料台数。",
            business=business,
            code="REGISTERED_GOAL_REQUIRED",
        )
    provenance = snapshot.get("provenance") or {}
    provenance = provenance if isinstance(provenance, dict) else {}
    initial_robot = provenance.get("robot") or {}
    home_id = provenance.get("home_dock_id", "HOME")
    pose = robot.get("current_pose") or (docks.get(home_id) or {}).get("pose")
    if pose is None:
        return _clarify(
            "缺少机器人当前位姿或已注册待命点，请先读取运行状态。", code="POSE_REQUIRED"
        )
    constraints = {
        "battery_pct": robot.get("battery_pct", initial_robot.get("battery_pct", 100)),
        "payload_capacity_kg": initial_robot.get("payload_kg", 18),
        "battery_reserve_pct": initial_robot.get("battery_reserve_pct", 15),
        "robot_class": initial_robot.get("id", "MB-R01"),
    }
    assumptions = {}
    if planning_battery is not None and not execute:
        constraints["battery_pct"] = planning_battery
        assumptions = {
            "battery_pct": planning_battery,
            "source": "instruction",
            "planning_only": True,
        }
    raw = {
        "map_id": snapshot["map_id"],
        "map_revision": snapshot["revision"],
        "profile": profile,
        "start_pose": copy.deepcopy(pose),
        "goal_ids": goals,
        "completed_goal_ids": [],
        "constraints": constraints,
        "return_home": not bool(re.search(r"不返回|无需返回|no\s+return", text, re.I)),
        "dynamic_overlays": copy.deepcopy(snapshot.get("dynamic_overlays", [])),
    }
    try:
        request = MissionRequest.model_validate(raw).model_dump(mode="json")
    except ValidationError:
        return _clarify(
            "当前机器人或地图状态不完整，请刷新状态后重新规划。", code="MISSION_REQUEST_INVALID"
        )
    return _response(
        "execute" if execute else "plan",
        request=request,
        business_grounding=business,
        args={"planning_assumptions": assumptions} if assumptions else {},
    )
