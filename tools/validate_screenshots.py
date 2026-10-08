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
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageStat

REQUIRED = {
    "01-warehouse-twin.png": ("/warehouse-twin", (1728, 1080), "warehouse"),
    "02-embodied-lab.png": ("/warehouse-twin?workspace=robot-lab", (1728, 1080), "laboratory"),
    "03-material-brain.png": ("/agent", (1600, 1000), "agent"),
    "04-dashboard.png": ("/dashboard", (1600, 1000), "dashboard"),
    "05-storage-equipment.png": ("/locations", (1600, 1000), "storage"),
    "06-task-recovery.png": ("/warehouse-twin?workspace=robot-lab", (1600, 1000), "recovery"),
}
LAB_GALLERY = {
    "01-lab-overview.png": ("/warehouse-twin?workspace=robot-lab", None, "laboratory"),
    "02-lab-aisle.png": ("/warehouse-twin?workspace=robot-lab", None, "laboratory"),
    "03-lab-topdown.png": ("/warehouse-twin?workspace=robot-lab", None, "laboratory"),
    "04-lab-follow.png": ("/warehouse-twin?workspace=robot-lab", None, "motion"),
    "05-lab-replan.png": ("/warehouse-twin?workspace=robot-lab", None, "replan"),
    "06-lab-handoff.png": ("/warehouse-twin?workspace=robot-lab", None, "handoff"),
    "07-material-brain.png": ("/agent", None, "agent"),
    "08-real-stockroom.png": ("/warehouse-twin", None, "warehouse"),
}
LAB_CAMERAS = dict(
    zip(
        LAB_GALLERY, ("overview", "aisle", "top", "robot", "overview", "overview", None, "overview")
    )
)
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


def recovery_cleanup_valid(cleanup: object, mission_id: str) -> bool:
    return (
        isinstance(cleanup, dict)
        and cleanup.get("status") == "COMPLETED"
        and cleanup.get("mission_id") == mission_id
        and isinstance(cleanup.get("completed_goal_ids"), list)
        and "HOME" in cleanup["completed_goal_ids"]
        and type(cleanup.get("collision_count")) in (int, float)
        and cleanup.get("collision_count") == 0
        and cleanup.get("inventory_unchanged") is True
        and _utc(cleanup.get("checked_at_utc"))
    )


