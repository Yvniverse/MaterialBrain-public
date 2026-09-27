"""Verify exact-SHA evidence manifest file existence, size and SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit(
    root: Path,
    manifest: dict,
    expected_candidate: str = "",
    *,
    manifest_path: Path | None = None,
) -> list[str]:
    root = root.resolve()
    failures: list[str] = []
    allowed_keys = {
        "candidate_sha",
        "runtime_backend_sha",
        "runtime_frontend_sha",
        "files",
        "external_references",
    }
    failures.extend(f"unknown_manifest_key:{key}" for key in sorted(set(manifest) - allowed_keys))
    sha_pattern = re.compile(r"^[0-9a-f]{40}$")
    for key in ("candidate_sha", "runtime_backend_sha", "runtime_frontend_sha"):
        if not sha_pattern.fullmatch(str(manifest.get(key) or "").lower()):
            failures.append(f"invalid_{key}")
    if expected_candidate and manifest.get("candidate_sha") != expected_candidate:
        failures.append("candidate_sha_mismatch")
    if manifest.get("runtime_backend_sha") != manifest.get("candidate_sha"):
        failures.append("backend_runtime_sha_mismatch")
    if manifest.get("runtime_frontend_sha") != manifest.get("candidate_sha"):
        failures.append("frontend_runtime_sha_mismatch")
    items = manifest.get("files")
    if not isinstance(items, list) or not items:
        failures.append("files_empty_or_invalid")
        items = []
    seen: set[str] = set()
    listed_paths: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            failures.append("file_entry_not_object")
            continue
        allowed_file_keys = {"path", "sha256", "bytes", "artifact_type"}
        failures.extend(f"unknown_file_key:{key}" for key in sorted(set(item) - allowed_file_keys))
        rel = str(item.get("path") or "")
        key = rel.casefold()
        if not rel or key in seen:
            failures.append(f"duplicate_or_empty_path:{rel}")
            continue
        seen.add(key)
        listed_paths.add(rel)
        if "\\" in rel or PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
            failures.append(f"unsafe_path:{rel}")
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or "").lower()):
            failures.append(f"invalid_sha256:{rel}")
        if type(item.get("bytes")) is not int or item["bytes"] < 0:
            failures.append(f"invalid_bytes:{rel}")
        if not str(item.get("artifact_type") or ""):
            failures.append(f"invalid_artifact_type:{rel}")
        path = (root / rel).resolve()
        if not path.is_relative_to(root):
            failures.append(f"path_escape:{rel}")
            continue
        if not path.is_file():
            failures.append(f"missing:{rel}")
            continue
        expected_bytes = item.get("bytes")
        if (
            isinstance(expected_bytes, int)
            and not isinstance(expected_bytes, bool)
            and expected_bytes >= 0
            and path.stat().st_size != expected_bytes
        ):
            failures.append(f"size_mismatch:{rel}")
        if sha256(path) != str(item.get("sha256") or "").lower():
            failures.append(f"sha256_mismatch:{rel}")
    ignored_path = None
    if manifest_path is not None:
        resolved_manifest = manifest_path.resolve()
        if resolved_manifest.is_relative_to(root):
            ignored_path = resolved_manifest.relative_to(root).as_posix()
    # A manifest is complete only if every physical file is explicitly listed.
    # The manifest itself is an allowed exception only when supplied by caller.
    for item_path in sorted(root.rglob("*")):
        if not item_path.is_file() and not item_path.is_symlink():
            continue
        relative = item_path.relative_to(root).as_posix()
        if relative == ignored_path or relative in listed_paths:
            continue
        if not item_path.resolve().is_relative_to(root):
            failures.append(f"unsafe_unlisted_path:{relative}")
        else:
            failures.append(f"unlisted_file:{relative}")
    if not isinstance(manifest.get("external_references", []), list):
        failures.append("external_references_invalid")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-candidate", default="")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    failures = audit(args.root, manifest, args.expected_candidate, manifest_path=args.manifest)
    print(
        json.dumps(
            {"status": "PASS" if not failures else "FAIL", "failures": failures},
            indent=2,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
