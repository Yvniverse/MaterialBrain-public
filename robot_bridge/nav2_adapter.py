"""Dependency-injected Nav2 action bridge; not a web-to-cmd_vel relay.

Tested here against FakeNavigator only. Run ROS/Nav2 integration in its own process.
A robot-side integrator supplies a BasicNavigator, PoseStamped factory, ROS clock,
and independent localization/collision-monitor readiness. No ROS import on web host.
"""

from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Callable, Protocol, Any


class Navigator(Protocol):
    def goToPose(self, pose: Any, behavior_tree: str = "") -> Any: ...
    def isTaskComplete(self) -> bool: ...
    def getFeedback(self) -> Any: ...
    def getResult(self) -> Any: ...
    def cancelTask(self) -> Any: ...


@dataclass(frozen=True)
class DockCommand:
    mission_id: str
    command_id: str
    goal_id: str
    world_revision: str
    pose: dict
    frame_id: str = "map"
    mode: str = "simulation"


@dataclass(frozen=True)
class RobotReadiness:
    localized: bool
    pose_age_s: float
    collision_monitor_active: bool
    motors_armed: bool
    world_revision: str
    physical_map_verified: bool = False


class Nav2DockBridge:
    def __init__(
        self,
        navigator: Navigator,
        pose_factory: Callable[[dict, str], Any],
        *,
        allowed_docks: dict,
        physical_enabled: bool = False,
    ):
        self.nav = navigator
        self.pose_factory = pose_factory
        self.docks = allowed_docks
        self.physical_enabled = physical_enabled
        self.active = None
        self.cancelling = False
        self.completed = {}
        self.seen = {}

    def dispatch(self, cmd: DockCommand, ready: RobotReadiness):
        if cmd.command_id in self.completed:
            if self.seen[cmd.command_id] != cmd:
                raise ValueError("COMMAND_ID_REUSE")
            return self.completed[cmd.command_id]
        if self.active:
            if self.active.command_id == cmd.command_id and self.active == cmd:
                return {
                    "state": "CANCELLING" if self.cancelling else "RUNNING",
                    "command_id": cmd.command_id,
                }
            raise ValueError("WAIT_FOR_TERMINAL_ACK")
        if cmd.frame_id != "map" or cmd.mode not in ("simulation", "physical"):
            raise ValueError("INVALID_FRAME_OR_MODE")
        dock = self.docks.get(cmd.goal_id)
        if dock is None or any(
            not isfinite(cmd.pose.get(k, float("nan"))) for k in ("x", "y", "yaw")
        ):
            raise ValueError("UNKNOWN_DOCK")
        # Commands use an approved docking registry, not arbitrary LLM coordinates.
        if any(abs(cmd.pose[k] - dock[k]) > 1e-7 for k in ("x", "y", "yaw")):
            raise ValueError("DOCK_POSE_MISMATCH")
        if cmd.world_revision != ready.world_revision:
            raise ValueError("WORLD_REVISION_MISMATCH")
        if not ready.localized or not isfinite(ready.pose_age_s) or not 0 <= ready.pose_age_s <= 1:
            raise ValueError("LOCALIZATION_STALE")
        if cmd.mode == "physical" and not (
            self.physical_enabled
            and ready.physical_map_verified
            and ready.collision_monitor_active
            and ready.motors_armed
        ):
            raise ValueError("PHYSICAL_NOT_READY")
        self.seen[cmd.command_id] = cmd
        result = self.nav.goToPose(self.pose_factory(cmd.pose, cmd.frame_id))
        if result is False:
            raise RuntimeError("NAV2_REJECTED")
        self.active = cmd
        self.cancelling = False
        return {"state": "RUNNING", "command_id": cmd.command_id}

    def cancel(self):
        if self.active and not self.cancelling:
            self.nav.cancelTask()
            self.cancelling = True
        return {"state": "CANCELLING" if self.active else "IDLE"}

    def poll(self):
        if not self.active:
            return {"state": "IDLE"}
        if not self.nav.isTaskComplete():
            return {
                "state": "CANCELLING" if self.cancelling else "RUNNING",
                "feedback": self.nav.getFeedback(),
                "command_id": self.active.command_id,
            }
        result = self.nav.getResult()
        name = getattr(result, "name", str(result))
        state = {
            "SUCCEEDED": "ARRIVED",
            "CANCELED": "CANCELLED",
            "CANCELLED": "CANCELLED",
            "FAILED": "FAILED",
        }.get(name, "UNKNOWN_TERMINAL")
        out = {
            "state": state,
            "nav2_result": name,
            "command_id": self.active.command_id,
            "goal_id": self.active.goal_id,
            "inventory_written": False,
        }
        self.completed[self.active.command_id] = out
        self.active = None
        self.cancelling = False
        return out


def ros_pose_factory(navigator):
    """Call inside an installed ROS 2 environment. TF frame: map, metres, radians."""
    from geometry_msgs.msg import PoseStamped
    from math import sin, cos

    def factory(p, frame):
        msg = PoseStamped()
        msg.header.frame_id = frame
        msg.header.stamp = navigator.get_clock().now().to_msg()
        msg.pose.position.x = float(p["x"])
        msg.pose.position.y = float(p["y"])
        msg.pose.orientation.z = sin(p["yaw"] / 2)
        msg.pose.orientation.w = cos(p["yaw"] / 2)
        return msg

    return factory
