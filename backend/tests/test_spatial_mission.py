"""Deterministic routing/regulatory/resource regressions; no database required."""

import copy
import itertools
import json
import math
import unittest

from pydantic import ValidationError

from app.schemas.spatial import MissionPlan
from app.services.spatial_mission import plan_mission
from app.services.spatial_mission.exact import held_karp_exact
from app.services.spatial_mission.geometry import active_overlays, parse_time
from app.services.spatial_mission.semantic import RobotProfile, SemanticGraph


def synthetic_snapshot():
    poses = {
        "H": (2, 2),
        "X": (5, 2),
        "A": (8, 2),
        "D": (2, 6),
        "B": (8, 6),
        "C": (15, 2),
    }
    nodes = [
        {"id": code, "x": x, "y": y, "yaw": 0, "kind": "dock"} for code, (x, y) in poses.items()
    ]
    edges = []
    for a, b in (
        ("H", "X"),
        ("X", "A"),
        ("H", "D"),
        ("D", "A"),
        ("D", "B"),
        ("A", "B"),
        ("A", "C"),
        ("B", "C"),
    ):
        risk = 0.3 if a == "X" or b == "X" else 0
        for source, target in ((a, b), (b, a)):
            length = math.dist(poses[source], poses[target])
            edges.append(
                {
                    "id": source + "-" + target,
                    "from": source,
                    "to": target,
                    "length_m": length,
                    "width_m": 4.0,
                    "speed_limit_mps": 1.0,
                    "risk_level": risk,
                    "energy_wh": length * 0.01,
                    "human_mixed": bool(risk),
                    "crosses_esd_sensitive": bool(risk),
                    "allowed_robot_classes": ["MB-R01"],
                    "zone_ids": [],
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [list(poses[source]), list(poses[target])],
                    },
                }
            )
    docks = [
        {
            "id": "HOME",
            "node_id": "H",
            "pose": {"x": 2, "y": 2, "yaw": 0},
            "kind": "home",
            "robot_classes": ["MB-R01"],
            "capabilities": ["navigate"],
        }
    ]
    for code in ("A", "B", "C"):
        docks.append(
            {
                "id": code,
                "node_id": code,
                "pose": {"x": poses[code][0], "y": poses[code][1], "yaw": 0},
                "kind": "handoff",
                "robot_classes": ["MB-R01"],
                "capabilities": ["navigate", "human_handoff"],
                "payload_kg": 0.5,
                "service_s": 1,
            }
        )
    docks.append(
        {
            "id": "CHARGER",
            "node_id": "D",
            "pose": {"x": 2, "y": 6, "yaw": 0},
            "kind": "charging",
            "robot_classes": ["MB-R01"],
            "capabilities": ["navigate", "charge"],
            "payload_kg": 0,
            "service_s": 0,
        }
    )
    return {
        "schema_version": 1,
        "map_id": "TEST",
        "revision": "test-content-hash",
        "frame": {"name": "warehouse_map", "unit": "m", "srid": 0, "site_georef": None},
        "geometry": {
            "floor": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [20, 0], [20, 10], [0, 10], [0, 0]]],
            },
            "assets": [],
            "walls": [],
            "columns": [],
        },
        "zones": [],
        "route_graph": {"nodes": nodes, "edges": edges},
        "docks": docks,
        "affordances": [],
        "dynamic_overlays": [],
        "provenance": {
            "source": "deterministic_test",
            "evaluation_time": "2026-10-04T12:00:00Z",
            "robot": {
                "width": 0.4,
                "length": 0.4,
                "margin": 0.1,
                "max_speed": 1,
                "angular_speed": 10,
                "battery_wh": 10,
                "idle_w": 0,
                "wh_per_m": 0.01,
                "charge_w": 60,
            },
        },
    }


def mission_request(snapshot, goals=None, **overrides):
    return {
        "map_id": snapshot["map_id"],
        "map_revision": snapshot["revision"],
        "start_pose": {"x": 2, "y": 2, "yaw": 0},
        "goal_ids": goals or ["A"],
        "constraints": {"robot_class": "MB-R01"},
        **overrides,
    }


class MissionContractTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = synthetic_snapshot()

    def plan(self, goals=None, **kwargs):
        return plan_mission(self.snapshot, mission_request(self.snapshot, goals, **kwargs))

    def test_registered_graph_route_and_frozen_schema(self):
        plan = self.plan()
        MissionPlan.model_validate(plan)
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["segments"][0]["node_ids"], ["H", "X", "A"])
        self.assertEqual(plan["segments"][-1]["to_goal_id"], "HOME")
        self.assertFalse(plan["metrics"]["inventory_written"])
        self.assertFalse(plan["metrics"]["execution_started"])
        json.dumps(plan, allow_nan=False)

    def test_semantic_profiles_choose_longer_lower_risk_path(self):
        fast, safe, esd = [self.plan(profile=p) for p in ("fastest", "safest", "esd_safe")]
        self.assertGreater(safe["metrics"]["distance_m"], fast["metrics"]["distance_m"])
        self.assertLess(safe["objective_terms"]["risk"], fast["objective_terms"]["risk"])
        self.assertNotEqual(esd["segments"][0]["edge_ids"], fast["segments"][0]["edge_ids"])

    def test_cost_terms_sum_to_graph_profile_cost(self):
        graph = SemanticGraph(
            self.snapshot,
            profile="safest",
            robot=RobotProfile.from_snapshot(self.snapshot, "MB-R01"),
        )
        path = graph.path("H", "A")
        terms, w = path["cost_terms"], graph.weights
        total = (
            terms["travel_time_s"]
            + w["clearance"] * terms["clearance_penalty"]
            + w["risk"] * terms["risk"]
            + w["energy"] * terms["energy_wh"]
            + w["semantic"] * terms["semantic_penalty"]
        )
        self.assertAlmostEqual(total, terms["cost_s"])

    def test_hard_width_excludes_edge(self):
        for edge in self.snapshot["route_graph"]["edges"]:
            if "X" in (edge["from"], edge["to"]):
                edge["width_m"] = 0.59
        plan = self.plan()
        self.assertEqual(plan["status"], "READY")
        self.assertNotIn("X", plan["segments"][0]["node_ids"])

    def test_all_narrow_edges_are_infeasible(self):
        for edge in self.snapshot["route_graph"]["edges"]:
            edge["width_m"] = 0.59
        self.assertEqual(self.plan()["status"], "INFEASIBLE")

    def test_robot_class_hard_rule(self):
        result = self.plan(constraints={"robot_class": "FORKLIFT"})
        self.assertEqual(result["status"], "INFEASIBLE")
        self.assertEqual(result["violations"][0]["code"], "DOCK_ROBOT_CLASS")

    def test_keepout_geometry_is_hard_even_without_zone_refs(self):
        self.snapshot["zones"] = [
            {
                "id": "K",
                "kind": "keepout",
                "polygon": {
                    "type": "Polygon",
                    "coordinates": [[[4, 1], [6, 1], [6, 2.5], [4, 2.5], [4, 1]]],
                },
            }
        ]
        result = self.plan()
        self.assertEqual(result["status"], "READY")
        self.assertNotIn("X", result["segments"][0]["node_ids"])

    def test_keepout_applies_to_footprint_not_only_centreline(self):
        self.snapshot["zones"] = [
            {
                "id": "near",
                "kind": "keepout",
                "polygon": {
                    "type": "Polygon",
                    "coordinates": [[[4, 2.2], [6, 2.2], [6, 2.4], [4, 2.4], [4, 2.2]]],
                },
            }
        ]
        result = self.plan()
        self.assertEqual(result["status"], "READY")
        self.assertNotIn("X", result["segments"][0]["node_ids"])

    def test_directed_graph_is_not_assumed_bidirectional(self):
        self.snapshot["route_graph"]["edges"] = [
            e for e in self.snapshot["route_graph"]["edges"] if e["from"] != "A"
        ]
        self.assertEqual(self.plan()["status"], "INFEASIBLE")
        self.assertEqual(self.plan(return_home=False)["status"], "READY")

    def test_unregistered_goal_clarifies_without_coordinate_guess(self):
        result = self.plan(["FAKE"])
        self.assertEqual(result["status"], "CLARIFICATION")
        self.assertEqual(result["segments"], [])

    def test_stale_revision_does_not_plan(self):
        result = self.plan(map_revision="stale")
        self.assertEqual(result["status"], "CLARIFICATION")
        self.assertEqual(result["violations"][0]["code"], "MAP_REVISION_STALE")

    def test_map_mismatch_does_not_plan(self):
        self.assertEqual(self.plan(map_id="OTHER")["status"], "CLARIFICATION")

    def test_duplicate_goals_are_deduplicated(self):
        self.assertEqual(self.plan(["A", "A"])["ordered_goal_ids"], ["A"])

    def test_completed_goals_not_repeated_and_payload_preserved(self):
        result = self.plan(
            ["A", "B"], completed_goal_ids=["A"], start_pose={"x": 8, "y": 2, "yaw": 0}
        )
        self.assertEqual(result["ordered_goal_ids"], ["B"])
        self.assertEqual(result["completed_goal_ids"], ["A"])
        self.assertEqual(result["constraints"]["initial_payload_kg"], 0.5)
        self.assertEqual(result["metrics"]["payload_kg"], 1)

    def test_completed_payload_counts_against_capacity(self):
        result = self.plan(
            ["A", "B"],
            completed_goal_ids=["A"],
            constraints={"payload_capacity_kg": 0.75, "robot_class": "MB-R01"},
        )
        self.assertEqual(result["status"], "INFEASIBLE")

    def test_all_goals_completed_only_returns_home(self):
        result = self.plan(["A"], completed_goal_ids=["A"], start_pose={"x": 8, "y": 2, "yaw": 0})
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["ordered_goal_ids"], [])
        self.assertEqual([s["to_goal_id"] for s in result["segments"]], ["HOME"])

    def test_no_return_keeps_final_service_time(self):
        result = self.plan(return_home=False, stops=[{"goal_id": "A", "service_s": 50}])
        self.assertEqual(len(result["segments"]), 1)
        self.assertGreaterEqual(result["metrics"]["eta_s"], result["metrics"]["travel_s"] + 50)

    def test_capacity_infeasible_not_silently_drop_task(self):
        result = self.plan(
            ["A", "B"], constraints={"payload_capacity_kg": 0.7, "robot_class": "MB-R01"}
        )
        self.assertEqual(result["status"], "INFEASIBLE")
        self.assertEqual(result["ordered_goal_ids"], [])

    def test_service_and_time_window_wait_are_observable(self):
        result = self.plan(stops=[{"goal_id": "A", "service_s": 20, "time_window_s": [100, 130]}])
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["stops"][0]["arrival_s"], 100)
        self.assertEqual(result["metrics"]["service_s"], 20)
        self.assertGreater(result["metrics"]["waiting_s"], 80)

    def test_impossible_time_window_infeasible(self):
        result = self.plan(stops=[{"goal_id": "A", "time_window_s": [0, 1]}])
        self.assertEqual(result["status"], "INFEASIBLE")
        self.assertEqual(result["violations"][0]["code"], "TIME_WINDOW_UNREACHABLE")

    def test_joint_time_windows_are_not_ignored(self):
        result = self.plan(
            ["A", "B"],
            stops=[
                {"goal_id": "A", "service_s": 20, "time_window_s": [0, 8]},
                {"goal_id": "B", "service_s": 20, "time_window_s": [0, 11]},
            ],
        )
        self.assertEqual(result["status"], "INFEASIBLE")

    def test_high_priority_goal_changes_order(self):
        result = self.plan(
            ["B", "C"], stops=[{"goal_id": "B", "priority": 0}, {"goal_id": "C", "priority": 100}]
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["ordered_goal_ids"][0], "C")
        self.assertGreater(result["objective_terms"]["priority_arrival_penalty_s"], 0)

    def test_low_battery_inserts_registered_charge_and_preserves_reserve(self):
        result = self.plan(
            constraints={"battery_pct": 21, "battery_reserve_pct": 20, "robot_class": "MB-R01"}
        )
        self.assertEqual(result["status"], "READY")
        self.assertIn("CHARGER", result["ordered_goal_ids"])
        self.assertEqual(result["metrics"]["charging_stops"], 1)
        self.assertGreaterEqual(result["metrics"]["battery_after_pct"], 20)
        self.assertEqual(
            next(s for s in result["stops"] if s["kind"] == "charge")["battery_departure_pct"], 100
        )

    def test_unreachable_charge_is_honest_infeasible(self):
        result = self.plan(
            constraints={"battery_pct": 20.1, "battery_reserve_pct": 20, "robot_class": "MB-R01"}
        )
        self.assertEqual(result["status"], "INFEASIBLE")

    def test_no_charger_low_battery_is_infeasible(self):
        self.snapshot["docks"] = [d for d in self.snapshot["docks"] if d["id"] != "CHARGER"]
        result = self.plan(
            constraints={"battery_pct": 21, "battery_reserve_pct": 20, "robot_class": "MB-R01"}
        )
        self.assertEqual(result["status"], "INFEASIBLE")

    def test_waiting_consumes_battery(self):
        self.snapshot["docks"] = [d for d in self.snapshot["docks"] if d["id"] != "CHARGER"]
        self.snapshot["provenance"]["robot"]["idle_w"] = 36
        result = self.plan(
            constraints={"battery_pct": 30, "battery_reserve_pct": 20, "robot_class": "MB-R01"},
            stops=[{"goal_id": "A", "time_window_s": [1000, 1100]}],
        )
        self.assertEqual(result["status"], "INFEASIBLE")

    def test_closure_replans_without_using_closed_edge(self):
        closed = ["H-X", "X-H", "X-A", "A-X"]
        result = self.plan(
            dynamic_overlays=[
                {
                    "id": "closure",
                    "kind": "closure",
                    "edge_ids": closed,
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            ]
        )
        self.assertEqual(result["status"], "READY")
        self.assertFalse(set(closed) & {e for s in result["segments"] for e in s["edge_ids"]})

    def test_expired_ttl_does_not_constrain(self):
        result = self.plan(
            dynamic_overlays=[
                {
                    "id": "old",
                    "kind": "closure",
                    "edge_ids": ["H-X", "X-A"],
                    "created_at": "2026-10-04T11:00:00Z",
                    "ttl_s": 1,
                }
            ]
        )
        self.assertEqual(result["segments"][0]["node_ids"], ["H", "X", "A"])
        self.assertEqual(result["constraints"]["expired_overlay_ids"], ["old"])

    def test_request_cannot_expire_persisted_closure(self):
        self.snapshot["dynamic_overlays"] = [
            {"id": "same", "kind": "closure", "edge_ids": ["H-X", "X-A"]}
        ]
        result = self.plan(
            dynamic_overlays=[
                {"id": "same", "kind": "closure", "created_at": "2000-01-01T00:00:00Z", "ttl_s": 0}
            ]
        )
        self.assertNotIn("X", result["segments"][0]["node_ids"])

    def test_current_pose_first_mile_checked_against_geometry(self):
        result = self.plan(start_pose={"x": 2.2, "y": 2.1, "yaw": 0})
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["segments"][0]["edge_ids"][0].startswith("__first_mile__"))

    def test_current_pose_inside_asset_is_rejected(self):
        self.snapshot["geometry"]["assets"] = [
            {
                "id": "block",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1.5, 1.5], [3, 1.5], [3, 3], [1.5, 3], [1.5, 1.5]]],
                },
            }
        ]
        result = self.plan(start_pose={"x": 2.1, "y": 2.1, "yaw": 0})
        self.assertEqual(result["status"], "INFEASIBLE")

    def test_inputs_are_not_mutated_and_mission_id_reproducible(self):
        request = mission_request(self.snapshot, ["A", "B"])
        before = copy.deepcopy((self.snapshot, request))
        first, second = plan_mission(self.snapshot, request), plan_mission(self.snapshot, request)
        self.assertEqual((self.snapshot, request), before)
        self.assertEqual(first["mission_id"], second["mission_id"])
        self.assertEqual(first["ordered_goal_ids"], second["ordered_goal_ids"])

    def test_nonfinite_pose_and_invalid_window_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.plan(start_pose={"x": float("nan"), "y": 2})
        with self.assertRaises(ValidationError):
            self.plan(stops=[{"goal_id": "A", "time_window_s": [10, 2]}])

    def test_extra_unrequested_stop_clarifies(self):
        result = self.plan(stops=[{"goal_id": "B"}])
        self.assertEqual(result["violations"][0]["code"], "STOP_NOT_REQUESTED")

    def test_exact_comparison_does_not_claim_ortools_optimality(self):
        result = self.plan(["A", "B", "C"])
        self.assertTrue(result["solver"]["exact_baseline"]["optimality_proven"])
        self.assertTrue(result["solver"]["exact_baseline"]["matches_unconstrained_bound"])
        if result["solver"]["status"] != "ROUTING_OPTIMAL":
            self.assertFalse(result["solver"]["optimality_proven"])


