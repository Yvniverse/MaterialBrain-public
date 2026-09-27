from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models import (
    InventoryLot,
    Location,
    Material,
    Product,
    ProductBomItem,
    ProductRevision,
)
from app.schemas.agent import CableSearchArgs

_PITCH = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*mm", re.I)
_BARE_DECIMAL_PITCH = re.compile(r"(?<!\d)(0\.\d+)\s*(?:的|间距)", re.I)
_LABELED_PITCH = re.compile(
    r"(?:间距|pitch)\s*(?:为|是|=|:|：)?\s*(\d+(?:\.\d+)?)\s*(?:mm)?",
    re.I,
)
_PIN_CONVERSION = re.compile(
    r"(\d+)\s*(?:pin|p)?\s*(?:→|->|转|to|[-/])\s*(\d+)\s*(?:pin|p)(?![A-Za-z0-9])",
    re.I,
)
_PIN = re.compile(r"(?<!\d)(\d+)\s*(?:pin|p)(?![A-Za-z0-9])", re.I)
_LENGTH_RANGE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:到|至|[-~～])\s*"
    r"(\d+(?:\.\d+)?)\s*(?:cm\b|厘米|公分)",
    re.I,
)
_LENGTH = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:cm\b|厘米|公分)", re.I)
_MIN_AVAILABLE = re.compile(r"(?:至少|不少于|可用)\s*(\d+(?:\.\d+)?)\s*(?:条|件|根)?", re.I)
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9./_-]{2,}")
_CONNECTOR_PITCH = re.compile(
    r"\b(?:SH|XH|MX|HC|PH|ZH|GH|JST)[- ]?(\d+(?:\.\d+)?)\b",
    re.I,
)


@dataclass(frozen=True)
class CableRequirement:
    query: str
    cable_kind: str | None
    connector_a: str | None
    connector_b: str | None
    connector_pitch_mm: Decimal | None
    pin_count: int | None
    pin_count_b: int | None
    direction: str | None
    end_style: str | None
    length_cm: Decimal | None
    length_tolerance_cm: Decimal
    min_available_quantity: Decimal | None


