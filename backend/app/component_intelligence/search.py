from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Material

from .extractor import canonical_component_type
from .schemas import ComponentQuery, NumericRange


def _normalized(value: object) -> str:
    return "".join(character.casefold() for character in str(value) if character.isalnum())


def _decimal_term(value: Decimal, unit: str) -> str:
    return f"{value.normalize()}{unit}".replace("E+", "e")


def _number(value: Any, *, current: bool = False) -> Decimal | None:
    if value is None:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(mA|A|V)?", str(value), re.I)
    if not match:
        return None
    result = Decimal(match.group(1))
    if current and (match.group(2) or "").casefold() == "ma":
        result /= Decimal("1000")
    return result


def _range(value: Any, *, current: bool = False) -> tuple[Decimal, Decimal] | None:
    if value is None:
        return None
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(mA|A|V)?", str(value), re.I)
    if len(matches) < 2:
        single = _number(value, current=current)
        return (single, single) if single is not None else None

    def convert(item: tuple[str, str]) -> Decimal:
        result = Decimal(item[0])
        if current and item[1].casefold() == "ma":
            result /= Decimal("1000")
        return result

    first, second = convert(matches[0]), convert(matches[1])
    return min(first, second), max(first, second)


def _metadata_confidence(material: Material) -> str:
    attributes = material.attributes or {}
    sample = attributes.get("sample_data") or {}
    provenance = attributes.get("catalog_provenance") or {}
    explicit = sample.get("catalog_confidence") or provenance.get("confidence")
    return str(explicit or "trusted_existing").casefold()


def _trusted(material: Material) -> bool:
    return _metadata_confidence(material) != "low"


def _attribute_values(material: Material, *keys: str) -> list[Any]:
    attributes = material.attributes or {}
    values: list[Any] = []
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value not in (None, ""):
            values.append(value)
    return values


def _contains(values: list[Any], expected: str) -> bool:
    needle = _normalized(expected)
    return any(needle and needle in _normalized(value) for value in values)


def _voltage_matches(material: Material, value: Decimal, *keys: str) -> bool:
    for key in keys:
        for raw in _attribute_values(material, key):
            if key.endswith("_max"):
                maximum = _number(raw)
                if maximum is not None and value <= maximum:
                    return True
            else:
                bounds = _range(raw)
                if bounds and bounds[0] <= value <= bounds[1]:
                    return True
    return False


def _current_capacity_matches(material: Material, value: Decimal) -> bool:
    for raw in _attribute_values(
        material,
        "current",
        "output_current",
        "continuous_current",
        "peak_current",
        "id",
    ):
        capacity = _number(raw, current=True)
        if capacity is not None and capacity >= value:
            return True
    return False


def _current_max_matches(material: Material, value: Decimal) -> bool:
    for raw in _attribute_values(
        material,
        "current",
        "output_current",
        "continuous_current",
        "peak_current",
        "id",
    ):
        capacity = _number(raw, current=True)
        if capacity is not None and capacity <= value:
            return True
    return False


def _component_type_matches(material: Material, expected: str) -> bool:
    actual = [
        canonical_component_type(value)
        for value in _attribute_values(material, "component_type")
    ]
    return expected in actual


def _resolution_matches(material: Material, bits: int) -> bool:
    for value in _attribute_values(material, "resolution_bits", "resolution"):
        actual = _number(value)
        if actual is not None and actual >= bits:
            return True
    return False


def _range_covers(material: Material, required: NumericRange, *keys: str) -> bool:
    for raw in _attribute_values(material, *keys):
        bounds = _range(raw)
        if bounds and bounds[0] <= required.minimum and bounds[1] >= required.maximum:
            return True
    return False


@dataclass(frozen=True)
class RankedMaterial:
    material: Material
    score: int
    reasons: tuple[str, ...]
    hard_constraint_matches: tuple[str, ...]
    soft_preference_matches: tuple[str, ...]
    metadata_confidence: str


