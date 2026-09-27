"""Append-only JSONL journal for controlled deployment provenance."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any


_SECRET_KEY = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|credential|"
    r"database[_-]?url|connection[_-]?string|private[_-]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:bearer\s+|-----BEGIN [^-]+ PRIVATE KEY-----|postgres(?:ql)?://|"
    r"(?:sk|pk)-[A-Za-z0-9_-]{12,})",
    re.IGNORECASE,
)


def assert_safe_payload(value: Any, *, path: str = "payload") -> None:
    """Fail closed if a journal event looks capable of carrying a secret."""

    if isinstance(value, dict):
        for key, child in value.items():
            if _SECRET_KEY.search(str(key)):
                raise ValueError(f"secret-like journal field rejected: {path}.{key}")
            assert_safe_payload(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_safe_payload(child, path=f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        raise ValueError(f"secret-like journal value rejected: {path}")


def append_event(path: Path, event: dict[str, Any]) -> None:
    assert_safe_payload(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        **event,
    }
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("--data must be a JSON object")
    return parsed


def _json_object_b64(value: str) -> dict[str, Any]:
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("--data-b64 must be base64-encoded UTF-8 JSON") from exc
    parsed = json.loads(decoded or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("--data-b64 must decode to a JSON object")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--attempt-id", default="")
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--status", choices=["STARTED", "PASS", "FAIL"], required=True)
    data_group = parser.add_mutually_exclusive_group()
    data_group.add_argument("--data")
    data_group.add_argument("--data-b64")
    args = parser.parse_args()
    attempt_id = args.attempt_id.strip() or str(uuid.uuid4())
    data = _json_object_b64(args.data_b64) if args.data_b64 else _json_object(args.data or "{}")
    append_event(
        args.journal,
        {
            "attempt_id": attempt_id,
            "candidate_sha": args.candidate_sha,
            "stage": args.stage,
            "status": args.status,
            "data": data,
        },
    )
    print(attempt_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