def mission_evidence_errors(evidence: dict, kind: str, mission_id: str) -> list[str]:
    """Require observed server events and finite odometry for the three mission shots."""
    errors: list[str] = []
    mission = evidence.get("mission", {})
    if not isinstance(mission, dict):
        mission = {}
    if (
        not isinstance(mission_id, str)
        or not mission_id
        or mission.get("mission_id") != mission_id
        or mission.get("execution_boundary") != "ros2_nav2_simulation"
        or mission.get("hardware_control") is not False
        or mission.get("inventory_written") is not False
    ):
        errors.append("MISSION BOUNDARY")
    source = mission.get("source", {})
    if not isinstance(source, dict):
        source = {}
    if (
        source.get("kind") != "server_episode"
        or source.get("source") != "observed_execution_events"
        or source.get("mission_id") != mission_id
        or source.get("path") != f"/api/v1/spatial/missions/{mission_id}/episode"
        or not SHA256.fullmatch(str(source.get("response_sha256", "")))
    ):
        errors.append("MISSION SOURCE")
    scene = evidence.get("scene", {})
    execution = scene.get("spatial_execution", {}) if isinstance(scene, dict) else {}
    if not isinstance(execution, dict) or (
        execution.get("mission_id") != mission_id
        or execution.get("execution_boundary") != "ros2_nav2_simulation"
        or execution.get("hardware_control") is not False
    ):
        errors.append("SCENE MISSION")
    events = mission.get("events", [])
    if not isinstance(events, list):
        events = []
    identified: dict[str, dict] = {}
    for event in events:
        if (
            not isinstance(event, dict)
            or event.get("mission_id") != mission_id
            or not isinstance(event.get("event_id"), str)
            or not event["event_id"]
            or type(event.get("sequence")) is not int
            or event["sequence"] < 0
            or not isinstance(event.get("timestamp"), str)
            or not event["timestamp"]
            or not isinstance(event.get("type"), str)
            or not event["type"]
        ):
            errors.append("MISSION EVENT IDENTITY")
            continue
        if event["event_id"] in identified:
            errors.append("DUPLICATE MISSION EVENT")
        identified[event["event_id"]] = event
    if kind == "motion":
        before, after = mission.get("odom_before"), mission.get("odom_after")
        valid = all(
            isinstance(pose, list)
            and len(pose) == 2
            and all(type(value) in (int, float) and math.isfinite(value) for value in pose)
            for pose in (before, after)
        )
        if not valid or math.dist(before, after) <= 0.15:
            errors.append("OBSERVED MOTION")
        if mission.get("moving_status") not in {"RUNNING", "NAVIGATING", "EXECUTING"}:
            errors.append("MOVING MISSION STATUS")
    elif kind == "replan":
        blocked_id, replan_id = mission.get("blocked_event_id"), mission.get("replan_event_id")
        blocked = identified.get(blocked_id, {}) if isinstance(blocked_id, str) else {}
        replan = identified.get(replan_id, {}) if isinstance(replan_id, str) else {}
        if (
            blocked.get("type") != "obstacle_added"
            or replan.get("type") != "replanning"
            or not blocked
            or not replan
            or replan["sequence"] <= blocked["sequence"]
        ):
            errors.append("OBSERVED REPLAN")
    elif kind == "handoff":
        handoff_id, scan_id = mission.get("handoff_event_id"), mission.get("scan_event_id")
        handoff = identified.get(handoff_id, {}) if isinstance(handoff_id, str) else {}
        scan = identified.get(scan_id, {}) if isinstance(scan_id, str) else {}
        completed_goals = (
            execution.get("completed_goal_ids", []) if isinstance(execution, dict) else []
        )
        if (
            mission.get("scan_verified") is not True
            or handoff.get("type") != "handoff_verified"
            or scan.get("type") != "scan_verified"
            or not scan.get("goal_id")
            or scan.get("goal_id") != handoff.get("goal_id")
            or not scan
            or not handoff
            or scan["sequence"] > handoff["sequence"]
            or not isinstance(completed_goals, list)
            or handoff.get("goal_id") not in completed_goals
        ):
            errors.append("OBSERVED HANDOFF")
    return errors


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
    lab_gallery = manifest.get("schema_version") == 2
    required = LAB_GALLERY if lab_gallery else REQUIRED
    if manifest.get("schema_version") not in (1, 2):
        errors.append("MANIFEST schema_version must be 1 or 2")
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
    if lab_gallery and runtime.get("robot_build_sha") != source_commit:
        errors.append("MANIFEST robot revision differs from application revision")
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
    if set(by_name) != set(required):
        errors.append(f"MANIFEST must contain exactly the {len(required)} required gallery images")
    mission_id = manifest.get("mission_id", "")
    if lab_gallery:
        cleanup = manifest.get("cleanup", {})
        if not recovery_cleanup_valid(cleanup, mission_id) or (
            cleanup.get("active_mission_id") not in (None, mission_id)
            or cleanup.get("held") is not True
            or cleanup.get("obstacle_ids") != []
            or cleanup.get("remaining_goal_ids") != []
            or type(cleanup.get("home_distance_m")) not in (float, int)
            or not math.isfinite(cleanup["home_distance_m"])
            or not 0 <= cleanup["home_distance_m"] <= 0.2
        ):
            errors.append(
                "MANIFEST mission cleanup must be idle at HOME without obstacles or stock changes"
            )
    hashes: list[str] = []
    for name, (route, viewport, kind) in required.items():
        path = gallery / name
        entry = by_name.get(name, {})
        if entry.get("path") != f"docs/screenshots/{name}":
            errors.append(f"PATH {name}")
        if not path.is_file():
            errors.append(f"MISSING {name}")
            continue
        image_bytes = path.read_bytes()
        if len(image_bytes) > 1_500_000:
            errors.append(f"PNG SIZE LIMIT {name}")
        digest = hashlib.sha256(image_bytes).hexdigest()
        hashes.append(digest)
        try:
            with Image.open(path) as image:
                image.load()
                size = image.size
                if image.format != "PNG":
                    errors.append(f"FORMAT {name}")
                if viewport is not None and size != viewport:
                    errors.append(f"VIEWPORT {name}: expected {viewport}, got {size}")
                if lab_gallery and (size[0] < 1280 or size[1] < 720):
                    errors.append(f"VIEWPORT {name}: requires at least 1280 by 720")
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
        if lab_gallery:
            if entry.get("robot_build_sha") != source_commit:
                errors.append(f"ROBOT BUILD {name}")
            if entry.get("camera") != LAB_CAMERAS[name]:
                errors.append(f"CAMERA {name}")
        evidence = entry.get("capture_evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
        if evidence.get("kind") != kind or evidence.get("verified") is not True:
            errors.append(f"CAPTURE EVIDENCE {name}")
        if kind in {"warehouse", "laboratory", "recovery", "motion", "replan", "handoff"}:
            scene = evidence.get("scene", {})
            if not isinstance(scene, dict):
                scene = {}
            draws = scene.get("drawCalls", scene.get("draw_calls", 0))
            triangles = scene.get("triangles", 0)
            if (
                scene.get("renderer") != "THREE.WebGLRenderer"
                or not isinstance(draws, (int, float))
                or draws < 10
                or not isinstance(triangles, (int, float))
                or triangles < 100
            ):
                errors.append(f"WEBGL EVIDENCE {name}")
        if lab_gallery and kind in {"motion", "replan", "handoff"}:
            errors.extend(
                f"{error} {name}" for error in mission_evidence_errors(evidence, kind, mission_id)
            )
        if lab_gallery and kind == "agent":
            result = evidence.get("grounded_result", {})
            if not isinstance(result, dict) or (
                not isinstance(result.get("query"), str)
                or len(result["query"].strip()) < 5
                or not isinstance(result.get("observed_text"), str)
                or len(result["observed_text"].strip()) < 30
                or evidence.get("scene") is not None
                or not SHA256.fullmatch(str(result.get("response_sha256", "")))
            ):
                errors.append(f"GROUNDED AGENT RESULT {name}")
        if (
            lab_gallery
            and kind == "warehouse"
            and (
                not isinstance(evidence.get("selection"), str)
                or len(evidence["selection"].strip()) < 15
            )
        ):
            errors.append(f"REGISTERED STOCKROOM SELECTION {name}")
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
                event["type"]
                for event in events
                if isinstance(event, dict) and isinstance(event.get("type"), str)
            }
            if not {"handoff_verified", "obstacle_added"}.issubset(types) or not types.intersection(
                {"replanning", "recovery"}
            ):
                errors.append(f"RECOVERY EVENTS {name}")
            source = evidence.get("recovery_source", {})
            if not isinstance(source, dict):
                source = {}
            mission_id = execution.get("mission_id")
            if (
                not isinstance(mission_id, str)
                or not mission_id
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
                not isinstance(event, dict)
                or event.get("mission_id") != mission_id
                or not isinstance(event.get("sequence"), int)
                or not event.get("event_id")
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
            if not recovery_cleanup_valid(cleanup, mission_id):
                errors.append(f"RECOVERY CLEANUP {name}")
        review = entry.get("visual_review", {})
        if (
            not isinstance(review, dict)
            or review.get("status") != "passed"
            or not _utc(review.get("reviewed_at_utc"))
            or not isinstance(review.get("reviewer"), str)
            or not review["reviewer"].strip()
        ):
            errors.append(f"VISUAL REVIEW {name}")
        records.append({"path": entry.get("path"), "size_px": list(size), "sha256": digest})
    if len(set(hashes)) != len(hashes):
        errors.append("DUPLICATE SCREENS")
    if not images_only:
        required_refs = {f"docs/screenshots/{name}" for name in required}
        for name in ("README.md", "README.zh-CN.md"):
            try:
                text = (root / name).read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(f"README {name}: {exc}")
                continue
            refs = set(local_images(text))
            if not required_refs.issubset(refs):
                errors.append(
                    f"GALLERY LINKS {name}: {len(required)} required references are missing"
                )
            if lab_gallery:
                product_images = [ref for ref in local_images(text) if ref in required_refs]
                if (
                    not product_images
                    or product_images[0] != "docs/screenshots/01-lab-overview.png"
                ):
                    errors.append(
                        f"GALLERY HERO {name}: logistics laboratory overview must come first"
                    )
                ordered = list(dict.fromkeys(product_images))
                if ordered != [f"docs/screenshots/{image_name}" for image_name in required]:
                    errors.append(f"GALLERY ORDER {name}")
            for ref in refs:
                candidate = (root / unquote(urlsplit(ref).path)).resolve()
                if not candidate.is_relative_to(root) or not candidate.is_file():
                    errors.append(f"IMAGE LINK {name}: {ref}")
    return {"valid": not errors, "app_sha": frontend_sha, "files": records, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path, nargs="?", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--app-sha")
    parser.add_argument(
        "--images-only", action="store_true", help="Pre-README image inspection only"
    )
    parser.add_argument(
        "--out", type=Path, help="Write the validation report, never a capture manifest"
    )
    args = parser.parse_args()
    report = validate(args.repo, args.app_sha, args.images_only)
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(output, end="")
    if args.out:
        args.out.write_text(output, encoding="utf-8")
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())
