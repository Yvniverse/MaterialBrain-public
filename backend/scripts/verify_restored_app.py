import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from alembic.config import Config
from alembic.script import ScriptDirectory
from app.core.database import SessionLocal
from app.main import app
from app.models import (
    Attachment,
    Material,
    PickAllocation,
    PickTask,
    Project,
    StockMovement,
    User,
    WarehouseMap,
)


def main() -> int:
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        openapi = client.get("/api/openapi.json")

    alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    script_head = ScriptDirectory.from_config(Config(str(alembic_ini))).get_current_head()

    with SessionLocal() as db:
        revision = db.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
        counts = {
            "materials": db.scalar(select(func.count(Material.id))) or 0,
            "movements": db.scalar(select(func.count(StockMovement.id))) or 0,
            "projects": db.scalar(select(func.count(Project.id))) or 0,
            "users": db.scalar(select(func.count(User.id))) or 0,
            "attachments": db.scalar(select(func.count(Attachment.id))) or 0,
            "pick_tasks": db.scalar(select(func.count(PickTask.id))) or 0,
            "pick_allocations": db.scalar(select(func.count(PickAllocation.id))) or 0,
            "warehouse_maps": db.scalar(select(func.count(WarehouseMap.id))) or 0,
        }
        invalid_inventory = (
            db.scalar(
                select(func.count(Material.id)).where(
                    (Material.quantity < 0)
                    | (Material.reserved_quantity < 0)
                    | (Material.reserved_quantity > Material.quantity)
                )
            )
            or 0
        )
    result = {
        "health_status_code": health.status_code,
        "health": health.json() if health.status_code == 200 else {},
        "openapi_status_code": openapi.status_code,
        "openapi_path_count": len(openapi.json().get("paths", {}))
        if openapi.status_code == 200
        else 0,
        "alembic_revision": revision,
        "alembic_script_head": script_head,
        "counts": counts,
        "invalid_inventory_rows": invalid_inventory,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    valid = (
        health.status_code == 200
        and openapi.status_code == 200
        and revision == script_head
        and invalid_inventory == 0
    )
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
