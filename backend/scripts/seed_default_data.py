import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.seed.defaults import seed_defaults

with SessionLocal() as db:
    seed_defaults(db)
print("默认角色、分类与库位已初始化")
