"""Idempotently register the canonical non-active lab map after spatial migration."""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.spatial import SpatialMapService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database-name", required=True)
    args = parser.parse_args()
    with SessionLocal() as db:
        current = db.execute(text("SELECT current_database()")).scalar_one()
        if current != args.expected_database_name:
            raise RuntimeError("DATABASE_IDENTITY_MISMATCH")
        if (
            db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            != "0016_spatial_hd_map"
        ):
            raise RuntimeError("SPATIAL_MIGRATION_REQUIRED")
        result = SpatialMapService(db).register_lab()
        db.commit()
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