class CableSearchService:
    """Deterministic read-only cable matching over Material and InventoryLot."""

    def __init__(self, db: Session):
        self.db = db

    def search(self, args: CableSearchArgs) -> dict[str, Any]:
        requirement = self._requirement(args)
        scoped_material_ids = self._product_cable_material_ids(requirement.query)
        materials = list(
            self.db.scalars(
                select(Material)
                .where(
                    Material.is_deleted.is_(False),
                    Material.is_active.is_(True),
                    Material.attributes["material_kind"].as_string() == "cable",
                )
                .order_by(Material.code)
            ).all()
        )
        if scoped_material_ids is not None:
            materials = [item for item in materials if item.id in scoped_material_ids]
        matches: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for material in materials:
            matched = self._match(material, requirement)
            if matched is not None:
                matches.append(matched)
        matches.sort(key=lambda row: row[0])
        items = [item for _rank, item in matches[: args.limit]]
        directions = {
            item["direction"] for item in items if item["direction"] in {"same", "reverse"}
        }
        needs_direction = (
            requirement.direction is None
            and requirement.cable_kind in {"flat_flex", "micro_coax"}
            and (directions == {"same", "reverse"} or not items)
        )
        result_state = "awaiting_clarification" if needs_direction else "exact_match"
        if not items and not needs_direction:
            near_matches = [
                matched
                for material in materials
                if (matched := self._near_match(material, requirement)) is not None
            ]
            near_matches.sort(key=lambda row: row[0])
            items = [item for _rank, item in near_matches[: args.limit]]
            result_state = "near_match" if items else "no_match"
        selected_id = items[0]["material_id"] if len(items) == 1 and not needs_direction else None
        candidates = [
            {
                "id": item["material_id"],
                "code": item["code"],
                "name": item["name"],
                "mpn": item["mpn"],
                "specification": item["specification"],
                "package": "线缆",
                "manufacturer": item["manufacturer"],
                "unit": item["unit"],
            }
            for item in items
        ]
        return {
            "query": requirement.query,
            "constraints": self._constraint_dict(requirement),
            "items": items,
            "count": len(items),
            "evaluated_count": len(materials),
            "needs_direction_disambiguation": needs_direction,
            "result_state": result_state,
            "clarification": ("触点方向需要同向(A型)还是反向(B型)？" if needs_direction else ""),
            "automatic_substitution": False,
            "inventory_source": "Material + InventoryLot",
            "material_candidates": {
                "query": requirement.query,
                "items": candidates,
                "count": len(candidates),
                "exact_match_ids": [],
                "selected_material_id": selected_id,
            },
        }

    @classmethod
    def parse_constraints(cls, query: str) -> dict[str, Any]:
        """Parse one turn into bounded slots without searching or storing prose."""

        return cls._constraint_dict(cls._requirement(CableSearchArgs(query=query)))

    @staticmethod
    def query_from_constraints(constraints: dict[str, Any]) -> str:
        """Render canonical slots back into a deterministic tool query."""

        parts: list[str] = []
        kind = {
            "terminal": "端子线",
            "flat_flex": "FFC 排线",
            "micro_coax": "极细同轴线",
            "rf_coax": "IPEX 射频同轴线",
        }.get(str(constraints.get("cable_kind") or ""))
        if kind:
            parts.append(kind)
        for field in ("connector_a", "connector_b"):
            if constraints.get(field):
                parts.append(str(constraints[field]))
        if constraints.get("connector_pitch_mm") is not None:
            parts.append(f"{constraints['connector_pitch_mm']}mm")
        if constraints.get("pin_count") is not None:
            pins = f"{constraints['pin_count']}Pin"
            if constraints.get("pin_count_b") is not None:
                pins += f" 转 {constraints['pin_count_b']}Pin"
            parts.append(pins)
        if constraints.get("direction") in {"same", "reverse"}:
            parts.append("同向" if constraints["direction"] == "same" else "反向")
        end_style = {
            "double": "双头",
            "single": "单头",
            "single_tinned": "单头沾锡",
            "male_female_pair": "公母对接",
        }.get(str(constraints.get("end_style") or ""))
        if end_style:
            parts.append(end_style)
        if constraints.get("length_cm") is not None:
            parts.append(f"{constraints['length_cm']}cm")
        if constraints.get("min_available_quantity") is not None:
            parts.append(f"可用至少 {constraints['min_available_quantity']} 根")
        return " ".join(parts)

    def detail(self, material_id: int) -> dict[str, Any]:
        material = self.db.scalar(
            select(Material).where(
                Material.id == material_id,
                Material.is_deleted.is_(False),
                Material.is_active.is_(True),
                Material.attributes["material_kind"].as_string() == "cable",
            )
        )
        if material is None:
            raise BusinessError("CABLE_NOT_FOUND", "线缆不存在", 404)
        return self._item(material, match_reasons=["已按 material_id 精确读取"])

    def _match(
        self,
        material: Material,
        requirement: CableRequirement,
    ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
        attrs = material.attributes or {}
        kind = str(attrs.get("cable_kind") or "terminal")
        direction = str(attrs.get("direction") or "unspecified")
        end_style = str(attrs.get("end_style") or "unspecified")
        pitch = self._decimal(attrs.get("connector_pitch_mm"))
        length = self._decimal(attrs.get("length_cm")) or Decimal("0")
        pins = int(attrs.get("pin_count") or 0)
        pins_b = int(attrs.get("pin_count_b") or 0)
        connector_a = str(attrs.get("connector_a") or "")
        connector_b = str(attrs.get("connector_b") or "")
        available = Decimal(material.available_quantity)

        hard = (
            (requirement.cable_kind is None or kind == requirement.cable_kind)
            and (requirement.connector_pitch_mm is None or pitch == requirement.connector_pitch_mm)
            and (requirement.pin_count is None or pins == requirement.pin_count)
            and (requirement.pin_count_b is None or pins_b == requirement.pin_count_b)
            and (requirement.direction is None or direction == requirement.direction)
            and (requirement.end_style is None or end_style == requirement.end_style)
            and self._connector_matches(
                requirement.connector_a,
                material.code,
                connector_a,
                connector_b,
                material.mpn or "",
                material.name,
                str(attrs.get("catalog_mpn_hint") or ""),
            )
            and self._connector_matches(
                requirement.connector_b,
                material.code,
                connector_b,
                connector_a,
                material.mpn or "",
                material.name,
                str(attrs.get("catalog_mpn_hint") or ""),
            )
            and (
                requirement.min_available_quantity is None
                or available >= requirement.min_available_quantity
            )
        )
        if not hard:
            return None

        structured = any(
            value is not None
            for value in (
                requirement.cable_kind,
                requirement.connector_a,
                requirement.connector_pitch_mm,
                requirement.pin_count,
                requirement.pin_count_b,
                requirement.direction,
                requirement.end_style,
                requirement.length_cm,
                requirement.min_available_quantity,
            )
        )
        if (
            not structured
            and requirement.query
            and not self._text_matches(material, requirement.query)
        ):
            return None

        reasons: list[str] = []
        if requirement.cable_kind:
            reasons.append("线缆类型匹配")
        if requirement.connector_pitch_mm is not None:
            reasons.append(f"间距 {requirement.connector_pitch_mm} mm 匹配")
        if requirement.pin_count is not None:
            pin_text = (
                f"{requirement.pin_count}→{requirement.pin_count_b} Pin"
                if requirement.pin_count_b
                else f"{requirement.pin_count} Pin"
            )
            reasons.append(f"{pin_text} 匹配")
        if requirement.direction:
            reasons.append("触点方向匹配")
        if requirement.min_available_quantity is not None:
            reasons.append("可用库存达到下限")
        length_deviation = (
            abs(length - requirement.length_cm)
            if requirement.length_cm is not None
            else Decimal("0")
        )
        if requirement.length_cm is not None:
            reasons.append(f"长度偏差 {length_deviation} cm（软排序）")
        if requirement.length_cm is not None and length_deviation > requirement.length_tolerance_cm:
            reasons.append("长度超出偏好容差，但未作为兼容性结论")
        item = self._item(material, match_reasons=reasons)
        availability_priority = any(
            marker in requirement.query.casefold()
            for marker in ("有现货", "现货优先", "库存多", "放前面")
        )
        rank = (
            (0 if available > 0 else 1, length_deviation, material.code)
            if availability_priority
            else (length_deviation, 0 if available > 0 else 1, material.code)
        )
        return rank, item

    def _near_match(
        self,
        material: Material,
        requirement: CableRequirement,
    ) -> tuple[tuple[int, Decimal, int, str], dict[str, Any]] | None:
        """Return a bounded one-difference candidate, never an approval verdict."""

        attrs = material.attributes or {}
        kind = str(attrs.get("cable_kind") or "terminal")
        direction = str(attrs.get("direction") or "unspecified")
        end_style = str(attrs.get("end_style") or "unspecified")
        pitch = self._decimal(attrs.get("connector_pitch_mm"))
        length = self._decimal(attrs.get("length_cm")) or Decimal("0")
        pins = int(attrs.get("pin_count") or 0)
        pins_b = int(attrs.get("pin_count_b") or 0)
        available = Decimal(material.available_quantity)
        if (
            requirement.min_available_quantity is not None
            and available < requirement.min_available_quantity
        ):
            # Explicit stock thresholds are hard business constraints, not a
            # soft geometric/spec difference suitable for near-match cards.
            return None
        connector_values = (
            material.code,
            str(attrs.get("connector_a") or ""),
            str(attrs.get("connector_b") or ""),
            material.mpn or "",
            material.name,
            str(attrs.get("catalog_mpn_hint") or ""),
        )
        differences: list[str] = []
        if requirement.cable_kind is not None and kind != requirement.cable_kind:
            differences.append(f"线缆类型为 {kind}")
        if requirement.connector_pitch_mm is not None and pitch != requirement.connector_pitch_mm:
            differences.append(f"间距为 {pitch if pitch is not None else '未标注'} mm")
        if requirement.pin_count is not None and pins != requirement.pin_count:
            differences.append(f"针数为 {pins} Pin")
        if requirement.pin_count_b is not None and pins_b != requirement.pin_count_b:
            differences.append(f"另一端针数为 {pins_b} Pin")
        if requirement.direction is not None and direction != requirement.direction:
            label = {"same": "同向", "reverse": "反向"}.get(direction, "未标注")
            differences.append(f"触点方向为 {label}")
        if requirement.end_style is not None and end_style != requirement.end_style:
            differences.append(f"端部形式为 {end_style}")
        if requirement.connector_a and not self._connector_matches(
            requirement.connector_a, *connector_values
        ):
            differences.append("连接器型号不同")
        if requirement.connector_b and not self._connector_matches(
            requirement.connector_b, *connector_values
        ):
            differences.append("另一端连接器型号不同")
        structured = any(
            value is not None
            for value in (
                requirement.cable_kind,
                requirement.connector_pitch_mm,
                requirement.pin_count,
                requirement.pin_count_b,
                requirement.direction,
                requirement.end_style,
                requirement.length_cm,
                requirement.min_available_quantity,
            )
        )
        if not structured or len(differences) != 1:
            return None
        length_deviation = (
            abs(length - requirement.length_cm)
            if requirement.length_cm is not None
            else Decimal("0")
        )
        item = self._item(material, match_reasons=[])
        item["match_state"] = "near_match"
        item["differences"] = differences
        return (
            len(differences),
            length_deviation,
            0 if available > 0 else 1,
            material.code,
        ), item

    def _item(self, material: Material, *, match_reasons: list[str]) -> dict[str, Any]:
        attrs = material.attributes or {}
        locations = self._locations(material.id)
        return {
            "material_id": material.id,
            "code": material.code,
            "name": material.name,
            "mpn": material.mpn,
            "manufacturer": material.manufacturer,
            "specification": material.specification,
            "unit": material.unit,
            "quantity": str(material.quantity),
            "reserved_quantity": str(material.reserved_quantity),
            "available_quantity": str(material.available_quantity),
            "cable_kind": str(attrs.get("cable_kind") or "terminal"),
            "end_style": str(attrs.get("end_style") or "unspecified"),
            "connector_a": str(attrs.get("connector_a") or ""),
            "connector_b": str(attrs.get("connector_b") or ""),
            "connector_pitch_mm": str(attrs.get("connector_pitch_mm") or ""),
            "pin_count": int(attrs.get("pin_count") or 0),
            "pin_count_b": int(attrs.get("pin_count_b") or 0),
            "pin_layout": str(attrs.get("pin_layout") or ""),
            "direction": str(attrs.get("direction") or "unspecified"),
            "length_cm": str(attrs.get("length_cm") or "0"),
            "locations": locations,
            "location_count": len(locations),
            "fallback_storage_location": str(attrs.get("storage_location") or ""),
            "location_truth_source": "InventoryLot",
            "match_reasons": match_reasons,
            "technical_claims_allowed": bool(attrs.get("technical_claims_allowed", False)),
            "match_state": "exact_match",
            "differences": [],
        }

    def _locations(self, material_id: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            select(InventoryLot, Location)
            .join(Location, Location.id == InventoryLot.location_id)
            .where(
                InventoryLot.material_id == material_id,
                InventoryLot.quantity > 0,
                Location.is_active.is_(True),
            )
            .order_by(Location.full_path)
        ).all()
        return [
            {
                "location_id": location.id,
                "code": location.code,
                "name": location.name,
                "full_path": location.full_path,
                "quantity": str(lot.quantity),
            }
            for lot, location in rows
        ]

    @classmethod
    def _requirement(cls, args: CableSearchArgs) -> CableRequirement:
        text = args.query.strip()
        folded = text.casefold()
        conversion = _PIN_CONVERSION.search(text)
        pitch = _PITCH.search(text)
        bare_pitch = _BARE_DECIMAL_PITCH.search(text)
        labeled_pitch = _LABELED_PITCH.search(text)
        connector_pitch = _CONNECTOR_PITCH.search(text)
        pin = _PIN.search(text)
        length_range = _LENGTH_RANGE.search(text)
        length = _LENGTH.search(text)
        minimum = _MIN_AVAILABLE.search(text)
        kind = args.cable_kind
        if kind is None:
            if any(marker in folded for marker in ("ffc", "fpc/ffc", "软排线", "排线")):
                kind = "flat_flex"
            elif any(marker in folded for marker in ("极细同轴", "micro coax")):
                kind = "micro_coax"
            elif any(marker in folded for marker in ("ipex", "射频同轴", "rf coax", "同轴线")):
                kind = "rf_coax"
            elif any(marker in folded for marker in ("端子线", "接口端子", "端子", "线束")):
                kind = "terminal"
        direction = args.direction
        if direction is None:
            direction_optional = any(marker in folded for marker in ("不知道", "无所谓", "不确定"))
            if not direction_optional and (
                "同向" in folded or "type a" in folded or re.search(r"\ba\s*型", folded)
            ):
                direction = "same"
            elif not direction_optional and (
                "反向" in folded or "type b" in folded or re.search(r"\bb\s*型", folded)
            ):
                direction = "reverse"
        end_style = args.end_style
        if end_style is None:
            if "单头沾锡" in text:
                end_style = "single_tinned"
            elif "单头" in text:
                end_style = "single"
            elif "双头" in text:
                end_style = "double"
            elif any(marker in text for marker in ("公母", "对接")):
                end_style = "male_female_pair"
        connector_a = args.connector_a
        if connector_a is None:
            policy_only = any(
                marker in folded
                for marker in (
                    "storage_location",
                    "采购单价",
                    "库存价值",
                    "订单里",
                    "方向含义",
                    "自动换",
                    "库存改成",
                )
            )
            identifiers = [
                token
                for token in _IDENTIFIER.findall(text)
                if token.casefold()
                not in {
                    "ffc",
                    "fpc",
                    "fpc/ffc",
                    "pin",
                    "type",
                    "cable",
                    "camera",
                    "atlas",
                    "cm",
                    "mm",
                }
                and not token.casefold().startswith("pin")
                and not re.fullmatch(r"\d+(?:\.\d+)?(?:mm|cm|pin|p)", token, re.I)
            ]
            connector_a = (
                None if policy_only else max(identifiers, key=len) if identifiers else None
            )
            family = re.search(r"\b(SH|XH|MX|HC|PH|ZH|GH|JST)\b", text, re.I)
            if (
                family
                and not policy_only
                and (
                    connector_a is None
                    or connector_a.casefold() == family.group(1).casefold()
                )
            ):
                # Do not collapse a specific catalog/model token such as
                # ``HC-0.8-7PWT`` to the broad ``HC`` family. Exact identity
                # must win; family matching is only a fallback when the user
                # actually supplied only the family name.
                connector_a = family.group(1)
        range_midpoint = None
        range_tolerance = None
        if length_range:
            low = Decimal(length_range.group(1))
            high = Decimal(length_range.group(2))
            if low > high:
                low, high = high, low
            range_midpoint = (low + high) / Decimal("2")
            range_tolerance = (high - low) / Decimal("2")

        return CableRequirement(
            query=text,
            cable_kind=kind,
            connector_a=connector_a,
            connector_b=args.connector_b,
            connector_pitch_mm=args.connector_pitch_mm
            or (
                Decimal(pitch.group(1))
                if pitch
                else Decimal(bare_pitch.group(1))
                if bare_pitch
                else Decimal(labeled_pitch.group(1))
                if labeled_pitch
                else Decimal(connector_pitch.group(1))
                if connector_pitch
                else None
            ),
            pin_count=args.pin_count
            or (int(conversion.group(1)) if conversion else int(pin.group(1)) if pin else None),
            pin_count_b=args.pin_count_b or (int(conversion.group(2)) if conversion else None),
            direction=direction,
            end_style=end_style,
            length_cm=args.length_cm
            or range_midpoint
            or (Decimal(length.group(1)) if length else None),
            length_tolerance_cm=(
                args.length_tolerance_cm
                if "length_tolerance_cm" in args.model_fields_set or range_tolerance is None
                else range_tolerance
            ),
            min_available_quantity=args.min_available_quantity
            or (Decimal(minimum.group(1)) if minimum else None),
        )

    @staticmethod
    def _connector_matches(needle: str | None, *values: str) -> bool:
        if not needle:
            return True
        folded = needle.casefold()
        return any(folded in value.casefold() for value in values)

    def _product_cable_material_ids(self, query: str) -> set[int] | None:
        """Restrict a named product query to cables on its released BOMs."""

        folded = query.casefold()
        products = list(
            self.db.scalars(
                select(Product).where(Product.lifecycle_status == "active").order_by(Product.code)
            ).all()
        )
        matched: list[Product] = []
        for product in products:
            aliases = {
                product.code.casefold(),
                *(
                    part.casefold()
                    for part in re.split(r"[-_\s]+", product.code)
                    if len(part) >= 4 and part.casefold() not in {"prod"}
                ),
                *(
                    part.casefold()
                    for part in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", product.name)
                ),
            }
            if any(alias in folded for alias in aliases):
                matched.append(product)
        if len(matched) != 1:
            return None
        return set(
            self.db.scalars(
                select(ProductBomItem.material_id)
                .join(
                    ProductRevision,
                    ProductRevision.id == ProductBomItem.product_revision_id,
                )
                .join(Material, Material.id == ProductBomItem.material_id)
                .where(
                    ProductRevision.product_id == matched[0].id,
                    ProductRevision.status == "released",
                    Material.attributes["material_kind"].as_string() == "cable",
                )
            ).all()
        )

    @staticmethod
    def _text_matches(material: Material, query: str) -> bool:
        attrs = material.attributes or {}
        searchable = " ".join(
            str(value or "")
            for value in (
                material.code,
                material.name,
                material.mpn,
                attrs.get("catalog_mpn_hint"),
                attrs.get("connector_a"),
                attrs.get("connector_b"),
            )
        ).casefold()
        tokens = [token.casefold() for token in _IDENTIFIER.findall(query)]
        return bool(tokens) and all(token in searchable for token in tokens)

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        return Decimal(str(value))

    @staticmethod
    def _constraint_dict(requirement: CableRequirement) -> dict[str, Any]:
        return {
            "cable_kind": requirement.cable_kind,
            "connector_a": requirement.connector_a,
            "connector_b": requirement.connector_b,
            "connector_pitch_mm": (
                str(requirement.connector_pitch_mm)
                if requirement.connector_pitch_mm is not None
                else None
            ),
            "pin_count": requirement.pin_count,
            "pin_count_b": requirement.pin_count_b,
            "direction": requirement.direction,
            "end_style": requirement.end_style,
            "length_cm": str(requirement.length_cm) if requirement.length_cm is not None else None,
            "length_is_soft": requirement.length_cm is not None,
            "min_available_quantity": (
                str(requirement.min_available_quantity)
                if requirement.min_available_quantity is not None
                else None
            ),
        }
