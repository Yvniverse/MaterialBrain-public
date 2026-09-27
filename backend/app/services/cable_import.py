import hashlib
import re
import unicodedata
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.exceptions import BusinessError

CABLE_ORDER_ALIASES = {
    "order_no": ("订单号", "主订单编号", "订单编号", "order_no", "order id"),
    "submitted_at": ("订单提交时间", "下单时间", "创建时间", "order time"),
    "status": ("订单状态", "交易状态", "status"),
    "shop": ("店铺名称", "商家名称", "卖家名称", "shop"),
    "product_name": ("商品名称", "商品标题", "产品名称", "宝贝名称", "product name"),
    "product_url": ("商品链接", "宝贝链接", "产品链接", "product url"),
    "variant": (
        "型号款式",
        "型号/款式",
        "商品规格",
        "销售属性",
        "规格型号",
        "规格",
        "variant",
        "sku",
    ),
    "quantity": ("商品数量", "购买数量", "数量", "qty", "quantity"),
    "unit_price": ("商品金额", "商品单价", "单价", "price", "unit price"),
}

CANCELLED_STATUS_WORDS = ("关闭", "取消", "退款成功", "交易失败")
CABLE_KIND_LABELS = {
    "terminal": "端子线",
    "flat_flex": "FPC/FFC 软排线",
    "micro_coax": "FPC 极细同轴线",
    "rf_coax": "IPEX 射频同轴线",
}
END_STYLE_LABELS = {
    "double": "双头",
    "single": "单头",
    "single_tinned": "单头沾锡",
    "male_female_pair": "公母一套",
    "unspecified": "端头未注明",
}


def _normalized_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return unicodedata.normalize("NFKC", str(value)).strip()


def _normalized_header(value: Any) -> str:
    return re.sub(r"\s+", "", _normalized_text(value)).casefold()


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", _normalized_text(value))


def _row_value(row: dict, aliases: tuple[str, ...]) -> Any:
    values = {_normalized_header(key): value for key, value in row.items()}
    for alias in aliases:
        key = _normalized_header(alias)
        if key in values and _normalized_text(values[key]):
            return values[key]
    return ""


def _decimal_text(value: Decimal | str | int) -> str:
    decimal = Decimal(str(value))
    text = format(decimal.normalize(), "f")
    return text if "." in text else f"{text}.0"


def _parse_positive_integer(value: Any) -> int | None:
    text = _normalized_text(value).replace(",", "")
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        number = Decimal(match.group())
    except InvalidOperation:
        return None
    if number <= 0 or number != number.to_integral_value():
        return None
    return int(number)


