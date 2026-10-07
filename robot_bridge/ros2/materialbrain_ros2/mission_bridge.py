"""Internal HTTP mission bridge backed by installed Nav2 actions and real ROS topics."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import queue
import re
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import rclpy
from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseStamped, Twist
from lifecycle_msgs.srv import GetState
from nav2_msgs.action import NavigateThroughPoses, NavigateToPose
from nav2_msgs.msg import BehaviorTreeLog
from nav_msgs.msg import OccupancyGrid, Odometry
from nav_msgs.msg import Path as RosPath
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.parameter_client import AsyncParameterClient
from rclpy.qos import (
    DurabilityPolicy,
    QoSProfile,
    ReliabilityPolicy,
    qos_profile_sensor_data,
)
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool, String
from tf2_msgs.msg import TFMessage

from .contracts import (
    ContractError,
    Mission,
    route_waypoints,
    stable_hash,
    validate_plan,
)
from .geometry import clearance, corners, footprint, load_world, polygons_overlap
from .sim_base import yaw_quaternion

TERMINAL = {"COMPLETED", "CANCELLED", "FAILED", "BLOCKED_LOW_BATTERY"}
ACTION_RESPONSE_TIMEOUT_S = 10.0
ACTION_TERMINATION_TIMEOUT_S = 10.0
REPLAN_PREPARATION_TIMEOUT_S = 30.0


@dataclass
class NavigationAction:
    """One transport action remains owned until rejection or a terminal result."""

    generation: int
    mission_id: str
    goal_id: str
    sent_at: float
    handle: object = None
    retire_started_at: float | None = None
    cancel_requested: bool = False
    transport_paused: bool = False


@dataclass
class ReplanPreparation:
    token: str
    mission_id: str
    map_id: str
    map_revision: str
    source_plan_hash: str
    completed_goal_ids: tuple
    generation: int
    started_at: float
    odom_messages: int


class Bridge(Node):
    def __init__(self):
        super().__init__("materialbrain_mission_bridge")
        self.world = load_world(
            os.environ.get("MATERIALBRAIN_WORLD", "/opt/materialbrain/world.v3.json")
        )
        self.map = json.loads(
            Path(
                os.environ.get(
                    "MATERIALBRAIN_SPATIAL_SNAPSHOT",
                    "/opt/materialbrain/spatial_snapshot.json",
                )
            ).read_text(encoding="utf-8")
        )
        self.world_source_hash = hashlib.sha256(
            Path(
                os.environ.get(
                    "MATERIALBRAIN_WORLD", "/opt/materialbrain/world.v3.json"
                )
            ).read_bytes()
        ).hexdigest()
        self.registry = {g["id"]: g for g in self.map["docks"]}
        for g in self.world["goals"]:
            if self.registry[g["id"]]["pose"] != g["pose"]:
                raise RuntimeError("canonical dock coordinate mismatch")
        self.lock = threading.RLock()
        self.commands = queue.Queue()
        self.missions = {}
        self.active = None
        self.navigation_action = None
        self.replan_preparation = None
        self.last_replan_commit = None
        self.replan_control_epoch = 0
        self.generation = 0
        self.pending_since = 0.0
        self.telemetry = {}
        self.last_odom = 0.0
        self.last_telemetry = 0.0
        self.odom_velocity = None
        self.last_scan = 0.0
        self.pose = dict(self.world["robot"]["pose"])
        self.obstacles = {}
        self.evidence_dir = Path(
            os.environ.get("MATERIALBRAIN_EVIDENCE_DIR", "/opt/materialbrain/.evidence")
        )
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_file = (self.evidence_dir / "topics.jsonl").open(
            "a", encoding="utf-8", buffering=1
        )
        self.event_file = (self.evidence_dir / "events.jsonl").open(
            "a", encoding="utf-8", buffering=1
        )
        self.topic_counts = {}
        self.health_plugins = {}
        self.lifecycle = {}
        self.last_probe = 0.0
        self.latest_path = None
        self.observed_tf = set()
        self.occupancy_map = None
        self.odom_messages = 0
        primitive_manifest = Path(
            "/opt/materialbrain/.runtime/diff_drive_lattice.manifest.json"
        )
        self.lattice_primitives = (
            json.loads(primitive_manifest.read_text(encoding="utf-8"))
            if primitive_manifest.is_file()
            else None
        )
        self.hold_pub = self.create_publisher(Bool, "/simulation/hold", 10)
        self.obstacle_pub = self.create_publisher(String, "/simulation/obstacles", 10)
        self.event_pub = self.create_publisher(
            String, "/materialbrain/execution_events", 10
        )
        self.create_subscription(Odometry, "/odom", self.on_odom, 10)
        self.create_subscription(
            LaserScan, "/scan", self.on_scan, qos_profile_sensor_data
        )
        self.create_subscription(Twist, "/cmd_vel", self.on_cmd, 10)
        self.create_subscription(String, "/simulation/telemetry", self.on_telemetry, 10)
        self.create_subscription(RosPath, "/plan", self.on_path, 10)
        self.create_subscription(BehaviorTreeLog, "/behavior_tree_log", self.on_bt, 10)
        self.create_subscription(TFMessage, "/tf", self.on_tf, 10)
        durable = QoSProfile(
            depth=10,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.create_subscription(TFMessage, "/tf_static", self.on_tf, durable)
        self.create_subscription(OccupancyGrid, "/map", self.on_map, durable)
        self.nav_through = ActionClient(
            self, NavigateThroughPoses, "navigate_through_poses"
        )
        self.nav_to = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self.param_clients = {
            name: AsyncParameterClient(self, name)
            for name in (
                "planner_server",
                "controller_server",
                "bt_navigator",
                "global_costmap/global_costmap",
                "local_costmap/local_costmap",
            )
        }
        self.state_clients = {
            name: self.create_client(GetState, f"/{name}/get_state")
            for name in (
                "map_server",
                "planner_server",
                "controller_server",
                "behavior_server",
                "bt_navigator",
            )
        }
        self.create_timer(0.1, self.tick)
        host = os.environ.get("MATERIALBRAIN_BRIDGE_BIND", "0.0.0.0")
        port = int(os.environ.get("MATERIALBRAIN_BRIDGE_PORT", "8766"))
        self.server = ThreadingHTTPServer((host, port), self.handler_type())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.get_logger().info(
            f"simulation HTTP bridge listening on {host}:{port}; no hardware/inventory capability"
        )

    def record(self, topic, data):
        self.topic_counts[topic] = self.topic_counts.get(topic, 0) + 1
        self.evidence_file.write(
            json.dumps(
                {
                    "topic": topic,
                    "wall_time": time.time(),
                    "ros_time_ns": self.get_clock().now().nanoseconds,
                    "data": data,
                },
                allow_nan=False,
            )
            + "\n"
        )

    def emit(self, kind, goal=None, details=None):
        mission = self.missions[self.active]
        mission.pose = copy.deepcopy(self.pose)
        event = mission.event(kind, goal, details)
        self.event_file.write(json.dumps(event, allow_nan=False) + "\n")
        self.event_pub.publish(String(data=json.dumps(event)))
        return event

    def on_odom(self, msg):
        with self.lock:
            q = msg.pose.pose.orientation
            self.pose = {
                "x": msg.pose.pose.position.x,
                "y": msg.pose.pose.position.y,
                "yaw": math.atan2(
                    2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z)
                ),
            }
            self.last_odom = time.monotonic()
            self.odom_messages += 1
            self.odom_velocity = (msg.twist.twist.linear.x, msg.twist.twist.angular.z)
            if self.odom_messages % 5 == 0:
                self.record(
                    "/odom",
                    {
                        "pose": self.pose,
                        "frame": msg.header.frame_id,
                        "child_frame": msg.child_frame_id,
                        "linear_x": msg.twist.twist.linear.x,
                        "angular_z": msg.twist.twist.angular.z,
                        "stamp": {
                            "sec": msg.header.stamp.sec,
                            "nanosec": msg.header.stamp.nanosec,
                        },
                    },
                )
            if self.active:
                self.missions[self.active].pose = copy.deepcopy(self.pose)

    def on_scan(self, msg):
        self.last_scan = time.monotonic()
        if self.topic_counts.get("/scan", 0) % 2 == 0:
            self.record(
                "/scan",
                {
                    "frame": msg.header.frame_id,
                    "range_min": msg.range_min,
                    "range_max": msg.range_max,
                    "ranges": list(msg.ranges),
                    "stamp": {
                        "sec": msg.header.stamp.sec,
                        "nanosec": msg.header.stamp.nanosec,
                    },
                },
            )
        else:
            self.topic_counts["/scan"] = self.topic_counts.get("/scan", 0) + 1

    def on_cmd(self, msg):
        with self.lock:
            self.record(
                "/cmd_vel", {"linear_x": msg.linear.x, "angular_z": msg.angular.z}
            )

    def on_tf(self, msg):
        with self.lock:
            transforms = []
            for tr in msg.transforms:
                self.observed_tf.add((tr.header.frame_id, tr.child_frame_id))
                transforms.append(
                    {
                        "parent": tr.header.frame_id,
                        "child": tr.child_frame_id,
                        "translation": {
                            "x": tr.transform.translation.x,
                            "y": tr.transform.translation.y,
                            "z": tr.transform.translation.z,
                        },
                        "rotation": {
                            "x": tr.transform.rotation.x,
                            "y": tr.transform.rotation.y,
                            "z": tr.transform.rotation.z,
                            "w": tr.transform.rotation.w,
                        },
                        "stamp": {
                            "sec": tr.header.stamp.sec,
                            "nanosec": tr.header.stamp.nanosec,
                        },
                    }
                )
            self.record("/tf", {"transforms": transforms})

    def on_map(self, msg):
        with self.lock:
            self.occupancy_map = {
                "frame": msg.header.frame_id,
                "width": msg.info.width,
                "height": msg.info.height,
                "resolution": msg.info.resolution,
                "origin": {
                    "x": msg.info.origin.position.x,
                    "y": msg.info.origin.position.y,
                },
                "occupancy_sha256": hashlib.sha256(
                    bytes(v % 256 for v in msg.data)
                ).hexdigest(),
            }
            self.record("/map", self.occupancy_map)

    def on_telemetry(self, msg):
        with self.lock:
            self.telemetry = json.loads(msg.data)
            self.last_telemetry = time.monotonic()
            self.record("/simulation/telemetry", self.telemetry)
            if self.active:
                m = self.missions[self.active]
                m.robot_state = copy.deepcopy(self.telemetry)
                m.robot_state.update(
                    battery_pct=m.battery,
                    payload_kg=m.payload,
                    planner=self.health_plugins.get("planner"),
                    controller=self.health_plugins.get("controller"),
                )
                if m.started_sim_time is not None and m.status not in TERMINAL:
                    traveled = max(0, self.telemetry["distance_m"] - m.distance_start)
                    elapsed = max(0, self.telemetry["sim_time_s"] - m.started_sim_time)
                    used_wh = (
                        traveled * self.world["robot"]["wh_per_m"]
                        + elapsed * self.world["robot"]["idle_w"] / 3600
                    )
                    m.battery = max(
                        0,
                        m.battery_base
                        - 100
                        * (used_wh - m.battery_energy_baseline_wh)
                        / self.world["robot"]["battery_wh"],
                    )
                    m.metrics.update(
                        distance_m=traveled,
                        elapsed_s=elapsed,
                        energy_wh=used_wh,
                        min_clearance_m=self.telemetry["min_clearance_m"],
                        collision_count=self.telemetry["collision_count"],
                    )

    def on_path(self, msg):
        with self.lock:
            points = [
                {
                    "x": p.pose.position.x,
                    "y": p.pose.position.y,
                    "yaw": 2 * math.atan2(p.pose.orientation.z, p.pose.orientation.w),
                }
                for p in msg.poses
            ]
            length = sum(
                math.hypot(b["x"] - a["x"], b["y"] - a["y"])
                for a, b in zip(points, points[1:], strict=False)
            )
            self.latest_path = points
            self.record("/plan", {"poses": points, "distance_m": length})
            action = self.navigation_action
            if (
                self.active
                and self.missions[self.active].status == "NAVIGATING"
                and action
                and action.generation == self.generation
                and action.handle
                and action.retire_started_at is None
            ):
                m = self.missions[self.active]
                m.metrics["global_paths"] += 1
                m.metrics["global_path_lengths_m"].append(length)
                if self.pending_since:
                    m.metrics["planning_latencies_ms"].append(
                        (time.monotonic() - self.pending_since) * 1000
                    )
                    self.pending_since = 0.0
                if self.obstacles:
                    m.metrics["replan_count"] += 1
                    self.emit(
                        "replanning",
                        m.current_goal_id,
                        {
                            "source": "nav2_global_path",
                            "obstacle_ids": list(self.obstacles),
                            "completed_goal_ids": m.completed[:],
                            "path_length_m": length,
                            "current_pose": self.pose,
                        },
                    )
                if m.overlay_hold and points:
                    m.overlay_valid_path_ready = all(
                        clearance(self.world, pose, list(self.obstacles.values()))
                        > self.world["robot"]["margin"]
                        for pose in points
                    )

    def on_bt(self, msg):
        with self.lock:
            changes = [
                {
                    "node": e.node_name,
                    "previous": e.previous_status,
                    "current": e.current_status,
                }
                for e in msg.event_log
            ]
            self.record("/behavior_tree_log", {"changes": changes})
            if self.active:
                for e in changes:
                    if e["current"] == "RUNNING" and e["node"] in (
                        "Wait",
                        "BackUp",
                        "Spin",
                        "RecoveryActions",
                        "ClearCostmaps",
                    ):
                        m = self.missions[self.active]
                        self.emit(
                            "recovery",
                            m.current_goal_id,
                            {"source": "nav2_behavior_tree_log", **e},
                        )

    def probe(self):
        requests = {
            "planner_server": ["GridBased.plugin", "GridBased.lattice_filepath"],
            "controller_server": [
                "FollowPath.plugin",
                "FollowPath.primary_controller",
                "FollowPath.rotate_to_goal_heading",
                "FollowPath.rotate_to_heading_once",
                "FollowPath.motion_model",
                "FollowPath.CostCritic.consider_footprint",
                "FollowPath.CostCritic.cost_weight",
                "FollowPath.PathFollowCritic.cost_weight",
                "FollowPath.GoalCritic.threshold_to_consider",
                "FollowPath.GoalAngleCritic.threshold_to_consider",
                "FollowPath.PathFollowCritic.threshold_to_consider",
                "FollowPath.PathAngleCritic.threshold_to_consider",
                "FollowPath.PathAngleCritic.mode",
                "FollowPath.PathAlignCritic.use_path_orientations",
            ],
            "bt_navigator": [
                "default_nav_to_pose_bt_xml",
                "default_nav_through_poses_bt_xml",
            ],
            "global_costmap/global_costmap": [
                "footprint",
                "footprint_padding",
                "resolution",
            ],
            "local_costmap/local_costmap": [
                "footprint",
                "footprint_padding",
                "resolution",
            ],
        }
        for name, keys in requests.items():
            client = self.param_clients[name]
            if client.services_are_ready():
                future = client.get_parameters(keys)

                def done(f, name=name, keys=keys):
                    if f.exception():
                        return
                    with self.lock:
                        values = {
                            k: (
                                v.string_value
                                if v.type == 4
                                else v.bool_value
                                if v.type == 1
                                else v.double_value
                                if v.type == 3
                                else v.integer_value
                            )
                            for k, v in zip(keys, f.result().values, strict=False)
                        }
                        self.health_plugins[name] = values
                        if name == "planner_server":
                            self.health_plugins["planner"] = values.get(
                                "GridBased.plugin"
                            )
                        if name == "controller_server":
                            self.health_plugins["controller"] = values.get(
                                "FollowPath.primary_controller"
                            )
                            self.health_plugins["heading_controller"] = values.get(
                                "FollowPath.plugin"
                            )

                future.add_done_callback(done)
        for name, client in self.state_clients.items():
            if client.service_is_ready():
                future = client.call_async(GetState.Request())
                future.add_done_callback(
                    lambda f, name=name: self.lifecycle.update(
                        {name: f.result().current_state.label}
                    )
                    if not f.exception()
                    else None
                )

    def health(self):
        with self.lock:
            ready = (
                len(self.lifecycle) == 5
                and all(v == "active" for v in self.lifecycle.values())
                and self.nav_through.server_is_ready()
                and self.nav_to.server_is_ready()
                and time.monotonic() - self.last_odom < 2
                and time.monotonic() - self.last_scan < 2
                and self.health_plugins.get("planner")
                == "nav2_smac_planner::SmacPlannerLattice"
                and self.health_plugins.get("controller")
                == "nav2_mppi_controller::MPPIController"
                and self.health_plugins.get("heading_controller")
                == "nav2_rotation_shim_controller::RotationShimController"
                and self.health_plugins.get("controller_server", {}).get(
                    "FollowPath.motion_model"
                )
                == "DiffDrive"
                and self.health_plugins.get("controller_server", {}).get(
                    "FollowPath.CostCritic.consider_footprint"
                )
                is True
                and self.occupancy_map is not None
                and {
                    ("warehouse_map", "odom"),
                    ("odom", "base_link"),
                    ("base_link", "laser"),
                }
                <= self.observed_tf
            )
            return {
                "ready": ready,
                "execution_boundary": "ros2_nav2_simulation",
                "hardware_control": False,
                "map_id": self.map["map_id"],
                "map_revision": self.map["revision"],
                "frame": "warehouse_map",
                "source_world_sha256": self.world_source_hash,
                "build_sha": os.environ.get("MATERIALBRAIN_BUILD_SHA", "unknown"),
                "ros_distro": os.environ.get("ROS_DISTRO"),
                "nav2_version": os.environ.get("MATERIALBRAIN_NAV2_VERSION"),
                "lattice_primitives": copy.deepcopy(self.lattice_primitives),
                "active_plugins": copy.deepcopy(self.health_plugins),
                "lifecycle": copy.deepcopy(self.lifecycle),
                "actions": {
                    "navigate_through_poses": self.nav_through.server_is_ready(),
                    "navigate_to_pose": self.nav_to.server_is_ready(),
                },
                "topic_message_counts": copy.deepcopy(self.topic_counts),
                "observed_tf": sorted([list(pair) for pair in self.observed_tf]),
                "occupancy_map": copy.deepcopy(self.occupancy_map),
                "robot_state": copy.deepcopy(self.telemetry),
                "registered_goal_ids": sorted(self.registry),
                "active_mission_id": self.active,
            }

    def tick(self):
        with self.lock:
            now = time.monotonic()
            if now - self.last_probe > 2:
                self.probe()
                self.last_probe = now
            for _ in range(8):
                try:
                    kind, body, future = self.commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    future.set_result(self.dispatch(kind, body))
                except Exception as exc:
                    future.set_exception(exc)
            action = self.navigation_action
            if action and not action.transport_paused:
                if (
                    action.retire_started_at is not None
                    and now - action.retire_started_at >= ACTION_TERMINATION_TIMEOUT_S
                ):
                    self.pause_navigation(action, "NAV2_ACTION_TERMINATION_TIMEOUT")
                elif (
                    action.handle is None
                    and now - action.sent_at >= ACTION_RESPONSE_TIMEOUT_S
                ):
                    self.pause_navigation(action, "NAV2_ACTION_RESPONSE_TIMEOUT")
                    self.retire_navigation()
            if not self.active:
                return
            m = self.missions[self.active]
            preparation = getattr(self, "replan_preparation", None)
            if (
                preparation
                and now - preparation.started_at >= REPLAN_PREPARATION_TIMEOUT_S
            ):
                self.abort_replan_preparation("REPLAN_PREPARATION_TIMEOUT")
            if getattr(self, "replan_preparation", None):
                # Preparation retires the old action and holds the simulator.
                # Its ordinary overlay/new-goal logic must not restart motion
                # while the backend plans from the confirmed stationary pose.
                return
            if m.status == "NAVIGATING" and (
                now - self.last_odom > 2 or now - self.last_scan > 2
            ):
                self.generation += 1
                self.hold_pub.publish(Bool(data=True))
                m.status = "TRANSPORT_PAUSED"
                self.retire_navigation()
                self.emit(
                    "transport_paused",
                    m.current_goal_id,
                    {
                        "reason": "ROS_ODOM_OR_SCAN_TIMEOUT",
                        "completed_goal_ids": m.completed[:],
                    },
                )
                return
            if m.status == "QUEUED" and self.health()["ready"]:
                m.status = "NAVIGATING"
                m.started_sim_time = self.telemetry.get("sim_time_s", 0)
                m.distance_start = self.telemetry.get("distance_m", 0)
                self.emit(
                    "started",
                    details={
                        "active_plugins": self.health_plugins,
                        "completed_goal_ids": m.completed[:],
                    },
                )
                if m.battery <= m.constraints.get("battery_reserve_pct", 15) and (
                    not m.remaining() or m.remaining()[0] != "CHARGER"
                ):
                    m.status = "BLOCKED_LOW_BATTERY"
                    self.hold_pub.publish(Bool(data=True))
                    self.emit(
                        "charging",
                        details={
                            "state": "required",
                            "reason": "LOW_BATTERY_RESERVE",
                            "navigation_started": False,
                            "charger_goal_id": "CHARGER",
                        },
                    )
                    return
            if (
                m.status == "CHARGING"
                and self.telemetry.get("sim_time_s", 0) >= m.charging_until
            ):
                m.battery_base = 100.0
                m.battery = 100.0
                m.battery_energy_baseline_wh = m.metrics.get("energy_wh", 0)
                if m.queue and m.queue[0] == "CHARGER":
                    m.queue.pop(0)
                self.emit(
                    "charging",
                    "CHARGER",
                    {
                        "state": "complete",
                        "battery_pct": 100,
                        "model": "deterministic_simulated_charge",
                        "hardware_control": False,
                    },
                )
                m.status = "NAVIGATING"
                m.current_goal_id = None
            if (
                m.status == "NAVIGATING"
                and m.overlay_hold
                and m.overlay_valid_path_ready
                and self.navigation_action
                and self.navigation_action.generation == self.generation
                and self.navigation_action.retire_started_at is None
                and now - m.overlay_hold_since >= 0.75
            ):
                m.overlay_hold = False
                self.hold_pub.publish(Bool(data=False))
                self.emit(
                    "replanning",
                    m.current_goal_id,
                    {
                        "source": "nav2_validated_overlay_path",
                        "state": "resumed",
                        "current_pose": self.pose,
                        "completed_goal_ids": m.completed[:],
                    },
                )
            if m.status == "NAVIGATING" and self.navigation_action is None:
                remaining = m.remaining()
                if remaining:
                    self.navigate(remaining[0], through=True)
                elif m.return_home and "HOME" not in m.completed:
                    self.navigate("HOME", through=False)
                else:
                    m.status = "COMPLETED"
                    self.hold_pub.publish(Bool(data=True))
                    self.emit(
                        "completed", details={"completed_goal_ids": m.completed[:]}
                    )

    def navigate(self, goal_id, through, alignment=False):
        if self.navigation_action is not None:
            return
        m = self.missions[self.active]
        segments = m.plan.get("segments", [])
        segment = next(
            (s for s in segments[m.segment_cursor :] if s.get("to_goal_id") == goal_id),
            {},
        )
        try:
            waypoints = (
                route_waypoints(self.map, segment, self.pose, goal_id, self.world)
                if segment.get("node_ids") and not alignment
                else [{"id": goal_id, **self.registry[goal_id]["pose"]}]
            )
        except ContractError as exc:
            m.status = "FAILED"
            self.hold_pub.publish(Bool(data=True))
            self.emit(
                "failed",
                goal_id,
                {"reason": exc.code, "completed_goal_ids": m.completed[:]},
            )
            return
        if segment and not alignment:
            m.segment_cursor = segments.index(segment) + 1
        m.active_waypoint_ids = [p["id"] for p in waypoints]
        if goal_id == "HOME" and len(waypoints) > 1 and not alignment:
            through = True
            m.home_alignment_pending = True
        self.generation += 1
        action = NavigationAction(self.generation, m.id, goal_id, time.monotonic())
        self.navigation_action = action
        m.current_goal_id = goal_id
        self.hold_pub.publish(Bool(data=m.overlay_hold))
        self.pending_since = action.sent_at
        dock = self.registry[goal_id]["pose"]
        poses = []
        for waypoint in waypoints:
            pose = PoseStamped()
            pose.header.frame_id = "warehouse_map"
            pose.header.stamp = self.get_clock().now().to_msg()
            pose.pose.position.x = waypoint["x"]
            pose.pose.position.y = waypoint["y"]
            yaw_quaternion(pose.pose.orientation, waypoint["yaw"])
            poses.append(pose)
        if through:
            goal = NavigateThroughPoses.Goal()
            goal.poses = poses
            client = self.nav_through
        else:
            goal = NavigateToPose.Goal()
            goal.pose = pose
            client = self.nav_to
        m.metrics["nav2_action_count"] += 1
        m.last_recoveries = 0
        self.emit(
            "feedback",
            goal_id,
            {
                "action": "NavigateThroughPoses" if through else "NavigateToPose",
                "state": "dispatching",
                "registered_pose": dock,
                "semantic_route_mode": "registered_graph_waypoints"
                if segment.get("node_ids") and not alignment
                else "registered_dock_baseline",
                "semantic_waypoint_ids": m.active_waypoint_ids[:],
                "route_profile": m.plan.get("profile", "fastest"),
            },
        )
        try:
            future = client.send_goal_async(
                goal,
                feedback_callback=lambda msg: self.on_feedback(msg, action.generation),
            )
            future.add_done_callback(lambda f: self.goal_accepted(f, action))
        except Exception:
            self.pause_navigation(action, "NAV2_ACTION_TRANSPORT_ERROR")
            self.retire_navigation()

    def pause_navigation(self, action, reason):
        if self.navigation_action is not action or action.transport_paused:
            return
        action.transport_paused = True
        preparation = getattr(self, "replan_preparation", None)
        if preparation and preparation.mission_id == action.mission_id:
            self.replan_preparation = None
        if action.generation == self.generation:
            self.generation += 1
        m = self.missions[action.mission_id]
        m.status = "TRANSPORT_PAUSED"
        self.hold_pub.publish(Bool(data=True))
        self.emit(
            "transport_paused",
            action.goal_id,
            {"reason": reason, "completed_goal_ids": m.completed[:]},
        )

    def retire_navigation(self):
        """Cancel once, but keep the action as a dispatch barrier until its result."""
        action = self.navigation_action
        if action is None:
            return
        if action.retire_started_at is None:
            action.retire_started_at = time.monotonic()
        if action.handle is None or action.cancel_requested:
            return
        action.cancel_requested = True
        try:
            future = action.handle.cancel_goal_async()
            future.add_done_callback(lambda f: self.cancel_acknowledged(f, action))
        except Exception:
            self.pause_navigation(action, "NAV2_CANCEL_TRANSPORT_ERROR")

    def cancel_acknowledged(self, future, action):
        with self.lock:
            if self.navigation_action is not action:
                return
            try:
                response = future.result()
                if response.return_code not in (
                    CancelGoal.Response.ERROR_NONE,
                    CancelGoal.Response.ERROR_GOAL_TERMINATED,
                ):
                    self.pause_navigation(action, "NAV2_CANCEL_REJECTED")
            except Exception:
                self.pause_navigation(action, "NAV2_CANCEL_TRANSPORT_ERROR")
            # Even a successful cancellation response only acknowledges a
            # request. Nav2's old BT/controller may still be halting; the
            # get_result callback alone can release this transport action.

    def goal_accepted(self, future, action):
        with self.lock:
            if self.navigation_action is not action:
                return
            try:
                handle = future.result()
            except Exception:
                self.pause_navigation(action, "NAV2_ACTION_TRANSPORT_ERROR")
                self.retire_navigation()
                return
            if not handle.accepted:
                self.navigation_action = None
                if action.generation != self.generation:
                    return
                m = self.missions[action.mission_id]
                m.status = "FAILED"
                self.hold_pub.publish(Bool(data=True))
                self.emit(
                    "failed", m.current_goal_id, {"reason": "NAV2_ACTION_REJECTED"}
                )
                return
            action.handle = handle
            try:
                handle.get_result_async().add_done_callback(
                    lambda f: self.goal_result(f, action)
                )
            except Exception:
                self.pause_navigation(action, "NAV2_RESULT_TRANSPORT")
            if self.navigation_action is action and (
                action.retire_started_at is not None
                or action.generation != self.generation
            ):
                # Includes goals accepted after a replan/cancel or timeout.
                # Register the result callback before requesting cancellation.
                self.retire_navigation()

    def on_feedback(self, msg, generation):
        with self.lock:
            if generation != self.generation or not self.active:
                return
            m = self.missions[self.active]
            feedback = msg.feedback
            m.metrics["action_feedback_messages"] += 1
            recovery = int(feedback.number_of_recoveries)
            if recovery > m.last_recoveries:
                m.metrics["bt_recovery_count"] += recovery - m.last_recoveries
                m.last_recoveries = recovery
                self.emit(
                    "recovery",
                    m.current_goal_id,
                    {
                        "source": "nav2_action_feedback",
                        "number_of_recoveries": recovery,
                        "completed_goal_ids": m.completed[:],
                    },
                )
            now = time.monotonic()
            if now - m.last_feedback_wall >= 1:
                m.last_feedback_wall = now
                self.emit(
                    "feedback",
                    m.current_goal_id,
                    {
                        "source": "nav2_action_feedback",
                        "distance_remaining_m": float(feedback.distance_remaining),
                        "number_of_recoveries": recovery,
                        "number_of_poses_remaining": getattr(
                            feedback, "number_of_poses_remaining", 1
                        ),
                    },
                )

    def goal_result(self, future, action):
        with self.lock:
            if self.navigation_action is not action:
                return
            try:
                result = future.result()
            except Exception:
                self.pause_navigation(action, "NAV2_RESULT_TRANSPORT")
                self.retire_navigation()
                return
            if result.status not in (
                GoalStatus.STATUS_SUCCEEDED,
                GoalStatus.STATUS_CANCELED,
                GoalStatus.STATUS_ABORTED,
            ):
                self.pause_navigation(action, "NAV2_ACTION_TERMINAL_UNCONFIRMED")
                self.retire_navigation()
                return
            self.navigation_action = None
            if (
                action.retire_started_at is not None
                or action.generation != self.generation
            ):
                return
            m = self.missions[action.mission_id]
            self.hold_pub.publish(Bool(data=True))
            goal = m.current_goal_id
            pose = self.registry[goal]["pose"]
            error = math.hypot(self.pose["x"] - pose["x"], self.pose["y"] - pose["y"])
            if result.status != GoalStatus.STATUS_SUCCEEDED or error > 0.25:
                m.status = "FAILED"
                self.emit(
                    "failed",
                    goal,
                    {
                        "reason": "NAV2_GOAL_FAILED",
                        "action_status": result.status,
                        "goal_error_m": error,
                        "nav2_error_code": getattr(result.result, "error_code", None),
                    },
                )
                return
            if goal == "HOME" and getattr(m, "home_alignment_pending", False):
                m.home_alignment_pending = False
                self.navigate("HOME", through=False, alignment=True)
                return
            self.emit(
                "arrived",
                goal,
                {
                    "source": "nav2_action_result",
                    "action_status": result.status,
                    "goal_error_m": error,
                },
            )
            if goal == "HOME":
                m.completed.append(goal)
                m.status = "COMPLETED"
                self.emit("returned_home", goal)
                self.emit("completed", details={"completed_goal_ids": m.completed[:]})
            elif goal == "CHARGER":
                m.status = "CHARGING"
                m.charging_until = self.telemetry.get("sim_time_s", 0) + 2.0
                self.emit(
                    "charging",
                    goal,
                    {
                        "state": "started",
                        "model": "deterministic_simulated_charge",
                        "dwell_s": 2.0,
                    },
                )
            else:
                m.status = "AWAITING_HANDOFF"

    def check_replan_preparation(self, mission, body):
        preparation = getattr(self, "replan_preparation", None)
        if preparation is None or preparation.token != body.get("token"):
            raise ContractError("REPLAN_PREPARATION_REQUIRED", 409)
        if (
            preparation.mission_id != mission.id
            or preparation.map_id != mission.plan["map_id"]
            or preparation.map_revision != mission.revision
            or preparation.source_plan_hash != mission.plan_hash
            or preparation.completed_goal_ids != tuple(mission.completed)
            or preparation.generation != self.generation
            or self.active != mission.id
        ):
            raise ContractError("REPLAN_PREPARATION_IDENTITY_CHANGED", 409)
        if time.monotonic() - preparation.started_at >= REPLAN_PREPARATION_TIMEOUT_S:
            self.abort_replan_preparation("REPLAN_PREPARATION_TIMEOUT")
            raise ContractError("REPLAN_PREPARATION_REQUIRED", 409)
        return preparation

    def prepare_replan(self, mission, body):
        token = body.get("token")
        if (
            not isinstance(token, str)
            or not re.fullmatch(r"[0-9a-f]{32}", token)
            or body.get("map_id") != mission.plan["map_id"]
            or body.get("map_revision") != mission.revision
        ):
            raise ContractError("INVALID_REPLAN_PREPARATION")
        if getattr(self, "replan_preparation", None):
            if self.replan_preparation.token != token:
                raise ContractError("REPLAN_PREPARATION_IN_PROGRESS", 409)
            self.check_replan_preparation(mission, body)
            return self.mission_snapshot(mission)
        if mission.status in ("AWAITING_HANDOFF", "CHARGING", "COMPLETED", "CANCELLED"):
            raise ContractError("MISSION_STATE_DOES_NOT_ALLOW_REPLAN", 409)
        if self.navigation_action and self.navigation_action.transport_paused:
            raise ContractError("NAV2_ACTION_TERMINAL_UNCONFIRMED", 409)
        self.generation += 1
        now = time.monotonic()
        self.replan_control_epoch = getattr(self, "replan_control_epoch", 0) + 1
        self.replan_preparation = ReplanPreparation(
            token=token,
            mission_id=mission.id,
            map_id=mission.plan["map_id"],
            map_revision=mission.revision,
            source_plan_hash=mission.plan_hash,
            completed_goal_ids=tuple(mission.completed),
            generation=self.generation,
            started_at=now,
            odom_messages=getattr(self, "odom_messages", 0),
        )
        mission.pose = copy.deepcopy(self.pose)
        mission.overlay_hold = True
        mission.overlay_hold_since = now
        mission.overlay_valid_path_ready = False
        self.hold_pub.publish(Bool(data=True))
        self.retire_navigation()
        return self.mission_snapshot(mission)

    def replan_ready(self, mission):
        preparation = getattr(self, "replan_preparation", None)
        if not preparation or preparation.mission_id != mission.id:
            return False
        now = time.monotonic()
        velocity = getattr(self, "odom_velocity", None) or ()
        speeds = (*velocity, self.telemetry.get("v_mps"), self.telemetry.get("w_rps"))
        return bool(
            preparation.generation == self.generation
            and preparation.map_id == mission.plan["map_id"]
            and preparation.map_revision == mission.revision
            and preparation.source_plan_hash == mission.plan_hash
            and preparation.completed_goal_ids == tuple(mission.completed)
            and self.active == mission.id
            and now - preparation.started_at < REPLAN_PREPARATION_TIMEOUT_S
            and self.navigation_action is None
            and self.telemetry.get("held") is True
            and getattr(self, "last_telemetry", 0) >= preparation.started_at
            and now - getattr(self, "last_telemetry", 0) <= 2
            and getattr(self, "last_odom", 0) >= preparation.started_at
            and now - getattr(self, "last_odom", 0) <= 2
            and getattr(self, "odom_messages", 0) > preparation.odom_messages
            and len(velocity) == 2
            and all(
                isinstance(speed, (int, float))
                and not isinstance(speed, bool)
                and math.isfinite(speed)
                and abs(speed) <= 1e-4
                for speed in speeds
            )
        )

    def mission_snapshot(self, mission, after_sequence=None):
        snapshot = mission.snapshot(after_sequence)
        snapshot["plan_hash"] = mission.plan_hash
        preparation = getattr(self, "replan_preparation", None)
        if preparation and preparation.mission_id == mission.id:
            snapshot["replan_control"] = {
                "token": preparation.token,
                "state": "ready" if self.replan_ready(mission) else "preparing",
                "generation": preparation.generation,
                "source_plan_hash": preparation.source_plan_hash,
                "map_id": preparation.map_id,
                "map_revision": preparation.map_revision,
                "completed_goal_ids": list(preparation.completed_goal_ids),
            }
        return snapshot

    def finish_replan_preparation(self, mission, token, plan_hash):
        self.last_replan_commit = {
            "mission_id": mission.id,
            "token": token,
            "plan_hash": plan_hash,
            "generation": self.generation,
            "control_epoch": self.replan_control_epoch,
        }
        self.replan_preparation = None

    def abort_replan_preparation(self, reason):
        preparation = getattr(self, "replan_preparation", None)
        if preparation is None:
            return
        self.replan_preparation = None
        self.generation += 1
        mission = self.missions[preparation.mission_id]
        self.hold_pub.publish(Bool(data=True))
        mission.status = "TRANSPORT_PAUSED"
        self.retire_navigation()
        self.emit(
            "transport_paused",
            mission.current_goal_id,
            {"reason": reason, "completed_goal_ids": mission.completed[:]},
        )

    def dispatch(self, kind, body):
        if kind == "start":
            if getattr(self, "replan_preparation", None):
                raise ContractError("REPLAN_PREPARATION_IN_PROGRESS", 409)
            plan = body.get("plan", body)
            new = Mission(plan, self.map, self.world)
            if new.id in self.missions:
                previous = self.missions[new.id]
                if previous.plan_hash != new.plan_hash:
                    raise ContractError("MISSION_IDEMPOTENCY_CONFLICT", 409)
                return previous.snapshot()
            if self.active and self.missions[self.active].status not in TERMINAL:
                raise ContractError("ROBOT_ALREADY_EXECUTING_MISSION", 409)
            if self.navigation_action is not None:
                raise ContractError("NAV2_ACTION_TERMINATION_PENDING", 409)
            self.missions[new.id] = new
            self.active = new.id
            new.pose = copy.deepcopy(self.pose)
            return new.snapshot()
        if kind == "replan":
            plan = body.get("plan", body)
            mid = body["mission_id"]
            m = self.missions.get(mid)
            if not m or self.active != mid:
                raise ContractError("UNKNOWN_ACTIVE_MISSION", 404)
            phase = body.get("phase")
            if phase not in (None, "prepare", "commit", "abort"):
                raise ContractError("INVALID_REPLAN_PREPARATION")
            if phase == "prepare":
                return self.prepare_replan(m, body)
            if phase == "abort":
                last = getattr(self, "last_replan_commit", None)
                if (
                    getattr(self, "replan_preparation", None) is None
                    and last
                    and last["mission_id"] == mid
                    and last["token"] == body.get("token")
                    and last["control_epoch"]
                    == getattr(self, "replan_control_epoch", 0)
                    and last["plan_hash"] == m.plan_hash
                    and m.status not in TERMINAL
                ):
                    # A commit may succeed while its HTTP receipt is lost.
                    # That exact accepted control may still stop its own plan;
                    # no old token can pause another/newer control epoch.
                    if m.status == "AWAITING_HANDOFF":
                        # The action already reached a registered arrival. It
                        # is held until explicit handoff; retain scan proof.
                        # CHARGING must pause below so its timer cannot restart
                        # navigation after the accepted control was aborted.
                        self.hold_pub.publish(Bool(data=True))
                        return self.mission_snapshot(m)
                    self.generation += 1
                    self.replan_control_epoch += 1
                    self.hold_pub.publish(Bool(data=True))
                    m.status = "TRANSPORT_PAUSED"
                    self.retire_navigation()
                    self.emit(
                        "transport_paused",
                        m.current_goal_id,
                        {
                            "reason": "REPLAN_COMMIT_RECEIPT_ABORTED",
                            "completed_goal_ids": m.completed[:],
                        },
                    )
                    return self.mission_snapshot(m)
                self.check_replan_preparation(m, body)
                self.abort_replan_preparation("REPLAN_PREPARATION_ABORTED")
                return self.mission_snapshot(m)
            if phase == "commit":
                last = getattr(self, "last_replan_commit", None)
                if (
                    last
                    and last["mission_id"] == mid
                    and last["token"] == body.get("token")
                    and last["control_epoch"]
                    == getattr(self, "replan_control_epoch", 0)
                    and last["plan_hash"] == m.plan_hash == stable_hash(plan)
                ):
                    return self.mission_snapshot(m)
                self.check_replan_preparation(m, body)
                if not self.replan_ready(m):
                    raise ContractError("REPLAN_HOLD_NOT_CONFIRMED", 409)
            elif getattr(self, "replan_preparation", None):
                raise ContractError("REPLAN_PREPARATION_IN_PROGRESS", 409)
            if m.status in ("AWAITING_HANDOFF", "CHARGING", "COMPLETED", "CANCELLED"):
                raise ContractError("MISSION_STATE_DOES_NOT_ALLOW_REPLAN", 409)
            if self.navigation_action and self.navigation_action.transport_paused:
                raise ContractError("NAV2_ACTION_TERMINAL_UNCONFIRMED", 409)
            validate_plan(plan, self.map, self.world)
            new_hash = stable_hash(plan)
            if m.plan_hash == new_hash and phase != "commit":
                return self.mission_snapshot(m)
            if set(plan.get("completed_goal_ids", [])) != set(m.completed):
                raise ContractError("COMPLETED_HANDOFFS_MUST_BE_PRESERVED", 409)
            next_goal = next(
                (
                    g
                    for g in plan["ordered_goal_ids"]
                    if g not in m.completed or g == "CHARGER"
                ),
                "HOME",
            )
            first = next(
                (
                    s
                    for s in plan.get("segments", [])
                    if s.get("to_goal_id") == next_goal
                ),
                {},
            )
            if first.get("node_ids"):
                route_waypoints(self.map, first, self.pose, next_goal, self.world)
            self.generation += 1
            if phase is None:
                self.replan_control_epoch = getattr(self, "replan_control_epoch", 0) + 1
            self.hold_pub.publish(Bool(data=True))
            m.overlay_hold = True
            m.overlay_hold_since = time.monotonic()
            m.overlay_valid_path_ready = False
            m.plan = copy.deepcopy(plan)
            m.plan_hash = new_hash
            m.queue = [
                g
                for g in plan["ordered_goal_ids"]
                if g not in m.completed or g == "CHARGER"
            ]
            m.segment_cursor = 0
            m.return_home = any(
                s.get("to_goal_id") == "HOME" for s in plan.get("segments", [])
            )
            m.current_goal_id = None
            m.status = "NAVIGATING"
            m.metrics["replan_count"] += 1
            self.emit(
                "replanning",
                details={
                    "source": "server_semantic_replan",
                    # Publish the validated semantic plan with its event. A
                    # backend poll can observe this before the control request
                    # commits its context; its previous plan is then stale.
                    "plan": copy.deepcopy(m.plan),
                    "plan_hash": m.plan_hash,
                    "completed_goal_ids": m.completed[:],
                    "current_pose": self.pose,
                    "planned_metrics": plan.get("metrics", {}),
                },
            )
            self.retire_navigation()
            if phase == "commit":
                self.finish_replan_preparation(m, body["token"], new_hash)
            return self.mission_snapshot(m)
        if kind == "cancel":
            mission = self.missions.get(body["mission_id"])
            if not mission:
                raise ContractError("UNKNOWN_MISSION", 404)
            if mission.status in TERMINAL:
                return mission.snapshot()
            if self.active != mission.id:
                raise ContractError("UNKNOWN_ACTIVE_MISSION", 404)
            self.generation += 1
            self.replan_control_epoch = getattr(self, "replan_control_epoch", 0) + 1
            preparation = getattr(self, "replan_preparation", None)
            if preparation and preparation.mission_id == mission.id:
                self.replan_preparation = None
            self.hold_pub.publish(Bool(data=True))
            mission.status = "CANCELLED"
            self.retire_navigation()
            self.emit(
                "cancelled",
                mission.current_goal_id,
                {"completed_goal_ids": mission.completed[:]},
            )
            return mission.snapshot()
        if kind == "handoff":
            if getattr(self, "replan_preparation", None):
                raise ContractError("REPLAN_PREPARATION_IN_PROGRESS", 409)
            m = self.missions.get(body["mission_id"])
            if not m:
                raise ContractError("UNKNOWN_MISSION", 404)
            goal = body.get("goal_id")
            if goal in m.completed:
                return m.snapshot()
            if m.status != "AWAITING_HANDOFF" or goal != m.current_goal_id:
                raise ContractError("REGISTERED_ARRIVAL_REQUIRED", 409)
            expected = self.registry[goal].get("slot", goal)
            self.replan_control_epoch = getattr(self, "replan_control_epoch", 0) + 1
            if body.get("scan_code", body.get("scanCode")) not in (expected, goal):
                self.emit(
                    "scan_rejected",
                    goal,
                    {"expected_slot": expected, "reason": "WRONG_REGISTERED_SLOT"},
                )
                return m.snapshot()
            self.emit("scan_verified", goal, {"slot": expected, "simulation": True})
            m.completed.append(goal)
            if m.queue and m.queue[0] == goal:
                m.queue.pop(0)
            m.payload += self.registry[goal].get("payload_kg", 0)
            m.metrics["completed_handoffs"] += 1
            self.emit(
                "handoff_verified",
                goal,
                {
                    "slot": expected,
                    "human_handoff_only": True,
                    "inventory_mutation": False,
                },
            )
            m.status = "NAVIGATING"
            m.current_goal_id = None
            return m.snapshot()
        if kind == "obstacle":
            if getattr(self, "replan_preparation", None):
                raise ContractError("REPLAN_PREPARATION_IN_PROGRESS", 409)
            self.replan_control_epoch = getattr(self, "replan_control_epoch", 0) + 1
            op = body.get("operation", "add")
            oid = body.get("id", "OB-PALLET-01")
            if op not in ("add", "remove"):
                raise ContractError("INVALID_OBSTACLE_OPERATION")
            if op == "remove":
                rect = self.obstacles.pop(oid, None)
            else:
                if body.get("scenario_id"):
                    scenario = next(
                        (
                            s
                            for s in self.world["scenarios"]
                            if s["id"] == body["scenario_id"]
                        ),
                        None,
                    )
                    if not scenario or not scenario["obstacles"]:
                        raise ContractError("UNKNOWN_OBSTACLE_SCENARIO")
                    rect = copy.deepcopy(scenario["obstacles"][0])
                    oid = rect["id"]
                elif "rect" in body:
                    rect = copy.deepcopy(body["rect"])
                elif body.get("geometry", {}).get("type") == "Polygon":
                    coords = body["geometry"]["coordinates"][0]
                    xs = [p[0] for p in coords]
                    ys = [p[1] for p in coords]
                    if len(coords) != 5 or any(
                        not math.isfinite(float(v)) for p in coords for v in p
                    ):
                        raise ContractError("INVALID_OBSTACLE_POLYGON")
                    # Rotated polygons require an explicit OBB, preserving their rotation.
                    if any(
                        p[0] not in (min(xs), max(xs)) or p[1] not in (min(ys), max(ys))
                        for p in coords
                    ):
                        raise ContractError(
                            "AXIS_ALIGNED_POLYGON_OR_EXPLICIT_OBB_REQUIRED"
                        )
                    rect = {
                        "x": (min(xs) + max(xs)) / 2,
                        "y": (min(ys) + max(ys)) / 2,
                        "width": max(xs) - min(xs),
                        "depth": max(ys) - min(ys),
                        "yaw_deg": 0,
                    }
                else:
                    raise ContractError("OBSTACLE_GEOMETRY_REQUIRED")
                if (
                    any(
                        not math.isfinite(float(rect.get(k, math.nan)))
                        for k in ("x", "y", "width", "depth")
                    )
                    or min(rect["width"], rect["depth"]) <= 0
                    or not math.isfinite(float(rect.get("yaw_deg", 0)))
                ):
                    raise ContractError("INVALID_OBSTACLE_GEOMETRY")
                if (
                    not 0 <= rect["x"] <= self.world["width"]
                    or not 0 <= rect["y"] <= self.world["height"]
                ):
                    raise ContractError("OBSTACLE_OUTSIDE_REGISTERED_MAP")
                rect["id"] = oid
                if polygons_overlap(footprint(self.world, self.pose), corners(rect)):
                    raise ContractError("OBSTACLE_OVERLAPS_CURRENT_ROBOT", 409)
                self.obstacles[oid] = rect
            command = {"operation": op, "id": oid, "rect": rect}
            self.obstacle_pub.publish(String(data=json.dumps(command)))
            if self.active:
                m = self.missions[self.active]
                if m.status not in TERMINAL:
                    if m.status == "NAVIGATING":
                        m.overlay_hold = True
                        m.overlay_hold_since = time.monotonic()
                        m.overlay_valid_path_ready = False
                        self.hold_pub.publish(Bool(data=True))
                        self.emit(
                            "replanning",
                            m.current_goal_id,
                            {
                                "source": "canonical_overlay_stop",
                                "state": "stopped_pending_nav2_path",
                                "current_pose": self.pose,
                                "completed_goal_ids": m.completed[:],
                            },
                        )
                    self.emit(
                        "obstacle_added" if op == "add" else "feedback",
                        m.current_goal_id,
                        {
                            "obstacle": command,
                            "source": "simulation_obstacle_service",
                            "completed_goal_ids": m.completed[:],
                        },
                    )
            return {
                "operation": op,
                "id": oid,
                "obstacles": copy.deepcopy(self.obstacles),
                "robot_pose": self.pose,
            }
        raise ContractError("UNKNOWN_OPERATION", 404)

    def submit(self, kind, body):
        future = Future()
        self.commands.put((kind, body, future))
        return future.result(timeout=10)

    def handler_type(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, status, payload):
                data = json.dumps(payload, allow_nan=False).encode()
                self.send_bytes(status, data)

            def send_bytes(self, status, data):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urlparse(self.path)
                parts = path.path.strip("/").split("/")
                if parts == ["health"]:
                    self.send(200, bridge.health())
                    return
                if len(parts) == 2 and parts[0] == "missions":
                    with bridge.lock:
                        mission = bridge.missions.get(parts[1])
                        if not mission:
                            status, payload = 404, {"error": "UNKNOWN_MISSION"}
                        else:
                            after = parse_qs(path.query).get("after_sequence", [None])[
                                0
                            ]
                            status, payload = (
                                200,
                                bridge.mission_snapshot(
                                    mission, int(after) if after is not None else None
                                ),
                            )
                        # Freeze one coherent mission before releasing the lock;
                        # slow socket writes must not block ROS callbacks.
                        data = json.dumps(payload, allow_nan=False).encode()
                    self.send_bytes(status, data)
                    return
                self.send(404, {"error": "UNKNOWN_ENDPOINT"})

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 <= length <= 1048576:
                        raise ContractError("REQUEST_TOO_LARGE", 413)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    parts = urlparse(self.path).path.strip("/").split("/")
                    if parts == ["missions"]:
                        kind = "start"
                    elif parts == ["obstacles"]:
                        kind = "obstacle"
                    elif (
                        len(parts) == 3
                        and parts[0] == "missions"
                        and parts[2] in ("cancel", "handoff", "replan")
                    ):
                        kind = parts[2]
                        body["mission_id"] = parts[1]
                    else:
                        raise ContractError("UNKNOWN_ENDPOINT", 404)
                    self.send(200, bridge.submit(kind, body))
                except ContractError as exc:
                    self.send(exc.status, {"error": exc.code})
                except (ValueError, TypeError, KeyError) as exc:
                    self.send(422, {"error": "INVALID_CONTRACT", "details": str(exc)})
                except Exception as exc:
                    bridge.get_logger().error(f"bridge request failed: {exc}")
                    self.send(503, {"error": "ROS_TRANSPORT_PAUSED"})

        return Handler


def main():
    rclpy.init()
    node = Bridge()
    try:
        rclpy.spin(node)
    finally:
        node.server.shutdown()
        node.evidence_file.close()
        node.event_file.close()
        node.destroy_node()
        rclpy.shutdown()
