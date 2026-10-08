"""Real planner, PostgreSQL sessions and production ROS callback preparation."""

import copy
import importlib
import threading
import types
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session
from test_spatial_integration import (
    _execution,
    _ready_preparation,
    _replay_event,
    _started_replay,
)
from test_spatial_integration import (
    setup as _setup,
)
from test_spatial_mission_concurrency import (
    _business,
)
from test_spatial_mission_concurrency import (
    isolated_owner as _isolated_owner,
)
from test_spatial_mission_concurrency import (
    postgis_engine as _postgis_engine,
)

import app.agent.spatial_integration as integration_module
from app.agent.conversation import ConversationContextService
from app.agent.spatial_integration import SpatialAgentIntegration
from app.core.config import Settings
from app.core.exceptions import BusinessError
from app.models import AgentConversationContext, User
from app.services.spatial_mission_store import SpatialMissionStore
from app.services.spatial_transport import SpatialRobotTransport

setup = _setup
isolated_owner = _isolated_owner
postgis_engine = _postgis_engine


@pytest.mark.parametrize(
    "payload",
    [[], {"error": []}, {"error": {}}, {"error": 1}, {"error": "arbitrary remote text"}],
)
def test_bridge_rejections_only_relay_allowlisted_strings(monkeypatch, payload):
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(409, json=payload))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(BusinessError) as caught:
        SpatialRobotTransport("http://bridge.invalid").request("POST", "/missions/test/replan", {})
    assert caught.value.code == "SPATIAL_BRIDGE_REJECTED"
    assert caught.value.status_code == 409 and caught.value.details == {}
    assert "remote" not in caught.value.message


def test_bridge_pose_guard_reason_is_typed_without_remote_details(monkeypatch):
    original = httpx.Client
    code = "OBSERVED_START_POSE_CHANGED_REPLAN_REQUIRED"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(409, json={"error": code, "details": "untrusted body"})
    )
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(BusinessError) as caught:
        SpatialRobotTransport("http://bridge.invalid").request("POST", "/missions/test/replan", {})
    assert caught.value.details == {"bridge_error_code": code}
    assert "untrusted" not in str(caught.value)


@pytest.mark.parametrize(
    "changed",
    [
        "token",
        "generation",
        "source_plan_hash",
        "map_id",
        "map_revision",
        "completed_goal_ids",
        "current_pose",
        "malformed_control",
        "actual_plan_hash",
    ],
)
def test_changed_or_malformed_ready_identity_aborts_without_planning(setup, monkeypatch, changed):
    service, _, graph, ledger = _started_replay(setup, "请重新规划剩余路线")
    phases = []
    ready = None

    class ChangedTransport:
        def request(self, method, path, body=None):
            nonlocal ready
            if method == "POST":
                phases.append(body["phase"])
                if body["phase"] == "prepare":
                    ready = _ready_preparation(graph, ledger, body)
                    ready["replan_control"]["state"] = "preparing"
                    return copy.deepcopy(ready)
                assert body["phase"] == "abort"
                value = _execution(graph, [*ledger, _replay_event(graph, 7, "transport_paused")])
                value["status"] = "TRANSPORT_PAUSED"
                return value
            assert method == "GET"
            ready["replan_control"]["state"] = "ready"
            if changed == "current_pose":
                ready[changed] = None
            elif changed == "malformed_control":
                ready["replan_control"] = ["wrong"]
            elif changed == "actual_plan_hash":
                ready["plan_hash"] = "a" * 64
            elif changed == "generation":
                ready["replan_control"][changed] += 1
            elif changed == "completed_goal_ids":
                ready["replan_control"][changed] = []
            elif changed == "source_plan_hash":
                ready["replan_control"][changed] = "a" * 64
            else:
                ready["replan_control"][changed] = "changed"
            return copy.deepcopy(ready)

    service.transport = ChangedTransport()
    monkeypatch.setattr(integration_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        integration_module,
        "plan_mission",
        lambda *args: pytest.fail("Must validate stationary lease identity before planning"),
    )
    with pytest.raises(BusinessError) as caught:
        service.command(graph["mission_id"], "replan")
    assert caught.value.code == "SPATIAL_INVALID_EXECUTION_EVENT"
    assert phases == ["prepare", "abort"]
    assert graph["completed_goal_ids"] == ["P-IC"]


