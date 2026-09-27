import io

import pytest
import xlrd
from openpyxl import Workbook

from app.api.v1 import files
from app.api.v1.files import normalize_material_rows, parse_upload
from app.core.exceptions import BusinessError
from app.services.cable_import import analyze_cable_order_rows


def test_csv_parser():
    rows = parse_upload("code,name,quantity\nR1,10k电阻,100\n".encode(), "materials.csv")
    assert rows == [{"code": "R1", "name": "10k电阻", "quantity": "100"}]


def test_xlsx_parser_uses_first_sheet_and_skips_blank_rows():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["code", "name", "quantity"])
    sheet.append(["C1", "电容", 25])
    sheet.append([None, None, None])
    content = io.BytesIO()
    workbook.save(content)

    assert parse_upload(content.getvalue(), "materials.xlsx") == [
        {"code": "C1", "name": "电容", "quantity": 25}
    ]


def test_xlsx_parser_detects_bom_header_after_report_metadata():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Synthetic report title"])
    sheet.append(["Generated", "portfolio fixture"])
    sheet.append([None, None, None])
    sheet.append(["Comment", "Description", "Designator", "Footprint", "Quantity"])
    sheet.append(["100nF", "X7R capacitor", "C1,C2", "C0603", 2])
    content = io.BytesIO()
    workbook.save(content)

    assert parse_upload(content.getvalue(), "bom.xlsx") == [
        {
            "Comment": "100nF",
            "Description": "X7R capacitor",
            "Designator": "C1,C2",
            "Footprint": "C0603",
            "Quantity": 2,
        }
    ]


def test_xls_parser_normalizes_legacy_cells(monkeypatch):
    class Cell:
        def __init__(self, value, cell_type):
            self.value = value
            self.ctype = cell_type

    class Sheet:
        nrows = 2
        ncols = 3
        rows = [
            [
                Cell("code", xlrd.XL_CELL_TEXT),
                Cell("name", xlrd.XL_CELL_TEXT),
                Cell("quantity", xlrd.XL_CELL_TEXT),
            ],
            [
                Cell("R2", xlrd.XL_CELL_TEXT),
                Cell("贴片电阻", xlrd.XL_CELL_TEXT),
                Cell(100.0, xlrd.XL_CELL_NUMBER),
            ],
        ]

        def cell_value(self, row, column):
            return self.rows[row][column].value

        def cell(self, row, column):
            return self.rows[row][column]

    class WorkbookStub:
        nsheets = 1
        datemode = 0

        @staticmethod
        def sheet_by_index(index):
            assert index == 0
            return Sheet()

        @staticmethod
        def release_resources():
            return None

    monkeypatch.setattr(files.xlrd, "open_workbook", lambda **kwargs: WorkbookStub())

    assert parse_upload(b"legacy-xls", "materials.XLS") == [
        {"code": "R2", "name": "贴片电阻", "quantity": 100}
    ]


def test_invalid_xls_has_business_error(monkeypatch):
    def fail(**kwargs):
        raise xlrd.XLRDError("broken workbook")

    monkeypatch.setattr(files.xlrd, "open_workbook", fail)
    with pytest.raises(BusinessError) as error:
        parse_upload(b"broken", "materials.xls")
    assert error.value.code == "INVALID_IMPORT_FILE"


def test_lcsc_cart_columns_are_mapped_to_material_fields():
    rows, detected_format = normalize_material_rows(
        [
            {
                "购买类型": "现货",
                "商品编号": "C7430445",
                "物料编码": "",
                "商品分类": "线对板针座",
                "名称": "1x3P 间距:1mm 卧贴",
                "商品型号": "ZX-SH1.0-3PWT",
                "品牌": "Megastar(兆星)",
                "封装规格": "SMD,P=1mm,卧贴",
                "单个毛重": 0.00023365,
                "购买数量": 20,
                "商品单价(元)": 0.33364,
                "金额(元)": 6.67,
            }
        ]
    )

    assert detected_format == "立创商城购物车"
    assert rows == [
        {
            "code": "C7430445",
            "name": "1x3P 间距:1mm 卧贴",
            "mpn": "ZX-SH1.0-3PWT",
            "specification": "",
            "package": "SMD,P=1mm,卧贴",
            "manufacturer": "Megastar(兆星)",
            "supplier_part_number": "C7430445",
            "unit": "pcs",
            "unit_price": "0.33364",
            "quantity": "20",
            "safety_stock": "0",
            "target_stock": "0",
            "attributes": {
                "source": "立创商城",
                "purchase_type": "现货",
                "source_category": "线对板针座",
                "gross_weight": "0.00023365",
                "line_amount": "6.67",
            },
        }
    ]


def test_lcsc_cart_prefers_internal_material_code():
    rows, _ = normalize_material_rows(
        [
            {
                "商品编号": "C225111",
                "物料编码": "CONN-001",
                "名称": "连接器",
                "商品型号": "A1251WR-S-2P",
                "购买数量": 20,
            }
        ]
    )
    assert rows[0]["code"] == "CONN-001"
    assert rows[0]["supplier_part_number"] == "C225111"