class ExactAndTTLTests(unittest.TestCase):
    def test_asymmetric_held_karp_equals_permutation_oracle(self):
        costs = [
            [0, 7, 2, 9, 4],
            [3, 0, 5, 1, 8],
            [6, 2, 0, 4, 3],
            [1, 5, 8, 0, 2],
            [4, 3, 2, 1, 0],
        ]
        oracle = min(
            (sum(costs[a][b] for a, b in zip([0, *order], [*order, 4], strict=False)), list(order))
            for order in itertools.permutations([1, 2, 3])
        )
        result = held_karp_exact(costs, [1, 2, 3], 4)
        self.assertEqual((result["cost"], result["order"]), oracle)

    def test_unreachable_exact_and_limit(self):
        self.assertFalse(
            held_karp_exact([[0, math.inf, 0], [0, 0, 0], [0, 0, 0]], [1], 2)["feasible"]
        )
        with self.assertRaises(ValueError):
            held_karp_exact([], list(range(9)), 10)

    def test_explicit_expiry_and_ttl_use_earliest_deadline(self):
        now = parse_time("2026-10-04T12:00:00Z")
        active, expired = active_overlays(
            [
                {
                    "id": "old",
                    "created_at": "2026-10-04T11:00:00Z",
                    "ttl_s": 2,
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            ],
            now,
        )
        self.assertEqual(active, [])
        self.assertEqual(expired, ["old"])

    def test_invalid_ttl_not_silently_ignored(self):
        with self.assertRaises(ValueError):
            active_overlays([{"id": "bad", "ttl_s": 10}], parse_time("2026-10-04T12:00:00Z"))


class CanonicalLabMissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.spatial.snapshot import build_lab_snapshot

        cls.snapshot = build_lab_snapshot()
        cls.request = {
            "map_id": cls.snapshot["map_id"],
            "map_revision": cls.snapshot["revision"],
            "start_pose": cls.snapshot["provenance"]["robot"]["pose"],
            "goal_ids": cls.snapshot["provenance"]["default_goal_ids"],
        }

    def test_real_fastest_and_safest_routes_are_distinct(self):
        plans = [
            plan_mission(self.snapshot, {**self.request, "profile": p})
            for p in ("fastest", "safest", "esd_safe")
        ]
        self.assertTrue(all(p["status"] == "READY" for p in plans))
        routes = [tuple(e for s in p["segments"] for e in s["edge_ids"]) for p in plans]
        self.assertNotEqual(routes[0], routes[1])
        self.assertGreater(plans[1]["metrics"]["distance_m"], plans[0]["metrics"]["distance_m"])

    def test_esd_profile_avoids_exposure_between_registered_docks(self):
        dock = next(d for d in self.snapshot["docks"] if d["id"] == "P-LAB")
        request = {**self.request, "goal_ids": ["P-MODULE"], "start_pose": dock["pose"]}
        safe, esd = [
            plan_mission(self.snapshot, {**request, "profile": p}) for p in ("safest", "esd_safe")
        ]
        self.assertEqual(safe["status"], "READY")
        self.assertEqual(esd["status"], "READY")
        self.assertGreater(esd["segments"][0]["distance_m"], safe["segments"][0]["distance_m"])
        self.assertLess(
            esd["objective_terms"]["semantic_penalty"],
            safe["objective_terms"]["semantic_penalty"],
        )

    def test_protected_esd_zone_has_no_exposure_penalty(self):
        graph = SemanticGraph(
            self.snapshot,
            profile="esd_safe",
            robot=RobotProfile.from_snapshot(self.snapshot, "MB-R01"),
        )
        protected = [
            e
            for e in graph.edges.values()
            if "esd_protected_payload" in e.get("rule_refs", [])
            and "esd_exposure" not in e.get("rule_refs", [])
            and not e.get("human_mixed")
        ]
        self.assertTrue(protected)
        self.assertTrue(all(graph.terms[e["id"]]["semantic_penalty"] == 0 for e in protected))

    def test_canonical_low_battery_requires_charge(self):
        result = plan_mission(
            self.snapshot,
            {**self.request, "constraints": {"battery_pct": 15.5, "robot_class": "MB-R01"}},
        )
        self.assertEqual(result["status"], "READY")
        self.assertIn("CHARGER", result["ordered_goal_ids"])
        self.assertGreaterEqual(result["metrics"]["battery_after_pct"], 15)
