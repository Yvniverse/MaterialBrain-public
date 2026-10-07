# ruff: noqa: E402
import itertools
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "robot_bridge"))
from nav2_adapter import DockCommand, Nav2DockBridge, RobotReadiness

from app.services.embodied_navigation.geometry import segment_clearance
from app.services.embodied_navigation.planner import MetricPlanner, canonical_hash, load_world

W = load_world()
P = MetricPlanner(W)
IDS = W["default_goal_ids"]
B = P.plan(IDS)


class PlannerTests(unittest.TestCase):
    def test_digest(self):
        self.assertEqual(
            canonical_hash({k: v for k, v in W.items() if k != "revision_sha256"}),
            W["revision_sha256"],
        )

    def test_js_python_parity(self):
        j = json.loads(
            (ROOT / "backend/tests/fixtures/embodied/benchmark-js.json").read_text(encoding="utf-8")
        )["baseline"]
        self.assertEqual(B["goal_ids"], j["goal_ids"])
        for k in ("distance_m", "cost_s", "motion_s", "eta_s", "min_clearance_m"):
            self.assertAlmostEqual(B[k], j[k], places=9)

    def test_all_segments_clear(self):
        for seg in B["segments"]:
            for a in seg["actions"]:
                self.assertGreater(segment_clearance(W, a["from"], a["to"], P.obstacles), P.r)

    def test_astar_dijkstra(self):
        self.assertAlmostEqual(
            P.path(W["home"], W["goals"][0]["pose"])["cost_s"],
            P.path(W["home"], W["goals"][0]["pose"], heuristic_enabled=False)["cost_s"],
            places=8,
        )

    def test_optimizer_vs_bruteforce(self):
        self.assertAlmostEqual(
            P.plan(IDS[:4])["cost_s"],
            min(
                P.plan(list(ids), optimize=False)["cost_s"]
                for ids in itertools.permutations(IDS[:4])
            ),
            places=8,
        )

    def test_blocked_and_parity(self):
        scenario = next(s for s in W["scenarios"] if s["id"] == "blocked-crossing")
        p = MetricPlanner(W, obstacles=scenario["obstacles"])
        r = p.plan(IDS)
        j = json.loads(
            (ROOT / "backend/tests/fixtures/embodied/benchmark-js.json").read_text(encoding="utf-8")
        )["blocked_crossing"]
        self.assertEqual(r["goal_ids"], j["goal_ids"])
        self.assertAlmostEqual(r["cost_s"], j["cost_s"], places=8)

    def test_isolated(self):
        self.assertEqual(
            MetricPlanner(W, obstacles=W["scenarios"][2]["obstacles"]).plan(IDS)["status"],
            "BLOCKED",
        )

    def test_battery(self):
        self.assertEqual(P.plan(IDS, battery_pct=12)["status"], "NEEDS_CHARGE")

    def test_overload(self):
        self.assertEqual(P.plan(IDS, payload_kg=16)["status"], "SPLIT_REQUIRED")

    def test_wrong_pose(self):
        with self.assertRaises(ValueError):
            P.path(W["home"], {"x": -1, "y": 2, "yaw": 0})

    def test_zero_goals(self):
        self.assertEqual(P.plan([])["distance_m"], 0)

    def test_read_cache_is_copy(self):
        a = P.path(W["home"], W["goals"][0]["pose"])
        a["poses"][0]["x"] = 999
        self.assertNotEqual(P.path(W["home"], W["goals"][0]["pose"])["poses"][0]["x"], 999)

    def test_grid_export(self):
        report = json.loads(
            (ROOT / "robot_bridge/world/export-report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["pgm_width"], 240)
        self.assertEqual(report["pgm_height"], 180)
        self.assertGreater(report["occupied_pixels"], 0)

    def test_bad_weight(self):
        with self.assertRaises(ValueError):
            MetricPlanner(W, clearance_weight=-1)

    def test_unknown_goal(self):
        with self.assertRaises(ValueError):
            P.plan(["fake"])


class FakeNav:
    def __init__(self):
        self.calls = 0
        self.done = False
        self.result = type("Result", (), {"name": "SUCCEEDED"})()

    def goToPose(self, p, behavior_tree=""):
        self.calls += 1
        self.done = False
        return True

    def isTaskComplete(self):
        return self.done

    def getFeedback(self):
        return {"distance_remaining": 1.2}

    def getResult(self):
        return self.result

    def cancelTask(self):
        self.result = type("Result", (), {"name": "CANCELED"})()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.nav = FakeNav()
        self.g = W["goals"][0]
        self.b = Nav2DockBridge(
            self.nav, lambda p, f: p, allowed_docks={self.g["id"]: self.g["pose"]}
        )
        self.c = DockCommand("m1", "c1", self.g["id"], W["revision_sha256"], self.g["pose"])
        self.r = RobotReadiness(True, 0.1, True, True, W["revision_sha256"])

    def test_sim_dispatch(self):
        self.assertEqual(self.b.dispatch(self.c, self.r)["state"], "RUNNING")

    def test_idempotent(self):
        self.b.dispatch(self.c, self.r)
        self.b.dispatch(self.c, self.r)
        self.assertEqual(self.nav.calls, 1)

    def test_no_parallel_dispatch(self):
        self.b.dispatch(self.c, self.r)
        with self.assertRaises(ValueError):
            self.b.dispatch(
                DockCommand("m1", "c2", self.g["id"], self.c.world_revision, self.g["pose"]), self.r
            )

    def test_cancel_requires_ack(self):
        self.b.dispatch(self.c, self.r)
        self.b.cancel()
        self.assertEqual(self.b.poll()["state"], "CANCELLING")
        self.nav.done = True
        self.assertEqual(self.b.poll()["state"], "CANCELLED")

    def test_no_inventory_write(self):
        self.b.dispatch(self.c, self.r)
        self.nav.done = True
        self.assertFalse(self.b.poll()["inventory_written"])

    def test_physical_not_armed(self):
        with self.assertRaises(ValueError):
            self.b.dispatch(
                DockCommand(
                    "m", "p", self.g["id"], self.c.world_revision, self.g["pose"], mode="physical"
                ),
                self.r,
            )

    def test_stale_pose(self):
        with self.assertRaises(ValueError):
            self.b.dispatch(self.c, RobotReadiness(True, 3, True, True, self.c.world_revision))

    def test_no_arbitrary_llm_pose(self):
        with self.assertRaises(ValueError):
            self.b.dispatch(
                DockCommand(
                    "m", "p", self.g["id"], self.c.world_revision, {"x": 2, "y": 2, "yaw": 0}
                ),
                self.r,
            )

    def test_wrong_world(self):
        with self.assertRaises(ValueError):
            self.b.dispatch(self.c, RobotReadiness(True, 0.1, True, True, "wrong"))