def test_taobao_cable_orders_are_recognized_and_same_specs_are_grouped():
    preview = analyze_cable_order_rows(
        [
            {
                "订单号": "ORDER-1",
                "订单状态": "交易成功",
                "店铺名称": "连接器店",
                "商品名称": "ZX-SH1.0 端子线",
                "商品链接": "https://example.test/item/1",
                "型号款式": "ZX-SH1.0-3PWT/PLT;100MM（10厘米）;双头同向",
                "商品数量": 10,
                "商品金额": "￥0.80",
            },
            {
                "商品名称": "ZX-SH1.0 端子线",
                "商品链接": "https://example.test/item/2",
                "型号款式": "ZX-SH1.0-3PWT/PLT;100MM（10厘米）;双头同向",
                "商品数量": 10,
                "商品金额": "￥0.88",
            },
            {
                "商品名称": "对插 A1251WV 端子线",
                "商品链接": "https://example.test/item/3",
                "型号款式": "A1251WV-2P;300MM（30厘米）;双头反向",
                "商品数量": 5,
                "商品金额": "￥0.95",
            },
        ],
        "淘宝订单.xlsx",
    )

    assert preview["summary"] == {
        "source_rows": 3,
        "recognized_rows": 3,
        "spec_count": 2,
        "merged_rows": 1,
        "quantity": 25,
        "valid_quantity": 25,
        "shops": ["连接器店"],
    }
    first, second = preview["rows"]
    assert first["model"] == "ZX-SH1.0-3PWT/PLT"
    assert first["connector_pitch_mm"] == "1.0"
    assert first["pin_count"] == 3
    assert first["length_cm"] == "10.0"
    assert first["direction"] == "same"
    assert first["quantity"] == 20
    assert first["unit_price"] == "0.84"
    assert len(first["source_items"]) == 2
    assert second["connector_pitch_mm"] == "1.25"
    assert second["direction"] == "reverse"
    assert second["confidence"] == "medium"


def test_cable_orders_recognize_tinned_fpc_conversion_and_rf_coax():
    preview = analyze_cable_order_rows(
        [
            {
                "订单号": "ORDER-MIXED",
                "订单状态": "交易成功",
                "商品名称": "XH连接线插头线2.54MM单/双头端子线",
                "商品链接": "https://item.taobao.com/item.htm?id=1001&mi_id=old",
                "型号款式": "XH-2P;单头沾锡;60mm",
                "商品数量": 30,
                "商品金额": "0.09",
            },
            {
                "商品名称": "树莓派摄像头专用排线1.0间距15pin 22pin转15pin",
                "商品链接": "https://item.taobao.com/item.htm?id=1002",
                "型号款式": "长度16CM 同面22pin转15pin",
                "商品数量": 7,
                "商品金额": "3.00",
            },
            {
                "商品名称": "ipx双头连接跳线 RF1.13同轴线 IPEX端子线",
                "商品链接": "https://item.taobao.com/item.htm?id=1003",
                "型号款式": "0.6m;【B1款】双头1代（黑色113线）",
                "商品数量": 30,
                "商品金额": "1.60",
            },
        ],
        "混合线缆订单.xlsx",
    )

    assert preview["summary"]["recognized_rows"] == 3
    assert preview["summary"]["valid_quantity"] == 67
    tinned, fpc, rf = preview["rows"]
    assert (tinned["cable_kind"], tinned["end_style"]) == (
        "terminal",
        "single_tinned",
    )
    assert tinned["connector_pitch_mm"] == "2.54"
    assert tinned["direction"] == "unspecified"
    assert fpc["cable_kind"] == "flat_flex"
    assert fpc["pin_count"] == 22
    assert fpc["pin_count_b"] == 15
    assert fpc["direction"] == "same"
    assert rf["cable_kind"] == "rf_coax"
    assert rf["connector_pitch_mm"] is None
    assert rf["pin_count"] == 0
    assert rf["length_cm"] == "60.0"


def test_cable_order_identity_ignores_taobao_mi_id():
    base = {
        "订单号": "ORDER-STABLE",
        "订单状态": "交易成功",
        "商品名称": "SH1.0 端子线",
        "型号款式": "SH1.0-3P;双头同向;300mm",
        "商品数量": 20,
    }
    first = analyze_cable_order_rows(
        [
            {
                **base,
                "商品链接": "https://item.taobao.com/item.htm?id=982510562656&mi_id=old",
            }
        ]
    )
    second = analyze_cable_order_rows(
        [
            {
                **base,
                "商品链接": "https://item.taobao.com/item.htm?id=982510562656&mi_id=new",
            }
        ]
    )

    assert first["rows"][0]["source_items"][0]["key"] == second["rows"][0]["source_items"][0]["key"]
