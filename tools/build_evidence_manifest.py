"""Build a deterministic SHA-256 manifest for an exact-SHA evidence package."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import re


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build(root: Path, output: Path, candidate: str, backend: str, frontend: str) -> dict:
    root = root.resolve()
    output = output.resolve()
    sha_pattern = re.compile(r"^[0-9a-f]{40}$")
    for value in (candidate, backend, frontend):
        if not sha_pattern.fullmatch(str(value).lower()):
            raise ValueError("candidate and runtime values must be 40-character Git SHAs")
    if not (backend == candidate == frontend):
        raise ValueError("candidate and backend/frontend runtime SHAs must match")
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path == output:
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"evidence path escapes root: {path}")
        relative = resolved.relative_to(root).as_posix()
        if not relative or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise ValueError(f"unsafe evidence path: {relative}")
        files.append(
            {
                "path": resolved.relative_to(root).as_posix(),
                "sha256": sha256(resolved),
                "bytes": resolved.stat().st_size,
                "artifact_type": resolved.suffix.lower().lstrip(".") or "file",
            }
        )
    return {
        "candidate_sha": candidate,
        "runtime_backend_sha": backend,
        "runtime_frontend_sha": frontend,
        "files": files,
        "external_references": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--runtime-backend-sha", required=True)
    parser.add_argument("--runtime-frontend-sha", required=True)
    args = parser.parse_args()
    payload = build(
        args.root,
        args.output,
        args.candidate_sha,
        args.runtime_backend_sha,
        args.runtime_frontend_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
