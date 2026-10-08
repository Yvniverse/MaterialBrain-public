import copy
import unittest
from pathlib import Path

from materialbrain_ros2.contracts import ContractError, Mission, route_waypoints, validate_plan
from materialbrain_ros2.geometry import clearance, load_world, ray_ranges, static_obstacles

ROOT = Path(__file__).resolve().parents[3]
WORLD = ROOT / "backend/app/services/embodied_navigation/world.v3.json"


class ContractAndGeometryTests(unittest.TestCase):
    def setUp(self):
        self.world = load_world(WORLD)
        self.snapshot = {
            "map_id": self.world["id"],
            "revision": "f" * 64,
            "docks": [*self.world["goals"], {"id": "HOME", "pose": self.world["home"]}],
        }
        self.plan = {
            "schema_version": 1,
            "mission_id": "test-001",
            "map_id": self.world["id"],
            "map_revision": "f" * 64,
            "status": "READY",
            "ordered_goal_ids": ["P-IC", "P-SENSOR", "P-LAB"],
            "completed_goal_ids": [],
            "constraints": {
                "battery_pct": 82,
                "battery_reserve_pct": 15,
                "payload_capacity_kg": 18,
            },
            "stops": [],
            "segments": [{"to_goal_id": "HOME"}],
        }

    def test_canonical_registered_goal_ids(self):
        registry = validate_plan(self.plan, self.snapshot, self.world)
        self.assertEqual(registry["P-IC"]["pose"], {"x": 2.75, "y": 3.75, "yaw": 3.141592653589793})

    def test_stale_hash_is_rejected(self):
        self.plan["map_revision"] = "3.0.0"
        with self.assertRaisesRegex(ContractError, "STALE_OR_UNKNOWN"):
            validate_plan(self.plan, self.snapshot, self.world)

    def test_model_coordinate_goal_is_rejected(self):
        self.plan["stops"] = [{"goal_id": "P-IC", "pose": {"x": 3, "y": 3, "yaw": 0}}]
        with self.assertRaisesRegex(ContractError, "FREE_COORDINATE"):
            validate_plan(self.plan, self.snapshot, self.world)

    def test_unknown_id_rejected(self):
        self.plan["ordered_goal_ids"] = ["invented"]
        with self.assertRaisesRegex(ContractError, "UNKNOWN_REGISTERED"):
            validate_plan(self.plan, self.snapshot, self.world)

    def test_duplicate_goal_rejected(self):
        self.plan["ordered_goal_ids"] = ["P-IC", "P-IC"]
        with self.assertRaisesRegex(ContractError, "UNIQUE_REGISTERED"):
            validate_plan(self.plan, self.snapshot, self.world)

    def test_hardware_flag_rejected(self):
        self.plan["hardware_control"] = True
        with self.assertRaisesRegex(ContractError, "SIMULATION_ONLY"):
            validate_plan(self.plan, self.snapshot, self.world)

    def test_unknown_robot_rejected(self):
        self.plan["constraints"]["robot_class"] = "quadruped"
        with self.assertRaisesRegex(ContractError, "INCOMPATIBLE_ROBOT"):
            validate_plan(self.plan, self.snapshot, self.world)

    def test_payload_registration_limits_cannot_be_increased(self):
        self.plan["constraints"]["payload_capacity_kg"] = 30
        with self.assertRaisesRegex(ContractError, "EXCEEDS_REGISTERED"):
            validate_plan(self.plan, self.snapshot, self.world)

    def test_nan_battery_rejected(self):
        self.plan["constraints"]["battery_pct"] = float("nan")
        with self.assertRaisesRegex(ContractError, "INVALID_BATTERY"):
            validate_plan(self.plan, self.snapshot, self.world)

    def test_remaining_preserves_completed_handoffs(self):
        self.plan["completed_goal_ids"] = ["P-IC"]
        mission = Mission(self.plan, self.snapshot, self.world)
        self.assertEqual(mission.remaining(), ["P-SENSOR", "P-LAB"])
        self.assertTrue(mission.return_home)

    def test_event_identity_and_sequence_are_monotonic(self):
        mission = Mission(self.plan, self.snapshot, self.world)
        events = [mission.event(t, "P-IC") for t in ("started", "arrived", "handoff_verified")]
        self.assertEqual([e["sequence"] for e in events], [1, 2, 3])
        self.assertEqual(len({e["event_id"] for e in events}), 3)
        self.assertEqual(events[0]["details"]["execution_boundary"], "ros2_nav2_simulation")
        self.assertEqual(len(mission.snapshot(1)["events"]), 2)

    def test_returned_snapshot_cannot_mutate_ledger(self):
        m = Mission(self.plan, self.snapshot, self.world)
        m.event("started")
        s = m.snapshot()
        s["events"][0]["details"]["hardware_control"] = True
        self.assertFalse(m.events[0]["details"]["hardware_control"])

    def graph_segment(self):
        dock = next(g for g in self.world["goals"] if g["id"] == "P-IC")
        nodes = [
            {"id": "A", "x": 4.0, "y": 4.0},
            {"id": "B", "x": 3.75, "y": 4.0},
            {"id": "P-IC", **dock["pose"]},
        ]
        self.snapshot["route_graph"] = {
            "nodes": nodes,
            "edges": [
                {"id": "AB", "from": "A", "to": "B", "width_m": 2},
                {"id": "BC", "from": "B", "to": "P-IC", "width_m": 2},
            ],
        }
        segment = {
            "from_goal_id": "HOME",
            "to_goal_id": "P-IC",
            "node_ids": ["A", "B", "P-IC"],
            "edge_ids": ["AB", "BC"],
            "poses": [{"x": n["x"], "y": n["y"], "yaw": 0} for n in nodes],
        }
        self.plan["segments"] = [segment]
        return segment

    def test_semantic_waypoints_retain_turn_and_registered_goal(self):
        segment = self.graph_segment()
        validate_plan(self.plan, self.snapshot, self.world)
        points = route_waypoints(
            self.snapshot, segment, {"x": 4, "y": 4, "yaw": 0}, "P-IC", self.world
        )
        self.assertEqual([p["id"] for p in points], ["B", "P-IC"])
        self.assertEqual(points[-1]["yaw"], 3.141592653589793)

    def test_semantic_directed_edge_order_rejected(self):
        segment = self.graph_segment()
        segment["edge_ids"] = ["BC", "AB"]
        with self.assertRaisesRegex(ContractError, "INVALID_DIRECTED_ROUTE_EDGE"):
            validate_plan(self.plan, self.snapshot, self.world)

    def test_semantic_invented_route_pose_rejected(self):
        segment = self.graph_segment()
        segment["poses"][1]["x"] = 100
        with self.assertRaisesRegex(ContractError, "FREE_COORDINATE_ROUTE_REJECTED"):
            validate_plan(self.plan, self.snapshot, self.world)

    def test_semantic_segment_cannot_invent_final_dock_connector(self):
        segment = self.graph_segment()
        segment["to_goal_id"] = "P-SENSOR"
        with self.assertRaisesRegex(ContractError, "DESTINATION_MUST_MATCH"):
            validate_plan(self.plan, self.snapshot, self.world)

    def test_semantic_observed_pose_requires_replan(self):
        segment = self.graph_segment()
        with self.assertRaisesRegex(ContractError, "OBSERVED_START_POSE_CHANGED"):
            route_waypoints(self.snapshot, segment, {"x": 6, "y": 4, "yaw": 0}, "P-IC", self.world)

    def test_repeated_registered_charger_occurrences_are_preserved(self):
        self.snapshot["docks"].append(
            {"id": "CHARGER", "pose": {"x": 21.5, "y": 15.5, "yaw": 1.5707963267948966}}
        )
        self.plan["ordered_goal_ids"] = ["CHARGER", "P-IC", "CHARGER"]
        mission = Mission(self.plan, self.snapshot, self.world)
        self.assertEqual(mission.queue, ["CHARGER", "P-IC", "CHARGER"])

    def test_full_robot_footprint_detects_obstacle_inside_front_corner(self):
        w = copy.deepcopy(self.world)
        r = {"x": 10.4, "y": 10.3, "width": 0.1, "depth": 0.1, "yaw_deg": 0}
        self.assertEqual(clearance(w, {"x": 10, "y": 10, "yaw": 0}, [r]), 0)

    def test_rotated_equipment_detected(self):
        a = self.world["assets"][0]
        self.assertEqual(clearance(self.world, {"x": a["x"], "y": a["y"], "yaw": 0}, [a]), 0)

    def test_registered_docks_have_positive_actual_footprint_clearance(self):
        obstacles = static_obstacles(self.world)
        for dock in self.world["goals"]:
            self.assertGreater(clearance(self.world, dock["pose"], obstacles), 0, dock["id"])

    def test_scan_is_geometry_derived_and_repeatable(self):
        pose = {"x": 12, "y": 9, "yaw": 0}
        obstacles = []
        ranges = ray_ranges(self.world, pose, obstacles)
        self.assertEqual(ranges, ray_ranges(self.world, pose, obstacles))
        self.assertAlmostEqual(ranges[180], 12)
        self.assertAlmostEqual(ranges[270], 9)
        obstacle = {"x": 14, "y": 9, "width": 1, "depth": 1}
        self.assertAlmostEqual(ray_ranges(self.world, pose, [obstacle])[180], 1.5)


if __name__ == "__main__":
    unittest.main()