@pytest.mark.parametrize(
    "failure", ["timeout", "planner_exception", "infeasible", "commit_guard", "commit_transport"]
)
def test_preparation_failure_keeps_robot_held_and_completed_memory(setup, monkeypatch, failure):
    service, _, graph, ledger = _started_replay(setup, "请重新规划剩余路线")
    phases = []

    class HeldTransport:
        def request(self, method, path, body=None):
            assert method == "POST"
            phases.append(body["phase"])
            if body["phase"] == "prepare":
                value = _ready_preparation(graph, ledger, body)
                if failure == "timeout":
                    value["replan_control"]["state"] = "preparing"
                return value
            if body["phase"] == "commit":
                if failure == "commit_transport":
                    raise BusinessError("SPATIAL_TRANSPORT_PAUSED", "transport lost", 503)
                raise BusinessError("SPATIAL_BRIDGE_REJECTED", "safe guard", 409)
            assert body["phase"] == "abort"
            value = _execution(graph, [*ledger, _replay_event(graph, 7, "transport_paused")])
            value["status"] = "TRANSPORT_PAUSED"
            return value

    service.transport = HeldTransport()
    if failure == "timeout":
        clock = iter([0, 13])
        monkeypatch.setattr(
            integration_module,
            "time",
            types.SimpleNamespace(monotonic=lambda: next(clock, 13), sleep=lambda _: None),
        )
    elif failure == "planner_exception":

        def broken_planner(*args):
            raise ValueError("deterministic planner exception")

        monkeypatch.setattr(integration_module, "plan_mission", broken_planner)
    elif failure == "infeasible":
        monkeypatch.setattr(
            integration_module,
            "plan_mission",
            lambda *args: {"status": "BLOCKED", "violations": ["NO_ROUTE"]},
        )
    if failure in {"timeout", "infeasible", "commit_transport"}:
        value = service.command(graph["mission_id"], "replan")
        assert value["task_graph"]["status"] == (
            "BLOCKED" if failure == "infeasible" else "TRANSPORT_PAUSED"
        )
        assert value["task_graph"]["completed_goal_ids"] == ["P-IC"]
        assert not value["inventory_written"]
        assert value["mission_plan"] == graph["last_valid_plan"]
    else:
        with pytest.raises(ValueError if failure == "planner_exception" else BusinessError):
            service.command(graph["mission_id"], "replan")
    assert phases == (
        ["prepare", "commit", "abort"]
        if failure in {"commit_guard", "commit_transport"}
        else ["prepare", "abort"]
    )


def test_lost_commit_receipt_retains_actual_accepted_plan_when_abort_observes_it(setup):
    db, _ = setup
    service, row, graph, ledger = _started_replay(setup, "请重新规划剩余路线，选择最安全路线")
    accepted_ledger = copy.deepcopy(ledger)
    phases, accepted_plan = [], None

    class LostReceiptTransport:
        def request(self, method, path, body=None):
            nonlocal accepted_plan
            assert method == "POST"
            phases.append(body["phase"])
            if body["phase"] == "prepare":
                return _ready_preparation(graph, ledger, body)
            if body["phase"] == "commit":
                accepted_plan = copy.deepcopy(body["plan"])
                accepted_ledger.append(
                    _replay_event(
                        graph, 7, "replanning", source="server_semantic_replan", plan=accepted_plan
                    )
                )
                raise BusinessError(
                    "SPATIAL_TRANSPORT_PAUSED", "receipt lost after acceptance", 503
                )
            assert body["phase"] == "abort"
            accepted_ledger.append(
                _replay_event(graph, 8, "transport_paused", reason="REPLAN_COMMIT_RECEIPT_ABORTED")
            )
            value = _execution(graph, accepted_ledger)
            value["status"] = "TRANSPORT_PAUSED"
            return value

    service.transport = LostReceiptTransport()
    result = service.command(graph["mission_id"], "replan", {"profile": "safest"})
    assert result["mission_plan"] == accepted_plan != graph["last_valid_plan"]
    assert result["task_graph"]["status"] == "TRANSPORT_PAUSED"
    assert result["task_graph"]["completed_goal_ids"] == ["P-IC"]
    assert result["task_graph"]["robot_state"]["last_event_sequence"] == 8
    db.expire_all()
    assert (
        db.get(AgentConversationContext, row.id).pending_disambiguation["spatial_task"][
            "last_valid_plan"
        ]
        == accepted_plan
    )
    assert phases == ["prepare", "commit", "abort"]


