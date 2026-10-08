"""Stationary replan protocol over the production bridge and ROS callbacks."""

import copy
import json
import math
import time
import types

import pytest
from test_navigation_action_lifecycle import (
    BridgeHarness,
    GoalStatus,
    PoseStamped,
    bridge_module,
)

from materialbrain_ros2.contracts import ContractError, Mission


@pytest.fixture
def preparing_bridge():
    bridge = BridgeHarness()
    bridge.odom_messages = 0
    bridge.last_telemetry = 0
    bridge.odom_velocity = None
    bridge.replan_preparation = None
    bridge.last_replan_commit = None
    bridge.navigate("P-SENSOR", through=True)
    old = bridge.nav_through.accept()
    return bridge, old


def preparation_body(bridge, token="a" * 32):
    mission = bridge.missions[bridge.active]
    return {
        "mission_id": mission.id,
        "phase": "prepare",
        "token": token,
        "map_id": mission.plan["map_id"],
        "map_revision": mission.revision,
    }


def stopped_observations(bridge, *, held=True, v=0.0, w=0.0):
    bridge.on_telemetry(
        types.SimpleNamespace(
            data=json.dumps({"held": held, "v_mps": v, "w_rps": w, "sim_time_s": 0})
        )
    )
    pose = PoseStamped().pose
    pose.position.x, pose.position.y = bridge.pose["x"], bridge.pose["y"]
    pose.orientation.z = math.sin(bridge.pose["yaw"] / 2)
    pose.orientation.w = math.cos(bridge.pose["yaw"] / 2)
    bridge.on_odom(
        types.SimpleNamespace(
            pose=types.SimpleNamespace(pose=pose),
            twist=types.SimpleNamespace(
                twist=types.SimpleNamespace(
                    linear=types.SimpleNamespace(x=v),
                    angular=types.SimpleNamespace(z=w),
                )
            ),
            header=types.SimpleNamespace(
                frame_id="odom", stamp=types.SimpleNamespace(sec=0, nanosec=0)
            ),
            child_frame_id="base_link",
        )
    )


def make_ready(bridge, old):
    body = preparation_body(bridge)
    bridge.dispatch("replan", body)
    old.acknowledge_cancel()
    stopped_observations(bridge)
    assert not bridge.replan_ready(bridge.missions[bridge.active])
    old.finish()
    stopped_observations(bridge)
    assert bridge.replan_ready(bridge.missions[bridge.active])
    return body


def commit_body(bridge, body, *, same_plan=False):
    plan = copy.deepcopy(bridge.plan)
    if not same_plan:
        plan["profile"] = "safest"
    return {
        "mission_id": bridge.active,
        "phase": "commit",
        "token": body["token"],
        "plan": plan,
    }


def test_prepare_waits_for_actual_terminal_and_fresh_stopped_odometry(preparing_bridge):
    bridge, old = preparing_bridge
    mission = bridge.missions[bridge.active]
    body = preparation_body(bridge)
    first = bridge.dispatch("replan", body)
    generation = bridge.generation
    assert first["replan_control"]["state"] == "preparing"
    assert first["plan_hash"] == first["replan_control"]["source_plan_hash"]
    old.acknowledge_cancel(return_code=3)
    stopped_observations(bridge)
    for _ in range(3):
        mission.overlay_valid_path_ready = (
            True  # A retired-path delivery cannot release hold.
        )
        mission.overlay_hold_since = time.monotonic() - 1
        bridge.tick()
        assert len(bridge.nav_through.sent) == 1
        assert bridge.hold_pub.messages[-1].data
        assert bridge.navigation_action is not None
        assert not bridge.replan_ready(mission)
    old.finish(GoalStatus.STATUS_SUCCEEDED)
    stopped_observations(bridge)
    second = bridge.dispatch("replan", body)
    assert second["replan_control"]["state"] == "ready"
    assert bridge.generation == generation and old.cancel_requests == 1
    bridge.tick()
    assert bridge.navigation_action is None and len(bridge.nav_through.sent) == 1
    assert mission.completed == ["P-IC"]


