"""Failure-path tests for the gallery gate; never writes product gallery assets."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from validate_screenshots import local_images, validate


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
        self.write_manifest({
            "schema_version": 1, "capture_method": "playwright-public-runtime",
            "source_commit": "a" * 40,
            "runtime": {"frontend_build_sha": "a" * 40, "backend_build_sha": "b" * 40},
        })
        self.assertIn("MANIFEST frontend, backend and source revisions differ", validate(self.root)["errors"])

    def test_expected_app_sha_cannot_replace_observed_sha(self):
        self.write_manifest({
            "schema_version": 1, "capture_method": "playwright-public-runtime",
            "source_commit": "a" * 40,
            "runtime": {"frontend_build_sha": "a" * 40, "backend_build_sha": "a" * 40},
        })
        self.assertIn("MANIFEST application revision differs from --app-sha", validate(self.root, "c" * 40)["errors"])

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
        refs = local_images('![scene](docs/a.png) <img src="docs/b.png" /> ![CI](https://example.org/status.svg)')
        self.assertEqual(refs, ["docs/a.png", "docs/b.png"])


if __name__ == "__main__":
    unittest.main()