def _parse_money(value: Any) -> Decimal | None:
    text = _normalized_text(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        number = Decimal(match.group())
    except InvalidOperation:
        return None
    return number if number >= 0 else None


def _extract_cable_kind(product_name: str, variant: str) -> str:
    combined = f"{product_name} {variant}".upper()
    if "极细同轴" in combined or "FPC同轴" in combined:
        return "micro_coax"
    if "IPEX" in combined or "IPX" in combined or "RF1.13" in combined:
        return "rf_coax"
    if (
        "FPC/FFC" in combined
        or "软排线" in combined
        or "PIN转" in combined
        or ("摄像头专用排线" in combined and "PIN" in combined)
    ):
        return "flat_flex"
    return "terminal"


def _extract_end_style(cable_kind: str, variant: str) -> str:
    if "单头沾锡" in variant:
        return "single_tinned"
    if "公母一套" in variant:
        return "male_female_pair"
    if "单头" in variant or "公头带线" in variant:
        return "single"
    if "双头" in variant:
        return "double"
    if cable_kind in {"flat_flex", "micro_coax", "rf_coax"}:
        return "double"
    return "unspecified"


def _extract_direction(variant: str) -> str:
    normalized = _normalized_text(variant).upper()
    if "反向" in normalized or "反面" in normalized or "B型" in normalized:
        return "reverse"
    if (
        "同向" in normalized
        or "同面" in normalized
        or "正向" in normalized
        or "A型" in normalized
    ):
        return "same"
    return "unspecified"


def _extract_pitch(
    product_name: str,
    variant: str,
    cable_kind: str,
) -> tuple[Decimal | None, str | None]:
    if cable_kind == "rf_coax":
        return None, None

    known_pitch = r"(0\.3|0\.5|0\.8|1\.25|1\.0|2\.54|2\.0|4\.2)"
    explicit_variant = re.search(
        rf"(?<![\d.]){known_pitch}\s*(?:MM|毫米)",
        variant,
        re.IGNORECASE,
    )
    if explicit_variant:
        return Decimal(explicit_variant.group(1)), None

    combined = f"{variant} {product_name}"
    interval_match = re.search(
        rf"(?:间距\s*{known_pitch}|{known_pitch}\s*(?:MM\s*)?间距)",
        product_name,
        re.IGNORECASE,
    )
    if interval_match:
        value = next(group for group in interval_match.groups() if group)
        return Decimal(value), None

    explicit_product = re.search(
        rf"(?<![\d.]){known_pitch}\s*(?:MM|毫米)",
        product_name,
        re.IGNORECASE,
    )
    if explicit_product:
        return Decimal(explicit_product.group(1)), None

    family_match = re.search(
        r"(?:SH|PH|HC|MX|GH|XH)[-_ ]?(\d+(?:\.\d+)?)",
        combined,
        re.IGNORECASE,
    )
    if family_match:
        return Decimal(family_match.group(1)), None

    if re.search(r"\bA(?:1251|1254)", combined, re.IGNORECASE):
        return Decimal("1.25"), "接头间距依据 A1251/A1254 系列型号识别为 1.25 mm"
    if re.search(r"\bXH(?:-|连接)", combined, re.IGNORECASE):
        return Decimal("2.54"), "接头间距依据 XH 系列识别为 2.54 mm"
    if re.search(r"\bPHB", combined, re.IGNORECASE):
        return Decimal("2.0"), "接头间距依据 PHB 系列识别为 2.0 mm"
    if re.search(r"\bC4201", combined, re.IGNORECASE):
        return Decimal("4.2"), "接头间距依据 C4201 系列识别为 4.2 mm"
    if "SUR-32S" in combined.upper():
        return Decimal("0.8"), "接头间距依据 SUR-32S 系列识别为 0.8 mm"
    return None, None


def _extract_pin_counts(variant: str, cable_kind: str) -> tuple[int, int]:
    if cable_kind == "rf_coax":
        return 0, 0

    conversion = re.search(
        r"(?<!\d)(\d{1,3})\s*PIN\s*转\s*(\d{1,3})\s*PIN",
        variant,
        re.IGNORECASE,
    )
    if conversion:
        return int(conversion.group(1)), int(conversion.group(2))

    multiplied = re.search(
        r"(?<!\d)(\d{1,2})\s*[X×]\s*(\d{1,2})\s*P",
        variant,
        re.IGNORECASE,
    )
    if multiplied:
        return int(multiplied.group(1)) * int(multiplied.group(2)), 0

    pin_match = re.search(r"(?<!\d)(\d{1,3})\s*(?:PIN|P)(?!\d)", variant, re.IGNORECASE)
    if pin_match:
        return int(pin_match.group(1)), 0

    sur_match = re.search(r"(?<!\d)(\d{2})SUR-", variant, re.IGNORECASE)
    if sur_match:
        return int(sur_match.group(1)), 0
    return 0, 0


def _extract_pin_layout(variant: str) -> str:
    multiplied = re.search(
        r"(?<!\d)(\d{1,2})\s*[X×]\s*(\d{1,2})",
        variant,
        re.IGNORECASE,
    )
    if multiplied:
        return f"{int(multiplied.group(1))}×{int(multiplied.group(2))}"
    if "双排" in variant:
        return "双排"
    return ""


def _extract_length_cm(variant: str) -> Decimal | None:
    centimeter_matches = re.findall(
        r"(\d+(?:\.\d+)?)\s*(?:厘米|CM)",
        variant,
        re.IGNORECASE,
    )
    if centimeter_matches:
        return Decimal(centimeter_matches[-1])

    millimeter_matches = [
        Decimal(value)
        for value in re.findall(
            r"(\d+(?:\.\d+)?)\s*(?:MM|毫米)",
            variant,
            re.IGNORECASE,
        )
    ]
    if millimeter_matches:
        return max(millimeter_matches) / Decimal("10")

    meter_match = re.search(
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*M(?!M)",
        variant,
        re.IGNORECASE,
    )
    if meter_match:
        return Decimal(meter_match.group(1)) * Decimal("100")
    return None


def _extract_model(
    product_name: str,
    variant: str,
    cable_kind: str,
    pitch: Decimal | None,
    pin_count: int,
    pin_count_b: int,
) -> str:
    if cable_kind == "rf_coax":
        generation = re.search(r"(\d+)代", variant)
        suffix = generation.group(1) if generation else "1"
        return f"IPEX-{suffix}-RF1.13"
    if cable_kind == "micro_coax":
        pitch_text = _decimal_text(pitch) if pitch is not None else "未知间距"
        return f"FPC-COAX-{pitch_text}-{pin_count}P"
    if cable_kind == "flat_flex":
        pitch_text = _decimal_text(pitch) if pitch is not None else "未知间距"
        if pin_count_b:
            return f"FPC-{pitch_text}-{pin_count}P-{pin_count_b}P"
        return f"FPC-{pitch_text}-{pin_count}P"

    compact_variant = _compact_text(variant).upper().replace("×", "X")
    c4201 = re.search(r"(C4201\w*-?\d+X\d+P)", compact_variant, re.IGNORECASE)
    if c4201:
        return c4201.group(1).upper()
    phb = re.search(r"(PHB-\d+X\d+(?:双排)?\d*P?)", compact_variant, re.IGNORECASE)
    if phb:
        return re.sub(r"双排.*$", "", phb.group(1), flags=re.IGNORECASE).upper()
    shld = re.search(r"SHLD", product_name, re.IGNORECASE)
    if shld and pin_count:
        return f"SHLD-{pin_count}P"

    first_part = re.split(r"[;；]", variant, maxsplit=1)[0].strip()
    generic_first = bool(
        re.fullmatch(r"\d{1,3}\s*P", first_part, re.IGNORECASE)
        or re.match(r"长度\s*\d", first_part, re.IGNORECASE)
        or re.match(r"\d+(?:\.\d+)?\s*M$", first_part, re.IGNORECASE)
        or first_part.startswith("带锁双排")
    )
    if first_part and not generic_first:
        return _compact_text(first_part).upper().replace("×", "X")

    if "公母一套" in variant and pin_count and pitch is not None:
        return f"{_decimal_text(pitch)}MM-{pin_count}P-公母对接"

    variant_family = re.search(
        r"\b(SH|GH|XH|PH|HC|MX)[-_ ]?(\d+(?:\.\d+)?)",
        variant,
        re.IGNORECASE,
    )
    if variant_family and pin_count:
        return (
            f"{variant_family.group(1).upper()}"
            f"{variant_family.group(2)}-{pin_count}P"
        )

    hc_family = re.search(r"\bHC[-_]?(\d+(?:\.\d+)?)", product_name, re.IGNORECASE)
    if hc_family and pin_count:
        upper_product = product_name.upper()
        if "PLT" in upper_product:
            suffix = "PLT"
        elif "PWT" in upper_product:
            suffix = "PWT"
        else:
            suffix = "P"
        return f"HC-{hc_family.group(1)}-{pin_count}{suffix}"

    wafer_family = re.search(
        r"\bWAFER[-_]?(PH|MX)(\d+(?:\.\d+)?)",
        product_name,
        re.IGNORECASE,
    )
    if wafer_family and pin_count:
        return f"WAFER-{wafer_family.group(1).upper()}{wafer_family.group(2)}-{pin_count}P"

    a_family = re.search(r"\b(A1251|A1254)", product_name, re.IGNORECASE)
    if a_family and pin_count:
        return f"{a_family.group(1).upper()}-{pin_count}P"

    gh_family = re.search(r"\bGH(\d+(?:\.\d+)?)", product_name, re.IGNORECASE)
    if gh_family and pin_count:
        return f"GH{gh_family.group(1)}-{pin_count}P"

    sur = re.search(r"(?<!\d)(\d{2}SUR-32S)", variant, re.IGNORECASE)
    if sur:
        return sur.group(1).upper()
    return _compact_text(first_part).upper()


def _connector_labels(
    cable_kind: str,
    end_style: str,
    model: str,
    pin_count: int,
    pin_count_b: int,
) -> tuple[str, str]:
    if cable_kind == "rf_coax":
        return "IPEX 1代", "IPEX 1代"
    if cable_kind == "micro_coax":
        return "FPC", "FPC"
    if cable_kind == "flat_flex":
        if pin_count_b:
            return f"{pin_count} Pin FPC", f"{pin_count_b} Pin FPC"
        return "FPC/FFC", "FPC/FFC"
    if end_style == "single_tinned":
        return model, "沾锡线端"
    if end_style == "male_female_pair":
        return "公头", "母头"
    if end_style == "single":
        return model, "裸线端"
    if end_style == "double":
        return model, model
    return model, ""


def _generated_name(cable_kind: str, end_style: str, model: str) -> str:
    if not model:
        return "待确认线缆"
    if cable_kind == "flat_flex":
        return f"{model} {'FPC 转接排线' if 'P-' in model else 'FPC/FFC 软排线'}"
    if cable_kind == "micro_coax":
        return f"{model} FPC 极细同轴线"
    if cable_kind == "rf_coax":
        return f"{model} IPEX 射频同轴线"
    suffix = {
        "double": "双头端子线",
        "single": "单头端子线",
        "single_tinned": "单头沾锡端子线",
        "male_female_pair": "公母对接线",
        "unspecified": "端子线",
    }[end_style]
    return f"{model} {suffix}"


def cable_import_item_id(product_url: str) -> str:
    try:
        query = dict(parse_qsl(urlsplit(product_url).query, keep_blank_values=True))
    except ValueError:
        return ""
    return str(query.get("id") or "").strip()


def _stable_product_reference(product_url: str) -> str:
    item_id = cable_import_item_id(product_url)
    if item_id:
        return f"item:{item_id}"
    try:
        parts = urlsplit(product_url.strip())
        stable_query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in {"mi_id", "spm", "sku_id"}
        ]
        return urlunsplit(
            (
                parts.scheme.casefold(),
                parts.netloc.casefold(),
                parts.path.rstrip("/").casefold(),
                urlencode(stable_query),
                "",
            )
        )
    except ValueError:
        return product_url.strip().casefold()


