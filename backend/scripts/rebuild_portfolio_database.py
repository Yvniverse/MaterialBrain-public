"""Destructively scrub business data behind an explicit portfolio-only gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import delete, select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import Base, SessionLocal
from app.core.exceptions import BusinessError
from app.models import User

PRESERVED_TABLES = {"alembic_version", "roles", "users"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preserve-admin", required=True)
    parser.add_argument("--confirm-destructive-rebuild", action="store_true")
    return parser.parse_args()


def _purge_business_tables(db) -> list[str]:
    tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name not in PRESERVED_TABLES
    ]
    if db.bind.dialect.name == "postgresql":
        preparer = db.bind.dialect.identifier_preparer
        names = ", ".join(preparer.quote(table.name) for table in tables)
        db.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
    else:
        for table in reversed(tables):
            db.execute(table.delete())
    return sorted(table.name for table in tables)


def rebuild(preserve_admin_username: str) -> dict:
    if not settings.portfolio_database_rebuild_enabled:
        raise BusinessError(
            "PORTFOLIO_DATABASE_REBUILD_DISABLED",
            "必须显式设置 PORTFOLIO_DATABASE_REBUILD_ENABLED=true",
            409,
        )
    with SessionLocal() as db:
        admins = list(
            db.scalars(
                select(User)
                .where(User.username == preserve_admin_username)
                .with_for_update()
            ).all()
        )
        if len(admins) != 1:
            raise BusinessError(
                "PORTFOLIO_ADMIN_SELECTION_INVALID",
                "必须精确选择一个要保留的管理员账号",
                409,
            )
        admin = admins[0]
        original_password_hash = admin.password_hash
        purged_tables = _purge_business_tables(db)
        deleted_users = db.execute(delete(User).where(User.id != admin.id)).rowcount
        admin.full_name = "Portfolio Admin"
        admin.department = "Demo Lab"
        admin.is_active = True
        admin.is_deleted = False
        admin.deleted_at = None
        admin.failed_attempts = 0
        admin.locked_until = None
        db.commit()
        db.refresh(admin)
        if admin.password_hash != original_password_hash:
            raise RuntimeError("Preserved administrator password hash changed")
        remaining_users = int(db.scalar(select(text("count(*)")).select_from(User)) or 0)
        return {
            "preserved_admin_username": admin.username,
            "preserved_admin_id": admin.id,
            "password_hash_unchanged": True,
            "remaining_users": remaining_users,
            "deleted_other_users": int(deleted_users or 0),
            "purged_business_table_count": len(purged_tables),
            "purged_business_tables": purged_tables,
        }


def main() -> int:
    args = parse_args()
    if not args.confirm_destructive_rebuild:
        raise SystemExit("Pass --confirm-destructive-rebuild explicitly.")
    result = rebuild(args.preserve_admin)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