class ComponentMaterialSearch:
    """Bounded, read-only structured matching over trusted Material metadata."""

    def __init__(self, db: Session):
        self.db = db

    def by_ids(self, material_ids: list[int], *, reason: str) -> list[RankedMaterial]:
        if not material_ids:
            return []
        rows = list(
            self.db.scalars(
                select(Material).where(
                    Material.id.in_(material_ids),
                    Material.is_active.is_(True),
                    Material.is_deleted.is_(False),
                )
            ).all()
        )
        by_id = {item.id: item for item in rows}
        return [
            RankedMaterial(
                material=by_id[material_id],
                score=100,
                reasons=(reason,),
                hard_constraint_matches=(),
                soft_preference_matches=(),
                metadata_confidence=_metadata_confidence(by_id[material_id]),
            )
            for material_id in material_ids
            if material_id in by_id
        ]

    def search(self, query: ComponentQuery, *, limit: int) -> list[RankedMaterial]:
        materials = list(
            self.db.scalars(
                select(Material)
                .where(Material.is_active.is_(True), Material.is_deleted.is_(False))
                .order_by(Material.code)
                .limit(5000)
            ).all()
        )
        ranked: list[RankedMaterial] = []
        raw_normalized = _normalized(query.raw_text)
        for material in materials:
            exact_reasons = []
            for exact_value, label in ((material.code, "物料编码"), (material.mpn, "MPN")):
                normalized = _normalized(exact_value)
                if normalized and normalized in raw_normalized:
                    exact_reasons.append(f"精确{label}匹配：{exact_value}")
            exact_identity = bool(exact_reasons)
            confidence = _metadata_confidence(material)
            if not exact_identity and not _trusted(material):
                continue

            hard_matches: list[str] = []
            hard_failed = False
            for component_type in query.component_types:
                if _component_type_matches(material, component_type):
                    hard_matches.append(f"器件类型匹配：{component_type}")
                else:
                    hard_failed = True
            interface_values = _attribute_values(material, "interfaces", "interface")
            for interface in query.interfaces:
                if _contains(interface_values, interface):
                    hard_matches.append(f"匹配：{interface}")
                else:
                    hard_failed = True
            if query.supply_voltage_v is not None:
                if _voltage_matches(
                    material,
                    query.supply_voltage_v,
                    "supply_voltage",
                    "supply_voltage_range",
                    "system_voltage",
                ):
                    hard_matches.append(f"匹配：{query.supply_voltage_v}V 供电")
                else:
                    hard_failed = True
            if query.input_voltage_v is not None:
                if _voltage_matches(
                    material,
                    query.input_voltage_v,
                    "input_voltage",
                    "input_range",
                    "input_voltage_max",
                    "system_voltage",
                ):
                    hard_matches.append(f"输入电压覆盖：{query.input_voltage_v}V")
                else:
                    hard_failed = True
            if query.output_voltage_v is not None:
                if _voltage_matches(material, query.output_voltage_v, "output_voltage"):
                    hard_matches.append(f"输出电压匹配：{query.output_voltage_v}V")
                else:
                    hard_failed = True
            if query.bus_voltage_v is not None:
                if _voltage_matches(
                    material,
                    query.bus_voltage_v,
                    "bus_voltage",
                    "bus_voltage_max",
                ):
                    hard_matches.append(f"匹配：总线 {query.bus_voltage_v}V")
                else:
                    hard_failed = True
            if query.logic_voltage_v is not None:
                if _voltage_matches(material, query.logic_voltage_v, "logic_voltage"):
                    hard_matches.append(f"匹配：逻辑 {query.logic_voltage_v}V")
                else:
                    hard_failed = True
            if query.voltage_range_v is not None:
                if _range_covers(
                    material,
                    query.voltage_range_v,
                    "input_range",
                    "input_voltage",
                    "system_voltage",
                ):
                    hard_matches.append(
                        "匹配：输入范围 "
                        f"{query.voltage_range_v.minimum}-{query.voltage_range_v.maximum}V"
                    )
                else:
                    hard_failed = True
            if query.current_min_a is not None:
                if _current_capacity_matches(material, query.current_min_a):
                    hard_matches.append(f"电流能力满足：≥{query.current_min_a}A")
                else:
                    hard_failed = True
            if query.current_max_a is not None:
                if _current_max_matches(material, query.current_max_a):
                    hard_matches.append(f"匹配：输出电流 ≤ {query.current_max_a}A")
                else:
                    hard_failed = True
            if query.current_range_a is not None:
                if _current_capacity_matches(
                    material, query.current_range_a.minimum
                ) and _current_max_matches(material, query.current_range_a.maximum):
                    hard_matches.append(
                        "匹配：输出电流 "
                        f"{query.current_range_a.minimum}-{query.current_range_a.maximum}A"
                    )
                else:
                    hard_failed = True
            if query.resolution_bits is not None:
                if _resolution_matches(material, query.resolution_bits):
                    hard_matches.append(f"匹配：{query.resolution_bits}-bit 分辨率")
                else:
                    hard_failed = True
            if hard_failed:
                continue

            metadata = " ".join(
                (
                    material.code,
                    material.name,
                    material.mpn,
                    material.specification,
                    material.package,
                    material.manufacturer,
                    material.category.name if material.category else "",
                    json.dumps(material.tags or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(material.attributes or {}, ensure_ascii=False, sort_keys=True),
                )
            )
            searchable = _normalized(metadata)
            score = len(exact_reasons) * 100 + len(hard_matches) * 30
            reasons = [*exact_reasons, *hard_matches]
            soft_matches: list[str] = []
            for package in query.package_preferences:
                if _normalized(package) in _normalized(material.package):
                    soft_matches.append(f"偏好匹配：{material.package}")
                    score += 15
            for manufacturer in query.manufacturer_preferences:
                if _normalized(manufacturer) in _normalized(material.manufacturer):
                    soft_matches.append(f"偏好匹配：{material.manufacturer}")
                    score += 15
            for voltage in query.voltage_values_v:
                term = _normalized(_decimal_term(voltage, "v"))
                if term in searchable:
                    reasons.append(f"电压线索匹配：{voltage}V")
                    score += 10
            for keyword in query.keywords:
                normalized = _normalized(keyword)
                if len(normalized) >= 2 and normalized in searchable:
                    reasons.append(f"关键词匹配：{keyword}")
                    score += 8
            reasons.extend(soft_matches)
            if confidence == "low":
                reasons.append("仅匹配 catalog identity；技术分类待确认")
            if score:
                ranked.append(
                    RankedMaterial(
                        material=material,
                        score=score,
                        reasons=tuple(dict.fromkeys(reasons)),
                        hard_constraint_matches=tuple(dict.fromkeys(hard_matches)),
                        soft_preference_matches=tuple(dict.fromkeys(soft_matches)),
                        metadata_confidence=confidence,
                    )
                )
        ranked.sort(key=lambda item: (-item.score, item.material.code))
        return ranked[:limit]
