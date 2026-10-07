#!/usr/bin/env python3
"""Validate the captured README gallery without manufacturing capture metadata.

The browser capture records runtime identity and observed UI state in manifest.json.
An explicit visual review completes that manifest before publication. This checker
only verifies those records against the actual files and the bilingual READMEs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageStat

REQUIRED = {
    "01-warehouse-twin.png": ("/warehouse-twin", (1728, 1080), "warehouse"),
    "02-embodied-lab.png": (
        "/warehouse-twin?workspace=robot-lab", (1728, 1080), "laboratory"
    ),
    "03-material-brain.png": ("/agent", (1600, 1000), "agent"),
    "04-dashboard.png": ("/dashboard", (1600, 1000), "dashboard"),
    "05-storage-equipment.png": ("/locations", (1600, 1000), "storage"),
    "06-task-recovery.png": (
        "/warehouse-twin?workspace=robot-lab", (1600, 1000), "recovery"
    ),
}
SHA = re.compile(r"[a-f0-9]{40}")
SHA256 = re.compile(r"[a-f0-9]{64}")


def local_images(text: str) -> list[str]:
    images = re.findall(r"!\[[^\]]*\]\(<?([^\s)>]+)>?(?:\s+[^)]*)?\)", text)
    images.extend(re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', text, re.I))
    return [p for p in images if not urlsplit(p).scheme and not p.startswith("//")]


def _utc(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0
    except ValueError:
        return False


def validate(root: Path, app_sha: str | None = None, images_only: bool = False) -> dict:
    root = root.resolve()
    errors: list[str] = []
    records: list[dict] = []
    gallery = root / "docs" / "screenshots"
    manifest_path = gallery / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"valid": False, "files": [], "errors": [f"MANIFEST: {exc}"]}
    if not isinstance(manifest, dict):
        return {"valid": False, "files": [], "errors": ["MANIFEST must be an object"]}
    if manifest.get("schema_version") != 1:
        errors.append("MANIFEST schema_version must be 1")
    if manifest.get("capture_method") != "playwright-public-runtime":
        errors.append("MANIFEST missing actual runtime capture method")
    runtime = manifest.get("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
        errors.append("MANIFEST runtime must be an object")
    frontend_sha = runtime.get("frontend_build_sha", "")
    backend_sha = runtime.get("backend_build_sha", "")
    source_commit = manifest.get("source_commit", "")
    if not all(SHA.fullmatch(str(s)) for s in (frontend_sha, backend_sha, source_commit)):
        errors.append("MANIFEST source and application SHAs must be full Git revisions")
    if frontend_sha != backend_sha or frontend_sha != source_commit:
        errors.append("MANIFEST frontend, backend and source revisions differ")
    if app_sha is not None and frontend_sha != app_sha:
        errors.append("MANIFEST application revision differs from --app-sha")
    entries = manifest.get("files", [])
    if not isinstance(entries, list):
        entries = []
        errors.append("MANIFEST files must be an array")
    by_name: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("MANIFEST invalid file record")
            continue
        path = entry.get("path", "")
        if not isinstance(path, str) or not path.startswith("docs/screenshots/"):
            errors.append("MANIFEST invalid gallery-relative path")
            continue
        name = Path(path).name
        if name in by_name:
            errors.append(f"DUPLICATE RECORD {name}")
        by_name[name] = entry
    if set(by_name) != set(REQUIRED):
        errors.append("MANIFEST must contain exactly the six required gallery images")
    hashes: list[str] = []
    for name, (route, viewport, kind) in REQUIRED.items():
        path = gallery / name
        entry = by_name.get(name, {})
        if entry.get("path") != f"docs/screenshots/{name}":
            errors.append(f"PATH {name}")
        if not path.is_file():
            errors.append(f"MISSING {name}")
            continue
        image_bytes = path.read_bytes()
        digest = hashlib.sha256(image_bytes).hexdigest()
        hashes.append(digest)
        try:
            with Image.open(path) as image:
                image.load()
                size = image.size
                if image.format != "PNG":
                    errors.append(f"FORMAT {name}")
                if size != viewport:
                    errors.append(f"VIEWPORT {name}: expected {viewport}, got {size}")
                if ImageStat.Stat(image.convert("L")).stddev[0] < 8:
                    errors.append(f"NEAR BLANK {name}")
        except (OSError, ValueError) as exc:
            errors.append(f"UNREADABLE {name}: {exc}")
            continue
        if not SHA256.fullmatch(str(entry.get("sha256", ""))) or entry.get("sha256") != digest:
            errors.append(f"HASH {name}")
        if entry.get("bytes") != len(image_bytes):
            errors.append(f"BYTE COUNT {name}")
        if entry.get("viewport") != {"width": size[0], "height": size[1]}:
            errors.append(f"METADATA VIEWPORT {name}")
        recorded_route = entry.get("route", "")
        if not isinstance(recorded_route, str):
            recorded_route = ""
        parsed_route = urlsplit(recorded_route)
        expected = urlsplit(route)
        if parsed_route.path != expected.path:
            errors.append(f"ROUTE {name}")
        if expected.query and "workspace=robot-lab" not in parsed_route.query.split("&"):
            errors.append(f"WORKSPACE {name}")
        if not _utc(entry.get("captured_at_utc")):
            errors.append(f"UTC TIMESTAMP {name}")
        if not isinstance(entry.get("state"), str) or len(entry["state"].strip()) < 15:
            errors.append(f"STATE {name}")
        if entry.get("source_commit") != source_commit:
            errors.append(f"SOURCE COMMIT {name}")
        if entry.get("frontend_build_sha") != frontend_sha:
            errors.append(f"FRONTEND BUILD {name}")
        if entry.get("backend_build_sha") != backend_sha:
            errors.append(f"BACKEND BUILD {name}")
        evidence = entry.get("capture_evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
        if evidence.get("kind") != kind or evidence.get("verified") is not True:
            errors.append(f"CAPTURE EVIDENCE {name}")
        if kind in {"warehouse", "laboratory", "recovery"}:
            scene = evidence.get("scene", {})
            if not isinstance(scene, dict):
                scene = {}
            draws = scene.get("drawCalls", scene.get("draw_calls", 0))
            triangles = scene.get("triangles", 0)
            if (
                scene.get("renderer") != "THREE.WebGLRenderer"
                or not isinstance(draws, (int, float)) or draws < 10
                or not isinstance(triangles, (int, float)) or triangles < 100
            ):
                errors.append(f"WEBGL EVIDENCE {name}")
        if kind == "recovery":
            execution = scene.get("spatial_execution", {})
            if not isinstance(execution, dict):
                execution = {}
            if (
                execution.get("execution_boundary") != "ros2_nav2_simulation"
                or execution.get("hardware_control") is not False
                or not execution.get("completed_goal_ids")
            ):
                errors.append(f"SERVER NAV2 RECOVERY {name}")
            events = evidence.get("recovery_events", [])
            if not isinstance(events, list):
                events = []
            types = {
                event["type"] for event in events
                if isinstance(event, dict) and isinstance(event.get("type"), str)
            }
            if (
                not {"handoff_verified", "obstacle_added"}.issubset(types)
                or not types.intersection({"replanning", "recovery"})
            ):
                errors.append(f"RECOVERY EVENTS {name}")
            source = evidence.get("recovery_source", {})
            if not isinstance(source, dict):
                source = {}
            mission_id = execution.get("mission_id")
            if (
                not isinstance(mission_id, str) or not mission_id
                or source.get("kind") != "server_episode"
                or source.get("mission_id") != mission_id
                or source.get("source") != "observed_execution_events"
                or source.get("execution_boundary") != "ros2_nav2_simulation"
                or source.get("inventory_written") is not False
                or source.get("path") != f"/api/v1/spatial/missions/{mission_id}/episode"
                or not SHA256.fullmatch(str(source.get("response_sha256", "")))
            ):
                errors.append(f"SERVER EPISODE SOURCE {name}")
            if any(
                not isinstance(event, dict) or event.get("mission_id") != mission_id
                or not isinstance(event.get("sequence"), int) or not event.get("event_id")
                or not event.get("timestamp")
                or not isinstance(event.get("details"), dict)
                or event["details"].get("execution_boundary") != "ros2_nav2_simulation"
                or event["details"].get("hardware_control") is not False
                for event in events
            ):
                errors.append(f"RECOVERY EVENT IDENTITY {name}")
            cleanup = entry.get("cleanup", {})
            if not isinstance(cleanup, dict):
                cleanup = {}
            if (
                cleanup.get("status") != "COMPLETED" or cleanup.get("mission_id") != mission_id
                or not isinstance(cleanup.get("completed_goal_ids"), list)
                or "HOME" not in cleanup.get("completed_goal_ids", [])
                or cleanup.get("inventory_unchanged") is not True
                or not _utc(cleanup.get("checked_at_utc"))
            ):
                errors.append(f"RECOVERY CLEANUP {name}")
        review = entry.get("visual_review", {})
        if (
            not isinstance(review, dict) or review.get("status") != "passed"
            or not _utc(review.get("reviewed_at_utc"))
            or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()
        ):
            errors.append(f"VISUAL REVIEW {name}")
        records.append({"path": entry.get("path"), "size_px": list(size), "sha256": digest})
    if len(set(hashes)) != len(hashes):
        errors.append("DUPLICATE SCREENS")
    if not images_only:
        required_refs = {f"docs/screenshots/{name}" for name in REQUIRED}
        for name in ("README.md", "README.zh-CN.md"):
            try:
                text = (root / name).read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(f"README {name}: {exc}")
                continue
            refs = set(local_images(text))
            if not required_refs.issubset(refs):
                errors.append(f"GALLERY LINKS {name}: six required references are missing")
            for ref in refs:
                candidate = (root / unquote(urlsplit(ref).path)).resolve()
                if not candidate.is_relative_to(root) or not candidate.is_file():
                    errors.append(f"IMAGE LINK {name}: {ref}")
    return {"valid": not errors, "app_sha": frontend_sha, "files": records, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path, nargs="?", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--app-sha")
    parser.add_argument("--images-only", action="store_true", help="Pre-README image inspection only")
    parser.add_argument("--out", type=Path, help="Write the validation report, never a capture manifest")
    args = parser.parse_args()
    report = validate(args.repo, args.app_sha, args.images_only)
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(output, end="")
    if args.out:
        args.out.write_text(output, encoding="utf-8")
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())
