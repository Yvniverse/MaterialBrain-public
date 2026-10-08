"""Actual ROS differential-drive base, analytic scan and swept footprint collision gate."""

import json
import math
import os
import time

import rclpy
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.clock import Clock, ClockType
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rosgraph_msgs.msg import Clock as ClockMessage
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, Trigger
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster

from .geometry import clearance, load_world, ray_ranges, static_obstacles


def yaw_quaternion(msg, yaw):
    msg.z = math.sin(yaw / 2)
    msg.w = math.cos(yaw / 2)


class SimBase(Node):
    def __init__(self):
        super().__init__("materialbrain_sim_base")
        self.world = load_world(
            os.environ.get("MATERIALBRAIN_WORLD", "/opt/materialbrain/world.v3.json")
        )
        self.pose = dict(self.world["robot"]["pose"])
        self.obstacles = {}
        self.held = True
        self.command = (0.0, 0.0)
        self.last_command = 0.0
        self.sim_time = 10.0
        self.dt = 0.02
        self.ticks = 0
        self.distance = 0.0
        self.min_clearance = 100.0
        self.prevented = 0
        self.cmd_count = 0
        self.collisions = 0
        self.collision_latched = False
        self.clock_pub = self.create_publisher(ClockMessage, "/clock", 10)
        self.odom_pub = self.create_publisher(Odometry, "/odom", 10)
        self.scan_pub = self.create_publisher(LaserScan, "/scan", qos_profile_sensor_data)
        self.telemetry_pub = self.create_publisher(String, "/simulation/telemetry", 10)
        self.create_subscription(Twist, "/cmd_vel", self.on_command, 10)
        self.create_subscription(
            Bool, "/simulation/hold", lambda m: setattr(self, "held", m.data), 10
        )
        self.create_subscription(String, "/simulation/obstacles", self.on_obstacle, 10)
        self.create_service(SetBool, "/simulation/central_obstacle", self.central_obstacle)
        self.create_service(Trigger, "/simulation/reset", self.reset)
        self.tf = TransformBroadcaster(self)
        self.static_tf = StaticTransformBroadcaster(self)
        transforms = []
        for parent, child, z in (("warehouse_map", "odom", 0.0), ("base_link", "laser", 0.23)):
            tr = TransformStamped()
            tr.header.frame_id = parent
            tr.child_frame_id = child
            tr.transform.translation.z = z
            tr.transform.rotation.w = 1.0
            transforms.append(tr)
        self.static_tf.sendTransform(transforms)
        scale = float(os.environ.get("MATERIALBRAIN_SIM_TIME_SCALE", "1.0"))
        self.create_timer(self.dt / scale, self.tick, clock=Clock(clock_type=ClockType.STEADY_TIME))

    def on_command(self, msg):
        self.command = (msg.linear.x, msg.angular.z)
        self.last_command = time.monotonic()
        self.cmd_count += 1

    def on_obstacle(self, msg):
        command = json.loads(msg.data)
        if command["operation"] == "remove":
            self.obstacles.pop(command["id"], None)
        else:
            self.obstacles[command["id"]] = command["rect"]

    def central_obstacle(self, req, res):
        r = self.world["scenarios"][1]["obstacles"][0]
        if req.data:
            self.obstacles[r["id"]] = r
        else:
            self.obstacles.pop(r["id"], None)
        res.success = True
        res.message = json.dumps({"id": r["id"], "inserted": req.data})
        return res

    def reset(self, req, res):
        if not self.held:
            res.success = False
            res.message = "pause navigation before resetting isolated simulation"
            return res
        self.pose = dict(self.world["robot"]["pose"])
        self.obstacles = {}
        self.distance = 0.0
        self.min_clearance = 100.0
        self.prevented = 0
        self.cmd_count = 0
        self.command = (0.0, 0.0)
        self.collisions = 0
        self.collision_latched = False
        res.success = True
        res.message = "isolated simulator reset"
        return res

    def tick(self):
        self.sim_time += self.dt
        self.ticks += 1
        stamp = ClockMessage()
        stamp.clock.sec = int(self.sim_time)
        stamp.clock.nanosec = int((self.sim_time % 1) * 1e9)
        self.clock_pub.publish(stamp)
        now = stamp.clock
        v, w = (
            self.command
            if not self.held and time.monotonic() - self.last_command < 0.6
            else (0.0, 0.0)
        )
        v = max(-0.3, min(self.world["robot"]["max_speed"], v))
        w = max(-1.0, min(1.0, w))
        # Enforce canonical slow areas for transient controller commands too.
        for z in self.world["zones"]:
            if (
                z["kind"] == "slow"
                and abs(self.pose["x"] - z["x"]) < z["width"] / 2
                and abs(self.pose["y"] - z["y"]) < z["depth"] / 2
            ):
                v = max(-z["speed"], min(z["speed"], v))
        obs = static_obstacles(self.world) + list(self.obstacles.values())
        candidate = dict(self.pose)
        for _ in range(4):
            d = self.dt / 4
            yaw = candidate["yaw"] + w * d / 2
            candidate["x"] += v * math.cos(yaw) * d
            candidate["y"] += v * math.sin(yaw) * d
            candidate["yaw"] = math.atan2(
                math.sin(candidate["yaw"] + w * d), math.cos(candidate["yaw"] + w * d)
            )
            if clearance(self.world, candidate, obs) <= 0.012:
                if abs(v) + abs(w) > 0.02:
                    self.prevented += 1
                candidate = dict(self.pose)
                v = w = 0.0
                break
        self.distance += math.hypot(
            candidate["x"] - self.pose["x"], candidate["y"] - self.pose["y"]
        )
        self.pose = candidate
        if self.ticks % 5 == 0:
            current_clearance = clearance(self.world, self.pose, obs)
            self.min_clearance = min(self.min_clearance, current_clearance)
            penetrating = current_clearance <= 0
            if penetrating and not self.collision_latched:
                self.collisions += 1
            self.collision_latched = penetrating
        odom = Odometry()
        odom.header.stamp = now
        odom.header.frame_id = "odom"
        odom.child_frame_id = "base_link"
        odom.pose.pose.position.x = self.pose["x"]
        odom.pose.pose.position.y = self.pose["y"]
        yaw_quaternion(odom.pose.pose.orientation, self.pose["yaw"])
        odom.twist.twist.linear.x = v
        odom.twist.twist.angular.z = w
        self.odom_pub.publish(odom)
        tf = TransformStamped()
        tf.header = odom.header
        tf.child_frame_id = "base_link"
        tf.transform.translation.x = self.pose["x"]
        tf.transform.translation.y = self.pose["y"]
        tf.transform.rotation = odom.pose.pose.orientation
        self.tf.sendTransform(tf)
        if self.ticks % 5 == 0:
            scan = LaserScan()
            scan.header.stamp = now
            scan.header.frame_id = "laser"
            scan.angle_min = -math.pi
            scan.angle_increment = 2 * math.pi / 360
            scan.angle_max = math.pi - scan.angle_increment
            scan.range_min = 0.025
            scan.range_max = 12.0
            scan.scan_time = 0.1
            scan.ranges = ray_ranges(self.world, self.pose, obs)
            self.scan_pub.publish(scan)
            facts = {
                "sim_time_s": self.sim_time,
                "pose": self.pose,
                "v_mps": v,
                "w_rps": w,
                "held": self.held,
                "distance_m": self.distance,
                "min_clearance_m": self.min_clearance,
                "collision_count": self.collisions,
                "collision_prevented_count": self.prevented,
                "cmd_vel_messages": self.cmd_count,
                "scan_beams": 360,
                "scan_nearest_m": min(scan.ranges),
                "obstacle_ids": list(self.obstacles),
            }
            self.telemetry_pub.publish(String(data=json.dumps(facts)))


def main():
    rclpy.init()
    node = SimBase()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
