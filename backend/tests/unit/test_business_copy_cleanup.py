import pytest

from app.services.business_copy_cleanup import FORBIDDEN_RE, clean_user_visible_copy
from app.services.engineering_evidence import normalize_page_text


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("业务数据：编码器 · IMU · ToF", "编码器 · IMU · ToF"),
        ("项目数据：机器人控制器", "机器人控制器"),
        ("演示数据：48V 功率模块", "48V 功率模块"),
        ("[Portfolio Demo v2]", ""),
        (
            "Portfolio demo synthetic data; for resume/project demonstration only.",
            "",
        ),
        ("LCSC catalog identity; demo stock is synthetic.", "LCSC catalog identity"),
        (
            "48V motor driver; CAN-FD; current sensing; demo metadata",
            "48V motor driver; CAN-FD; current sensing",
        ),
        ("线缆目录物料。当前数量仅来自受控的库存入库流程。", ""),
        (
            "cable catalog item. Current quantity comes only from the governed movement",
            "",
        ),
        (
            "作品集演示：计算模块低库存与真实缺料；后续适合接入 Vision/RAG",
            "计算模块低库存与真实缺料。",
        ),
        ("机器人移动底盘作品集产品", "机器人移动底盘产品"),
        (
            "No lot allocation on purpose; demonstrates primary location without exact "
            "location quantity.",
            "No lot allocation is recorded; the primary location does not imply an exact "
            "location quantity.",
        ),
        ("BOM derivative; no private product identity.", ""),
        (
            "作品集演示：无线 MCU、BMS、电源与 CAN；暂停项目用于状态筛选演示。",
            "无线 MCU、BMS、电源与 CAN；当前项目状态为暂停。",
        ),
        (
            "无线 MCU、BMS、电源与 CAN；暂停项目用于状态筛选演示。",
            "无线 MCU、BMS、电源与 CAN；当前项目状态为暂停。",
        ),
        ("One unit remains unallocated.", "One unit remains unallocated."),
    ],
)
def test_clean_user_visible_copy_deletes_boilerplate_without_replacement(source, expected):
    cleaned = clean_user_visible_copy(source)
    assert cleaned == expected
    assert not FORBIDDEN_RE.search(cleaned)


def test_clean_user_visible_copy_is_idempotent():
    source = "业务数据：内部工程项目：编码器 · IMU · ToF"
    once = clean_user_visible_copy(source)
    assert clean_user_visible_copy(once) == once == "编码器 · IMU · ToF"


def test_pdf_text_normalization_removes_database_invalid_control_bytes():
    assert normalize_page_text("GHx\x00\x01\n\tVGS") == "GHx\n\tVGS"
