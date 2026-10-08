"""Slow HTTP consumers must not hold the production bridge's callback lock."""

import copy
import io
import json
import threading
import time
import types

import pytest
from test_navigation_action_lifecycle import BridgeHarness, bridge_module


class BlockingWriter:
    def __init__(self, stage):
        self.stage = stage
        self.entered = threading.Event()
        self.release = threading.Event()
        self.body = io.BytesIO()

    def block(self):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("test did not release blocked HTTP writer")

    def write(self, data):
        if self.stage == "body":
            self.block()
        return self.body.write(data)


@pytest.mark.parametrize(
    "known_mission", [True, False], ids=["mission-200", "missing-404"]
)
@pytest.mark.parametrize("stage", ["body", "headers"])
def test_slow_response_allows_real_feedback_and_preserves_snapshot(
    known_mission, stage
):
    bridge = BridgeHarness()
    mission = bridge.missions[bridge.active]
    mission.current_goal_id = "P-SENSOR"
    mission.pose = copy.deepcopy(bridge.pose)
    mission.robot_state = {"sim_time_s": 1, "held": False}
    bridge.emit("feedback", "P-SENSOR", {"source": "nav2_action_feedback"})
    bridge.replan_preparation = bridge_module.ReplanPreparation(
        token="a" * 32,
        mission_id=mission.id,
        map_id=mission.plan["map_id"],
        map_revision=mission.revision,
        source_plan_hash=mission.plan_hash,
        completed_goal_ids=tuple(mission.completed),
        generation=bridge.generation,
        started_at=time.monotonic(),
        odom_messages=0,
    )
    cursor = mission.sequence
    expected = bridge.mission_snapshot(mission, after_sequence=0)
    original_plan = copy.deepcopy(mission.plan)
    original_completed = mission.completed[:]
    original_hold_messages = bridge.hold_pub.messages[:]
    writer = BlockingWriter(stage)
    handler_type = bridge.handler_type()
    handler = object.__new__(handler_type)
    mission_id = mission.id if known_mission else "unknown-mission"
    handler.path = f"/missions/{mission_id}?after_sequence=0"
    handler.wfile = writer
    statuses, headers, failures = [], {}, []
    handler.send_response = statuses.append
    handler.send_header = headers.__setitem__

    def end_headers():
        if stage == "headers":
            writer.block()

    handler.end_headers = end_headers

    def serve():
        try:
            handler.do_GET()
        except BaseException as exc:
            failures.append(exc)

    progressed = threading.Event()

    def feedback():
        try:
            with bridge.lock:
                bridge.pose = {**bridge.pose, "x": bridge.pose["x"] + 0.1}
                mission.robot_state["sim_time_s"] = 2
            bridge.on_feedback(
                types.SimpleNamespace(
                    feedback=types.SimpleNamespace(
                        number_of_recoveries=0,
                        distance_remaining=1.5,
                        number_of_poses_remaining=1,
                    )
                ),
                bridge.generation,
            )
            progressed.set()
        except BaseException as exc:
            failures.append(exc)

    serving = threading.Thread(target=serve, daemon=True)
    callback = threading.Thread(target=feedback, daemon=True)
    serving.start()
    try:
        assert writer.entered.wait(2), "actual HTTP handler did not reach blocked write"
        callback.start()
        assert progressed.wait(1), (
            "slow HTTP response retained bridge.lock and blocked feedback"
        )
        assert not writer.release.is_set()
    finally:
        writer.release.set()
        serving.join(2)
        if callback.ident is not None:
            callback.join(2)
        assert not serving.is_alive(), "HTTP worker did not stop"
        assert not callback.is_alive(), "feedback worker did not stop"
        assert not failures, f"worker errors: {failures!r}"

    data = writer.body.getvalue()
    assert statuses == [200 if known_mission else 404]
    assert headers["Content-Type"] == "application/json"
    assert int(headers["Content-Length"]) == len(data)
    assert json.loads(data) == (
        expected if known_mission else {"error": "UNKNOWN_MISSION"}
    )
    assert mission.sequence == cursor + 1
    assert mission.pose != expected["current_pose"]
    assert mission.robot_state != expected["robot_state"]
    assert mission.metrics["action_feedback_messages"] == 1
    assert mission.plan == original_plan
    assert mission.completed == original_completed
    assert bridge.hold_pub.messages == original_hold_messages
    assert bridge.nav_through.sent == bridge.nav_to.sent == []
    assert bridge.commands.empty()
