"""Audit or explicitly reconcile Portfolio Cable lots into visible drawer locations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import User
from app.portfolio_demo.cable_locations import (
    CableLocationReconciliationService,
    audit_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument(
        "--require-release-gate",
        action="store_true",
        help="Return a failure status unless the physical-location release gate passes.",
    )
    parser.add_argument("--expected-database-name", default="pengka_material")
    parser.add_argument("--operator", default="admin")
    parser.add_argument(
        "--output-dir",
        default=".local-evals/cable-location-reconciliation",
    )
    args = parser.parse_args()
    if make_url(settings.database_url).database != args.expected_database_name:
        raise RuntimeError("Refusing to audit or repair an unexpected database")
    if args.repair and os.getenv(
        "PORTFOLIO_CABLE_LOCATION_RECONCILE_ENABLED", "false"
    ).casefold() != "true":
        raise RuntimeError(
            "Set PORTFOLIO_CABLE_LOCATION_RECONCILE_ENABLED=true for the explicit repair"
        )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with SessionLocal() as db:
        operator = db.scalar(
            select(User).where(
                User.username == args.operator,
                User.is_deleted.is_(False),
                User.is_active.is_(True),
            )
        )
        if args.repair and operator is None:
            raise RuntimeError("Selected reconciliation operator was not found")
        service = CableLocationReconciliationService(db, operator)
        report = service.reconcile() if args.repair else service.audit(verify_agent=bool(operator))

    stem = f"cable-location-{'after' if args.repair else 'audit'}-{stamp}"
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(audit_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "mode": "repair" if args.repair else "audit",
                "summary": report.get("after", report.get("summary")),
                "json_report": str(json_path),
                "markdown_report": str(markdown_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    summary = report.get("after", report.get("summary", {}))
    if (args.repair or args.require_release_gate):
        return 0 if summary.get("release_gate_passed") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
