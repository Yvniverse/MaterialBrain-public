"""Exercise the bridge's real callbacks with controllable ROS action futures."""

import copy
import importlib.util
import io
import queue
import sys
import threading
import time
import types
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from materialbrain_ros2.contracts import ContractError, Mission
from materialbrain_ros2.geometry import load_world

ROOT = Path(__file__).resolve().parents[3]


def message_module(name, **symbols):
    module = types.ModuleType(name)
    module.__dict__.update(symbols)
    return module


class PoseStamped:
    def __init__(self):
        self.header = types.SimpleNamespace(frame_id=None, stamp=None)
        self.pose = types.SimpleNamespace(
            position=types.SimpleNamespace(x=0, y=0, z=0),
            orientation=types.SimpleNamespace(x=0, y=0, z=0, w=1),
        )


class Message:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class GoalStatus:
    STATUS_SUCCEEDED = 4
    STATUS_CANCELED = 5
    STATUS_ABORTED = 6


def load_bridge():
    """Stub only ROS imports; execute unmodified production bridge methods."""
    placeholders = {
        "rclpy": message_module("rclpy"),
        "rclpy.action": message_module("rclpy.action", ActionClient=object),
        "rclpy.node": message_module("rclpy.node", Node=object),
        "rclpy.parameter_client": message_module(
            "rclpy.parameter_client", AsyncParameterClient=object
        ),
        "rclpy.qos": message_module(
            "rclpy.qos",
            DurabilityPolicy=object,
            QoSProfile=object,
            ReliabilityPolicy=object,
            qos_profile_sensor_data=object(),
        ),
        "action_msgs.msg": message_module("action_msgs.msg", GoalStatus=GoalStatus),
        "action_msgs.srv": message_module(
            "action_msgs.srv",
            CancelGoal=types.SimpleNamespace(
                Response=types.SimpleNamespace(ERROR_NONE=0, ERROR_GOAL_TERMINATED=3)
            ),
        ),
        "geometry_msgs.msg": message_module(
            "geometry_msgs.msg", PoseStamped=PoseStamped, Twist=object
        ),
        "lifecycle_msgs.srv": message_module("lifecycle_msgs.srv", GetState=object),
        "nav2_msgs.action": message_module(
            "nav2_msgs.action",
            NavigateThroughPoses=types.SimpleNamespace(Goal=types.SimpleNamespace),
            NavigateToPose=types.SimpleNamespace(Goal=types.SimpleNamespace),
        ),
        "nav2_msgs.msg": message_module("nav2_msgs.msg", BehaviorTreeLog=object),
        "nav_msgs.msg": message_module(
            "nav_msgs.msg", OccupancyGrid=object, Odometry=object, Path=object
        ),
        "sensor_msgs.msg": message_module("sensor_msgs.msg", LaserScan=object),
        "std_msgs.msg": message_module("std_msgs.msg", Bool=Message, String=Message),
        "tf2_msgs.msg": message_module("tf2_msgs.msg", TFMessage=object),
        "materialbrain_ros2.sim_base": message_module(
            "materialbrain_ros2.sim_base", yaw_quaternion=lambda orientation, yaw: None
        ),
    }
    name = "materialbrain_ros2._bridge_lifecycle_tests"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "robot_bridge/ros2/materialbrain_ros2/mission_bridge.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {**placeholders, name: module}):
        spec.loader.exec_module(module)
    return module


bridge_module = load_bridge()


class Publisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


class GoalHandle:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.result_future = Future()
        self.cancel_future = Future()
        self.cancel_requests = 0

    def get_result_async(self):
        return self.result_future

    def cancel_goal_async(self):
        self.cancel_requests += 1
        return self.cancel_future

    def acknowledge_cancel(self, return_code=0):
        self.cancel_future.set_result(types.SimpleNamespace(return_code=return_code))

    def finish(self, status=GoalStatus.STATUS_CANCELED):
        self.result_future.set_result(
            types.SimpleNamespace(status=status, result=types.SimpleNamespace(error_code=0))
        )


class ActionClient:
    def __init__(self):
        self.sent = []

    def send_goal_async(self, goal, feedback_callback):
        response = Future()
        self.sent.append((goal, response, feedback_callback))
        return response

    def accept(self, index=0, accepted=True):
        handle = GoalHandle(accepted)
        self.sent[index][1].set_result(handle)
        return handle


