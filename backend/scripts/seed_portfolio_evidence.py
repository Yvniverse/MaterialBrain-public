"""Test/Golden-only CLI for deterministic synthetic evidence ingestion."""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import User
from app.portfolio_demo.evidence_seeder import PortfolioEvidenceSeeder


def main() -> int:
    database_name = settings.database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not re.search(r"(?:test|eval|golden)", database_name, re.I):
        raise RuntimeError(
            "Synthetic evidence is isolated to test/Golden databases and cannot seed "
            "the normal Portfolio DB"
        )
    with SessionLocal() as db:
        operator = db.scalar(
            select(User).where(
                User.username == "admin",
                User.is_active.is_(True),
                User.is_deleted.is_(False),
            )
        )
        if operator is None:
            raise RuntimeError("The preserved Portfolio administrator identity is unavailable")
        result = PortfolioEvidenceSeeder(db, operator, settings).seed()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["write_sensitive_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