def test_handoff_observed_while_preparing_survives_later_transport_pause(setup, monkeypatch):
    db, _ = setup
    service, row, graph, ledger = _started_replay(setup, "保留已完成交接，重新规划")
    later = [
        *ledger,
        _replay_event(graph, 7, "arrived", "P-SENSOR"),
        _replay_event(graph, 8, "scan_verified", "P-SENSOR", scan_code="P-SENSOR"),
        _replay_event(graph, 9, "handoff_verified", "P-SENSOR"),
    ]
    phases = []

    class LaterHandoffTransport:
        def request(self, method, path, body=None):
            assert method == "POST"
            phases.append(body["phase"])
            if body["phase"] == "prepare":
                value = _ready_preparation(graph, later, body)
                value["replan_control"].update(
                    state="preparing", completed_goal_ids=["P-IC", "P-SENSOR"]
                )
                return value
            assert body["phase"] == "abort"
            value = _execution(graph, [*later, _replay_event(graph, 10, "transport_paused")])
            value["status"] = "TRANSPORT_PAUSED"
            return value

    service.transport = LaterHandoffTransport()
    clock = iter([0, 13])
    monkeypatch.setattr(
        integration_module, "time", types.SimpleNamespace(monotonic=lambda: next(clock, 13))
    )
    value = service.command(graph["mission_id"], "replan")
    assert value["task_graph"]["status"] == "TRANSPORT_PAUSED"
    assert value["task_graph"]["completed_goal_ids"] == ["P-IC", "P-SENSOR"]
    assert value["task_graph"]["robot_state"]["last_event_sequence"] == 10
    db.expire_all()
    stored = db.get(AgentConversationContext, row.id).pending_disambiguation["spatial_task"]
    assert stored["completed_goal_ids"] == ["P-IC", "P-SENSOR"]
    assert stored["last_valid_plan"] == graph["last_valid_plan"]
    assert phases == ["prepare", "abort"]


@pytest.fixture
def bridge_components(monkeypatch):
    ros_root = Path(__file__).resolve().parents[2] / "robot_bridge" / "ros2"
    monkeypatch.syspath_prepend(str(ros_root))
    monkeypatch.syspath_prepend(str(ros_root / "tests"))
    lifecycle = importlib.import_module("test_navigation_action_lifecycle")
    protocol = importlib.import_module("test_replan_preparation")
    contracts = importlib.import_module("materialbrain_ros2.contracts")
    return lifecycle, protocol, contracts


