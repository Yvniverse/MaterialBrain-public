"""WarehouseBench metrics, artifact provenance, and real planner audit tests."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.services.spatial_mission import plan_mission
from warehouse_bench.catalog import scenario_catalog
from warehouse_bench.metrics import percentile, summarize
from warehouse_bench.nav2 import import_nav2_summary
from warehouse_bench.runner import run_benchmark, write_results
from warehouse_bench.verify import audit_route, graph_distance_bound


class MetricTests(unittest.TestCase):
    def test_percentile_linear_interpolation(self):
        self.assertEqual(percentile([10, 20, 30, 40], 0.5), 25)
        self.assertAlmostEqual(percentile([10, 20, 30, 40], 0.95), 38.5)
        self.assertIsNone(percentile([], 0.5))

    def test_percentile_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            percentile([1], 1.5)
        with self.assertRaises(ValueError):
            percentile([float("nan")], 0.5)

    def test_infeasible_is_correct_decision_but_not_success(self):
        summary = summarize(
            [
                {"status": "READY", "success": True, "decision_correct": True, "planning_ms": 10},
                {
                    "status": "INFEASIBLE",
                    "success": False,
                    "decision_correct": True,
                    "planning_ms": 20,
                },
                {
                    "status": "SKIP",
                    "success": False,
                    "skip_reason": "unsupported baseline contract",
                },
            ]
        )
        self.assertEqual(summary["success_rate"], 0.5)
        self.assertEqual(summary["decision_correct_rate"], 1)
        self.assertEqual(summary["executed_episodes"], 2)
        self.assertEqual(summary["skipped_episodes"], 1)

    def test_empty_summary_does_not_manufacture_zero_percent(self):
        self.assertIsNone(summarize([])["success_rate"])
        self.assertIsNone(summarize([])["planning_p50_ms"])

    def test_real_latency_distribution_is_not_scenario_average(self):
        summary = summarize(
            [
                {
                    "status": "PASS",
                    "success": True,
                    "planning_ms": 50,
                    "planning_latencies_ms": [1, 2, 100],
                    "recovery_attempted": True,
                    "recovery_success": True,
                    "replanning_latencies_ms": [7, 9],
                }
            ]
        )
        self.assertEqual(summary["planning_p50_ms"], 2)
        self.assertEqual(summary["replan_p50_ms"], 8)


class CanonicalBenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.spatial.snapshot import build_lab_snapshot

        cls.snapshot = build_lab_snapshot()
        cls.case = next(
            c for c in scenario_catalog(cls.snapshot, 17) if c["id"] == "easy_static_single"
        )
        cls.plan = plan_mission(cls.snapshot, cls.case["request"])

    def test_catalog_seed_and_registered_goals(self):
        first = scenario_catalog(self.snapshot, 17)
        second = scenario_catalog(self.snapshot, 17)
        self.assertEqual(first, second)
        self.assertNotEqual(
            first[1]["request"]["goal_ids"],
            scenario_catalog(self.snapshot, 18)[1]["request"]["goal_ids"],
        )
        registered = {d["id"] for d in self.snapshot["docks"]}
        self.assertTrue(all(set(case["request"]["goal_ids"]) <= registered for case in first))

    def test_independent_audit_accepts_actual_plan(self):
        audit = audit_route(self.snapshot, self.case["request"], self.plan)
        self.assertEqual(audit["collisions"], 0)
        self.assertEqual(audit["constraint_violations"], 0)
        self.assertGreater(audit["min_clearance_m"], 0)

    def test_audit_detects_static_collision(self):
        plan = copy.deepcopy(self.plan)
        asset = self.snapshot["geometry"]["assets"][0]
        plan["segments"][0]["poses"] = [
            {"x": asset["x"], "y": asset["y"], "yaw": 0},
            {"x": asset["x"] + 0.05, "y": asset["y"], "yaw": 0},
        ]
        self.assertGreater(audit_route(self.snapshot, self.case["request"], plan)["collisions"], 0)

    def test_audit_detects_completed_goal_repetition(self):
        request = {**self.case["request"], "completed_goal_ids": self.plan["ordered_goal_ids"]}
        audit = audit_route(self.snapshot, request, self.plan)
        self.assertIn("REPEATED_COMPLETED_GOAL", [v["code"] for v in audit["violations"]])

    def test_audit_detects_closed_edge(self):
        edge = self.plan["segments"][0]["edge_ids"][0]
        request = {
            **self.case["request"],
            "dynamic_overlays": [
                {
                    "id": "close",
                    "kind": "closure",
                    "edge_ids": [edge],
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            ],
        }
        audit = audit_route(self.snapshot, request, self.plan)
        self.assertIn("CLOSED_EDGE", [v["code"] for v in audit["violations"]])

    def test_audit_recomputes_registered_payload(self):
        plan = copy.deepcopy(self.plan)
        plan["metrics"]["payload_kg"] = 0
        request = {
            **self.case["request"],
            "constraints": {**self.case["request"]["constraints"], "payload_capacity_kg": 0.01},
        }
        codes = [v["code"] for v in audit_route(self.snapshot, request, plan)["violations"]]
        self.assertIn("PAYLOAD_CAPACITY", codes)
        self.assertIn("PAYLOAD_METRIC_MISMATCH", codes)

    def test_audit_uses_requested_window_even_if_plan_omits_it(self):
        goal = self.case["request"]["goal_ids"][0]
        request = {**self.case["request"], "stops": [{"goal_id": goal, "time_window_s": [0, 1]}]}
        audit = audit_route(self.snapshot, request, self.plan)
        self.assertIn("TIME_WINDOW", [v["code"] for v in audit["violations"]])

    def test_audit_detects_service_time_tampering(self):
        plan = copy.deepcopy(self.plan)
        plan["stops"][0]["service_s"] = 0
        audit = audit_route(self.snapshot, self.case["request"], plan)
        self.assertIn("SERVICE_TIME", [v["code"] for v in audit["violations"]])

    def test_audit_rejects_battery_increase_without_charging(self):
        plan = copy.deepcopy(self.plan)
        plan["metrics"]["battery_after_pct"] = 100
        audit = audit_route(self.snapshot, self.case["request"], plan)
        self.assertIn("BATTERY_INCREASE_WITHOUT_CHARGE", [v["code"] for v in audit["violations"]])

    def test_graph_distance_is_topological_bound(self):
        bound = graph_distance_bound(self.snapshot, self.case["request"])
        self.assertTrue(bound["optimality_proven"])
        self.assertLessEqual(bound["distance_m"], self.plan["metrics"]["distance_m"] + 1e-6)
        self.assertIn("dijkstra", bound["method"])

    def test_more_than_eight_has_honest_missing_exact_bound(self):
        case = next(
            c for c in scenario_catalog(self.snapshot, 17) if c["id"] == "hard_capacity_priority"
        )
        bound = graph_distance_bound(self.snapshot, case["request"])
        self.assertIsNone(bound["distance_m"])
        self.assertIn("eight", bound["reason"])

    def test_actual_two_algorithms_have_finite_recorded_metrics(self):
        report = run_benchmark(
            self.snapshot, seed=17, repetitions=1, case_ids=["easy_static_single"]
        )
        self.assertEqual(
            {e["algorithm"] for e in report["episodes"]},
            {"heading_grid_astar", "semantic_graph_ortools"},
        )
        self.assertTrue(all(e["success"] for e in report["episodes"]))
        for episode in report["episodes"]:
            self.assertGreater(episode["planning_ms"], 0)
            self.assertGreater(episode["path_length_m"], 0)
            self.assertGreater(episode["energy_wh"], 0)
            self.assertEqual(episode["map_revision"], self.snapshot["revision"])
        self.assertEqual(report["algorithm_availability"][-1]["status"], "NOT_RUN")

    def test_seed_reproduces_paths_but_latency_is_measured(self):
        reports = [
            run_benchmark(
                self.snapshot,
                seed=17,
                repetitions=1,
                algorithms=("semantic_graph_ortools",),
                case_ids=["medium_multi_6"],
            )
            for _ in range(2)
        ]
        first, second = [r["episodes"][0] for r in reports]
        self.assertEqual(first["configuration_hash"], second["configuration_hash"])
        self.assertEqual(first["plan"]["ordered_goal_ids"], second["plan"]["ordered_goal_ids"])
        self.assertEqual(first["path_length_m"], second["path_length_m"])

    def test_recovery_preserves_completed_handoff(self):
        report = run_benchmark(
            self.snapshot,
            repetitions=1,
            algorithms=("semantic_graph_ortools",),
            case_ids=["recovery_blocked_aisle"],
        )
        episode = report["episodes"][0]
        self.assertTrue(episode["recovery_success"])
        self.assertEqual(episode["task_goals_completed_before_replan"], 1)
        self.assertFalse(
            set(episode["request"]["completed_goal_ids"]) & set(episode["plan"]["ordered_goal_ids"])
        )
        self.assertGreater(episode["replan_ms"], 0)

    def test_artifact_json_jsonl_roundtrip_and_hash(self):
        report = run_benchmark(
            self.snapshot,
            repetitions=1,
            algorithms=("semantic_graph_ortools",),
            case_ids=["easy_static_single"],
        )
        with tempfile.TemporaryDirectory() as folder:
            manifest = write_results(report, folder)
            saved = json.loads(Path(manifest["json"]).read_text(encoding="utf-8"))
            lines = Path(manifest["jsonl"]).read_bytes()
            self.assertEqual(hashlib.sha256(lines).hexdigest(), manifest["jsonl_sha256"])
            self.assertEqual(saved["artifacts"]["episodes_jsonl_sha256"], manifest["jsonl_sha256"])
            self.assertEqual(
                [json.loads(line) for line in lines.decode().splitlines()], report["episodes"]
            )

    def test_unsupported_v3_is_reasoned_skip(self):
        report = run_benchmark(self.snapshot, repetitions=1, case_ids=["infeasible_time_window"])
        baseline = next(e for e in report["episodes"] if e["algorithm"] == "heading_grid_astar")
        semantic = next(e for e in report["episodes"] if e["algorithm"] == "semantic_graph_ortools")
        self.assertEqual(baseline["status"], "SKIP")
        self.assertTrue(baseline["skip_reason"])
        self.assertEqual(semantic["status"], "INFEASIBLE")
        self.assertTrue(semantic["decision_correct"])
        self.assertFalse(semantic["success"])

    def test_bad_benchmark_arguments_rejected(self):
        for kwargs in ({"repetitions": 0}, {"algorithms": ("fake",)}, {"case_ids": ["fake"]}):
            with self.assertRaises(ValueError):
                run_benchmark(self.snapshot, **kwargs)


class Nav2ImporterTests(unittest.TestCase):
    def setUp(self):
        from app.spatial.snapshot import build_lab_snapshot

        self.snapshot = build_lab_snapshot()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "summary.json"
        # These are importer UNIT fixtures, never benchmark execution evidence.
        artifacts = {}
        for label in ("topics", "events"):
            raw = (
                json.dumps(
                    {"test_only": True, "topic": "/odom" if label == "topics" else "feedback"}
                )
                + "\n"
            ).encode()
            (self.path.parent / f"{label}.jsonl").write_bytes(raw)
            artifacts[f"{label}_jsonl_sha256"] = hashlib.sha256(raw).hexdigest()
        self.summary = {
            "schema_version": 1,
            "map_id": self.snapshot["map_id"],
            "map_revision": self.snapshot["revision"],
            "execution_boundary": "ros2_nav2_simulation",
            "hardware_control": False,
            "build_sha": "unit-fixture",
            "active_plugins": {
                "planner_server": {"GridBased.plugin": "nav2_smac_planner::SmacPlannerLattice"},
                "controller_server": {
                    "FollowPath.plugin": "nav2_mppi_controller::MPPIController",
                    "FollowPath.motion_model": "DiffDrive",
                    "FollowPath.CostCritic.consider_footprint": True,
                },
            },
            "artifacts": artifacts,
            "scenarios": [
                {
                    "name": "UNIT_ONLY",
                    "status": "PASS",
                    "completed_goal_ids": ["P-IC"],
                    "metrics": {
                        "distance_m": 2,
                        "elapsed_s": 4,
                        "min_clearance_m": 0.1,
                        "collision_count": 0,
                        "action_feedback_messages": 5,
                        "nav2_action_count": 1,
                        "planning_latencies_ms": [1, 2, 3],
                    },
                    "assertions": {"completed": True},
                }
            ],
        }

    def import_fixture(self):
        self.path.write_text(json.dumps(self.summary), encoding="utf-8")
        return import_nav2_summary(self.path, self.snapshot)

    def test_unit_importer_validates_plugins_and_hashes(self):
        episode = self.import_fixture()[0]
        self.assertTrue(episode["success"])
        self.assertEqual(episode["execution_boundary"], "ros2_nav2_simulation")
        self.assertIsNone(episode["energy_wh"])
        self.assertEqual(episode["verified_artifacts"]["topics"]["records"], 1)

    def test_stale_map_rejected(self):
        self.summary["map_revision"] = "stale"
        with self.assertRaisesRegex(ValueError, "REVISION_MISMATCH"):
            self.import_fixture()

    def test_heading_shim_requires_actual_mppi_primary_and_full_footprint(self):
        controller = self.summary["active_plugins"]["controller_server"]
        controller["FollowPath.plugin"] = "nav2_rotation_shim_controller::RotationShimController"
        controller["FollowPath.primary_controller"] = "nav2_mppi_controller::MPPIController"
        self.assertTrue(self.import_fixture()[0]["success"])
        controller["FollowPath.CostCritic.consider_footprint"] = False
        with self.assertRaisesRegex(ValueError, "FULL_DIFFERENTIAL_FOOTPRINT_REQUIRED"):
            self.import_fixture()

    def test_heading_shim_cannot_hide_a_different_primary_controller(self):
        controller = self.summary["active_plugins"]["controller_server"]
        controller["FollowPath.plugin"] = "nav2_rotation_shim_controller::RotationShimController"
        for primary in ("dwb_core::DWBLocalPlanner", ""):
            controller["FollowPath.primary_controller"] = primary
            with self.assertRaisesRegex(ValueError, "MPPI_NOT_ACTIVE"):
                self.import_fixture()

    def test_wrong_active_planner_rejected(self):
        self.summary["active_plugins"]["planner_server"]["GridBased.plugin"] = "NavfnPlanner"
        with self.assertRaisesRegex(ValueError, "LATTICE_NOT_ACTIVE"):
            self.import_fixture()

    def test_changed_artifact_rejected(self):
        (self.path.parent / "topics.jsonl").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "HASH_MISMATCH"):
            self.import_fixture()

    def test_missing_feedback_rejected(self):
        self.summary["scenarios"][0]["metrics"]["action_feedback_messages"] = 0
        with self.assertRaisesRegex(ValueError, "FEEDBACK_REQUIRED"):
            self.import_fixture()

    def test_failed_actual_status_not_relabelled_pass(self):
        self.summary["scenarios"][0]["status"] = "FAILED"
        self.assertFalse(self.import_fixture()[0]["success"])

    def test_nonfinite_metric_rejected(self):
        self.summary["scenarios"][0]["metrics"]["distance_m"] = float("nan")
        with self.assertRaisesRegex(ValueError, "INVALID_NAV2_METRIC"):
            self.import_fixture()
