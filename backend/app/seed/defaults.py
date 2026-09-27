from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Location, Role

ROLE_DEFAULTS = {
    "系统管理员": ["*"],
    "仓库管理员": [
        "dashboard:view",
        "material:view",
        "material:manage",
        "inventory:view",
        "inventory:operate",
        "category:manage",
        "location:manage",
        "supplier:manage",
        "project:view",
        "picking:view",
        "picking:operate",
        "import:manage",
        "export:view",
        "attachment:manage",
    ],
    "硬件工程师": [
        "dashboard:view",
        "material:view",
        "component:validate",
        "inventory:view",
        "project:view",
        "project:manage",
        "picking:view",
    ],
    "项目负责人": [
        "dashboard:view",
        "material:view",
        "inventory:view",
        "inventory:operate",
        "project:view",
        "project:manage",
        "picking:view",
        "picking:operate",
        "component:validate",
        "export:view",
    ],
    "采购人员": [
        "dashboard:view",
        "material:view",
        "inventory:view",
        "supplier:manage",
        "purchase:manage",
        "export:view",
    ],
}

CATEGORIES = {
    "芯片 IC": [
        "MCU",
        "ADC",
        "DAC",
        "运放",
        "比较器",
        "电源管理",
        "通信接口",
        "存储器",
        "逻辑芯片",
        "驱动芯片",
        "时钟芯片",
        "传感器芯片",
        "其他 IC",
    ],
    "电阻": ["贴片电阻", "插件电阻", "精密电阻", "功率电阻", "可调电阻", "其他电阻"],
    "电容": ["陶瓷电容", "电解电容", "钽电容", "薄膜电容", "安规电容", "超级电容", "其他电容"],
    "电感": [],
    "二极管": [],
    "三极管 / MOS": [],
    "连接器": [],
    "传感器": [],
    "模块": [],
    "晶振 / 时钟": [],
    "电源器件": [],
    "开发板 / PCB": [],
    "线缆": [],
    "结构件": [],
    "工具耗材": [],
    "其他": [],
}


def seed_defaults(db: Session) -> None:
    if not db.scalar(select(Role.id).limit(1)):
        for name, permissions in ROLE_DEFAULTS.items():
            db.add(
                Role(
                    name=name,
                    description=f"内置{name}角色",
                    permissions=permissions,
                    is_system=True,
                )
            )
        db.flush()
    if not db.scalar(select(Category.id).limit(1)):
        order = 0
        for parent_name, children in CATEGORIES.items():
            parent = Category(name=parent_name, code=f"CAT-{order + 1:02d}", sort_order=order)
            db.add(parent)
            db.flush()
            for suborder, child in enumerate(children):
                db.add(
                    Category(
                        name=child,
                        code=f"{parent.code}-{suborder + 1:02d}",
                        parent_id=parent.id,
                        sort_order=suborder,
                    )
                )
            order += 1
    if not db.scalar(select(Location.id).limit(1)):
        db.add(Location(code="WH-RD", name="研发仓库", type="warehouse", full_path="研发仓库"))
    db.commit()
