"""Failure-path tests for the gallery gate; never writes product gallery assets."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from validate_screenshots import (
    local_images,
    mission_evidence_errors,
    recovery_cleanup_valid,
    validate,
)


class ScreenshotValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.gallery = self.root / "docs" / "screenshots"
        self.gallery.mkdir(parents=True)

    def write_manifest(self, value):
        (self.gallery / "manifest.json").write_text(json.dumps(value), encoding="utf-8")

    def test_missing_manifest_fails(self):
        report = validate(self.root)
        self.assertFalse(report["valid"])
        self.assertIn("MANIFEST", report["errors"][0])

    def test_non_object_manifest_fails_cleanly(self):
        self.write_manifest([])
        self.assertEqual(validate(self.root)["errors"], ["MANIFEST must be an object"])

    def test_missing_required_gallery_fails(self):
        self.write_manifest({"schema_version": 1, "capture_method": "playwright-public-runtime"})
        report = validate(self.root, images_only=True)
        self.assertFalse(report["valid"])
        self.assertEqual(sum(error.startswith("MISSING ") for error in report["errors"]), 6)

    def test_disagreeing_builds_fail(self):
        self.write_manifest(
            {
                "schema_version": 1,
                "capture_method": "playwright-public-runtime",
                "source_commit": "a" * 40,
                "runtime": {"frontend_build_sha": "a" * 40, "backend_build_sha": "b" * 40},
            }
        )
        self.assertIn(
            "MANIFEST frontend, backend and source revisions differ", validate(self.root)["errors"]
        )

    def test_expected_app_sha_cannot_replace_observed_sha(self):
        self.write_manifest(
            {
                "schema_version": 1,
                "capture_method": "playwright-public-runtime",
                "source_commit": "a" * 40,
                "runtime": {"frontend_build_sha": "a" * 40, "backend_build_sha": "a" * 40},
            }
        )
        self.assertIn(
            "MANIFEST application revision differs from --app-sha",
            validate(self.root, "c" * 40)["errors"],
        )

    def test_invalid_runtime_does_not_crash(self):
        self.write_manifest({"runtime": ["not", "metadata"]})
        self.assertIn("MANIFEST runtime must be an object", validate(self.root)["errors"])

    def test_six_bilingual_gallery_links_are_required(self):
        self.write_manifest({})
        for name in ("README.md", "README.zh-CN.md"):
            (self.root / name).write_text("# Product\n", encoding="utf-8")
        errors = validate(self.root)["errors"]
        self.assertEqual(sum(error.startswith("GALLERY LINKS ") for error in errors), 2)

    def test_local_markdown_and_html_images_are_checked(self):
        refs = local_images(
            '![scene](docs/a.png) <img src="docs/b.png" /> ![CI](https://example.org/status.svg)'
        )
        self.assertEqual(refs, ["docs/a.png", "docs/b.png"])

    def test_recovery_cleanup_requires_home_and_zero_observed_collisions(self):
        cleanup = {
            "status": "COMPLETED",
            "mission_id": "SM-observed",
            "completed_goal_ids": ["P-CABLE", "HOME"],
            "collision_count": 0,
            "inventory_unchanged": True,
            "checked_at_utc": "2026-10-08T01:00:00Z",
        }
        self.assertTrue(recovery_cleanup_valid(cleanup, "SM-observed"))
        for collision_count in (None, False, 1):
            self.assertFalse(
                recovery_cleanup_valid(
                    {**cleanup, "collision_count": collision_count}, "SM-observed"
                )
            )
        self.assertFalse(
            recovery_cleanup_valid({**cleanup, "completed_goal_ids": ["P-CABLE"]}, "SM-observed")
        )

    def test_recovery_cleanup_rejects_another_mission_or_changed_inventory(self):
        cleanup = {
            "status": "COMPLETED",
            "mission_id": "SM-observed",
            "completed_goal_ids": ["HOME"],
            "collision_count": 0,
            "inventory_unchanged": True,
            "checked_at_utc": "2026-10-08T01:00:00Z",
        }
        self.assertFalse(recovery_cleanup_valid(cleanup, "SM-other"))
        self.assertFalse(
            recovery_cleanup_valid({**cleanup, "inventory_unchanged": False}, "SM-observed")
        )

    def test_new_gallery_requires_all_eight_images_and_robot_identity(self):
        self.write_manifest(
            {
                "schema_version": 2,
                "capture_method": "playwright-public-runtime",
                "source_commit": "a" * 40,
                "runtime": {
                    "frontend_build_sha": "a" * 40,
                    "backend_build_sha": "a" * 40,
                    "robot_build_sha": "b" * 40,
                },
            }
        )
        errors = validate(self.root, images_only=True)["errors"]
        self.assertEqual(sum(error.startswith("MISSING ") for error in errors), 8)
        self.assertIn("MANIFEST robot revision differs from application revision", errors)

    def test_completed_bridge_reference_is_valid_but_another_active_mission_is_not(self):
        cleanup = {
            "status": "COMPLETED",
            "mission_id": "SM-completed",
            "completed_goal_ids": ["HOME"],
            "remaining_goal_ids": [],
            "collision_count": 0,
            "inventory_unchanged": True,
            "checked_at_utc": "2026-10-08T01:00:00Z",
            "held": True,
            "obstacle_ids": [],
            "home_distance_m": 0.1,
        }
        manifest = {"schema_version": 2, "mission_id": "SM-completed", "cleanup": cleanup}
        message = "MANIFEST mission cleanup must be idle at HOME without obstacles or stock changes"
        for reference in (None, "SM-completed"):
            cleanup["active_mission_id"] = reference
            self.write_manifest(manifest)
            self.assertNotIn(message, validate(self.root, images_only=True)["errors"])
        cleanup["active_mission_id"] = "SM-other"
        self.write_manifest(manifest)
        self.assertIn(message, validate(self.root, images_only=True)["errors"])
        cleanup.update(active_mission_id="SM-completed", status="NAVIGATING")
        self.write_manifest(manifest)
        self.assertIn(message, validate(self.root, images_only=True)["errors"])


class MissionEvidenceValidationTests(unittest.TestCase):
    def setUp(self):
        self.mission_id = "SM-test-fixture"
        events = [
            {"event_id": "blocked", "type": "obstacle_added", "sequence": 1},
            {"event_id": "replan", "type": "replanning", "sequence": 2},
            {"event_id": "scan", "type": "scan_verified", "sequence": 3, "goal_id": "P-CABLE"},
            {
                "event_id": "handoff",
                "type": "handoff_verified",
                "sequence": 4,
                "goal_id": "P-CABLE",
            },
        ]
        self.evidence = {
            "scene": {
                "spatial_execution": {
                    "mission_id": self.mission_id,
                    "execution_boundary": "ros2_nav2_simulation",
                    "hardware_control": False,
                    "completed_goal_ids": ["P-CABLE"],
                }
            },
            "mission": {
                "mission_id": self.mission_id,
                "execution_boundary": "ros2_nav2_simulation",
                "hardware_control": False,
                "inventory_written": False,
                "source": {
                    "kind": "server_episode",
                    "source": "observed_execution_events",
                    "mission_id": self.mission_id,
                    "path": f"/api/v1/spatial/missions/{self.mission_id}/episode",
                    "response_sha256": "a" * 64,
                },
                "events": [
                    {**event, "mission_id": self.mission_id, "timestamp": "2026-10-08T01:00:00Z"}
                    for event in events
                ],
                "odom_before": [18, 2.5],
                "odom_after": [17.5, 3],
                "moving_status": "NAVIGATING",
                "blocked_event_id": "blocked",
                "replan_event_id": "replan",
                "scan_event_id": "scan",
                "handoff_event_id": "handoff",
                "scan_verified": True,
            },
        }

    def test_coherent_observations_pass_each_mission_shot(self):
        for kind in ("motion", "replan", "handoff"):
            self.assertEqual(mission_evidence_errors(self.evidence, kind, self.mission_id), [])

    def test_motion_rejects_static_small_nonfinite_or_boolean_coordinates(self):
        for after in (
            [18, 2.5],
            [18.1, 2.5],
            [float("nan"), 3],
            [float("inf"), 3],
            [True, 3],
            None,
        ):
            evidence = deepcopy(self.evidence)
            evidence["mission"]["odom_after"] = after
            self.assertIn(
                "OBSERVED MOTION", mission_evidence_errors(evidence, "motion", self.mission_id)
            )

    def test_stopped_mission_cannot_establish_motion(self):
        evidence = deepcopy(self.evidence)
        evidence["mission"]["moving_status"] = "AWAITING_HANDOFF"
        self.assertIn(
            "MOVING MISSION STATUS", mission_evidence_errors(evidence, "motion", self.mission_id)
        )

    def test_another_mission_or_hardware_boundary_is_rejected(self):
        self.assertIn(
            "MISSION BOUNDARY", mission_evidence_errors(self.evidence, "motion", "SM-other")
        )
        evidence = deepcopy(self.evidence)
        evidence["mission"]["hardware_control"] = True
        self.assertIn(
            "MISSION BOUNDARY", mission_evidence_errors(evidence, "motion", self.mission_id)
        )
        evidence = deepcopy(self.evidence)
        evidence["scene"]["spatial_execution"]["mission_id"] = "SM-other"
        self.assertIn("SCENE MISSION", mission_evidence_errors(evidence, "motion", self.mission_id))

    def test_replan_needs_identified_obstacle_then_replan(self):
        for field, value in (("blocked_event_id", "absent"), ("replan_event_id", "absent")):
            evidence = deepcopy(self.evidence)
            evidence["mission"][field] = value
            self.assertIn(
                "OBSERVED REPLAN", mission_evidence_errors(evidence, "replan", self.mission_id)
            )
        evidence = deepcopy(self.evidence)
        evidence["mission"]["events"][1]["sequence"] = 0
        self.assertIn(
            "OBSERVED REPLAN", mission_evidence_errors(evidence, "replan", self.mission_id)
        )

    def test_handoff_needs_verified_scan_and_same_completed_dock(self):
        for field, value in (
            ("scan_verified", False),
            ("scan_event_id", "absent"),
            ("handoff_event_id", "absent"),
        ):
            evidence = deepcopy(self.evidence)
            evidence["mission"][field] = value
            self.assertIn(
                "OBSERVED HANDOFF", mission_evidence_errors(evidence, "handoff", self.mission_id)
            )
        evidence = deepcopy(self.evidence)
        evidence["mission"]["events"][2]["goal_id"] = "P-SENSOR"
        self.assertIn(
            "OBSERVED HANDOFF", mission_evidence_errors(evidence, "handoff", self.mission_id)
        )
        evidence = deepcopy(self.evidence)
        evidence["scene"]["spatial_execution"]["completed_goal_ids"] = []
        self.assertIn(
            "OBSERVED HANDOFF", mission_evidence_errors(evidence, "handoff", self.mission_id)
        )

    def test_episode_hash_and_event_identity_are_required(self):
        evidence = deepcopy(self.evidence)
        evidence["mission"]["source"]["response_sha256"] = "not-a-hash"
        self.assertIn(
            "MISSION SOURCE", mission_evidence_errors(evidence, "motion", self.mission_id)
        )
        evidence = deepcopy(self.evidence)
        evidence["mission"]["events"][0]["mission_id"] = "SM-other"
        self.assertIn(
            "MISSION EVENT IDENTITY", mission_evidence_errors(evidence, "replan", self.mission_id)
        )


if __name__ == "__main__":
    unittest.main()
