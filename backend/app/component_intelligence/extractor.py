from __future__ import annotations

import re
from decimal import Decimal

from .schemas import ComponentQuery, NumericRange

_VOLTAGE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*[Vv](?![A-Za-z0-9.])")
_CURRENT = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*(mA|A)(?![A-Za-z0-9.])", re.I)
_VOLTAGE_RANGE = re.compile(
    r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*[Vv]\s*(?:-|~|～|至|到|→|->|\bto\b)\s*"
    r"(\d+(?:\.\d+)?)\s*[Vv](?![A-Za-z0-9.])"
)
_VOLTAGE_CONVERSION = re.compile(
    r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*[Vv]\s*(?:→|->|\bto\b)\s*"
    r"(\d+(?:\.\d+)?)\s*[Vv](?![A-Za-z0-9.])",
    re.IGNORECASE,
)
_VOLTAGE_RAIL_WITH_CURRENT = re.compile(
    r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*[Vv]\s*/\s*"
    r"\d+(?:\.\d+)?\s*(?:mA|A)(?![A-Za-z0-9.])",
    re.IGNORECASE,
)
_CURRENT_RANGE = re.compile(
    r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*(mA|A)\s*(?:-|~|～|至|到)\s*"
    r"(\d+(?:\.\d+)?)\s*(mA|A)(?![A-Za-z0-9.])",
    re.I,
)
_CURRENT_MIN_PREFIX = re.compile(
    r"(?:至少|不低于|at\s+least|>=|≥)\s*(\d+(?:\.\d+)?)\s*(mA|A)(?![A-Za-z0-9.])",
    re.I,
)
_CURRENT_MIN_SUFFIX = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mA|A)(?![A-Za-z0-9.])\s*(?:以上|起|或更高)", re.I
)
_CURRENT_MAX_PREFIX = re.compile(
    r"(?:至多|不高于|at\s+most|<=|≤)\s*(\d+(?:\.\d+)?)\s*(mA|A)(?![A-Za-z0-9.])",
    re.I,
)
_CURRENT_MAX_SUFFIX = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mA|A)(?![A-Za-z0-9.])\s*(?:以下|以内|或更低)", re.I
)
_RESOLUTION = re.compile(r"(?<!\d)(\d{1,3})\s*(?:(?:-|\s)?bit\b|位)", re.I)
_ALNUM = re.compile(r"[A-Za-z][A-Za-z0-9._+-]*")
_CJK_PHRASE = re.compile(r"[\u3400-\u9fff]{2,}")
_INTERFACE_ALIASES = {
    "CAN-FD": ("can-fd", "can fd"),
    "CAN": ("can",),
    "I2C": ("i2c", "i²c"),
    "I2S": ("i2s", "i²s"),
    "SPI": ("spi",),
    "UART": ("uart",),
    "RS485": ("rs485", "rs-485"),
    "USB": ("usb",),
}
COMPONENT_TYPE_ALIASES = {
    "CAN transceiver": (
        "can transceiver",
        "can 收发",
        "can收发",
        "can 总线收发",
        "can 总线的收发",
    ),
    "RS485 transceiver": (
        "rs485 transceiver",
        "rs485 收发",
        "rs485收发",
        "rs485 总线收发",
        "rs-485 总线收发",
    ),
    "Ethernet PHY": ("ethernet phy", "以太网 phy"),
    "USB-UART bridge": ("usb-uart bridge", "usb uart bridge", "usb转串口"),
    "wireless MCU module": ("wireless mcu module", "无线 mcu 模块"),
    "compute module": ("compute module", "计算模块"),
    "audio DAC": ("audio dac", "音频 dac"),
    "audio amplifier": ("audio amplifier", "音频功放"),
    "DC/DC module": ("dc/dc", "dcdc", "电源模块", "降压模块", "电源转换模块"),
    "buck converter": (
        "buck converter",
        "buck",
        "dcdc buck",
        "dc/dc buck",
        "降压转换器",
        "降压方案",
        "降压芯片",
    ),
    "BMS module": ("bms module", "bms 模块", "电池管理模块"),
    "stepper motor driver": ("stepper motor driver", "步进电机驱动"),
    "motor driver board": ("motor driver board", "电机驱动板"),
    "motor driver": ("motor driver", "电机驱动"),
    "gate driver": ("gate driver", "栅极驱动", "三相驱动"),
    "temperature/humidity sensor": ("temperature/humidity sensor", "温湿度传感器"),
    "current sense amplifier": ("current sense amplifier", "电流检测放大器"),
    "current monitor": ("current monitor", "power monitor", "电流监测", "电流监控"),
    "ToF sensor": (
        "tof sensor",
        "tof distance sensor",
        "tof 测距",
        "tof传感器",
        "tof 传感器",
    ),
    "RF antenna": ("rf antenna", "射频天线", "天线"),
    "MCU": ("mcu", "微控制器", "单片机"),
    "ADC": ("adc", "模数转换"),
    "DAC": ("dac", "数模转换"),
    "LDO": ("ldo", "低压差稳压"),
    "IMU": ("imu", "惯性测量"),
    "load cell": ("load cell", "称重传感器", "力传感器"),
    "display": ("display", "显示屏"),
    "connector": ("connector", "连接器"),
    "crystal": ("crystal", "晶振"),
    "resistor": ("resistor", "电阻"),
    "capacitor": ("capacitor", "电容"),
    "MOSFET": ("mosfet", "mos 管", "mos管"),
    "sensor": ("sensor", "传感器"),
}
_MANUFACTURERS = {
    "Texas Instruments": ("texas instruments", "ti", "德州仪器"),
    "STMicroelectronics": ("stmicroelectronics", "st", "意法半导体"),
    "Microchip": ("microchip",),
    "Yageo": ("yageo", "国巨"),
    "Murata": ("murata", "村田"),
    "Infineon": ("infineon", "英飞凌"),
    "Espressif": ("espressif", "乐鑫"),
    "JST": ("jst",),
    "AMASS": ("amass",),
}
_PACKAGE = re.compile(
    r"\b(?:SOIC-\d+|SOP-\d+|SOT-23-\d+|LQFP-\d+|TSSOP-\d+|QFN-\d+|"
    r"VQFN|VSSOP-\d+|LGA(?:-\d+)?|DFN-\d+(?:\([^)]*\))?|0402|0603|0805|MODULE)\b",
    re.I,
)
_FILLERS = (
    "有没有",
    "帮我找",
    "请帮我",
    "找一个",
    "之前那个",
    "一个",
    "左右",
    "大约",
    "大概",
    "放哪儿了",
    "放在哪里",
    "在哪里",
    "替代这个的",
    "能替代",
    "最好",
)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _amps(value: str, unit: str) -> Decimal:
    amount = Decimal(value)
    return amount / Decimal("1000") if unit.casefold() == "ma" else amount


