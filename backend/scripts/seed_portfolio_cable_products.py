"""Dry-run or apply anonymous Phase 2.5 cable-aware Product revisions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import User
from app.portfolio_demo import PortfolioCableProductSeeder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write products; requires PORTFOLIO_CABLE_SEED_ENABLED=true.",
    )
    args = parser.parse_args()
    with SessionLocal() as db:
        operator = db.scalar(select(User).order_by(User.id))
        if operator is None:
            raise SystemExit("No operator exists; administrator credentials are never reset here.")
        seeder = PortfolioCableProductSeeder(db, operator, settings)
        result = seeder.seed() if args.apply else seeder.dry_run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