def cable_import_source_identity(order_no: str, product_url: str, variant: str) -> str:
    identity = "|".join(
        [
            order_no.strip().casefold(),
            _stable_product_reference(product_url),
            _compact_text(variant).casefold(),
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def cable_import_source_spec_identity(
    order_no: str,
    product_url: str,
    spec_key: tuple[Any, ...],
) -> str:
    identity = "|".join(
        [
            order_no.strip().casefold(),
            _stable_product_reference(product_url),
            *(str(value).strip().casefold() for value in spec_key),
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _spec_key(row: dict) -> tuple[Any, ...]:
    model_or_name = str(row.get("model") or row.get("name") or "").strip().casefold()
    return (
        str(row.get("cable_kind") or "terminal"),
        str(row.get("end_style") or "double"),
        model_or_name,
        str(row.get("connector_pitch_mm") or ""),
        str(row.get("direction") or "unspecified"),
        str(row.get("length_cm") or ""),
        int(row.get("pin_count") or 0),
        int(row.get("pin_count_b") or 0),
        str(row.get("pin_layout") or "").strip().casefold(),
    )


def _row_issues(row: dict) -> list[str]:
    issues: list[str] = []
    if not row["model"]:
        issues.append("未识别型号")
    if row["cable_kind"] != "rf_coax" and not row["connector_pitch_mm"]:
        issues.append("未识别接头间距")
    if row["cable_kind"] != "rf_coax" and not row["pin_count"]:
        issues.append("未识别 Pin 数")
    if not row["length_cm"]:
        issues.append("未识别线缆长度")
    if row["quantity"] <= 0:
        issues.append("商品数量不是正整数")
    if any(word in row["status"] for word in CANCELLED_STATUS_WORDS):
        issues.append(f"订单状态为“{row['status']}”，默认不导入")
    return issues


def _confidence(row: dict) -> str:
    if row["issues"]:
        return "low"
    inference_warnings = [
        warning
        for warning in row["warnings"]
        if not warning.startswith("已合并 ")
    ]
    return "medium" if inference_warnings else "high"


def analyze_cable_order_rows(raw_rows: list[dict], filename: str = "") -> dict:
    if not raw_rows:
        raise BusinessError("EMPTY_IMPORT_FILE", "文件中没有可分析的订单明细")

    available_headers = {
        _normalized_header(header) for row in raw_rows for header in row.keys()
    }
    has_variant = any(
        _normalized_header(alias) in available_headers
        for alias in CABLE_ORDER_ALIASES["variant"]
    )
    has_quantity = any(
        _normalized_header(alias) in available_headers
        for alias in CABLE_ORDER_ALIASES["quantity"]
    )
    if not has_variant or not has_quantity:
        raise BusinessError(
            "CABLE_IMPORT_COLUMNS_MISSING",
            "没有找到“型号款式/商品规格”和“商品数量”列，请选择淘宝订单导出表或相似格式表格",
            details={"filename": filename},
        )

    parsed_rows: list[dict] = []
    inherited = {
        "order_no": "",
        "submitted_at": "",
        "status": "",
        "shop": "",
    }
    for source_row, raw in enumerate(raw_rows, start=2):
        canonical: dict[str, str] = {}
        for field, aliases in CABLE_ORDER_ALIASES.items():
            canonical[field] = _normalized_text(_row_value(raw, aliases))
        for field in inherited:
            if canonical[field]:
                inherited[field] = canonical[field]
            else:
                canonical[field] = inherited[field]

        product_name = canonical["product_name"]
        variant = canonical["variant"]
        if not variant and not product_name:
            continue

        cable_kind = _extract_cable_kind(product_name, variant)
        end_style = _extract_end_style(cable_kind, variant)
        direction = _extract_direction(variant)
        pitch, pitch_warning = _extract_pitch(product_name, variant, cable_kind)
        pin_count, pin_count_b = _extract_pin_counts(variant, cable_kind)
        pin_layout = _extract_pin_layout(variant)
        length_cm = _extract_length_cm(variant)
        model = _extract_model(
            product_name,
            variant,
            cable_kind,
            pitch,
            pin_count,
            pin_count_b,
        )
        connector_a, connector_b = _connector_labels(
            cable_kind,
            end_style,
            model,
            pin_count,
            pin_count_b,
        )
        quantity = _parse_positive_integer(canonical["quantity"]) or 0
        unit_price = _parse_money(canonical["unit_price"])
        warnings = [pitch_warning] if pitch_warning else []
        if direction == "unspecified" and end_style == "double" and cable_kind != "rf_coax":
            warnings.append("订单未注明同向或反向，已保留为“方向未注明”")
        if end_style == "unspecified":
            warnings.append("订单未注明端头形式，已保留为“端头未注明”")
        name = _generated_name(cable_kind, end_style, model)
        source_identity = cable_import_source_identity(
            canonical["order_no"],
            canonical["product_url"],
            variant,
        )
        row = {
            "selected": True,
            "valid": False,
            "confidence": "low",
            "source_rows": [source_row],
            "source_line_count": 1,
            "source_items": [
                {
                    "key": source_identity,
                    "identity": source_identity,
                    "item_id": cable_import_item_id(canonical["product_url"]),
                    "variant": variant,
                    "quantity": quantity,
                    "order_no": canonical["order_no"],
                    "source_row": source_row,
                    "unit_price": str(unit_price) if unit_price is not None else "",
                    "product_url": canonical["product_url"],
                }
            ],
            "name": name[:200],
            "model": model[:200],
            "cable_kind": cable_kind,
            "end_style": end_style,
            "connector_a": connector_a[:100],
            "connector_b": connector_b[:100],
            "connector_pitch_mm": _decimal_text(pitch) if pitch is not None else None,
            "direction": direction,
            "length_cm": _decimal_text(length_cm) if length_cm is not None else "",
            "pin_count": pin_count,
            "pin_count_b": pin_count_b,
            "pin_layout": pin_layout,
            "quantity": quantity,
            "unit_price": str(unit_price) if unit_price is not None else "",
            "storage_location": "",
            "notes": "",
            "shop": canonical["shop"],
            "status": canonical["status"],
            "raw_product_name": product_name,
            "raw_variant": variant,
            "warnings": [warning for warning in warnings if warning],
            "issues": [],
            "existing_cable_id": None,
            "existing_cable_code": "",
            "import_action": "create",
        }
        row["issues"] = _row_issues(row)
        row["valid"] = not row["issues"]
        row["selected"] = row["valid"]
        row["confidence"] = _confidence(row)
        parsed_rows.append(row)

    if not parsed_rows:
        raise BusinessError("EMPTY_IMPORT_FILE", "文件中没有可分析的线缆订单明细")

    source_quantity = sum(row["quantity"] for row in parsed_rows)
    valid_quantity = sum(row["quantity"] for row in parsed_rows if row["valid"])
    grouped: OrderedDict[tuple[Any, ...], dict] = OrderedDict()
    for row in parsed_rows:
        key = _spec_key(row)
        if key not in grouped or not row["valid"]:
            unique_key = key if key not in grouped else (*key, row["source_rows"][0])
            grouped[unique_key] = row
            continue

        target = grouped[key]
        previous_quantity = target["quantity"]
        target["quantity"] += row["quantity"]
        target["source_rows"].extend(row["source_rows"])
        target["source_line_count"] += 1
        target["source_items"].extend(row["source_items"])
        target["warnings"] = list(dict.fromkeys(target["warnings"] + row["warnings"]))
        if row["unit_price"]:
            priced_quantity = sum(
                Decimal(item["unit_price"]) * Decimal(item["quantity"])
                for item in target["source_items"]
                if item["unit_price"]
            )
            priced_units = sum(
                Decimal(item["quantity"])
                for item in target["source_items"]
                if item["unit_price"]
            )
            if priced_units:
                average = (priced_quantity / priced_units).quantize(
                    Decimal("0.0001"),
                    rounding=ROUND_HALF_UP,
                )
                target["unit_price"] = format(average.normalize(), "f")
        if target["quantity"] != previous_quantity:
            merge_note = f"已合并 {target['source_line_count']} 条相同规格订单明细"
            target["warnings"] = [
                item for item in target["warnings"] if not item.startswith("已合并 ")
            ]
            target["warnings"].append(merge_note)
        target["confidence"] = _confidence(target)

    rows = list(grouped.values())
    shops = sorted({row["shop"] for row in parsed_rows if row["shop"]})
    return {
        "filename": filename,
        "detected_format": "淘宝订单导出表",
        "rows": rows,
        "summary": {
            "source_rows": len(parsed_rows),
            "recognized_rows": sum(row["valid"] for row in parsed_rows),
            "spec_count": len(rows),
            "merged_rows": len(parsed_rows) - len(rows),
            "quantity": source_quantity,
            "valid_quantity": valid_quantity,
            "shops": shops,
        },
    }


def cable_import_spec_key(row: dict) -> tuple[Any, ...]:
    return _spec_key(row)
