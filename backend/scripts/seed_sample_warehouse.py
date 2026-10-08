"""Preview or add the public sample warehouse to an explicitly named development database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import Settings, settings
from app.core.database import SessionLocal
from app.models import User
from app.sample_data import SampleDataV2Seeder
from scripts.seed_warehouse_map import current_database_name


def validate_target(config: Settings, actual: str, expected: str, *, apply: bool) -> None:
    if config.environment not in {"development", "test"}:
        raise ValueError("Sample warehouse requires ENVIRONMENT=development or test.")
    if not expected or actual != expected:
        raise ValueError("Database identity differs from --expected-database-name.")
    if apply and not config.sample_data_seed_enabled:
        raise ValueError("Writing sample data requires SAMPLE_DATA_SEED_ENABLED=true.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Apply additive sample rows; default is read-only."
    )
    args = parser.parse_args()
    if settings.environment not in {"development", "test"}:
        raise ValueError("Sample warehouse requires ENVIRONMENT=development or test.")
    with SessionLocal() as db:
        validate_target(
            settings, current_database_name(db), args.expected_database_name, apply=args.apply
        )
        operator = db.scalar(
            select(User)
            .where(User.is_active.is_(True), User.is_deleted.is_(False))
            .order_by(User.id)
        )
        if operator is None:
            raise ValueError("Create an operator with scripts/create_admin.py first.")
        seeder = SampleDataV2Seeder(db, operator, settings)
        report = seeder.seed() if args.apply else seeder.dry_run()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