@pytest.mark.parametrize(
    "phrase",
    [
        "当前位置重新规划，改为最快路线，已完成的不要重做。",
        "请重新规划剩余路线，选择最安全路线，保留已完成交接。",
        "V4 任务重规划，使用防静电路线，已核验交接保留。",
    ],
)
def test_actual_stationary_pose_replan_with_five_stale_postgres_poll_sessions(
    isolated_owner, bridge_components, monkeypatch, phrase
):
    db, users = isolated_owner
    lifecycle, protocol, contracts = bridge_components
    service, row, graph, ledger = _started_replay(isolated_owner, phrase)
    before_business = _business(db)
    cached_pose = {"x": 3.4482236819692744, "y": 3.735401843439918, "yaw": 0.23288974390419276}
    held_pose = {"x": 4.324511961876515, "y": 4.228595345962005, "yaw": 0.816364633897861}
    graph["robot_state"]["current_pose"] = cached_pose
    graph["robot_state"]["execution"]["current_pose"] = cached_pose
    snapshot = service.maps.snapshot(graph["map_id"])
    source = integration_module.plan_mission(
        snapshot,
        {
            **graph["robot_state"]["request"],
            "start_pose": cached_pose,
            "completed_goal_ids": ["P-IC"],
        },
    )
    source["mission_id"] = graph["mission_id"]
    graph["last_valid_plan"] = source
    service._save(row, graph)
    old_version = row.context_version
    completed_keys = {
        node["id"]: node["idempotency_key"]
        for node in graph["nodes"]
        if node["status"] == "SUCCEEDED"
    }
    owner_id, row_id = users[0].id, row.id
    db.rollback()
    bridge = lifecycle.BridgeHarness()
    bridge.map, bridge.plan, bridge.pose = snapshot, source, copy.deepcopy(cached_pose)
    bridge.registry = {dock["id"]: dock for dock in snapshot["docks"]}
    mission = contracts.Mission(source, snapshot, bridge.world)
    mission.status, mission.pose = "NAVIGATING", copy.deepcopy(cached_pose)
    mission.battery = graph["robot_state"]["battery_pct"]
    mission.payload = graph["robot_state"]["payload_kg"]
    mission.events, mission.sequence = copy.deepcopy(ledger), ledger[-1]["sequence"]
    bridge.missions, bridge.active = {mission.id: mission}, mission.id
    bridge.odom_messages, bridge.odom_velocity, bridge.last_telemetry = 0, None, 0
    bridge.replan_preparation = bridge.last_replan_commit = None
    bridge.navigate("P-SENSOR", through=True)
    old = bridge.nav_through.accept()
    # This is the exact unchanged production guard that rejected 9a's moving
    # start: both cached and current pose are copied from that failed event chain.
    with pytest.raises(
        contracts.ContractError, match="OBSERVED_START_POSE_CHANGED_REPLAN_REQUIRED"
    ):
        contracts.route_waypoints(
            snapshot, source["segments"][0], held_pose, "P-SENSOR", bridge.world
        )
    barrier = threading.Barrier(6, timeout=30)
    committed = threading.Event()
    thread_lock = threading.Lock()
    misses, loaded_versions, phases = Counter(), [], []
    original_find, original_save = SpatialMissionStore.find, SpatialMissionStore.save
    original_planner = integration_module.plan_mission
    controller_gets = 0

    def stale_find(store, mid):
        value = original_find(store, mid)
        with thread_lock:
            loaded_versions.append(value[0].context_version)
        barrier.wait()
        return value

    def record_save(store, candidate_row, candidate):
        try:
            result = original_save(store, candidate_row, candidate)
        except BusinessError as error:
            if error.code == "AGENT_CONVERSATION_CONFLICT":
                with thread_lock:
                    misses[threading.current_thread().name] += 1
            raise
        if threading.current_thread().name == "held-control":
            committed.set()
        return result

    def stationary_planner(observed_map, request):
        assert bridge.navigation_action is None and bridge.replan_ready(mission)
        assert request["start_pose"] == bridge.pose == held_pose
        assert request["completed_goal_ids"] == ["P-IC"]
        return original_planner(observed_map, request)

    class ActualCallbackTransport:
        def request(self, method, path, body=None):
            nonlocal controller_gets
            if method == "POST":
                with bridge.lock:
                    phases.append(body["phase"])
                    return bridge.dispatch("replan", {**body, "mission_id": mission.id})
            if threading.current_thread().name == "held-control":
                with bridge.lock:
                    controller_gets += 1
                    bridge.pose = copy.deepcopy(held_pose)
                    if controller_gets == 1:
                        old.acknowledge_cancel()
                        protocol.stopped_observations(bridge)
                        assert bridge.navigation_action is not None
                    else:
                        old.finish()
                        protocol.stopped_observations(bridge)
                    return bridge.mission_snapshot(mission)
            assert committed.wait(30)
            with bridge.lock:
                after = int(path.partition("after_sequence=")[2] or 0)
                return bridge.mission_snapshot(mission, after)

    monkeypatch.setattr(SpatialMissionStore, "find", stale_find)
    monkeypatch.setattr(SpatialMissionStore, "save", record_save)
    monkeypatch.setattr(integration_module, "plan_mission", stationary_planner)
    monkeypatch.setattr(integration_module.time, "sleep", lambda _: None)

    def run(index):
        threading.current_thread().name = "held-control" if index == 5 else f"held-poll-{index}"
        with Session(db.get_bind()) as independent:
            owner = independent.get(User, owner_id)
            integration = SpatialAgentIntegration(
                independent, owner, transport=ActualCallbackTransport()
            )
            if index < 5:
                return integration.poll(graph["mission_id"])
            conversation = ConversationContextService(independent, owner, Settings()).open(row_id)
            response = integration.query(
                phrase, conversation=conversation, operation_id="held-pose", request_id="p3-held-pg"
            )
            assert response.model_call_count == 0 and response.intent == "spatial_mission"
            return response.entities["spatial_mission"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(run, index) for index in range(6)]
        results = [future.result(timeout=45) for future in futures]
    assert loaded_versions == [old_version] * 6 and len(misses) == 5
    assert phases == ["prepare", "commit"] and controller_gets == 2
    assert old.cancel_requests == 1 and bridge.navigation_action is None
    assert bridge.hold_pub.messages[-1].data
    db.expire_all()
    final = db.get(AgentConversationContext, row_id).pending_disambiguation["spatial_task"]
    for result in results:
        assert result["task_graph"]["completed_goal_ids"] == ["P-IC"]
        assert result["mission_plan"] == mission.plan
        assert result["task_graph"]["robot_state"]["current_pose"] == held_pose
        assert not result["inventory_written"]
    assert final["last_valid_plan"] == mission.plan
    assert final["robot_state"]["last_event_sequence"] == mission.sequence
    assert completed_keys == {
        node["id"]: node["idempotency_key"]
        for node in final["nodes"]
        if node["id"] in completed_keys and node["status"] == "SUCCEEDED"
    }
    assert final["robot_state"]["request"]["profile"] == mission.plan["profile"]
    assert final["map_revision"] == graph["map_revision"] and _business(db) == before_business