class BridgeHarness(bridge_module.Bridge):
    def __init__(self):
        self.world = load_world(ROOT / "backend/app/services/embodied_navigation/world.v3.json")
        self.pose = {"x": 2.9086, "y": 3.7400, "yaw": -1.4211}
        self.map = {
            "map_id": self.world["id"],
            "revision": "f" * 64,
            "docks": [*self.world["goals"], {"id": "HOME", "pose": self.world["home"]}],
        }
        self.registry = {g["id"]: g for g in self.map["docks"]}
        # This fixture supplies registered graph IDs; no ROS server or physical
        # path feasibility is simulated by these action lifecycle tests.
        self.map["route_graph"] = {
            "nodes": [{"id": "START", **self.pose}]
            + [{"id": g["id"], **g["pose"]} for g in self.map["docks"]],
            "edges": [
                {"id": f"START:{goal}", "from": "START", "to": goal, "width_m": 2}
                for goal in ("P-SENSOR", "P-LAB", "HOME")
            ],
        }
        self.plan = {
            "schema_version": 1,
            "mission_id": "replan-race",
            "map_id": self.world["id"],
            "map_revision": "f" * 64,
            "profile": "fastest",
            "status": "READY",
            "ordered_goal_ids": ["P-IC", "P-SENSOR", "P-LAB"],
            "completed_goal_ids": ["P-IC"],
            "constraints": {
                "battery_pct": 78,
                "battery_reserve_pct": 15,
                "payload_capacity_kg": 18,
            },
            "stops": [],
            "segments": [
                {
                    "to_goal_id": goal,
                    "node_ids": ["START", goal],
                    "edge_ids": [f"START:{goal}"],
                }
                for goal in ("P-SENSOR", "P-LAB", "HOME")
            ],
        }
        mission = Mission(self.plan, self.map, self.world)
        mission.status = "NAVIGATING"
        mission.payload = self.registry["P-IC"]["payload_kg"]
        self.missions = {mission.id: mission}
        self.active = mission.id
        self.lock = threading.RLock()
        self.commands = queue.Queue()
        self.navigation_action = None
        self.generation = 0
        self.pending_since = 0.0
        self.telemetry = {"sim_time_s": 0}
        self.health_plugins = {}
        self.last_probe = time.monotonic()
        self.last_odom = self.last_probe
        self.last_scan = self.last_probe
        self.nav_through = ActionClient()
        self.nav_to = ActionClient()
        self.hold_pub = Publisher()
        self.event_pub = Publisher()
        self.event_file = io.StringIO()
        self.evidence_file = io.StringIO()
        self.topic_counts = {}
        self.obstacles = {}

    def health(self):
        return {"ready": True}

    def probe(self):
        pass

    def get_clock(self):
        return types.SimpleNamespace(
            now=lambda: types.SimpleNamespace(nanoseconds=0, to_msg=lambda: None)
        )

    def replan(self, profile="safest", ordered=None):
        plan = copy.deepcopy(self.plan)
        plan["profile"] = profile
        if ordered is not None:
            plan["ordered_goal_ids"] = ordered
        return self.dispatch("replan", {"mission_id": self.active, "plan": plan})


class NavigationActionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.bridge = BridgeHarness()
        self.mission = self.bridge.missions[self.bridge.active]
        self.bridge.navigate("P-SENSOR", through=True)

    def assert_preserved(self):
        self.assertEqual(self.mission.completed, ["P-IC"])
        self.assertEqual(self.mission.payload, self.bridge.registry["P-IC"]["payload_kg"])
        self.assertEqual(self.mission.battery, 78)
        self.assertEqual(self.bridge.pose, {"x": 2.9086, "y": 3.7400, "yaw": -1.4211})

    def test_semantic_replan_publishes_independent_verified_plan(self):
        snapshot = self.bridge.replan()
        event = next(
            item
            for item in snapshot["events"]
            if item["details"].get("source") == "server_semantic_replan"
        )
        self.assertEqual(event["details"]["plan"], self.mission.plan)
        self.assertEqual(event["details"]["plan_hash"], self.mission.plan_hash)
        self.assertEqual(event["details"]["plan"]["completed_goal_ids"], ["P-IC"])
        published = self.mission.events[-1]["details"]["plan"]
        self.assertIsNot(published, self.mission.plan)
        self.mission.plan["profile"] = "fastest"
        self.assertEqual(published["profile"], "safest")

    def test_accepted_old_goal_blocks_new_dispatch_until_result_not_cancel_ack(self):
        old = self.bridge.nav_through.accept()
        self.bridge.replan()
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        self.assertEqual(old.cancel_requests, 1)
        old.acknowledge_cancel()
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        old.finish()
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 2)
        self.assertEqual(self.mission.status, "NAVIGATING")
        self.assert_preserved()

    def test_pending_old_acceptance_is_cancelled_and_drained_before_new_dispatch(self):
        self.bridge.replan()
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        old = self.bridge.nav_through.accept()
        self.assertEqual(old.cancel_requests, 1)
        old.acknowledge_cancel()
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        old.finish(GoalStatus.STATUS_ABORTED)
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 2)
        self.assertEqual(self.mission.status, "NAVIGATING")
        self.assert_preserved()

    def test_repeated_replans_wait_once_then_dispatch_latest_plan(self):
        old = self.bridge.nav_through.accept()
        self.bridge.replan()
        self.bridge.replan("esd_safe", ["P-IC", "P-LAB", "P-SENSOR"])
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        self.assertEqual(old.cancel_requests, 1)
        old.acknowledge_cancel()
        old.finish()
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 2)
        self.assertEqual(self.mission.current_goal_id, "P-LAB")
        self.assert_preserved()

    def test_retired_result_does_not_hide_failure_of_replacement_goal(self):
        old = self.bridge.nav_through.accept()
        self.bridge.replan()
        old.finish(GoalStatus.STATUS_ABORTED)
        self.bridge.tick()
        new = self.bridge.nav_through.accept(1)
        new.finish(GoalStatus.STATUS_ABORTED)
        self.assertEqual(self.mission.status, "FAILED")
        self.assertEqual(self.mission.events[-1]["details"]["reason"], "NAV2_GOAL_FAILED")

    def test_rejected_old_pending_goal_can_release_barrier_without_an_action_result(self):
        self.bridge.replan()
        self.bridge.nav_through.accept(accepted=False)
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 2)
        self.assertEqual(self.mission.status, "NAVIGATING")

    def test_cancel_transport_error_pauses_until_terminal_and_explicit_replan(self):
        old = self.bridge.nav_through.accept()
        self.bridge.replan()
        old.cancel_future.set_exception(RuntimeError("cancel transport lost"))
        self.bridge.tick()
        self.assertEqual(self.mission.status, "TRANSPORT_PAUSED")
        self.assertTrue(self.bridge.hold_pub.messages[-1].data)
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        with self.assertRaisesRegex(ContractError, "TERMINAL_UNCONFIRMED"):
            self.bridge.replan("esd_safe")
        old.finish()
        self.bridge.tick()
        self.assertEqual(self.mission.status, "TRANSPORT_PAUSED")
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        self.bridge.replan("esd_safe")
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 2)

    def test_missing_terminal_result_times_out_without_dispatch_or_resume(self):
        old = self.bridge.nav_through.accept()
        with patch.object(bridge_module.time, "monotonic", return_value=100.0):
            self.bridge.last_probe = self.bridge.last_odom = self.bridge.last_scan = 100.0
            self.bridge.replan()
        old.acknowledge_cancel()
        with patch.object(bridge_module.time, "monotonic", return_value=111.0):
            self.bridge.last_probe = self.bridge.last_odom = self.bridge.last_scan = 111.0
            self.bridge.tick()
        self.assertEqual(self.mission.status, "TRANSPORT_PAUSED")
        self.assertTrue(self.bridge.hold_pub.messages[-1].data)
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        old.finish()
        self.bridge.tick()
        self.assertEqual(self.mission.status, "TRANSPORT_PAUSED")
        self.assertEqual(len(self.bridge.nav_through.sent), 1)

    def test_result_transport_error_keeps_terminal_unconfirmed(self):
        old = self.bridge.nav_through.accept()
        self.bridge.replan()
        old.result_future.set_exception(RuntimeError("result transport lost"))
        self.bridge.tick()
        self.assertEqual(self.mission.status, "TRANSPORT_PAUSED")
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        self.assertTrue(self.bridge.hold_pub.messages[-1].data)

    def test_cancelled_mission_cannot_start_another_until_old_action_is_terminal(self):
        old = self.bridge.nav_through.accept()
        self.bridge.dispatch("cancel", {"mission_id": self.bridge.active})
        plan = copy.deepcopy(self.bridge.plan)
        plan["mission_id"] = "next-mission"
        with self.assertRaisesRegex(ContractError, "TERMINATION_PENDING"):
            self.bridge.dispatch("start", plan)
        old.acknowledge_cancel()
        old.finish()
        self.bridge.dispatch("start", plan)
        self.assertEqual(self.bridge.active, "next-mission")

    def test_retired_path_does_not_resume_robot_while_cancellation_is_pending(self):
        old = self.bridge.nav_through.accept()
        self.bridge.replan()
        self.bridge.on_path(types.SimpleNamespace(poses=[PoseStamped()]))
        self.assertFalse(self.mission.overlay_valid_path_ready)
        self.mission.overlay_valid_path_ready = True
        self.mission.overlay_hold_since = time.monotonic() - 1
        self.bridge.tick()
        self.assertTrue(self.bridge.hold_pub.messages[-1].data)
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        old.acknowledge_cancel()

    def test_late_acceptance_after_timeout_is_cancelled_but_does_not_auto_resume(self):
        with patch.object(bridge_module.time, "monotonic", return_value=100.0):
            bridge = BridgeHarness()
            bridge.navigate("P-SENSOR", through=True)
        with patch.object(bridge_module.time, "monotonic", return_value=111.0):
            bridge.last_probe = bridge.last_odom = bridge.last_scan = 111.0
            bridge.tick()
        mission = bridge.missions[bridge.active]
        self.assertEqual(mission.status, "TRANSPORT_PAUSED")
        old = bridge.nav_through.accept()
        self.assertEqual(old.cancel_requests, 1)
        old.acknowledge_cancel()
        old.finish()
        bridge.tick()
        self.assertEqual(mission.status, "TRANSPORT_PAUSED")
        self.assertEqual(len(bridge.nav_through.sent), 1)

    def test_rejected_cancel_keeps_robot_paused_and_action_owned(self):
        for return_code in (1, 2):
            with self.subTest(return_code=return_code):
                bridge = BridgeHarness()
                bridge.navigate("P-SENSOR", through=True)
                old = bridge.nav_through.accept()
                bridge.replan()
                old.acknowledge_cancel(return_code)
                bridge.tick()
                self.assertEqual(bridge.missions[bridge.active].status, "TRANSPORT_PAUSED")
                self.assertTrue(bridge.hold_pub.messages[-1].data)
                self.assertEqual(len(bridge.nav_through.sent), 1)

    def test_already_terminated_cancel_response_still_waits_for_result(self):
        old = self.bridge.nav_through.accept()
        self.bridge.replan()
        old.acknowledge_cancel(return_code=3)
        self.bridge.tick()
        self.assertEqual(self.mission.status, "NAVIGATING")
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        old.finish(GoalStatus.STATUS_SUCCEEDED)
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 2)
        self.assert_preserved()

    def test_stale_cancel_error_and_feedback_do_not_pause_or_update_replacement(self):
        old = self.bridge.nav_through.accept()
        self.bridge.replan()
        old.finish()
        self.bridge.tick()
        self.bridge.nav_through.accept(1)
        old.cancel_future.set_exception(RuntimeError("late old cancel error"))
        feedback = types.SimpleNamespace(
            feedback=types.SimpleNamespace(number_of_recoveries=1, distance_remaining=3)
        )
        self.bridge.nav_through.sent[0][2](feedback)
        self.assertEqual(self.mission.metrics["action_feedback_messages"], 0)
        self.assertEqual(self.mission.status, "NAVIGATING")
        self.bridge.nav_through.sent[1][2](feedback)
        self.assertEqual(self.mission.metrics["action_feedback_messages"], 1)

    def test_nonterminal_result_does_not_release_barrier(self):
        old = self.bridge.nav_through.accept()
        self.bridge.replan()
        old.finish(status=2)
        self.bridge.tick()
        self.assertEqual(self.mission.status, "TRANSPORT_PAUSED")
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        self.assertTrue(self.bridge.hold_pub.messages[-1].data)

    def test_retired_success_cannot_arrive_but_replacement_success_can(self):
        old = self.bridge.nav_through.accept()
        self.bridge.replan()
        old.finish(GoalStatus.STATUS_SUCCEEDED)
        self.assertFalse(any(e["type"] == "arrived" for e in self.mission.events))
        self.bridge.tick()
        new = self.bridge.nav_through.accept(1)
        self.bridge.pose = copy.deepcopy(self.bridge.registry["P-SENSOR"]["pose"])
        new.finish(GoalStatus.STATUS_SUCCEEDED)
        self.assertEqual(self.mission.status, "AWAITING_HANDOFF")
        self.assertEqual(self.mission.completed, ["P-IC"])
        self.assertEqual(self.mission.events[-1]["type"], "arrived")

    def test_pending_goal_response_error_keeps_transport_paused(self):
        self.bridge.replan()
        self.bridge.nav_through.sent[0][1].set_exception(RuntimeError("goal response lost"))
        self.bridge.tick()
        self.assertEqual(self.mission.status, "TRANSPORT_PAUSED")
        self.assertTrue(self.bridge.hold_pub.messages[-1].data)
        self.assertEqual(len(self.bridge.nav_through.sent), 1)

    def test_sensor_timeout_drains_old_action_before_explicit_replan(self):
        old = self.bridge.nav_through.accept()
        self.bridge.last_odom = 0.0
        self.bridge.tick()
        self.assertEqual(self.mission.status, "TRANSPORT_PAUSED")
        self.assertEqual(old.cancel_requests, 1)
        self.bridge.last_odom = time.monotonic()
        self.bridge.replan()
        old.acknowledge_cancel()
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 1)
        old.finish()
        self.bridge.tick()
        self.assertEqual(len(self.bridge.nav_through.sent), 2)


if __name__ == "__main__":
    unittest.main()
