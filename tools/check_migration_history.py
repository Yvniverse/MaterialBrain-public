#!/usr/bin/env python3
"""Reject edits/deletions to migrations frozen by MIGRATION_HISTORY_SHA256.json.

New Alembic revisions are allowed. Once a revision has shipped, deliberately add
its checksum to the manifest in a dedicated baseline/freeze change rather than
rewriting any prior migration.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "backend" / "alembic" / "MIGRATION_HISTORY_SHA256.json"
VERSIONS = ROOT / "backend" / "alembic" / "versions"


def main() -> int:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures: list[str] = []
    rows = []
    for name, expected in sorted((data.get("files") or {}).items()):
        path = VERSIONS / name
        if not path.exists():
            failures.append(f"frozen migration missing: {name}")
            rows.append({"file": name, "status": "MISSING", "expected": expected})
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        status = "PASS" if actual == expected else "CHANGED"
        rows.append({"file": name, "status": status, "expected": expected, "actual": actual})
        if actual != expected:
            failures.append(f"frozen migration changed: {name}")
    result = {
        "frozen_through_revision": data.get("frozen_through_revision"),
        "frozen_count": len(rows),
        "failures": failures,
        "rows": rows,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
