"""Create the requested demonstration materials only in a development environment."""

import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Material, User
from app.services.inventory import InventoryService

DEMO = [
    ("DEMO-ADS1282", "ADS1282", "TQFP-32", 5, 2),
    ("DEMO-OPA1632", "OPA1632", "SOIC-8", 10, 5),
    ("DEMO-VL53L0X", "VL53L0X", "LGA", 8, 3),
    ("DEMO-R-10K", "10kΩ 电阻", "0603", 200, 100),
    ("DEMO-C-100NF", "100nF 电容", "0603", 300, 100),
    ("DEMO-TPS65261", "TPS65261", "HTSSOP", 4, 2),
    ("DEMO-TCAN1044", "TCAN1044AVDRQ1", "SOIC-8", 20, 5),
    ("DEMO-LSM6DSV16X", "LSM6DSV16XTR", "LGA", 8, 3),
    ("DEMO-LTR-F216A", "LTR-F216A 环境光传感器", "", 15, 5),
    ("DEMO-AHT20", "AHT20 温湿度传感器", "", 12, 5),
]


def main():
    if settings.environment != "development":
        raise SystemExit("示例数据只能在 development 环境创建")
    with SessionLocal() as db:
        operator = db.scalar(select(User).order_by(User.id))
        if not operator:
            raise SystemExit("请先运行 scripts/create_admin.py 创建操作人")
        for code, name, package, quantity, safety in DEMO:
            if db.scalar(select(Material.id).where(Material.code == code)):
                continue
            material = Material(
                code=code,
                name=name,
                mpn=name.split()[0],
                package=package,
                safety_stock=Decimal(safety),
                target_stock=Decimal(safety * 2),
                created_by_id=operator.id,
                updated_by_id=operator.id,
            )
            db.add(material)
            db.commit()
            db.refresh(material)
            InventoryService(db, operator.id, f"demo-{uuid.uuid4()}").inbound(
                material.id,
                Decimal(quantity),
                f"demo-{uuid.uuid4()}",
                "开发环境示例初始库存",
                operation_type="initial",
            )
    print("示例物料和对应初始库存流水已创建")


if __name__ == "__main__":
    main()
