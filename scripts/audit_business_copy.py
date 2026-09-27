#!/usr/bin/env python3
"""Fail when normal business surfaces contain development/showcase copy.

The stable ``Portfolio Demo v2`` seed marker is an internal identity used by
idempotent seed/restore logic. It is intentionally excluded; the marker is
sanitized before any normal-user presentation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (ROOT / "frontend" / "src", ROOT / "backend" / "app" / "portfolio_demo")
PATTERNS = (
    r"Current quantity comes only from the governed synthetic inbound movement",
    r"governed synthetic inbound",
    r"synthetic portfolio provenance",
    r"只读证据不会自动验证",
    r"长度用于偏好排序，不代表自动兼容或替代批准",
    r"内部工程项目",
    r"内部工程",
    r"秋招",
    r"作品集",
    r"作品展示",
    r"求职",
    r"简历项目",
    r"面试展示",
    r"Portfolio Showcase",
    r"Synthetic Test Data",
)
TEXT_EXTENSIONS = {".py", ".ts", ".tsx", ".vue", ".js", ".json", ".jsonl"}
EXCLUDED_FILES = {
    ROOT / "frontend" / "src" / "utils" / "businessCopy.ts",
}


def is_internal_seed_identity(path: Path, line: str) -> bool:
    return path.name == "seeder.py" and "Portfolio Demo v2" in line


def main() -> int:
    violations: list[str] = []
    for target in TARGETS:
        for path in target.rglob("*"):
            if (
                not path.is_file()
                or path.suffix.lower() not in TEXT_EXTENSIONS
                or path in EXCLUDED_FILES
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if is_internal_seed_identity(path, line):
                    continue
                for pattern in PATTERNS:
                    match = re.search(pattern, line, re.I)
                    if match:
                        relative = path.relative_to(ROOT)
                        violations.append(
                            f"{relative}:{line_number}: {match.group(0)}"
                        )
    if violations:
        print("Business-copy audit failed:")
        print("\n".join(violations))
        return 1
    print("Business-copy audit PASS (normal UI + Portfolio seed strings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
