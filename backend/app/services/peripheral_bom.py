from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER = r"[-+]?\d+(?:\.\d+)?"
_CAPACITANCE = re.compile(
    rf"(?P<value>{_NUMBER})\s*(?P<unit>pF|nF|uF|μF|µF|mF|F)\b",
    re.IGNORECASE,
)
_VOLTAGE = re.compile(rf"(?P<value>{_NUMBER})\s*(?:V|伏)\b", re.IGNORECASE)
_TOLERANCE = re.compile(rf"(?:±|\+/-|\+\s*/\s*-)?\s*(?P<value>{_NUMBER})\s*%")
_RESISTANCE = re.compile(
    rf"(?P<value>{_NUMBER})\s*(?P<prefix>[kKmMμµu]?)[\s]*(?:Ω|ohm|ohms|欧姆)\b",
    re.IGNORECASE,
)
_INDUCTANCE = re.compile(
    rf"(?P<value>{_NUMBER})\s*(?P<unit>nH|uH|μH|µH|mH|H)\b", re.IGNORECASE
)
_POWER = re.compile(rf"(?P<value>{_NUMBER})\s*(?:mW|W|瓦)\b", re.IGNORECASE)
_LABELED_CURRENT = re.compile(
    rf"(?P<label>i\s*[_-]?\s*(?:sat|rms)|saturation\s+current|rms\s+current|饱和电流|额定电流)"
    rf"\s*[:=：]?\s*(?P<value>{_NUMBER})\s*(?P<unit>mA|A)\b",
    re.IGNORECASE,
)
_LABELED_DCR = re.compile(
    rf"(?:dcr|dc\s+resistance|直流电阻)\s*[:=：]?\s*(?P<value>{_NUMBER})\s*"
    rf"(?P<prefix>[kKmMμµu]?)[\s]*(?:Ω|ohm|ohms|欧姆)\b",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return str(value or "").replace("μ", "u").replace("µ", "u").strip()


_COMPONENT_CLASS_ALIASES: dict[str, tuple[str, ...]] = {
    "capacitor": ("capacitor", "cap", "电容"),
    "resistor": ("resistor", "res", "电阻"),
    "inductor": ("inductor", "coil", "电感"),
    "inductor_ferrite": ("inductor_ferrite", "ferrite bead", "磁珠"),
    "buck converter": (
        "buck converter",
        "buck",
        "降压转换器",
        "开关降压",
    ),
    "switching regulator": ("switching regulator", "switch regulator", "开关稳压"),
    "dc-dc converter": ("dc-dc converter", "dcdc converter", "dc dc", "dc/dc"),
    "ldo": ("ldo", "低压差稳压"),
    "linear regulator": ("linear regulator", "linear voltage regulator", "线性稳压"),
    "cable": ("cable", "wire", "线缆", "电缆"),
    "connector": ("connector", "连接器"),
}


def _canonical_component_class(value: Any) -> str | None:
    text = _text(value).casefold()
    if not text:
        return None
    compact = re.sub(r"[\s_/]+", "", text)
    for canonical, aliases in _COMPONENT_CLASS_ALIASES.items():
        for alias in aliases:
            folded = _text(alias).casefold()
            if folded and (folded in text or re.sub(r"[\s_/]+", "", folded) in compact):
                return canonical
    # Broad catalog labels identify an electronic part but do not prove the
    # functional class required by an engineering BOM role.  Treat them as
    # unknown rather than as an incompatible class; the gate can then keep
    # the candidate visible as partial/untyped without promoting it.
    if compact in {"poweric", "ic", "semiconductor", "electroniccomponent", "power"}:
        return "unknown"
    return text


def resolve_component_class(item: dict[str, Any]) -> dict[str, str]:
    """Resolve class from explicit structured metadata only; never from an LLM."""

    attributes = item.get("attributes") or {}
    if not isinstance(attributes, dict):
        attributes = {}
    explicit = attributes.get("component_type")
    if explicit in (None, ""):
        explicit = attributes.get("component_class")
    if explicit not in (None, ""):
        if isinstance(explicit, (list, tuple)):
            explicit = next((value for value in explicit if value not in (None, "")), None)
        return {
            "component_class": _canonical_component_class(explicit) or "unknown",
            "class_source": "attributes.component_type",
        }

    for key in ("catalog_component_type", "structured_component_type", "category_type"):
        value = item.get(key) or attributes.get(key)
        if value not in (None, ""):
            return {
                "component_class": _canonical_component_class(value) or "unknown",
                "class_source": f"{key}",
            }

    category = item.get("category")
    category_value = (
        category.get("name") or category.get("code")
        if isinstance(category, dict)
        else category
    )
    if category_value not in (None, ""):
        return {
            "component_class": _canonical_component_class(category_value) or "unknown",
            "class_source": "category",
        }
    for key in ("category_name", "category_code"):
        value = item.get(key)
        if value not in (None, ""):
            return {
                "component_class": _canonical_component_class(value) or "unknown",
                "class_source": key,
            }
    return {"component_class": "unknown", "class_source": "unknown"}


def expected_component_classes(role: Any) -> set[str]:
    folded = _text(role).casefold()
    if "capacitor" in folded or "电容" in folded:
        return {"capacitor"}
    if "resistor" in folded or "divider" in folded or "电阻" in folded:
        return {"resistor"}
    if "inductor" in folded or "电感" in folded:
        return {"inductor", "inductor_ferrite"}
    if "ldo" in folded or "linear" in folded or "线性稳压" in folded:
        return {"ldo", "linear regulator"}
    if "primary" in folded and ("buck" in folded or "regulator" in folded):
        return {"buck converter", "switching regulator", "dc-dc converter"}
    return set()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None


def _capacitance_pf(value: Any) -> Decimal | None:
    """Normalize capacitor values to pF so 2.2nF and 2200pF compare exactly."""

    if isinstance(value, (int, float, Decimal)):
        return None
    match = _CAPACITANCE.search(_text(value))
    if not match:
        return None
    amount = _decimal(match.group("value"))
    if amount is None:
        return None
    scale = {
        "pf": Decimal("1"),
        "nf": Decimal("1000"),
        "uf": Decimal("1000000"),
        "mf": Decimal("1000000000"),
        "f": Decimal("1000000000000"),
    }[match.group("unit").replace("μ", "u").replace("µ", "u").casefold()]
    return amount * scale


def _voltage_v(value: Any) -> Decimal | None:
    if isinstance(value, (int, float, Decimal)):
        return _decimal(value)
    match = _VOLTAGE.search(_text(value))
    return _decimal(match.group("value")) if match else None


def _dielectric(value: Any) -> str | None:
    folded = _text(value).casefold().replace("-", "")
    for marker in ("x7r", "x5r", "c0g", "np0", "y5v", "z5u"):
        if marker in folded:
            return marker.upper()
    return None


def _package(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _scaled_decimal(value: Any, prefix: str | None, *, resistance: bool = False) -> Decimal | None:
    amount = _decimal(value)
    if amount is None:
        return None
    scales = {
        "": Decimal("1"),
        "k": Decimal("1000"),
        "K": Decimal("1000"),
        "m": Decimal("0.001") if resistance else Decimal("0.001"),
        "u": Decimal("0.000001"),
        "μ": Decimal("0.000001"),
        "µ": Decimal("0.000001"),
        "n": Decimal("0.000000001"),
        "M": Decimal("1000000"),
    }
    return amount * scales.get(prefix or "", Decimal("1"))


def _resistance_ohms(value: Any) -> Decimal | None:
    if isinstance(value, (int, float, Decimal)):
        return _decimal(value)
    match = _RESISTANCE.search(_text(value))
    if not match:
        return None
    return _scaled_decimal(match.group("value"), match.group("prefix"), resistance=True)


def _inductance_h(value: Any) -> Decimal | None:
    if isinstance(value, (int, float, Decimal)):
        return None
    match = _INDUCTANCE.search(_text(value))
    if not match:
        return None
    unit = match.group("unit").replace("μ", "u").replace("µ", "u").casefold()
    scale = {
        "nh": Decimal("0.000000001"),
        "uh": Decimal("0.000001"),
        "mh": Decimal("0.001"),
        "h": Decimal("1"),
    }[unit]
    return _decimal(match.group("value")) * scale


def _current_a(value: Any) -> Decimal | None:
    if isinstance(value, (int, float, Decimal)):
        return _decimal(value)
    text = _text(value)
    match = re.search(rf"(?P<value>{_NUMBER})\s*(?P<unit>mA|A)\b", text, re.IGNORECASE)
    if not match:
        return None
    amount = _decimal(match.group("value"))
    if amount is None:
        return None
    return amount / Decimal("1000") if match.group("unit").casefold() == "ma" else amount


def _power_w(value: Any) -> Decimal | None:
    if isinstance(value, (int, float, Decimal)):
        return _decimal(value)
    match = _POWER.search(_text(value))
    if not match:
        return None
    amount = _decimal(match.group("value"))
    if amount is None:
        return None
    return amount / Decimal("1000") if _text(match.group(0)).casefold().endswith("mw") else amount


def _tolerance_percent(value: Any) -> str | None:
    text = _text(value)
    match = _TOLERANCE.search(text)
    return f"{match.group('value')}%" if match else (text or None)


def _join_material_text(item: dict[str, Any]) -> str:
    attributes = item.get("attributes") or {}
    attribute_text = " ".join(
        f"{key}={value}"
        for key, value in attributes.items()
        if value is not None
    )
    return " ".join(
        str(value or "")
        for value in (
            item.get("name"),
            item.get("specification"),
            item.get("package"),
            item.get("mpn"),
            attribute_text,
        )
    )


def material_spec(item: dict[str, Any]) -> dict[str, Any]:
    """Extract only explicit passive-component attributes from a material row."""

    attributes = item.get("attributes") or {}
    combined = _join_material_text(item)
    capacitance = None
    voltage = None
    dielectric = None
    tolerance = None
    resistance = None
    power_rating = None
    inductance = None
    saturation_current = None
    rms_current = None
    dcr = None
    for key, raw_value in attributes.items():
        folded = _text(key).casefold()
        if capacitance is None and any(
            marker in folded for marker in ("capacitance", "容值", "电容")
        ):
            capacitance = _capacitance_pf(raw_value)
        if voltage is None and any(
            marker in folded
            for marker in ("voltage", "rated_voltage", "耐压", "额定电压")
        ):
            voltage = _voltage_v(raw_value)
        if dielectric is None and any(
            marker in folded for marker in ("dielectric", "material", "材质", "介质")
        ):
            dielectric = _dielectric(raw_value)
        if tolerance is None and "tolerance" in folded:
            tolerance = _tolerance_percent(raw_value)
        if resistance is None and any(
            marker in folded for marker in ("resistance", "电阻", "阻值")
        ):
            resistance = _resistance_ohms(raw_value)
        if power_rating is None and any(
            marker in folded for marker in ("power", "功率", "额定功率")
        ):
            power_rating = _power_w(raw_value)
        if inductance is None and any(
            marker in folded for marker in ("inductance", "电感量", "感值")
        ):
            inductance = _inductance_h(raw_value)
        if saturation_current is None and any(
            marker in folded for marker in ("isat", "saturation", "饱和电流")
        ):
            saturation_current = _current_a(raw_value)
        if rms_current is None and any(
            marker in folded for marker in ("irms", "rms current", "额定电流")
        ):
            rms_current = _current_a(raw_value)
        if dcr is None and any(
            marker in folded for marker in ("dcr", "dc resistance", "直流电阻")
        ):
            dcr = _resistance_ohms(raw_value)
    if capacitance is None:
        capacitance = _capacitance_pf(combined)
    if voltage is None:
        voltage = _voltage_v(combined)
    if dielectric is None:
        dielectric = _dielectric(combined)
    if tolerance is None:
        match = _TOLERANCE.search(combined)
        tolerance = match.group("value") + "%" if match else None
    if resistance is None:
        resistance = _resistance_ohms(combined)
    if power_rating is None:
        power_rating = _power_w(combined)
    if inductance is None:
        inductance = _inductance_h(combined)
    # A current in an inductor description is deliberately not treated as Isat
    # unless the source key/label makes the meaning explicit.
    if saturation_current is None:
        for match in _LABELED_CURRENT.finditer(combined):
            if "sat" in match.group("label").casefold() or "饱和" in match.group("label"):
                saturation_current = _current_a(match.group(0))
                break
    if rms_current is None:
        for match in _LABELED_CURRENT.finditer(combined):
            if "rms" in match.group("label").casefold() or "额定" in match.group("label"):
                rms_current = _current_a(match.group(0))
                break
    if dcr is None:
        match = _LABELED_DCR.search(combined)
        if match:
            dcr = _scaled_decimal(match.group("value"), match.group("prefix"), resistance=True)
    return {
        "capacitance_pf": capacitance,
        "rated_voltage_v": voltage,
        "dielectric": dielectric,
        "tolerance": tolerance,
        "resistance_ohms": resistance,
        "power_rating_w": power_rating,
        "inductance_h": inductance,
        "saturation_current_a": saturation_current,
        "rms_current_a": rms_current,
        "dcr_ohms": dcr,
        "package": _package(item.get("package")),
    }


def requirement_spec(exact_value: Any) -> dict[str, Any]:
    text = _text(exact_value)
    capacitance = _capacitance_pf(text)
    voltage = _voltage_v(text)
    dielectric = _dielectric(text)
    tolerance_match = _TOLERANCE.search(text)
    tolerance = tolerance_match.group("value") + "%" if tolerance_match else None
    resistance = _resistance_ohms(text)
    power_rating = _power_w(text)
    inductance = _inductance_h(text)
    saturation_current = None
    rms_current = None
    dcr = None
    for match in _LABELED_CURRENT.finditer(text):
        label = match.group("label").casefold()
        if "sat" in label or "饱和" in match.group("label"):
            saturation_current = _current_a(match.group(0))
        if "rms" in label or "额定" in match.group("label"):
            rms_current = _current_a(match.group(0))
    dcr_match = _LABELED_DCR.search(text)
    if dcr_match:
        dcr = _scaled_decimal(dcr_match.group("value"), dcr_match.group("prefix"), resistance=True)
    unit = None
    value = None
    if capacitance is not None:
        match = _CAPACITANCE.search(text)
        if match:
            unit = match.group("unit").replace("μ", "u").replace("µ", "u")
            value = f"{match.group('value')} {unit}"
    elif resistance is not None:
        match = _RESISTANCE.search(text)
        if match:
            unit = "ohm"
            value = f"{match.group('value')}{match.group('prefix') or ''} Ω"
    elif inductance is not None:
        match = _INDUCTANCE.search(text)
        if match:
            unit = match.group("unit").replace("μ", "u").replace("µ", "u")
            value = f"{match.group('value')} {unit}"
    return {
        "value": value,
        "unit": unit,
        "capacitance_pf": capacitance,
        "rated_voltage_v": voltage,
        "dielectric": dielectric,
        "tolerance": tolerance,
        "resistance_ohms": resistance,
        "power_rating_w": power_rating,
        "inductance_h": inductance,
        "saturation_current_a": saturation_current,
        "rms_current_a": rms_current,
        "dcr_ohms": dcr,
    }


def role_slug(role: str) -> str:
    folded = _text(role).casefold()
    replacements = {
        "bootstrap capacitor": "bootstrap-capacitor",
        "input capacitor": "input-capacitor",
        "output capacitor": "output-capacitor",
        "feedback divider": "feedback-divider",
        "inductor": "inductor",
        "cot feedback ripple": "cot-feedback-ripple",
    }
    return replacements.get(folded, re.sub(r"[^a-z0-9]+", "-", folded).strip("-") or "peripheral")


def _source_metadata(
    grounded_roles: list[dict[str, Any]],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    source = next((item for item in grounded_roles if isinstance(item, dict)), {})
    anchor = source.get("source_anchor") or source.get("citation") or {}
    if not isinstance(anchor, dict):
        anchor = {}
    citation = dict(anchor)
    source_kind = str(source.get("source_kind") or "datasheet")
    if source.get("value_status") == "deterministic_calculation":
        source_kind = "deterministic_calculation"
    elif source.get("source_type") == "reference_design":
        source_kind = "reference_design"
    return source_kind, anchor, citation


def build_constraints(
    role: str,
    exact_value: Any,
    grounded_roles: list[dict[str, Any]] | None = None,
    *,
    source_kind: str | None = None,
) -> list[dict[str, Any]]:
    """Build typed constraints from grounded engineering facts only.

    ``exact_value`` remains the display/backward-compatible representation.  A
    role without a grounded value intentionally produces no guessed passive
    constraint.
    """

    spec = requirement_spec(exact_value)
    grounded = list(grounded_roles or [])
    inferred_kind, anchor, citation = _source_metadata(grounded)
    kind = source_kind or inferred_kind
    role_folded = _text(role).casefold()
    constraints: list[dict[str, Any]] = []

    def add(key: str, operator: str, value: Any, unit: str | None, *, hard: bool = True):
        if value is None:
            return
        constraints.append(
            {
                "key": key,
                "operator": operator,
                "value": value,
                "unit": unit,
                "hard": hard,
                "source_kind": kind,
                "source_anchor": dict(anchor),
                "citation": dict(citation),
            }
        )

    if spec.get("capacitance_pf") is not None:
        add("capacitance", "eq", str(spec["capacitance_pf"]), "pF")
    if spec.get("rated_voltage_v") is not None:
        add("rated_voltage", "gte", str(spec["rated_voltage_v"]), "V")
    if spec.get("dielectric"):
        add("dielectric", "eq", spec["dielectric"], None)
    if spec.get("tolerance"):
        tolerance = _decimal(str(spec["tolerance"]).replace("%", ""))
        add("tolerance", "lte", str(tolerance), "%")
    if spec.get("resistance_ohms") is not None and (
        "resistor" in role_folded
        or "divider" in role_folded
        or "电阻" in role_folded
        or "阻值" in role_folded
    ):
        add("resistance", "eq", str(spec["resistance_ohms"]), "ohm")
    if spec.get("power_rating_w") is not None:
        add("power_rating", "gte", str(spec["power_rating_w"]), "W")
    if spec.get("inductance_h") is not None:
        add("inductance", "eq", str(spec["inductance_h"]), "H")
    if spec.get("saturation_current_a") is not None:
        add("saturation_current", "gte", str(spec["saturation_current_a"]), "A")
    if spec.get("rms_current_a") is not None:
        add("rms_current", "gte", str(spec["rms_current_a"]), "A")
    if spec.get("dcr_ohms") is not None:
        add("dcr", "lte", str(spec["dcr_ohms"]), "ohm")

    grounded_package = next(
        (
            item.get("package")
            for item in grounded
            if isinstance(item, dict) and item.get("package")
        ),
        None,
    )
    if grounded_package:
        add("package", "eq", str(grounded_package), None)
    # A COT/ripple role is an evidence fact, not a passive material match.
    if "cot" in role_folded or "ripple" in role_folded:
        return []
    return constraints


def _legacy_constraints(requirement: dict[str, Any]) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []

    def add(key: str, operator: str, value: Any, unit: str | None):
        if value is not None:
            constraints.append(
                {
                    "key": key,
                    "operator": operator,
                    "value": value,
                    "unit": unit,
                    "hard": True,
                    "source_kind": "datasheet",
                    "source_anchor": {},
                    "citation": {},
                }
            )

    add("capacitance", "eq", requirement.get("capacitance_pf"), "pF")
    add("rated_voltage", "gte", requirement.get("rated_voltage_v"), "V")
    add("dielectric", "eq", requirement.get("dielectric"), None)
    tolerance = requirement.get("tolerance")
    if tolerance is not None:
        add("tolerance", "lte", str(tolerance).replace("%", ""), "%")
    add("resistance", "eq", requirement.get("resistance_ohms"), "ohm")
    add("power_rating", "gte", requirement.get("power_rating_w"), "W")
    add("inductance", "eq", requirement.get("inductance_h"), "H")
    add("saturation_current", "gte", requirement.get("saturation_current_a"), "A")
    add("rms_current", "gte", requirement.get("rms_current_a"), "A")
    add("dcr", "lte", requirement.get("dcr_ohms"), "ohm")
    if requirement.get("package"):
        add("package", "eq", requirement.get("package"), None)
    return constraints


def _constraint_actual(key: str, actual: dict[str, Any]) -> Any:
    return {
        "capacitance": actual.get("capacitance_pf"),
        "rated_voltage": actual.get("rated_voltage_v"),
        "dielectric": actual.get("dielectric"),
        "tolerance": actual.get("tolerance"),
        "resistance": actual.get("resistance_ohms"),
        "power_rating": actual.get("power_rating_w"),
        "inductance": actual.get("inductance_h"),
        "saturation_current": actual.get("saturation_current_a"),
        "rms_current": actual.get("rms_current_a"),
        "dcr": actual.get("dcr_ohms"),
        "package": actual.get("package"),
    }.get(key)


def _constraint_value(value: Any, key: str) -> Any:
    if key in {
        "capacitance",
        "rated_voltage",
        "resistance",
        "power_rating",
        "inductance",
        "saturation_current",
        "rms_current",
        "dcr",
    }:
        return _decimal(value)
    if key == "tolerance":
        return _decimal(str(value).replace("%", ""))
    if key == "dielectric":
        return _text(value).upper() or None
    return _text(value).casefold() or None


def _actual_constraint_value(value: Any, key: str) -> Any:
    if key == "tolerance":
        return _decimal(str(value).replace("%", "")) if value is not None else None
    if key == "dielectric":
        return _text(value).upper() or None
    if key == "package":
        return _text(value).casefold() or None
    return value


def material_match(
    requirement: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    class_info = resolve_component_class(item)
    expected_classes = {
        str(value)
        for value in (requirement.get("expected_component_classes") or [])
        if value
    }
    if "expected_component_classes" not in requirement:
        expected_classes = expected_component_classes(requirement.get("role"))
    strict_class_gate = requirement.get("component_class_gate")
    if strict_class_gate is None:
        strict_class_gate = "expected_component_classes" in requirement
    class_match = "unknown"
    if class_info["component_class"] != "unknown":
        class_match = (
            "compatible"
            if not expected_classes or class_info["component_class"] in expected_classes
            else "mismatch"
        )
    actual = material_spec(item)
    constraints = list(requirement.get("constraints") or [])
    if not constraints:
        constraints = _legacy_constraints(requirement)
    mismatches: list[str] = []
    unknowns: list[str] = []
    reasons: list[str] = []
    compatible_only = False
    if class_match == "mismatch":
        return {
            "status": "mismatch",
            "match_status": "mismatch",
            "mismatches": [],
            "unknowns": [],
            "match_reasons": [],
            "normalized_spec": {
                key: (
                    str(value)
                    if value is not None and key not in {"dielectric", "tolerance", "package"}
                    else value
                )
                for key, value in actual.items()
            },
            "component_class": class_info["component_class"],
            "class_source": class_info["class_source"],
            "class_match": "mismatch",
            "rejection_reason": "component_class_mismatch",
        }
    if class_match == "unknown" and expected_classes and strict_class_gate:
        unknowns.append("物料 component class 未知，不能确认其属于该工程角色")
    for constraint in constraints:
        if not isinstance(constraint, dict):
            continue
        key = str(constraint.get("key") or "")
        operator = str(constraint.get("operator") or "eq")
        expected = _constraint_value(constraint.get("value"), key)
        actual_value = _actual_constraint_value(_constraint_actual(key, actual), key)
        if operator == "preferred" and not bool(constraint.get("hard", False)):
            if actual_value is not None:
                reasons.append(f"偏好 {key}={constraint.get('value')} 已满足")
            continue
        if actual_value is None:
            unknowns.append(f"物料 {key} 未填，无法确认")
            continue
        satisfied = False
        comparison_text = f"{key}={actual_value}"
        if operator == "eq":
            satisfied = actual_value == expected
            if satisfied:
                reasons.append(f"{key} {actual_value} = {expected}")
        elif operator == "gte":
            satisfied = actual_value >= expected
            if satisfied:
                compatible_only = actual_value != expected
                reasons.append(f"{key} {actual_value} ≥ {expected}")
        elif operator == "lte":
            satisfied = actual_value <= expected
            if satisfied:
                compatible_only = actual_value != expected
                reasons.append(f"{key} {actual_value} ≤ {expected}")
        elif operator == "range":
            bounds = constraint.get("value")
            if isinstance(bounds, dict):
                minimum = _constraint_value(bounds.get("min"), key)
                maximum = _constraint_value(bounds.get("max"), key)
            elif isinstance(bounds, (list, tuple)) and len(bounds) >= 2:
                minimum = _constraint_value(bounds[0], key)
                maximum = _constraint_value(bounds[1], key)
            else:
                minimum = maximum = None
            satisfied = (
                minimum is not None
                and maximum is not None
                and minimum <= actual_value <= maximum
            )
            if satisfied:
                reasons.append(f"{key} {actual_value} 在 {minimum}–{maximum} 范围内")
        elif operator == "one_of":
            options = constraint.get("value")
            options = options if isinstance(options, (list, tuple, set)) else [options]
            normalized_options = {_constraint_value(option, key) for option in options}
            satisfied = actual_value in normalized_options
            if satisfied:
                reasons.append(f"{key} {actual_value} 在允许集合内")
                compatible_only = len(normalized_options) > 1
        else:
            unknowns.append(f"约束操作符 {operator} 未实现，无法确认 {key}")
            continue
        if not satisfied and bool(constraint.get("hard", True)):
            display_expected = constraint.get("value")
            if key == "rated_voltage" and operator == "gte":
                mismatches.append(
                    f"耐压不足（要求至少 {display_expected} V，物料 {actual_value} V）"
                )
            elif key == "dielectric":
                mismatches.append(f"介质不匹配（要求 {display_expected}，物料 {actual_value}）")
            elif key == "package":
                mismatches.append(f"封装不匹配（要求 {display_expected}，物料 {actual_value}）")
            else:
                mismatches.append(
                    f"{key} 不满足 {operator} 约束（要求 {display_expected}，"
                    f"物料 {comparison_text}）"
                )
    # ``status`` is kept for the Phase 3.3.2 capacitor helper contract.  The
    # new structured result is ``match_status``; a higher-than-minimum voltage
    # is therefore compatible there while legacy callers still see exact.
    legacy_status = "exact" if not mismatches and not unknowns else (
        "partial" if not mismatches else "mismatch"
    )
    match_status = (
        "mismatch"
        if mismatches
        else "partial"
        if unknowns
        else "compatible"
        if compatible_only
        else "exact"
    )
    if (
        class_match == "unknown"
        and expected_classes
        and strict_class_gate
        and match_status in {"exact", "compatible"}
    ):
        match_status = "partial"
    if class_match == "unknown" and expected_classes and strict_class_gate and not mismatches:
        legacy_status = "partial"
    normalized_spec = {
        "capacitance_pf": (
            str(actual.get("capacitance_pf"))
            if actual.get("capacitance_pf") is not None
            else None
        ),
        "rated_voltage_v": (
            str(actual.get("rated_voltage_v"))
            if actual.get("rated_voltage_v") is not None
            else None
        ),
        "dielectric": actual.get("dielectric"),
        "tolerance": actual.get("tolerance"),
        "resistance_ohms": (
            str(actual.get("resistance_ohms"))
            if actual.get("resistance_ohms") is not None
            else None
        ),
        "power_rating_w": (
            str(actual.get("power_rating_w"))
            if actual.get("power_rating_w") is not None
            else None
        ),
        "inductance_h": (
            str(actual.get("inductance_h"))
            if actual.get("inductance_h") is not None
            else None
        ),
        "saturation_current_a": (
            str(actual.get("saturation_current_a"))
            if actual.get("saturation_current_a") is not None
            else None
        ),
        "rms_current_a": (
            str(actual.get("rms_current_a"))
            if actual.get("rms_current_a") is not None
            else None
        ),
        "dcr_ohms": (
            str(actual.get("dcr_ohms"))
            if actual.get("dcr_ohms") is not None
            else None
        ),
        "package": actual.get("package"),
    }
    return {
        "status": legacy_status,
        "match_status": match_status,
        "mismatches": mismatches,
        "unknowns": unknowns,
        "match_reasons": reasons,
        "normalized_spec": normalized_spec,
        "component_class": class_info["component_class"],
        "class_source": class_info["class_source"],
        "class_match": class_match,
        "rejection_reason": None,
    }


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    """Stable candidate ordering; inventory never changes selection state."""

    status_rank = {"exact": 0, "compatible": 1, "partial": 2, "mismatch": 3}
    status = str(candidate.get("match_status") or candidate.get("status") or "partial")
    inventory = candidate.get("inventory") or {}
    available = _decimal(inventory.get("available_quantity"))
    locations = candidate.get("locations") or {}
    if isinstance(locations, dict):
        locatable = bool(locations.get("locations"))
    else:
        locatable = bool(locations)
    try:
        quantity_rank = -(available or Decimal("0"))
    except Exception:
        quantity_rank = Decimal("0")
    return (
        0
        if candidate.get("selection_basis") == "explicit_user"
        or candidate.get("selected_material_id") is not None
        else 1,
        status_rank.get(status, 9),
        0 if not (candidate.get("match_unknowns") or []) else 1,
        0 if available is not None and available > 0 else 1,
        0 if locatable else 1,
        quantity_rank,
        str(candidate.get("code") or "").casefold(),
        str(candidate.get("mpn") or "").casefold(),
    )


def completeness_summary(requirements: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up requirement state without conflating candidates and selections."""

    required = [
        item
        for item in requirements
        if item.get("required", True) and item.get("status") != "not_applicable"
    ]
    grounded = [
        item
        for item in required
        if item.get("evidence_status") == "grounded" or bool(item.get("constraints"))
    ]
    covered = []
    selected = []
    needs_input = []
    out_of_stock = []
    blockers: list[str] = []
    for item in required:
        candidates = [
            candidate
            for candidate in item.get("candidates") or []
            if isinstance(candidate, dict)
        ]
        expected_classes = {
            str(value)
            for value in (item.get("expected_component_classes") or [])
            if value
        }
        valid = [
            candidate
            for candidate in candidates
            if str(candidate.get("match_status") or candidate.get("status") or "")
            in {"exact", "compatible"}
            and not (
                expected_classes
                and candidate.get("class_match") == "unknown"
            )
        ]
        if valid:
            covered.append(item)
        selected_candidate = next(
            (
                candidate
                for candidate in candidates
                if int(candidate.get("material_id") or 0)
                == int(item.get("selected_material_id") or 0)
            ),
            None,
        )
        selected_candidate_valid = bool(
            selected_candidate is not None and selected_candidate in valid
        )
        if (
            item.get("selected_material_id") is not None
            and item.get("selection_basis") == "explicit_user"
            and not item.get("selection_conflict")
            and selected_candidate_valid
        ):
            selected.append(item)
        state = str(item.get("selection_status") or item.get("status") or "")
        selected_candidate = next(
            (
                candidate
                for candidate in candidates
                if int(candidate.get("material_id") or 0)
                == int(item.get("selected_material_id") or 0)
            ),
            None,
        )
        selected_inventory = (selected_candidate or {}).get("inventory") or {}
        selected_available = _decimal(selected_inventory.get("available_quantity"))
        required_quantity_value = _decimal(item.get("required_quantity"))
        selected_is_out_of_stock = (
            selected_candidate is not None
            and (
                selected_candidate.get("inventory_status") == "out_of_stock"
                or selected_available == 0
            )
        )
        if state in {"needs_input", "needs_design_selection"}:
            needs_input.append(item)
            blockers.append(f"{item.get('role') or item.get('requirement_id')} 缺少必要输入")
        if (
            state == "out_of_stock"
            or item.get("status") == "out_of_stock"
            or item.get("inventory_status") == "out_of_stock"
            or selected_is_out_of_stock
        ):
            out_of_stock.append(item)
            blockers.append(
                f"{item.get('role') or item.get('requirement_id')} 的匹配物料可用量为 0"
            )
        if state == "shortage" or (
            selected_available is not None
            and required_quantity_value is not None
            and selected_available < required_quantity_value
        ):
            blockers.append(
                f"{item.get('role') or item.get('requirement_id')} 的匹配物料库存不足"
            )
        if state in {"no_grounded_candidate", "no_matching_material"}:
            blockers.append(
                (
                    f"{item.get('role') or item.get('requirement_id')} 没有受控工程证据"
                    if state == "no_grounded_candidate"
                    else f"{item.get('role') or item.get('requirement_id')} 没有满足约束的物料候选"
                )
            )
        if state in {"needs_selection", "pending_inventory"} and not valid:
            blockers.append(
                f"{item.get('role') or item.get('requirement_id')} 尚未形成满足约束的物料候选"
            )
        elif state == "ambiguous_candidates" and item.get("selected_material_id") is None:
            blockers.append(
                f"{item.get('role') or item.get('requirement_id')} 存在多个候选，尚未显式选择"
            )
        elif state in {
            "candidate_found",
            "selected",
            "stocked_matched",
            "stocked_location_unassigned",
        } and item.get("selected_material_id") is None:
            blockers.append(
                f"{item.get('role') or item.get('requirement_id')} 有候选但尚未显式选择"
            )
        if item.get("selection_conflict"):
            blockers.append(str(item["selection_conflict"]))
        if (
            item.get("selected_material_id") is not None
            and item.get("selection_basis") == "explicit_user"
            and not selected_candidate_valid
            and not item.get("selection_conflict")
        ):
            blockers.append(
                f"{item.get('role') or item.get('requirement_id')} 的显式选择未通过"
                "当前类别/规格约束"
            )
    required_count = len(required)
    selected_count = len(selected)
    evidence_gap_count = sum(
        1
        for item in required
        if item.get("evidence_status") not in {"grounded", "supported"}
    )
    complete_for_review = (
        bool(required_count)
        and selected_count == required_count
        and not needs_input
        and not out_of_stock
        and not blockers
    )
    return {
        "requirements_defined": required_count,
        "requirements_grounded": len(grounded),
        "candidate_covered": len(covered),
        "explicitly_selected": selected_count,
        "unresolved": max(0, required_count - selected_count),
        "needs_input": len(needs_input),
        "evidence_gap": evidence_gap_count,
        "complete_for_engineering_review": complete_for_review,
        "complete_for_bom_preview_ready_path": complete_for_review,
        "required_roles": required_count,
        "grounded_roles": len(grounded),
        "candidate_covered_roles": len(covered),
        "selected_roles": selected_count,
        "unresolved_roles": max(0, required_count - selected_count),
        "needs_input_roles": len(needs_input),
        "out_of_stock_roles": len(out_of_stock),
        "complete_for_review": complete_for_review,
        "blocking_reasons": list(dict.fromkeys(blockers)),
    }


def required_quantity(message: str, role: str, default: Decimal | None) -> Decimal | None:
    """Keep quantity user-scoped; unknown design roles do not get a fake quantity."""

    if default is None:
        return None
    folded = _text(message)
    if any(
        marker in folded
        for marker in ("两颗", "两只", "两枚", "两件", "2颗", "2只", "2枚", "2个")
    ):
        return Decimal("2")
    return default


def quantity_text(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def resolve_inventory_status(
    requirement: dict[str, Any],
    candidates: list[dict[str, Any]],
    inventory_by_id: dict[int, dict[str, Any]],
    locations_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve a role without treating unknown specs or wrong variants as stock."""

    exact = [
        item
        for item in candidates
        if (item.get("match_status") or item.get("status")) in {"exact", "compatible"}
    ]
    partial = [
        item
        for item in candidates
        if (item.get("match_status") or item.get("status")) == "partial"
    ]
    selected_id = requirement.get("selected_material_id")
    selected_candidate = next(
        (
            item
            for item in candidates
            if selected_id is not None
            and int(item.get("material_id") or 0) == int(selected_id)
            and (item.get("match_status") or item.get("status")) in {"exact", "compatible"}
        ),
        None,
    )
    # An explicit draft choice is allowed to remain selected even when other
    # valid or partial candidates are also present.  Stock is reported on its
    # own axis below and never turns into an automatic replacement.
    if selected_candidate is not None:
        exact = [selected_candidate]
        partial = []
    quantity = _decimal(requirement.get("required_quantity"))
    base = {
        "required_quantity": quantity_text(quantity),
        "shortage_quantity": None,
        "available_quantity": None,
        "location": None,
        "location_status": "not_checked",
        "matched_material_id": None,
        "selected_material_id": requirement.get("selected_material_id"),
        "selection_basis": requirement.get("selection_basis"),
        "selection_provenance": dict(requirement.get("selection_provenance") or {}),
        "selection_conflict": requirement.get("selection_conflict"),
        "inventory_status": "not_checked",
        "selection_status": "no_matching_material",
    }
    if not exact:
        base["selection_status"] = (
            "ambiguous_candidates" if partial else "no_matching_material"
        )
        base["unknowns"] = list(
            dict.fromkeys(
                unknown
                for item in partial
                for unknown in item.get("match_unknowns") or []
            )
        )
        return base
    if partial:
        base["selection_status"] = "ambiguous_candidates"
        base["unknowns"] = [
            "存在一个或多个属性未填的近似候选，未自动选定精确物料或替代料。"
        ]
        return base
    if len(exact) > 1:
        base["selection_status"] = "ambiguous_candidates"
        base["unknowns"] = [
            "存在多个满足已知容值、耐压和介质约束的候选，未自动选定替代料。"
        ]
        return base

    selected = exact[0]
    material_id = int(selected["material_id"])
    inventory = inventory_by_id.get(material_id) or {}
    locations = locations_by_id.get(material_id) or {}
    available = _decimal(inventory.get("available_quantity"))
    if available is not None:
        base["inventory_status"] = "out_of_stock" if available == 0 else "in_stock"
    base.update(
        {
            "matched_material_id": material_id,
            "available_quantity": quantity_text(available),
            "location": locations.get("locations") or [],
            "location_status": locations.get("distribution_status") or "unknown",
        }
    )
    if available is None or quantity is None:
        base["selection_status"] = "selected" if selected_id is not None else "stocked_matched"
        base["unknowns"] = ["库存工具未返回可用量或需求数量尚未确定。"]
        return base
    shortage = max(Decimal("0"), quantity - available)
    base["shortage_quantity"] = quantity_text(shortage)
    if selected_id is not None:
        base["selection_status"] = "selected"
    elif shortage > 0:
        base["selection_status"] = "shortage"
    elif available == 0:
        base["selection_status"] = "out_of_stock"
    elif not locations.get("locations"):
        base["selection_status"] = "stocked_location_unassigned"
        base["unknowns"] = ["有可用库存，但当前没有可确认的实际库位记录。"]
    else:
        base["selection_status"] = "stocked_matched"
    return base
