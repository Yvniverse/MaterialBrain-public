"""Remove only ephemeral login/conversation rows before a Portfolio baseline dump."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import AgentConversationContext, Session, User
from scripts.verify_portfolio_database import verify

_EPHEMERAL_KEYS = {"sessions", "agent_conversation_contexts"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-admin", required=True)
    parser.add_argument("--expected-database-name", default="pengka_material")
    args = parser.parse_args()

    if os.getenv("PORTFOLIO_BASELINE_PREPARE_ENABLED", "false").casefold() != "true":
        print("SKIP: set PORTFOLIO_BASELINE_PREPARE_ENABLED=true explicitly")
        return 0
    actual_database = make_url(settings.database_url).database
    if actual_database != args.expected_database_name:
        raise RuntimeError("Refusing to prepare an unexpected database")

    before = verify(args.expected_admin)
    prohibited = before["details"]["prohibited_history_counts"]
    non_ephemeral = {key: value for key, value in prohibited.items() if key not in _EPHEMERAL_KEYS}
    if any(non_ephemeral.values()):
        raise RuntimeError("Refusing to remove non-ephemeral business history")
    allowed_failures = {"no_preexisting_business_history"}
    if set(before["failures"]) - allowed_failures:
        raise RuntimeError("Portfolio database verification has non-ephemeral failures")

    with SessionLocal() as db:
        users = list(db.scalars(select(User).order_by(User.id)).all())
        if len(users) != 1 or users[0].username != args.expected_admin:
            raise RuntimeError("Selected administrator identity does not match")
        admin_id = users[0].id
        password_hash = users[0].password_hash
        context_count = int(prohibited["agent_conversation_contexts"])
        session_count = int(prohibited["sessions"])

        db.execute(delete(AgentConversationContext))
        db.execute(delete(Session))
        db.commit()

        preserved = db.get(User, admin_id)
        if preserved is None or preserved.password_hash != password_hash:
            raise RuntimeError("Administrator password hash changed during baseline preparation")

    after = verify(args.expected_admin)
    if not after["portfolio_database_verified"]:
        raise RuntimeError("Portfolio database did not pass verification after preparation")
    print(
        json.dumps(
            {
                "portfolio_database_verified": True,
                "removed_session_rows": session_count,
                "removed_conversation_context_rows": context_count,
                "admin_password_hash_preserved": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