@pytest.mark.parametrize("same_plan", [False, True])
def test_commit_dispatches_validated_plan_once_and_duplicate_does_not_reopen(
    preparing_bridge, same_plan
):
    bridge, old = preparing_bridge
    body = make_ready(bridge, old)
    commit = commit_body(bridge, body, same_plan=same_plan)
    value = bridge.dispatch("replan", commit)
    mission = bridge.missions[bridge.active]
    semantic = [
        e
        for e in value["events"]
        if e["details"].get("source") == "server_semantic_replan"
    ]
    assert len(semantic) == 1 and semantic[0]["details"]["plan"] == commit["plan"]
    sequence = mission.sequence
    bridge.tick()
    assert len(bridge.nav_through.sent) == 2 and bridge.hold_pub.messages[-1].data
    bridge.nav_through.accept(1)
    path_pose = PoseStamped()
    path_pose.pose.position.x, path_pose.pose.position.y = (
        bridge.pose["x"],
        bridge.pose["y"],
    )
    bridge.on_path(types.SimpleNamespace(poses=[path_pose]))
    mission.overlay_hold_since = time.monotonic() - 1
    bridge.tick()
    assert not bridge.hold_pub.messages[-1].data
    generation = bridge.generation
    result = bridge.dispatch("replan", commit)
    assert bridge.generation == generation
    assert mission.sequence == sequence + 2  # dispatch feedback + validated-path resume
    assert len(bridge.nav_through.sent) == 2 and result["completed_goal_ids"] == [
        "P-IC"
    ]


@pytest.mark.parametrize(
    "v,w,held", [(0.001, 0, True), (0, 0.001, True), (0, 0, False)]
)
def test_non_stationary_or_unheld_feedback_cannot_commit(preparing_bridge, v, w, held):
    bridge, old = preparing_bridge
    body = make_ready(bridge, old)
    stopped_observations(bridge, held=held, v=v, w=w)
    with pytest.raises(ContractError, match="REPLAN_HOLD_NOT_CONFIRMED"):
        bridge.dispatch("replan", commit_body(bridge, body))
    assert bridge.replan_preparation and bridge.hold_pub.messages[-1].data
    assert bridge.missions[bridge.active].completed == ["P-IC"]


@pytest.mark.parametrize("field", ["last_odom", "last_telemetry", "odom_messages"])
def test_preparation_requires_post_prepare_and_fresh_sensors(preparing_bridge, field):
    bridge, old = preparing_bridge
    body = make_ready(bridge, old)
    setattr(bridge, field, 0)
    with pytest.raises(ContractError, match="REPLAN_HOLD_NOT_CONFIRMED"):
        bridge.dispatch("replan", commit_body(bridge, body))


@pytest.mark.parametrize(
    "key,value",
    [
        ("token", "other"),
        ("token", []),
        ("map_id", "wrong"),
        ("map_revision", "f" * 63),
    ],
)
def test_malformed_prepare_is_non_mutating(preparing_bridge, key, value):
    bridge, old = preparing_bridge
    body = {**preparation_body(bridge), key: value}
    generation = bridge.generation
    with pytest.raises(ContractError, match="INVALID_REPLAN_PREPARATION"):
        bridge.dispatch("replan", body)
    assert bridge.generation == generation and old.cancel_requests == 0
    assert bridge.replan_preparation is None


@pytest.mark.parametrize(
    "field",
    [
        "mission_id",
        "map_id",
        "map_revision",
        "source_plan_hash",
        "completed_goal_ids",
        "generation",
    ],
)
def test_changed_bound_identity_cannot_commit(preparing_bridge, field):
    bridge, old = preparing_bridge
    body = make_ready(bridge, old)
    preparation = bridge.replan_preparation
    value = getattr(preparation, field)
    setattr(
        preparation,
        field,
        value + 1
        if isinstance(value, int)
        else ("changed",)
        if isinstance(value, tuple)
        else "changed",
    )
    with pytest.raises(ContractError, match="REPLAN_PREPARATION_IDENTITY_CHANGED"):
        bridge.dispatch("replan", commit_body(bridge, body))
    assert bridge.hold_pub.messages[-1].data


def test_lost_commit_receipt_abort_holds_latest_accepted_plan_and_waits_for_terminal(
    preparing_bridge,
):
    bridge, old = preparing_bridge
    body = make_ready(bridge, old)
    commit = commit_body(bridge, body)
    bridge.dispatch("replan", commit)
    bridge.tick()
    current = bridge.nav_through.accept(1)
    bridge.dispatch(
        "replan",
        {"mission_id": bridge.active, "phase": "abort", "token": body["token"]},
    )
    mission = bridge.missions[bridge.active]
    assert mission.plan == commit["plan"] and mission.completed == ["P-IC"]
    assert mission.status == "TRANSPORT_PAUSED" and bridge.hold_pub.messages[-1].data
    assert current.cancel_requests == 1 and bridge.navigation_action is not None
    current.acknowledge_cancel()
    bridge.tick()
    assert bridge.navigation_action is not None and len(bridge.nav_through.sent) == 2
    current.finish()
    bridge.tick()
    assert bridge.navigation_action is None and len(bridge.nav_through.sent) == 2
    with pytest.raises(ContractError, match="REPLAN_PREPARATION_REQUIRED"):
        bridge.dispatch("replan", commit)


