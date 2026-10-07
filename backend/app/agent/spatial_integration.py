"""Server-owned mission execution in the existing Agent conversation context.

Only the internal ROS2 simulator receives motion commands. Handoffs are observed
simulation events; this service never settles stock or reservations.
"""

import copy
import hashlib
import json
import re
import time
import uuid

from sqlalchemy import text

from app.agent.conversation import ConversationContextService
from app.core.config import Settings, settings
from app.core.exceptions import BusinessError
from app.models import AgentConversationContext
from app.schemas.spatial import ExecutionEvent, MissionRequest, TaskGraph
from app.services.embodied_navigation.service import world_snapshot
from app.services.spatial_business_grounding import SpatialBusinessGrounding
from app.services.spatial_mission import plan_mission
from app.services.spatial_mission_store import SpatialMissionStore
from app.services.spatial_poll_diagnostics import (
    diagnostic_count,
    poll_mark,
    poll_phase,
    poll_timed,
)
from app.services.spatial_transport import SpatialRobotTransport
from app.spatial import SpatialMapService


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class SpatialAgentIntegration:
    def __init__(self, db, user, *, config: Settings | None = None, transport=None):
        self.db, self.user = db, user
        self.config = settings if config is None else config
        self.maps = SpatialMapService(db)
        self.store = SpatialMissionStore(db, user, self.config)
        self.transport = transport or SpatialRobotTransport(self.config.spatial_robot_bridge_url)

    def _permission(self):
        permissions = set(self.user.role.permissions or [])
        if "*" not in permissions and not permissions.intersection(
            {"material:view", "location:manage", "picking:view"}
        ):
            raise BusinessError("SPATIAL_PERMISSION_REQUIRED", "当前账户无法使用空间任务。", 403)

    @staticmethod
    def projection(graph):
        return {
            "mission_id": graph["mission_id"],
            "conversation_id": graph["conversation_id"],
            "task_graph": graph,
            "mission_plan": graph.get("last_valid_plan")
            or graph["robot_state"].get("planning_result"),
            "execution": graph["robot_state"].get("execution"),
            "execution_boundary": "ros2_nav2_simulation",
            "inventory_written": False,
            "hardware_control": False,
        }

    @poll_timed("integration_save")
    def _save(self, row, graph):
        row_id = row.id
        original = copy.deepcopy((row.pending_disambiguation or {}).get("spatial_task"))
        for attempt in range(3):
            with poll_phase("save_validate"):
                checked = TaskGraph.model_validate(graph).model_dump(mode="json")
            try:
                return self.projection(self.store.save(row, checked))
            except BusinessError as exc:
                if exc.code != "AGENT_CONVERSATION_CONFLICT":
                    raise
                poll_mark("save_cas_conflict", count=1)
                if attempt == 2:
                    # Five independent polls plus a controller can exhaust the
                    # optimistic retries while each retry performs robot I/O.
                    # Finish with a short atomic reconciliation of already
                    # validated observations; no network request runs under lock.
                    return self._save_locked(row_id, graph, original)
                # Polling can commit while a slower control request plans. Rebase
                # on the current context and actual bridge event sequence;
                # never replay the motion command or overwrite a different task.
                with poll_phase("save_rebase_read"):
                    self.db.expire_all()
                    row = self.db.get(AgentConversationContext, row_id)
                    current = (
                        (row.pending_disambiguation or {}).get("spatial_task") if row else None
                    )
                if not current or current["mission_id"] != graph["mission_id"]:
                    raise
                candidate = graph
                if current["robot_state"].get("last_event_sequence", -1) > graph["robot_state"].get(
                    "last_event_sequence", -1
                ):
                    # A slow poll must not replace newer handoff/replan state.
                    graph = copy.deepcopy(current)
                # Overlay IDs refer to committed map changes, not estimated
                # motion state. Preserve the newer controller bookkeeping even
                # when an intervening poll owns the latest event sequence.
                overlays = max(
                    (candidate["robot_state"], current["robot_state"]),
                    key=lambda state: state.get("overlay_generation", 0),
                )
                if overlays.get("overlay_generation", 0) > graph["robot_state"].get(
                    "overlay_generation", 0
                ):
                    for key in ("overlay_ids", "overlay_generation"):
                        graph["robot_state"][key] = copy.deepcopy(overlays.get(key))
                if current["robot_state"].get("execution"):
                    last = (graph["robot_state"].get("execution") or {}).get("last_sequence", 0)
                    observed = self.transport.request(
                        "GET", f"/missions/{graph['mission_id']}?after_sequence={last}"
                    )
                    if observed.get("last_sequence", last) >= last:
                        with poll_phase(
                            "save_rebase_accept", count=diagnostic_count(observed.get("events"))
                        ):
                            graph = self._accept(graph, observed)
        raise AssertionError("Bounded context retry exhausted")

    @poll_timed("locked_reconcile")
    def _save_locked(self, row_id, candidate, original):
        with self.store.locked_context(row_id) as row:
            current = (row.pending_disambiguation or {}).get("spatial_task")
            if not current or any(
                current.get(key) != candidate.get(key)
                for key in ("mission_id", "conversation_id", "map_id", "map_revision")
            ):
                raise BusinessError(
                    "AGENT_CONVERSATION_CONFLICT", "任务上下文已变化，请刷新后重试。", 409
                )
            current_cursor = current["robot_state"].get("last_event_sequence", -1)
            candidate_cursor = candidate["robot_state"].get("last_event_sequence", -1)
            # Equal/already-applied event cursors retain the committed semantic
            # plan and terminal state. Newer verified observations may advance
            # the graph; an older response must never undo a handoff.
            graph = copy.deepcopy(candidate if candidate_cursor > current_cursor else current)
            if not set(current["completed_goal_ids"]).issubset(graph["completed_goal_ids"]):
                raise BusinessError(
                    "SPATIAL_INVALID_EXECUTION_EVENT", "导航反馈丢失已核验交接，请暂停并核验。", 409
                )
            if current_cursor == candidate_cursor:
                if candidate_cursor < 0 and current == original:
                    # A plan-only replan/cancel has no execution event yet.
                    graph = copy.deepcopy(candidate)
                elif (
                    candidate["status"] in {"TRANSPORT_PAUSED", "BLOCKED"}
                    and candidate["status"]
                    != (candidate["robot_state"].get("execution") or {}).get("status")
                    and current["status"] not in {"COMPLETED", "CANCELLED", "FAILED"}
                ):
                    # Preserve a server-side transport/map/infeasibility error,
                    # while keeping the latest observed pose and semantic plan.
                    graph["status"] = candidate["status"]
                    graph["recovery"] = copy.deepcopy(candidate.get("recovery"))
            overlays = max(
                (candidate["robot_state"], current["robot_state"]),
                key=lambda state: state.get("overlay_generation", 0),
            )
            if overlays.get("overlay_generation", 0) > graph["robot_state"].get(
                "overlay_generation", 0
            ):
                for key in ("overlay_ids", "overlay_generation"):
                    graph["robot_state"][key] = copy.deepcopy(overlays.get(key))
            if graph == current:
                return self.projection(copy.deepcopy(current))
            with poll_phase("locked_validate"):
                checked = TaskGraph.model_validate(graph).model_dump(mode="json")
            return self.projection(self.store.save(row, checked))

    def create_mission(
        self,
        request,
        *,
        conversation_id=None,
        operation_id,
        instruction="",
        business_grounding=None,
    ):
        from app.agent.spatial_agent import build_task_graph

        self._permission()
        context = ConversationContextService(self.db, self.user, self.config).open(conversation_id)
        row = self.db.get(AgentConversationContext, context.id)
        old = (row.pending_disambiguation or {}).get("spatial_task")
        signature = digest(request)
        if old and old["robot_state"].get("operation_id") == operation_id:
            if old["robot_state"].get("request_signature") != signature:
                raise BusinessError(
                    "SPATIAL_IDEMPOTENCY_CONFLICT", "同一操作不能替换任务参数。", 409
                )
            return self.projection(copy.deepcopy(old))
        if (
            old
            and old["robot_state"].get("execution")
            and old["status"] not in {"COMPLETED", "CANCELLED", "FAILED"}
        ):
            raise BusinessError(
                "SPATIAL_ACTIVE_TASK_EXISTS", "请完成或取消当前空间任务后再建立任务。", 409
            )
        frozen = MissionRequest.model_validate(request).model_dump(mode="json")
        snapshot = self.maps.snapshot(frozen["map_id"])
        # Browser-provided overlays cannot bypass registered PostGIS truth.
        frozen["dynamic_overlays"] = snapshot["dynamic_overlays"]
        plan = plan_mission(snapshot, frozen)
        plan["mission_id"] = "SM-" + digest([self.user.id, context.id, operation_id])[:28]
        graph = build_task_graph(
            plan,
            conversation_id=context.id,
            instruction=instruction,
            business_grounding=business_grounding,
        )
        graph["robot_state"].update(
            request=frozen,
            operation_id=operation_id,
            request_signature=signature,
            instruction=instruction,
            business_grounding=business_grounding,
            execution_boundary="ros2_nav2_simulation",
        )
        if plan["status"] != "READY":
            graph["robot_state"]["planning_result"] = plan
        return self._save(row, graph)

    def _fresh(self, graph):
        snapshot = self.maps.snapshot(graph["map_id"])
        if snapshot["revision"] != graph["map_revision"]:
            raise BusinessError(
                "SPATIAL_STALE_MAP", "地图修订已变化，任务已保留，请重新规划。", 409
            )
        return snapshot

    def _accept(self, graph, execution):
        from app.agent.spatial_agent import reduce_execution_event

        if (
            execution.get("mission_id") != graph["mission_id"]
            or execution.get("map_revision") != graph["map_revision"]
        ):
            raise BusinessError("SPATIAL_EXECUTION_MISMATCH", "导航反馈与任务身份不一致。", 409)
        registered = {dock["id"] for dock in self.maps.snapshot(graph["map_id"])["docks"]}
        for raw in sorted(execution.get("events", []), key=lambda item: item["sequence"]):
            try:
                event = ExecutionEvent.model_validate(raw).model_dump(mode="json")
            except ValueError as exc:
                raise BusinessError(
                    "SPATIAL_INVALID_EXECUTION_EVENT", "导航反馈不符合任务契约，请暂停并核验。", 409
                ) from exc
            if event["goal_id"] and event["goal_id"] not in registered:
                raise BusinessError(
                    "SPATIAL_UNREGISTERED_FEEDBACK", "导航反馈包含未注册站点。", 409
                )
            try:
                graph = reduce_execution_event(graph, event)
            except ValueError as exc:
                raise BusinessError(
                    "SPATIAL_INVALID_EXECUTION_EVENT", "导航反馈不符合任务契约，请暂停并核验。", 409
                ) from exc
            if event["type"] == "replanning" and event["details"].get("source") == (
                "server_semantic_replan"
            ):
                # The reducer has verified identity, remaining and completed
                # stops. A poll receiving the new plan before its publishing
                # command commits must retain that plan's user preference for
                # any later recovery, rather than restoring the old profile.
                request = graph["robot_state"].get("request")
                if request:
                    request["profile"] = graph["last_valid_plan"]["profile"]
                    request["completed_goal_ids"] = graph["completed_goal_ids"][:]
        # These values are observed odometry/action state, never elapsed-time UI estimates.
        graph["robot_state"]["execution"] = {
            **execution,
            "events": execution.get("events", [])[-128:],
        }
        graph["robot_state"].update(
            current_pose=execution.get("current_pose"), **execution.get("robot_state", {})
        )
        graph["status"] = execution["status"]
        return graph

    def _paused(self, row, graph, error):
        graph["status"] = "TRANSPORT_PAUSED"
        graph["recovery"] = {
            "code": error.code,
            "action": "retry_transport",
            "completed_goal_ids": graph["completed_goal_ids"],
            "safe_retry": True,
        }
        return self._save(row, graph)

    @poll_timed("poll_total")
    def poll(self, mission_id):
        with poll_phase("poll_permission"):
            self._permission()
        with poll_phase("poll_find"):
            row, graph = self.store.find(mission_id)
        try:
            with poll_phase("poll_map"):
                self._fresh(graph)
            if not graph["robot_state"].get("execution"):
                return self.projection(graph)
            last = graph["robot_state"].get("execution", {}).get("last_sequence", 0)
            with poll_phase("poll_transport"):
                execution = self.transport.request(
                    "GET", f"/missions/{mission_id}?after_sequence={last}"
                )
            with poll_phase("digest_before"):
                previous = digest(graph)
            with poll_phase("accept_events", count=diagnostic_count(execution.get("events"))):
                graph = self._accept(graph, execution)
            with poll_phase("digest_after"):
                changed = digest(graph) != previous
            return self._save(row, graph) if changed else self.projection(graph)
        except BusinessError as exc:
            if exc.code in {"SPATIAL_TRANSPORT_PAUSED", "SPATIAL_STALE_MAP"}:
                return self._paused(row, graph, exc)
            raise

    def _replan(self, graph, *, profile=None):
        snapshot = self._fresh(graph)
        token = None
        committed = False
        aborted = False
        if graph["robot_state"].get("execution"):
            token = uuid.uuid4().hex
            try:
                graph = self._prepare_replan(graph, token)
                # The robot is now held after the old action's actual terminal
                # result. Reload registered overlays after that barrier, then
                # plan from its fresh, stationary odometry instead of DB pose.
                snapshot = self._fresh(graph)
            except Exception:
                self._abort_replan(graph, token)
                raise
        try:
            # A candidate becomes last_valid_plan only after the bridge accepts
            # it. A lost/rejected commit must retain the observed source plan.
            result = self._plan_remaining(copy.deepcopy(graph), snapshot, profile=profile)
            if token is None:
                return result
            if result["status"] == "BLOCKED":
                paused = self._abort_replan(graph, token)
                aborted = True
                if paused is not None:
                    paused["status"] = result["status"]
                    paused["recovery"] = result["recovery"]
                    return paused
                return result
            result = self._accept(
                result,
                self.transport.request(
                    "POST",
                    f"/missions/{graph['mission_id']}/replan",
                    {"phase": "commit", "token": token, "plan": result["last_valid_plan"]},
                ),
            )
            committed = True
            return result
        finally:
            if token is not None and not committed and not aborted:
                self._abort_replan(graph, token)

    def _prepare_replan(self, graph, token):
        path = f"/missions/{graph['mission_id']}"
        execution = self.transport.request(
            "POST",
            path + "/replan",
            {
                "phase": "prepare",
                "token": token,
                "map_id": graph["map_id"],
                "map_revision": graph["map_revision"],
            },
        )
        deadline = time.monotonic() + 12.0
        lease_identity = None
        while True:
            accepted = self._accept(graph, execution)
            # Keep the caller's graph current for its paused/error save too.
            # Otherwise a handoff discovered while preparing could disappear
            # when a later transport failure is caught by command().
            graph.clear()
            graph.update(accepted)
            control = execution.get("replan_control") or {}
            if not isinstance(control, dict):
                raise BusinessError(
                    "SPATIAL_INVALID_EXECUTION_EVENT", "重规划暂停反馈不符合任务契约。", 409
                )
            if graph["status"] == "TRANSPORT_PAUSED" or not control:
                raise BusinessError(
                    "SPATIAL_TRANSPORT_PAUSED", "重规划暂停准备未完成，已保留任务与交接。", 503
                )
            if (
                control.get("token") != token
                or control.get("map_id") != graph["map_id"]
                or control.get("map_revision") != graph["map_revision"]
                or control.get("completed_goal_ids") != graph["completed_goal_ids"]
                or not isinstance(control.get("generation"), int)
                or isinstance(control.get("generation"), bool)
                or control["generation"] < 0
                or not isinstance(control.get("source_plan_hash"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", control["source_plan_hash"])
                or control["source_plan_hash"] != digest(graph["last_valid_plan"])
                or control["source_plan_hash"] != execution.get("plan_hash")
                or control.get("state") not in {"preparing", "ready"}
            ):
                raise BusinessError(
                    "SPATIAL_INVALID_EXECUTION_EVENT", "重规划暂停反馈与任务身份不一致。", 409
                )
            identity = (
                control["token"],
                control["map_id"],
                control["map_revision"],
                control["generation"],
                control["source_plan_hash"],
                tuple(control["completed_goal_ids"]),
            )
            if lease_identity is not None and identity != lease_identity:
                raise BusinessError(
                    "SPATIAL_INVALID_EXECUTION_EVENT", "重规划暂停身份已变化，请核验任务状态。", 409
                )
            lease_identity = identity
            if control["state"] == "ready":
                if not execution.get("current_pose"):
                    raise BusinessError(
                        "SPATIAL_INVALID_EXECUTION_EVENT", "停稳反馈缺少实际位姿。", 409
                    )
                return graph
            if time.monotonic() >= deadline:
                raise BusinessError(
                    "SPATIAL_TRANSPORT_PAUSED", "重规划等待停稳已超时，任务保持暂停。", 503
                )
            time.sleep(0.25)
            execution = self.transport.request("GET", path)

    def _abort_replan(self, graph, token):
        try:
            accepted = self._accept(
                graph,
                self.transport.request(
                    "POST",
                    f"/missions/{graph['mission_id']}/replan",
                    {"phase": "abort", "token": token},
                ),
            )
            graph.clear()
            graph.update(accepted)
            return graph
        except BusinessError:
            # This request only ends preparation while keeping the robot held.
            # If transport is lost, the ROS lease expires into a held pause;
            # preserve the original error instead of replaying any command.
            return None

    def _plan_remaining(self, graph, snapshot, *, profile=None):
        request = copy.deepcopy(graph["robot_state"]["request"])
        execution = graph["robot_state"].get("execution") or {}
        pose = execution.get("current_pose") or graph["robot_state"].get("current_pose")
        if pose:
            request["start_pose"] = pose
        request["completed_goal_ids"] = graph["completed_goal_ids"][:]
        request["dynamic_overlays"] = snapshot["dynamic_overlays"]
        if profile:
            request["profile"] = profile
        robot = execution.get("robot_state") or graph["robot_state"]
        if not pose and robot.get("pose"):
            request["start_pose"] = robot["pose"]
        request["constraints"]["battery_pct"] = robot.get(
            "battery_pct", request["constraints"]["battery_pct"]
        )
        plan = plan_mission(snapshot, request)
        plan["mission_id"] = graph["mission_id"]
        if plan["status"] != "READY":
            graph["status"] = "BLOCKED"
            graph["recovery"] = {
                "code": "REMAINING_MISSION_INFEASIBLE",
                "action": "wait_or_yield",
                "violations": plan["violations"],
                "completed_goal_ids": graph["completed_goal_ids"],
            }
            return graph
        graph["last_valid_plan"] = plan
        graph["robot_state"]["request"] = request
        if execution:
            return graph
        from app.agent.spatial_agent import build_task_graph

        rebuilt = build_task_graph(
            plan,
            conversation_id=graph["conversation_id"],
            instruction=graph["robot_state"].get("instruction", ""),
            business_grounding=graph["robot_state"].get("business_grounding"),
        )
        rebuilt["robot_state"].update(graph["robot_state"])
        return rebuilt

    def command(self, mission_id, command, args=None):
        self._permission()
        args = args or {}
        row, graph = self.store.find(mission_id)
        try:
            self._fresh(graph)
            if command == "start":
                if (graph.get("last_valid_plan") or {}).get("status") != "READY":
                    raise BusinessError(
                        "SPATIAL_READY_PLAN_REQUIRED", "请先取得可执行的任务规划。", 409
                    )
                # Refresh first-mile route from actual odometry before the first dispatch.
                health = self.transport.request("GET", "/health")
                if not health.get("ready") or health.get("map_revision") != graph["map_revision"]:
                    raise BusinessError("SPATIAL_TRANSPORT_PAUSED", "导航仿真尚未就绪。", 503)
                if graph["robot_state"].get("execution"):
                    graph = self._accept(
                        graph, self.transport.request("GET", f"/missions/{mission_id}")
                    )
                    if graph["status"] in {"TRANSPORT_PAUSED", "FAILED", "BLOCKED_LOW_BATTERY"}:
                        graph = self._replan(graph)
                    return self._save(row, graph)
                if not graph["robot_state"].get("execution"):
                    graph["robot_state"].update(health.get("robot_state", {}))
                    graph = self._replan(graph)
                if graph["status"] == "BLOCKED":
                    return self._save(row, graph)
                graph = self._accept(
                    graph, self.transport.request("POST", "/missions", graph["last_valid_plan"])
                )
            elif command in {"cancel", "handoff"}:
                if command == "cancel" and not graph["robot_state"].get("execution"):
                    graph["status"] = "CANCELLED"
                    graph["remaining_goal_ids"] = []
                else:
                    graph = self._accept(
                        graph,
                        self.transport.request("POST", f"/missions/{mission_id}/{command}", args),
                    )
            elif command == "replan":
                graph = self._replan(graph, profile=args.get("profile"))
            elif command == "obstacles":
                world = world_snapshot()
                scenario = next(s for s in world["scenarios"] if s["id"] == args["scenario_id"])
                rect = scenario["obstacles"][0]
                oid = rect["id"]
                self.transport.request("POST", "/obstacles", {**args, "id": oid})
                overlay_ids = graph["robot_state"].setdefault("overlay_ids", {})
                if args["operation"] == "add":
                    from app.spatial.geometry import rectangle_polygon

                    if oid not in overlay_ids:
                        generation = graph["robot_state"].get("overlay_generation", 0) + 1
                        graph["robot_state"]["overlay_generation"] = generation
                        overlay_ids[oid] = oid + ":" + digest([mission_id, generation])[:16]
                    self.maps.put_overlay(
                        graph["map_id"],
                        {
                            "id": overlay_ids[oid],
                            "kind": "obstacle",
                            "geometry": rectangle_polygon(rect),
                            "source": "ros2_simulation",
                            "reason": args["scenario_id"],
                            "ttl_s": 86400,
                        },
                    )
                else:
                    if oid in overlay_ids:
                        graph["robot_state"]["overlay_generation"] = (
                            graph["robot_state"].get("overlay_generation", 0) + 1
                        )
                    self.db.execute(
                        text(
                            "UPDATE warehouse_dynamic_overlays SET is_active=false "
                            "WHERE code=:code AND warehouse_map_id="
                            "(SELECT id FROM warehouse_maps WHERE code=:map)"
                        ),
                        {"code": overlay_ids.pop(oid, oid), "map": graph["map_id"]},
                    )
                self.db.commit()
                # Preserve an arrived handoff. Replan after its verified completion.
                observed = self.transport.request("GET", f"/missions/{mission_id}")
                graph = self._accept(graph, observed)
                if observed["status"] not in {
                    "AWAITING_HANDOFF",
                    "CHARGING",
                    "COMPLETED",
                    "CANCELLED",
                }:
                    graph = self._replan(graph)
            else:
                raise BusinessError("SPATIAL_COMMAND_UNKNOWN", "未注册的任务操作。", 422)
            return self._save(row, graph)
        except BusinessError as exc:
            if exc.code in {"SPATIAL_TRANSPORT_PAUSED", "SPATIAL_STALE_MAP"}:
                return self._paused(row, graph, exc)
            raise

    def episode(self, mission_id):
        from app.agent.spatial_agent import export_episode

        self._permission()
        _, graph = self.store.find(mission_id)
        return export_episode(graph)

    def query(self, message, *, conversation, operation_id, request_id):
        from app.agent.spatial_agent import ground_instruction
        from app.schemas.agent import AgentQueryResponse

        previous = (conversation.pending_disambiguation or {}).get("spatial_task")
        snapshot = self.maps.snapshot("MB-EMB-LAB-03")
        grounded = ground_instruction(
            message,
            snapshot,
            previous_graph=previous,
            business_resolver=lambda instruction: SpatialBusinessGrounding(
                self.db, self.user
            ).resolve(
                instruction,
                snapshot,
                selected_project_id=conversation.selected_project_id,
                selected_product_revision_id=conversation.selected_product_revision_id,
            ),
        )
        if not grounded.get("handled"):
            return None
        self._permission()
        action = grounded["action"]
        if action in {"clarify", "query"}:
            if action == "query" and previous and not grounded.get("args", {}).get("query"):
                result = self.poll(previous["mission_id"])
                answer = f"空间任务状态：{result['task_graph']['status']}。进度来自导航反馈。"
                return AgentQueryResponse(
                    answer=answer,
                    narrative=answer,
                    intent="spatial_mission",
                    entities={"spatial_mission": result},
                    request_id=request_id,
                    conversation_id=conversation.id,
                )
            if action == "query" and grounded.get("args", {}).get("query"):
                grounded["result"] = self.maps.query(snapshot["map_id"], grounded["args"]["query"])
            answer = (
                grounded.get("clarification") or "空间查询已读取注册地图，请在实验仓查看语义图层。"
            )
            return AgentQueryResponse(
                answer=answer,
                narrative=answer,
                intent="spatial_query",
                entities={"spatial_query": grounded},
                request_id=request_id,
                conversation_id=conversation.id,
            )
        if action in {"plan", "execute"}:
            if previous and grounded.get("args", {}).get("existing_mission"):
                result = self.projection(previous)
            else:
                result = self.create_mission(
                    grounded["request"],
                    conversation_id=conversation.id,
                    operation_id=operation_id,
                    instruction=message,
                    business_grounding=grounded.get("business_grounding"),
                )
            if action == "execute":
                result = self.command(result["mission_id"], "start")
        else:
            if not previous:
                raise BusinessError("SPATIAL_CONTEXT_REQUIRED", "请先建立空间任务。", 409)
            result = self.command(previous["mission_id"], action, grounded.get("args"))
        status = result["task_graph"]["status"]
        done = len(result["task_graph"]["completed_goal_ids"])
        remaining = len(result["task_graph"]["remaining_goal_ids"])
        answer = (
            f"空间任务状态：{status}。已核验 {done} 个站点，剩余 {remaining} 个。"
            "到站后扫描核验交接，完成后返回 HOME。"
        )
        return AgentQueryResponse(
            answer=answer,
            narrative=answer,
            intent="spatial_mission",
            entities={"spatial_mission": result},
            request_id=request_id,
            conversation_id=conversation.id,
        )
