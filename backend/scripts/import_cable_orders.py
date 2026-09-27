import argparse
import hashlib
import json
import sys
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.api.v1.cables import _decorate_cable_import_preview, commit_cable_import
from app.api.v1.files import parse_upload
from app.core.database import SessionLocal
from app.models import User
from app.schemas.domain import CableImportCommit
from app.services.cable_import import analyze_cable_order_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="分析并导入线缆订单表格")
    parser.add_argument("file", type=Path)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    content = args.file.read_bytes()
    preview = analyze_cable_order_rows(
        parse_upload(content, args.file.name),
        args.file.name,
    )
    if args.dry_run:
        with SessionLocal() as db:
            preview = _decorate_cable_import_preview(db, preview)
        selected_rows = [
            row for row in preview["rows"] if row["selected"] and row["valid"]
        ]
        print(
            json.dumps(
                {
                    "summary": preview["summary"],
                    "actions": Counter(
                        row["import_action"] for row in preview["rows"]
                    ),
                    "selected_quantity": sum(
                        row["quantity"] for row in selected_rows
                    ),
                    "rows": selected_rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    selected_rows = [
        row for row in preview["rows"] if row["selected"] and row["valid"]
    ]
    idempotency_key = f"cable-file-{hashlib.sha256(content).hexdigest()[:48]}"
    payload = CableImportCommit(
        rows=selected_rows,
        idempotency_key=idempotency_key,
    )
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == args.username))
        if not user:
            raise SystemExit(f"用户不存在：{args.username}")
        result = commit_cable_import(
            payload,
            db,
            user,
            f"cli-cable-import-{uuid.uuid4().hex[:24]}",
        )
    print(
        json.dumps(
            {
                "analysis": preview["summary"],
                "import": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
