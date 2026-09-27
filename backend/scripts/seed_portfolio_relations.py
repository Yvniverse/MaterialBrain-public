"""Dry-run or apply the synthetic Phase 2.3 relation review dataset."""

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
from app.portfolio_demo.relation_seeder import PortfolioRelationSeeder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write review rows; requires PORTFOLIO_RELATION_SEED_ENABLED=true.",
    )
    parser.add_argument("--operator", default="admin")
    args = parser.parse_args()
    with SessionLocal() as db:
        operator = db.scalar(select(User).where(User.username == args.operator))
        if operator is None:
            raise SystemExit(
                "The selected operator does not exist; credentials are never reset here."
            )
        seeder = PortfolioRelationSeeder(db, operator, settings)
        result = seeder.seed() if args.apply else seeder.dry_run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