def _has_alias(text: str, alias: str) -> bool:
    compact = alias.replace("-", "").replace("/", "").replace(" ", "")
    if alias.isascii() and compact.isalnum():
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
                text,
                re.I,
            )
        )
    return alias.casefold() in text.casefold()


def canonical_component_type(value: object) -> str | None:
    text = str(value or "")
    for canonical, aliases in COMPONENT_TYPE_ALIASES.items():
        if _has_alias(text, canonical) or any(_has_alias(text, alias) for alias in aliases):
            return canonical
    return None


def _component_types(raw: str) -> list[str]:
    types = [
        canonical
        for canonical, aliases in COMPONENT_TYPE_ALIASES.items()
        if _has_alias(raw, canonical) or any(_has_alias(raw, alias) for alias in aliases)
    ]
    if any(item.endswith("sensor") and item != "sensor" for item in types):
        types = [item for item in types if item != "sensor"]
    if "motor driver board" in types:
        types = [item for item in types if item != "motor driver"]
    if "audio DAC" in types:
        types = [item for item in types if item != "DAC"]
    if "wireless MCU module" in types:
        types = [item for item in types if item != "MCU"]
    if "ADC" in types and "采集" in raw:
        types = [item for item in types if item not in {"load cell", "sensor"}]
    return _unique(types)