@pytest.mark.parametrize("status", ["AWAITING_HANDOFF", "CHARGING"])
def test_lost_commit_receipt_abort_keeps_registered_arrival_proof(
    preparing_bridge, status
):
    bridge, old = preparing_bridge
    body = make_ready(bridge, old)
    commit = commit_body(bridge, body)
    bridge.dispatch("replan", commit)
    mission = bridge.missions[bridge.active]
    mission.status, mission.current_goal_id = status, "P-SENSOR"
    events = copy.deepcopy(mission.events)
    value = bridge.dispatch(
        "replan",
        {"mission_id": bridge.active, "phase": "abort", "token": body["token"]},
    )
    expected = status if status == "AWAITING_HANDOFF" else "TRANSPORT_PAUSED"
    assert value["status"] == expected and value["current_goal_id"] == "P-SENSOR"
    assert mission.plan == commit["plan"] and mission.completed == ["P-IC"]
    assert mission.events[: len(events)] == events
    assert bridge.hold_pub.messages[-1].data


def test_lost_commit_receipt_abort_cannot_resume_after_registered_charge_deadline(
    preparing_bridge,
):
    bridge, old = preparing_bridge
    charger = {"id": "CHARGER", "pose": copy.deepcopy(bridge.world["charger"])}
    bridge.registry["CHARGER"] = charger
    bridge.map["docks"].append(charger)
    bridge.map["route_graph"]["nodes"].append({"id": "CHARGER", **charger["pose"]})
    bridge.map["route_graph"]["edges"].extend(
        [
            {"id": "START:CHARGER", "from": "START", "to": "CHARGER", "width_m": 2},
            {
                "id": "CHARGER:P-SENSOR",
                "from": "CHARGER",
                "to": "P-SENSOR",
                "width_m": 2,
            },
        ]
    )
    body = make_ready(bridge, old)
    commit = commit_body(bridge, body)
    plan = commit["plan"]
    plan["ordered_goal_ids"].insert(1, "CHARGER")
    plan["segments"].insert(
        0,
        {
            "to_goal_id": "CHARGER",
            "node_ids": ["START", "CHARGER"],
            "edge_ids": ["START:CHARGER"],
        },
    )
    plan["segments"][1] = {
        "to_goal_id": "P-SENSOR",
        "node_ids": ["CHARGER", "P-SENSOR"],
        "edge_ids": ["CHARGER:P-SENSOR"],
    }
    bridge.dispatch("replan", commit)
    bridge.tick()
    charging_action = bridge.nav_through.accept(1)
    bridge.pose = copy.deepcopy(bridge.registry["CHARGER"]["pose"])
    stopped_observations(bridge)
    bridge.telemetry["sim_time_s"] = 5
    charging_action.finish(GoalStatus.STATUS_SUCCEEDED)
    mission = bridge.missions[bridge.active]
    assert mission.status == "CHARGING" and mission.current_goal_id == "CHARGER"
    assert mission.events[-2]["type"] == "arrived"
    assert mission.events[-1]["details"]["state"] == "started"
    arrival_events = copy.deepcopy(mission.events)
    charging_until = mission.charging_until
    state = (mission.queue[:], mission.segment_cursor, mission.battery, mission.payload)
    hold_count = len(bridge.hold_pub.messages)
    value = bridge.dispatch(
        "replan",
        {"mission_id": bridge.active, "phase": "abort", "token": body["token"]},
    )
    # An old path callback or the automatic charging timer cannot release this
    # stop after the receipt was lost, even when another goal is reachable.
    mission.overlay_valid_path_ready = True
    mission.overlay_hold_since = time.monotonic() - 1
    for delta in (-0.01, 0.01, 100):
        bridge.telemetry["sim_time_s"] = charging_until + delta
        bridge.tick()
    assert value["status"] == mission.status == "TRANSPORT_PAUSED"
    assert mission.current_goal_id == "CHARGER" and mission.completed == ["P-IC"]
    assert (
        mission.plan == plan and mission.events[: len(arrival_events)] == arrival_events
    )
    assert (
        mission.queue,
        mission.segment_cursor,
        mission.battery,
        mission.payload,
    ) == state
    assert mission.events[-1]["details"]["reason"] == "REPLAN_COMMIT_RECEIPT_ABORTED"
    assert bridge.navigation_action is None and len(bridge.nav_through.sent) == 2
    assert all(message.data for message in bridge.hold_pub.messages[hold_count:])


