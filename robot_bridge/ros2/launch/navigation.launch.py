from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    share = Path(get_package_share_directory("materialbrain_ros2"))
    params = str(share / "config/nav2_params.yaml")
    nav_parameters = {
        "default_nav_to_pose_bt_xml": str(share / "config/navigate_to_pose.xml"),
        "default_nav_through_poses_bt_xml": str(share / "config/navigate_through_poses.xml"),
    }
    nodes = [
        Node(package="materialbrain_ros2", executable="sim_base", output="screen"),
        Node(
            package="materialbrain_ros2",
            executable="mission_bridge",
            parameters=[{"use_sim_time": True}],
            output="screen",
        ),
    ]
    servers = [
        ("nav2_map_server", "map_server"),
        ("nav2_planner", "planner_server"),
        ("nav2_controller", "controller_server"),
        ("nav2_behaviors", "behavior_server"),
        ("nav2_bt_navigator", "bt_navigator"),
    ]
    nodes += [
        Node(
            package=p,
            executable=e,
            name=e,
            parameters=[params] + ([nav_parameters] if e == "bt_navigator" else []),
            output="screen",
        )
        for p, e in servers
    ]
    nodes += [
        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_navigation",
            parameters=[
                {
                    "use_sim_time": True,
                    "autostart": True,
                    "bond_timeout": 0.0,
                    "node_names": [e for _, e in servers],
                }
            ],
            output="screen",
        )
    ]
    return LaunchDescription(nodes)