class RequirementExtractor:
    """Deterministic, role-aware requirement extraction with no provider calls."""

    def extract(self, text: str) -> ComponentQuery:
        raw = text.strip()
        voltage_range_match = _VOLTAGE_RANGE.search(raw)
        voltage_conversion_match = _VOLTAGE_CONVERSION.search(raw)
        voltage_range = (
            NumericRange(
                minimum=Decimal(voltage_range_match.group(1)),
                maximum=Decimal(voltage_range_match.group(2)),
            )
            if voltage_range_match
            else None
        )
        current_range_match = _CURRENT_RANGE.search(raw)
        current_range = (
            NumericRange(
                minimum=_amps(current_range_match.group(1), current_range_match.group(2)),
                maximum=_amps(current_range_match.group(3), current_range_match.group(4)),
            )
            if current_range_match
            else None
        )
        cleaned_for_values = _VOLTAGE_RANGE.sub(" ", raw)
        cleaned_for_values = _CURRENT_RANGE.sub(" ", cleaned_for_values)
        voltages = [Decimal(value) for value in _VOLTAGE.findall(cleaned_for_values)]
        current_match = _CURRENT.search(cleaned_for_values)
        current = (
            _amps(current_match.group(1), current_match.group(2))
            if current_match and not current_range
            else None
        )
        minimum_match = _CURRENT_MIN_PREFIX.search(raw) or _CURRENT_MIN_SUFFIX.search(raw)
        maximum_match = _CURRENT_MAX_PREFIX.search(raw) or _CURRENT_MAX_SUFFIX.search(raw)
        current_min = (
            _amps(minimum_match.group(1), minimum_match.group(2))
            if minimum_match
            else (current if current is not None and not maximum_match else None)
        )
        current_max = (
            _amps(maximum_match.group(1), maximum_match.group(2)) if maximum_match else None
        )
        resolution_match = _RESOLUTION.search(raw)
        resolution_bits = int(resolution_match.group(1)) if resolution_match else None
        interfaces = [
            canonical
            for canonical, aliases in _INTERFACE_ALIASES.items()
            if any(_has_alias(raw, alias) for alias in aliases)
        ]
        folded = raw.casefold()
        component_types = _component_types(raw)
        if any(item in interfaces for item in ("CAN-FD", "CAN")) and any(
            marker in folded for marker in ("收发器", "收发", "transceiver")
        ):
            component_types = _unique([*component_types, "CAN transceiver"])
        if "RS485" in interfaces and any(
            marker in folded for marker in ("收发器", "收发", "transceiver")
        ):
            component_types = _unique([*component_types, "RS485 transceiver"])

        is_conversion = (
            "转" in raw
            or ("进" in raw and "出" in raw)
            or ("变成" in raw and len(voltages) > 1)
            or bool(re.search(r"\bto\b", folded))
            or voltage_conversion_match is not None
        )
        input_voltage = (
            Decimal(voltage_conversion_match.group(1))
            if voltage_conversion_match
            else voltages[0]
            if is_conversion and voltages
            else None
        )
        output_voltage = (
            Decimal(voltage_conversion_match.group(2))
            if voltage_conversion_match
            else voltages[1]
            if is_conversion and len(voltages) > 1
            else None
        )
        if not is_conversion and voltages:
            has_input_role = "输入" in raw or "input" in folded
            has_output_role = "输出" in raw or "output" in folded
            if has_input_role:
                input_voltage = voltages[0]
            if has_output_role and (not has_input_role or len(voltages) > 1):
                output_voltage = voltages[-1]
        rail_voltage_match = _VOLTAGE_RAIL_WITH_CURRENT.search(raw)
        if output_voltage is None and input_voltage is not None and rail_voltage_match:
            rail_voltage = Decimal(rail_voltage_match.group(1))
            if rail_voltage != input_voltage:
                output_voltage = rail_voltage
        supply_voltage = None
        if voltages and (
            "供电" in raw
            or "supply" in folded
            or ("CAN transceiver" in component_types and not is_conversion)
        ):
            supply_voltage = voltages[0]
        bus_voltage = (
            voltages[0] if voltages and ("总线电压" in raw or "bus voltage" in folded) else None
        )
        logic_voltage = (
            voltages[0] if voltages and ("逻辑电压" in raw or "logic voltage" in folded) else None
        )

        manufacturers = [
            name
            for name, aliases in _MANUFACTURERS.items()
            if any(_has_alias(raw, alias) for alias in aliases)
        ]
        packages = [match.group(0).upper() for match in _PACKAGE.finditer(raw)]
        cleaned = raw
        for filler in _FILLERS:
            cleaned = cleaned.replace(filler, " ")
        for pattern in (_VOLTAGE_RANGE, _CURRENT_RANGE, _VOLTAGE, _CURRENT, _RESOLUTION):
            cleaned = pattern.sub(" ", cleaned)
        keywords = [
            token for token in _ALNUM.findall(cleaned) if token.casefold() not in {"v", "a"}
        ]
        keywords.extend(_CJK_PHRASE.findall(cleaned))
        keywords.extend(interfaces)

        # ComponentQuery bounds these collections. Long engineering questions
        # still keep their full raw_text, while the searchable facets remain
        # within the schema contract and preserve the earlier alphanumeric
        # identifiers and values first.
        keywords = _unique(keywords)[:30]
        component_types = component_types[:10]
        interfaces = _unique(interfaces)[:20]
        voltages = voltages[:10]
        packages = _unique(packages)[:10]
        manufacturers = _unique(manufacturers)[:10]

        hard_constraints = {
            key: value
            for key, value in {
                "component_types": component_types,
                "interfaces": interfaces,
                "supply_voltage_v": supply_voltage,
                "input_voltage_v": input_voltage,
                "output_voltage_v": output_voltage,
                "bus_voltage_v": bus_voltage,
                "logic_voltage_v": logic_voltage,
                "voltage_range_v": voltage_range.model_dump() if voltage_range else None,
                "current_min_a": current_min,
                "current_max_a": current_max,
                "current_range_a": current_range.model_dump() if current_range else None,
                "resolution_bits": resolution_bits,
            }.items()
            if value not in (None, [], {})
        }
        soft_preferences = {
            key: value
            for key, value in {
                "packages": packages,
                "manufacturers": manufacturers,
            }.items()
            if value
        }
        return ComponentQuery(
            raw_text=raw,
            keywords=keywords,
            component_types=component_types,
            interfaces=interfaces,
            voltage_values_v=voltages,
            voltage_range_v=voltage_range,
            supply_voltage_v=supply_voltage,
            input_voltage_v=input_voltage,
            output_voltage_v=output_voltage,
            bus_voltage_v=bus_voltage,
            logic_voltage_v=logic_voltage,
            current_a=current,
            current_min_a=current_min,
            current_max_a=current_max,
            current_range_a=current_range,
            resolution_bits=resolution_bits,
            package_preferences=packages,
            manufacturer_preferences=manufacturers,
            hard_constraints=hard_constraints,
            soft_preferences=soft_preferences,
            context_reference=(
                "它" in folded
                or folded.startswith(("这个", "那个", "之前那个"))
                or any(
                    marker in folded
                    for marker in (
                        "第一个",
                        "第二个",
                        "第三个",
                        "first",
                        "second",
                        "third",
                        "还有多少",
                        "在哪",
                    )
                )
            ),
            location_requested=any(
                marker in raw for marker in ("在哪里", "在哪", "放哪", "库位", "位置")
            ),
            replacement_intent=any(
                marker in folded for marker in ("替代", "replacement", "pin compatible", "兼容替换")
            ),
        )
