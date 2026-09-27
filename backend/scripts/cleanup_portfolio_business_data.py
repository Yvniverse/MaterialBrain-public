"""Audit or idempotently clean normal Portfolio copy and synthetic evidence rows."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.business_copy_cleanup import (
    audit_database_copy,
    cleanup_database_copy,
    remove_synthetic_business_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    actual_database = make_url(settings.database_url).database
    if actual_database != args.expected_database_name:
        raise RuntimeError(
            f"Refusing unexpected database: expected={args.expected_database_name!r} "
            f"actual={actual_database!r}"
        )
    if args.apply and os.getenv("PORTFOLIO_BUSINESS_CLEANUP_ENABLED", "false").casefold() != "true":
        raise RuntimeError("Set PORTFOLIO_BUSINESS_CLEANUP_ENABLED=true for --apply")

    with SessionLocal() as db:
        before = audit_database_copy(db)
        if not args.apply:
            print(json.dumps({"dry_run": True, "before": before}, ensure_ascii=False, indent=2))
            return 0
        copy_changes = cleanup_database_copy(db)
        evidence_changes = remove_synthetic_business_evidence(db)
        db.commit()
        after = audit_database_copy(db)
        result = {
            "dry_run": False,
            "before": before,
            "copy_changes": copy_changes,
            "synthetic_evidence_changes": evidence_changes,
            "after": after,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if after["forbidden_match_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
