"""State/safety regressions for the portable interactive environment."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from training.materialbrain_training.environment import WarehouseTrainingEnv
from training.materialbrain_training.vendor.skills import skill_manifest


def snapshot_fixture() -> dict:
    nodes = [
        {"id": goal, "x": float(index + 2), "y": 2.0, "yaw": 0.0}
        for index, goal in enumerate(("HOME", "CHARGER", "P-A", "P-B"))
    ]
    edges = [
        {
            "id": f"{a['id']}-{b['id']}",
            "from": a["id"],
            "to": b["id"],
            "length_m": abs(a["x"] - b["x"]),
            "width_m": 2.0,
            "speed_limit_mps": 0.8,
            "risk_level": 0.0,
            "energy_wh": 0.1,
            "min_clearance_m": 0.6,
        }
        for a in nodes
        for b in nodes
        if a != b
    ]
    docks = [
        {
            "id": node["id"],
            "node_id": node["id"],
            "pose": {"x": node["x"], "y": node["y"], "yaw": 0.0},
            "kind": "home"
            if node["id"] == "HOME"
            else "charge"
            if node["id"] == "CHARGER"
            else "human_handoff",
            "capabilities": ["charge"] if node["id"] == "CHARGER" else ["human_handoff"],
        }
        for node in nodes
    ]
    return {
        "contract_version": "materialbrain-warehousebench-v1",
        "source_sha": "0" * 40,
        "exported_at": "2026-10-05T00:00:00+00:00",
        "skills": skill_manifest(),
        "map": {
            "schema_version": 1,
            "map_id": "TEST-ONLY",
            "revision": "test-map-revision",
            "frame": {"unit": "m"},
            "geometry": {
                "floor": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [20, 0], [20, 20], [0, 20], [0, 0]]],
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
            "provenance": {"source": "explicit synthetic unit fixture"},
        },
    }


def scenario_fixture(**overrides) -> dict:
    scenario = {
        "scenario_template": "T2",
        "scenario_seed": 11,
        "inventory_snapshot_id": "SYN-test",
        "instruction": "送到 P-A 和 P-B，扫码并人工交接后回 HOME。",
        "goal_ids": ["P-A", "P-B"],
        "profile": "fastest",
        "battery_pct": 80.0,
        "payload_kg": 2.0,
        "service_s": 2.0,
        "priority": 3,
        "time_window_s": [0, 3600],
        "stock_status": "full",
        "ambiguity": False,
        "events": [],
        "max_steps": 24,
    }
    scenario.update(overrides)
    return scenario


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "snapshot.json"
        self.path.write_text(json.dumps(snapshot_fixture()), encoding="utf-8")
        self.env = WarehouseTrainingEnv(self.path)

    def rollout(self, scenario=None):
        self.env.reset(scenario or scenario_fixture())
        observations = []
        while not self.env.terminated and not self.env.truncated:
            action = self.env.expert_action()
            observation, reward, _, _, info = self.env.step(action)
            self.assertFalse(info["invalid_skill"], info["verifier_code"])
            observations.append((observation, reward, info))
        return observations

    def ready(self):
        self.env.reset(scenario_fixture())
        for _ in range(5):
            self.env.step(self.env.expert_action())

    def test_reset_replay_is_independent_and_deterministic(self):
        first = self.rollout()
        first_trace = copy.deepcopy(self.env.trace)
        self.assertTrue(self.env.summary()["success"])
        second = self.rollout()
        self.assertEqual(first, second)
        self.assertEqual(first_trace, self.env.trace)
        self.assertEqual(self.env.metrics["completed_handoffs"], 2)

    def test_policy_observation_has_no_oracle_action_or_reward(self):
        observation = self.env.reset(scenario_fixture())
        serialized = json.dumps(observation)
        for field in (
            "expert_action",
            "next_action",
            "reward_components",
            "total_reward",
            "database_url",
            "password",
        ):
            self.assertNotIn(field, serialized)
        self.assertEqual(len(observation["available_skill_names"]), 13)

    def test_scan_does_not_complete_handoff(self):
        self.ready()
        self.env.step(self.env.expert_action())  # navigate
        self.env.step(self.env.expert_action())  # scan
        self.assertTrue(self.env.observe()["execution"]["scan_verified"])
        self.assertEqual(self.env.metrics["completed_handoffs"], 0)
        self.assertEqual(self.env.observe()["execution"]["completed_goal_ids"], [])

    def test_wrong_scan_has_no_completion_or_stock_write(self):
        self.ready()
        self.env.step(self.env.expert_action())
        action = self.env.expert_action()
        action["args"]["scan_code"] = "WRONG"
        _, reward, terminated, _, info = self.env.step(action)
        self.assertEqual(info["verifier_code"], "WRONG_SCAN")
        self.assertLess(reward, 0)
        self.assertFalse(terminated)
        self.assertFalse(self.env.observe()["execution"]["scan_verified"])
        self.assertEqual(self.env.metrics["completed_handoffs"], 0)
        self.assertEqual(self.env.metrics["inventory_writes"], 0)

    def test_handoff_without_scan_is_rejected_as_safety(self):
        self.ready()
        self.env.step(self.env.expert_action())
        action = self.env.expert_action()
        action["name"] = "verify_handoff"
        del action["args"]["scan_code"]
        _, reward, terminated, _, info = self.env.step(action)
        self.assertTrue(terminated)
        self.assertEqual(info["verifier_code"], "VERIFIED_SCAN_REQUIRED")
        self.assertTrue(info["safety_violation"])
        self.assertLess(reward, -20)
        self.assertFalse(self.env.metrics["task_success"])

    def test_completed_stops_survive_block_wait_and_replan(self):
        self.rollout(
            scenario_fixture(
                events=[
                    {"type": "blocked_path", "at_step": 9, "goal_id": "P-B", "reopen_after_s": 5}
                ]
            )
        )
        self.assertTrue(self.env.metrics["task_success"])
        names = [entry["action"].get("name") for entry in self.env.trace]
        self.assertIn("replan_remaining", names)
        self.assertIn("wait_or_yield", names)
        completed = [
            entry["completed_goal_ids"]
            for entry in self.env.trace
            if entry["mission_id"] == self.env.observe()["mission_id"]
        ]
        seen = set()
        for values in completed:
            self.assertTrue(seen.issubset(values))
            seen.update(values)
        self.assertEqual(self.env.metrics["completed_handoffs"], 2)

    def test_recovery_is_observable_before_next_decision(self):
        self.env.reset(
            scenario_fixture(
                events=[
                    {"type": "blocked_path", "at_step": 6, "goal_id": "P-A", "reopen_after_s": 5}
                ]
            )
        )
        for _ in range(5):
            self.env.step(self.env.expert_action())
        self.assertEqual(self.env.observe()["recovery"]["code"], "BLOCKED_PATH")
        self.assertEqual(self.env.expert_action()["name"], "replan_remaining")

    def test_wrong_reading_and_handoff_delay_recover(self):
        self.rollout(
            scenario_fixture(
                events=[
                    {"type": "wrong_scan", "at_step": 7, "goal_id": "P-A", "duration_s": 5},
                    {"type": "handoff_delay", "at_step": 10, "goal_id": "P-B", "duration_s": 8},
                ]
            )
        )
        self.assertTrue(self.env.metrics["task_success"])
        self.assertGreater(self.env.metrics["recovery_successes"], 0)
        self.assertEqual(self.env.metrics["invalid_actions"], 0)

    def test_low_battery_at_registered_charger_requires_actual_charge(self):
        self.rollout(scenario_fixture(battery_pct=8.0, start_goal_id="CHARGER"))
        self.assertTrue(self.env.metrics["task_success"])
        self.assertIn("charge_robot", [entry["action"].get("name") for entry in self.env.trace])
        self.assertGreater(self.env.battery_pct, 15)

    def test_too_low_battery_without_charger_is_verified_denial(self):
        self.rollout(scenario_fixture(battery_pct=8.0))
        self.assertTrue(self.env.metrics["denial_success"])
        self.assertEqual(self.env.metrics["completed_handoffs"], 0)

    def test_twenty_occurrences_use_original_docks_and_owned_batches(self):
        self.rollout(
            scenario_fixture(scenario_template="T3", goal_ids=["P-A", "P-B"] * 10, max_steps=180)
        )
        self.assertTrue(self.env.metrics["task_success"])
        self.assertEqual(self.env.metrics["completed_handoffs"], 20)
        self.assertEqual(self.env.summary()["mission_batches"], 10)
        self.assertEqual(len({entry["mission_id"] for entry in self.env.trace}), 10)

    def test_missing_stock_and_overload_are_denied_without_writes(self):
        for scenario in (
            scenario_fixture(stock_status="missing"),
            scenario_fixture(payload_kg=25.0),
        ):
            self.rollout(scenario)
            self.assertTrue(self.env.metrics["denial_success"])
            self.assertEqual(self.env.metrics["inventory_writes"], 0)

    def test_ambiguity_and_single_skill_are_distinct_verified_successes(self):
        self.rollout(scenario_fixture(scenario_template="T0", ambiguity=True))
        self.assertEqual(self.env.step_count, 1)
        self.assertTrue(self.env.metrics["clarification_success"])
        self.rollout(
            scenario_fixture(scenario_template="T1", terminal_skill="query_spatial_context")
        )
        self.assertEqual(self.env.step_count, 1)
        self.assertTrue(self.env.metrics["task_success"])
        self.assertFalse(self.env.metrics["clarification_success"])

    def test_mission_identity_and_coordinate_injection_are_unauthorized(self):
        for mutate in (
            lambda a: a["args"].update(mission_id="P3-other"),
            lambda a: a["args"].update(x=4.2),
        ):
            self.ready()
            action = self.env.expert_action()
            mutate(action)
            pose = copy.deepcopy(self.env.pose)
            _, _, terminated, _, info = self.env.step(action)
            self.assertTrue(terminated)
            self.assertTrue(info["unauthorized_action"])
            self.assertEqual(pose, self.env.pose)

    def test_premature_success_and_tool_spam_are_penalized(self):
        self.ready()
        observation = self.env.observe()
        summary = {
            "name": "summarize_mission",
            "args": {
                "mission_id": observation["mission_id"],
                "map_revision": observation["map_revision"],
            },
        }
        _, reward, terminated, _, _ = self.env.step(summary)
        self.assertTrue(terminated)
        self.assertLess(reward, 0)
        self.assertFalse(self.env.metrics["task_success"])
        self.ready()
        query = {
            "name": "query_spatial_context",
            "args": {"map_id": "TEST-ONLY", "map_revision": "test-map-revision"},
        }
        _, reward, _, _, info = self.env.step(query)
        self.assertLess(reward, -3)
        self.assertEqual(info["verifier_code"], "CONTEXT_ALREADY_QUERIED")

    def test_step_budget_is_real_truncation_without_success_reward(self):
        self.env.reset(scenario_fixture(max_steps=2))
        self.env.step(self.env.expert_action())
        _, reward, terminated, truncated, _ = self.env.step(self.env.expert_action())
        self.assertFalse(terminated)
        self.assertTrue(truncated)
        self.assertLess(reward, 0)
        self.assertFalse(self.env.metrics["task_success"])

    def test_time_window_delays_are_verified_from_actual_state(self):
        self.env.reset(
            scenario_fixture(
                goal_ids=["P-A"],
                time_window_s=[0, 10],
                events=[
                    {"type": "handoff_delay", "at_step": 7, "goal_id": "P-A", "duration_s": 30}
                ],
            )
        )
        while not self.env.terminated and not self.env.truncated:
            _, _, _, _, info = self.env.step(self.env.expert_action())
        self.assertFalse(self.env.metrics["task_success"])
        self.assertEqual(info["verifier_code"], "TIME_WINDOW_MISSED")
        self.assertEqual(self.env.metrics["completed_handoffs"], 0)


if __name__ == "__main__":
    unittest.main()
