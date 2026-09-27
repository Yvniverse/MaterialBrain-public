from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models import Location


def organizer_module_slots(side: str, module: str) -> list[str]:
    count = 28 if module == "small" else 8
    side_code = "L" if side == "left" else "R"
    module_code = "S" if module == "small" else "L"
    return [f"{side_code}-{module_code}{index:02}" for index in range(1, count + 1)]


def organizer_slot_names(style: str, left_module: str, right_module: str) -> list[str]:
    if style == "standard_56":
        return [
            f"{chr(65 + row)}{column:02}"
            for row in range(7)
            for column in range(1, 9)
        ]
    if style == "drawer_rack_100":
        return [
            f"{chr(65 + column)}{row:02}"
            for row in range(1, 21)
            for column in range(5)
        ]
    if style == "shelf_rack_6":
        return [f"L{level:02}" for level in range(1, 7)]
    return [
        *organizer_module_slots("left", left_module),
        *organizer_module_slots("right", right_module),
    ]


def organizer_bin_note(style: str, name: str) -> str:
    if style == "standard_56":
        return "56格贴片元件盒小格"
    if style == "drawer_rack_100":
        return f"100抽货架 · 第 {int(name[1:]):02d} 行 · {name[0]} 列"
    if style == "shelf_rack_6":
        return f"六层箱式货架 · 第 {int(name[1:])} 层"
    side = "左" if name.startswith("L-") else "右"
    module = "小格" if "-S" in name else "大格"
    return f"左右可配置元件盒 · {side}半区{module}"


def organizer_child_name(style: str, slot: str) -> str:
    if style == "shelf_rack_6":
        return f"第 {int(slot[1:])} 层"
    return slot


class LocationOrganizerService:
    """Shared location-layout engine used by both the API and Portfolio seeders."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        parent_id: int | None,
        code: str,
        name: str,
        organizer_style: str,
        organizer_left_module: str = "small",
        organizer_right_module: str = "small",
        manager: str = "",
        notes: str = "",
        is_active: bool = True,
    ) -> tuple[Location, list[Location]]:
        if self.db.scalar(select(Location.id).where(Location.code == code)):
            raise BusinessError("LOCATION_CODE_EXISTS", "库位编码已存在", 409)
        parent = self.db.get(Location, parent_id) if parent_id else None
        if parent_id and parent is None:
            raise BusinessError("PARENT_LOCATION_NOT_FOUND", "上级库位不存在", 404)
        slot_names = organizer_slot_names(
            organizer_style,
            organizer_left_module,
            organizer_right_module,
        )
        child_codes = [f"{code}-{slot}" for slot in slot_names]
        if self.db.scalar(select(Location.id).where(Location.code.in_(child_codes))):
            raise BusinessError("LOCATION_CODE_EXISTS", "自动生成的子库位编码已存在", 409)

        full_path = f"{parent.full_path} / {name}" if parent else name
        organizer = Location(
            code=code,
            name=name,
            parent_id=parent_id,
            type="box",
            full_path=full_path,
            manager=manager,
            notes=notes,
            is_active=is_active,
            organizer_style=organizer_style,
            organizer_left_module=organizer_left_module,
            organizer_right_module=organizer_right_module,
        )
        self.db.add(organizer)
        self.db.flush()
        bins: list[Location] = []
        for slot in slot_names:
            child_name = organizer_child_name(organizer_style, slot)
            item = Location(
                code=f"{code}-{slot}",
                name=child_name,
                parent_id=organizer.id,
                type="shelf" if organizer_style == "shelf_rack_6" else "bin",
                full_path=f"{organizer.full_path} / {child_name}",
                manager=manager,
                notes=organizer_bin_note(organizer_style, slot),
                is_active=is_active,
            )
            self.db.add(item)
            bins.append(item)
        self.db.flush()
        return organizer, bins

    def ensure_drawer_rack(
        self,
        *,
        parent_id: int,
        code: str,
        name: str,
        manager: str,
        notes: str,
    ) -> tuple[Location, list[Location], int]:
        """Idempotently create/validate one 100-drawer rack and all its slots."""

        parent = self.db.get(Location, parent_id)
        if parent is None:
            raise BusinessError("PARENT_LOCATION_NOT_FOUND", "上级库位不存在", 404)
        organizer = self.db.scalar(select(Location).where(Location.code == code))
        created = 0
        if organizer is None:
            organizer, bins = self.create(
                parent_id=parent_id,
                code=code,
                name=name,
                organizer_style="drawer_rack_100",
                manager=manager,
                notes=notes,
            )
            return organizer, bins, 101
        if (
            organizer.parent_id != parent_id
            or organizer.type != "box"
            or organizer.organizer_style != "drawer_rack_100"
        ):
            raise BusinessError(
                "CABLE_DRAWER_RACK_CONFLICT",
                "线缆抽屉柜结构与固定 Portfolio 定义冲突。",
                409,
                details={"code": code},
            )
        expected_path = f"{parent.full_path} / {name}"
        organizer.name = name
        organizer.full_path = expected_path
        organizer.manager = manager
        organizer.notes = notes
        organizer.is_active = True

        bins: list[Location] = []
        for slot in organizer_slot_names("drawer_rack_100", "small", "small"):
            child_code = f"{code}-{slot}"
            child = self.db.scalar(select(Location).where(Location.code == child_code))
            child_name = organizer_child_name("drawer_rack_100", slot)
            if child is None:
                child = Location(
                    code=child_code,
                    name=child_name,
                    parent_id=organizer.id,
                    type="bin",
                    full_path=f"{expected_path} / {child_name}",
                    manager=manager,
                    notes=organizer_bin_note("drawer_rack_100", slot),
                    is_active=True,
                )
                self.db.add(child)
                self.db.flush()
                created += 1
            elif child.parent_id != organizer.id or child.type != "bin":
                raise BusinessError(
                    "CABLE_DRAWER_SLOT_CONFLICT",
                    "线缆抽屉结构与固定 Portfolio 定义冲突。",
                    409,
                    details={"code": child_code},
                )
            else:
                child.name = child_name
                child.full_path = f"{expected_path} / {child_name}"
                child.manager = manager
                child.notes = organizer_bin_note("drawer_rack_100", slot)
                child.is_active = True
            bins.append(child)
        self.db.flush()
        return organizer, bins, created