def test_independent_prepare_and_direct_commands_cannot_steal_lease(preparing_bridge):
    bridge, old = preparing_bridge
    body = make_ready(bridge, old)
    for kind, payload in (
        ("replan", preparation_body(bridge, "b" * 32)),
        ("replan", {"mission_id": bridge.active, "plan": bridge.plan}),
        ("obstacle", {}),
        ("handoff", {"mission_id": bridge.active}),
        ("start", {**bridge.plan, "mission_id": "different"}),
    ):
        with pytest.raises(ContractError, match="REPLAN_PREPARATION_IN_PROGRESS"):
            bridge.dispatch(kind, payload)
    assert bridge.replan_preparation.token == body["token"]
    assert bridge.missions[bridge.active].completed == ["P-IC"]


def test_cancel_ends_only_owned_preparation_and_old_token_cannot_reopen(
    preparing_bridge,
):
    bridge, old = preparing_bridge
    body = make_ready(bridge, old)
    other_plan = {**bridge.plan, "mission_id": "historical-other"}
    other = Mission(other_plan, bridge.map, bridge.world)
    other.status = "NAVIGATING"
    bridge.missions[other.id] = other
    generation = bridge.generation
    with pytest.raises(ContractError, match="UNKNOWN_ACTIVE_MISSION"):
        bridge.dispatch("cancel", {"mission_id": other.id})
    assert (
        bridge.generation == generation
        and bridge.replan_preparation.token == body["token"]
    )
    bridge.dispatch("cancel", {"mission_id": bridge.active})
    assert bridge.replan_preparation is None
    with pytest.raises(ContractError, match="REPLAN_PREPARATION_REQUIRED"):
        bridge.dispatch("replan", commit_body(bridge, body))
    bridge.tick()
    assert bridge.missions[bridge.active].status == "CANCELLED"
    assert bridge.hold_pub.messages[-1].data and len(bridge.nav_through.sent) == 1


@pytest.mark.parametrize("phase", ["prepare", None])
@pytest.mark.parametrize(
    "status", ["AWAITING_HANDOFF", "CHARGING", "COMPLETED", "CANCELLED"]
)
def test_original_arrival_and_terminal_guards_remain_strict(
    preparing_bridge, phase, status
):
    bridge, old = preparing_bridge
    bridge.missions[bridge.active].status = status
    payload = (
        preparation_body(bridge)
        if phase
        else {"mission_id": bridge.active, "plan": bridge.plan}
    )
    with pytest.raises(ContractError, match="MISSION_STATE_DOES_NOT_ALLOW_REPLAN"):
        bridge.dispatch("replan", payload)
    assert old.cancel_requests == 0


@pytest.mark.parametrize("failure", ["pose", "completed", "malformed"])
def test_commit_preserves_original_pose_completed_and_plan_validation(
    preparing_bridge, failure
):
    bridge, old = preparing_bridge
    body = make_ready(bridge, old)
    commit = commit_body(bridge, body)
    if failure == "pose":
        bridge.pose["x"] += 1
        stopped_observations(bridge)
        code = "OBSERVED_START_POSE_CHANGED_REPLAN_REQUIRED"
    elif failure == "completed":
        commit["plan"]["completed_goal_ids"] = []
        code = "COMPLETED_HANDOFFS_MUST_BE_PRESERVED"
    else:
        commit["plan"] = {}
        code = "READY_MISSION_PLAN_REQUIRED"
    with pytest.raises(ContractError, match=code):
        bridge.dispatch("replan", commit)
    bridge.tick()
    assert bridge.replan_preparation and bridge.hold_pub.messages[-1].data
    assert bridge.missions[bridge.active].completed == ["P-IC"]
    assert len(bridge.nav_through.sent) == 1


@pytest.mark.parametrize("end", ["abort", "timeout"])
def test_preparation_abort_and_lease_timeout_stay_paused_without_redispatch(
    preparing_bridge, end, monkeypatch
):
    bridge, old = preparing_bridge
    body = make_ready(bridge, old)
    if end == "abort":
        bridge.dispatch("replan", {**body, "phase": "abort"})
    else:
        future_time = (
            bridge.replan_preparation.started_at
            + bridge_module.REPLAN_PREPARATION_TIMEOUT_S
            + 1
        )
        monkeypatch.setattr(bridge_module.time, "monotonic", lambda: future_time)
        bridge.tick()
    for _ in range(2):
        bridge.tick()
    mission = bridge.missions[bridge.active]
    assert bridge.replan_preparation is None and mission.status == "TRANSPORT_PAUSED"
    assert mission.completed == ["P-IC"] and bridge.hold_pub.messages[-1].data
    assert len(bridge.nav_through.sent) == 1
    assert mission.events[-1]["type"] == "transport_paused"
