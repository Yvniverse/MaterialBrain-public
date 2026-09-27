import uuid
from decimal import Decimal

from app.agent.task_contract import classify_task_contract
from app.component_intelligence.core import ComponentSearchCore
from app.component_intelligence.extractor import RequirementExtractor
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import InventoryLot, Location, Material, StockMovement, User


def test_requirement_extractor_structures_power_interface_and_location_constraints():
    power = RequirementExtractor().extract("有没有一个 24V 转 5V、3A 左右的电源模块？")
    historical = RequirementExtractor().extract("之前那个 48V CAN 电机驱动板放哪儿了？")
    ranged = RequirementExtractor().extract("找一个 36V-60V 输入、5A、TI 的 SOIC-8 电源模块")
    input_with_output_current = RequirementExtractor().extract(
        "找一个 60V 输入、5A 输出能力的 buck converter"
    )

    assert power.input_voltage_v == Decimal("24")
    assert power.output_voltage_v == Decimal("5")
    assert power.current_a == Decimal("3")
    assert historical.voltage_values_v == [Decimal("48")]
    assert historical.interfaces == ["CAN"]
    assert historical.location_requested is True
    assert power.component_types == ["DC/DC module"]
    assert power.hard_constraints["output_voltage_v"] == Decimal("5")
    assert ranged.voltage_range_v.minimum == Decimal("36")
    assert ranged.voltage_range_v.maximum == Decimal("60")
    assert ranged.package_preferences == ["SOIC-8"]
    assert ranged.manufacturer_preferences == ["Texas Instruments"]
    assert input_with_output_current.input_voltage_v == Decimal("60")
    assert input_with_output_current.output_voltage_v is None
    assert input_with_output_current.current_min_a == Decimal("5")


def test_requirement_extractor_handles_phase21_natural_paraphrases_without_model():
    power = RequirementExtractor().extract("找个 24V 进 5V 出、至少 3A 的降压模块")
    bridge = RequirementExtractor().extract("我想采应变桥，24 位 ADC，SPI 最好")
    buck = RequirementExtractor().extract("来个 100V 输入能用的降压方案")
    can_bus = RequirementExtractor().extract("给我找个能接 5V CAN 总线的收发芯片")
    rs485 = RequirementExtractor().extract("给我找颗 RS-485 总线收发器")
    power_variant = RequirementExtractor().extract("找个能把 48V 变成 5V、输出不低于 3A 的电源模块")

    assert power.component_types == ["DC/DC module"]
    assert power.input_voltage_v == Decimal("24")
    assert power.output_voltage_v == Decimal("5")
    assert power.current_min_a == Decimal("3")
    assert bridge.component_types == ["ADC"]
    assert bridge.resolution_bits == 24
    assert bridge.interfaces == ["SPI"]
    assert buck.component_types == ["buck converter"]
    assert buck.input_voltage_v == Decimal("100")
    assert can_bus.component_types == ["CAN transceiver"]
    assert can_bus.supply_voltage_v == Decimal("5")
    assert rs485.component_types == ["RS485 transceiver"]
    assert power_variant.input_voltage_v == Decimal("48")
    assert power_variant.output_voltage_v == Decimal("5")
    for query in (
        "给我找个能接 5V CAN 总线的收发芯片",
        "想要一颗跑 CAN-FD 的 MCU",
        "给机器人挑个支持 SPI 的 6 轴 IMU",
        "来个 100V 输入能用的降压方案",
        "找个 24V 进 5V 出、至少 3A 的降压模块",
        "找个能把 48V 变成 5V、输出不低于 3A 的电源模块",
    ):
        contract = classify_task_contract(query)
        assert contract.entity_kind == "component"
        assert contract.requested_facts == {"component_search"}


def test_requirement_extractor_parses_chinese_voltage_conversion_before_cjk_suffixes():
    buck = RequirementExtractor().extract(
        "我需要一个5V转3.3V的降压芯片，以及其配套的物料，能在哪里找到？"
    )
    design = RequirementExtractor().extract("请给我一套12V转3.3V的硬件设计方案")

    assert buck.input_voltage_v == Decimal("5")
    assert buck.output_voltage_v == Decimal("3.3")
    assert buck.component_types == ["buck converter"]
    assert buck.location_requested is True
    assert design.input_voltage_v == Decimal("12")
    assert design.output_voltage_v == Decimal("3.3")


def test_requirement_extractor_parses_arrow_voltage_conversion_for_power_design():
    for query, input_voltage, output_voltage in (
        ("5V→3.3V 负载 100mA", Decimal("5"), Decimal("3.3")),
        ("12V -> 3.3V 负载 50mA", Decimal("12"), Decimal("3.3")),
    ):
        requirement = RequirementExtractor().extract(query)
        contract = classify_task_contract(query)

        assert requirement.input_voltage_v == input_voltage
        assert requirement.output_voltage_v == output_voltage
        assert contract.entity_kind == "power"
        assert contract.requested_facts == {"power_design"}


def test_requirement_extractor_bounds_facets_for_long_power_architecture_question():
    requirement = (
        "给一套 12V 转 3.3V、100mA MCU 电源，模拟传感器也要低噪声。请比较 "
        "12V→3.3V 直接 Buck、12V→5V Buck→3.3V LDO、数字与敏感模拟负载分轨三种方案，"
        "解释开关纹波、LDO 的频率相关 PSRR、压差、效率边界、各级损耗和热预算。"
        "请先用简短自然语言回答，再展开三种架构的适用条件；"
        "基于当前 MaterialBrain 数据查询 LM5164、TLV76133、TPS54560 及真正相关的外围物料、"
        "库存和库位，引用可追溯的数据手册版本/页码，指出缺料和待选型项，"
        "并给出只读工程 BOM 草案。模拟支路电流尚未提供，请明确保持未知并按其自身电流计算；"
        "本例 MCU 负载为 100mA，不要继承 800mA 压测条件。"
        "LM5164 的 COT FB 注入纹波不能直接当成输出纹波；"
        "5V→3.3V 后级 LDO 的损耗必须按 5V 输入计算，"
        "Buck 效率和噪声数据没有工作点证据时不要编造。"
        "若有任何参数/库存/库位/PDF 证据缺失，请清楚标成未知。"
    )

    parsed = RequirementExtractor().extract(requirement)

    assert parsed.raw_text == requirement
    assert len(parsed.keywords) <= 30
    assert {"LM5164", "TLV76133", "TPS54560"}.issubset(parsed.keywords)
    assert parsed.input_voltage_v == Decimal("12")
    assert parsed.output_voltage_v == Decimal("3.3")
    assert parsed.current_a == Decimal("0.1")


def test_power_architecture_comparison_routes_natural_chinese_to_power_design():
    for query in (
        "12V 转 3.3V，日常约 50mA，给 MCU 供电，请比较 Buck/LDO/两级。",
        "帮我比较 12 V → 3.3 V MCU 电源拓扑，日常负载约 50 mA。",
        "12V 转 3.3V，比较电源架构。",
        "12V 转 3.3V，100mA 左右，想要纹波小一些，Buck 后接 LDO 是否有意义？",
        "12V 转 3.3V，Buck 后接 LDO 可以降低输出纹波吗？",
        "12V→3.3V 100mA",
        "5V→3.3V 用 TLV76133，100mA 预计损耗和温升？",
        (
            "这是单独的高负载边界测试：12V转3.3V、800mA。比较直接LDO和"
            "12V→5V Buck→3.3V LDO的线性级损耗，并明确这不是日常默认负载。"
        ),
        (
            "12V输入、3.3V/100mA MCU，我比较在意开关噪声。请用简短结论说明："
            "什么时候直接Buck足够，什么时候Buck到5V再接LDO更有意义，还需要核对"
            "哪些PSRR、压差、静态电流和热条件。"
        ),
    ):
        contract = classify_task_contract(query)

        assert contract.entity_kind == "power"
        assert contract.requested_facts == {"power_design"}

    requirement = RequirementExtractor().extract(
        "12V 转 3.3V，日常约 50mA，给 MCU 供电，请比较 Buck/LDO/两级。"
    )
    assert requirement.input_voltage_v == Decimal("12")
    assert requirement.output_voltage_v == Decimal("3.3")
    assert requirement.current_a == Decimal("0.05")

    slashed_rail = RequirementExtractor().extract("12V输入、3.3V/100mA MCU，我比较在意开关噪声。")
    assert slashed_rail.input_voltage_v == Decimal("12")
    assert slashed_rail.output_voltage_v == Decimal("3.3")


def test_power_design_can_request_calculation_and_explicit_datasheet_evidence_together():
    contract = classify_task_contract(
        "12V→3.3V、100mA；LM5164 做 Buck 前级、TLV76133 做后级；PDF 在哪一页？"
    )

    assert contract.entity_kind == "material"
    assert contract.requested_facts == {"power_design", "engineering_evidence"}
    assert contract.requires_material_resolution is True


def test_simple_buck_component_search_stays_on_component_route():
    contract = classify_task_contract("找个 24V 进 5V 出、至少 3A 的降压模块")
    inventory_contract = classify_task_contract("12V→3.3V 100mA，查 LM5164 库存")

    assert contract.entity_kind == "component"
    assert contract.requested_facts == {"component_search"}
    assert inventory_contract.entity_kind == "material"
    assert "inventory" in inventory_contract.requested_facts


def test_component_intelligence_endpoint_is_disabled_by_default(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "component_intelligence_enabled", False)

    response = client.post(
        "/api/v1/component-intelligence/search",
        json={"requirement": "有没有 5V CAN transceiver？"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "COMPONENT_INTELLIGENCE_DISABLED"


def test_component_intelligence_search_is_read_only_grounded_and_candidate_only(
    client, admin, monkeypatch
):
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        location = Location(
            code=f"CI-LOC-{suffix}",
            name="CI test bin",
            full_path=f"CI Test / {suffix}",
        )
        db.add(location)
        db.flush()
        material = Material(
            code=f"CI-CAN-{suffix}",
            name="5V CAN Transceiver",
            mpn=f"CI-CAN-MPN-{suffix}",
            specification="5V high-speed CAN transceiver",
            package="SOIC-8",
            tags=["CAN", "transceiver"],
            attributes={
                "component_type": "CAN transceiver",
                "interfaces": ["CAN"],
                "supply_voltage": "5V",
            },
            location_id=location.id,
            quantity=Decimal("12"),
            reserved_quantity=Decimal("2"),
        )
        db.add(material)
        db.flush()
        db.add(
            InventoryLot(material_id=material.id, location_id=location.id, quantity=Decimal("12"))
        )
        db.commit()
        material_id = material.id

    monkeypatch.setattr(settings, "component_intelligence_enabled", True)
    with SessionLocal() as db:
        movement_count_before = db.query(StockMovement).count()
    response = client.post(
        "/api/v1/component-intelligence/search",
        json={"requirement": "有没有一个能替代这个的 5V CAN transceiver？", "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    candidate = next(item for item in payload["candidates"] if item["material_id"] == material_id)
    assert candidate["inventory"]["available_quantity"] == "10.0000"
    assert candidate["locations"]["locations"][0]["location_id"] is not None
    assert payload["candidate_only"] is True
    assert "需要工程验证" in payload["engineering_caveat"]
    assert "pin_compatible" in payload["engineering_caveat"]
    assert payload["read_only"] is True
    assert payload["conversation_id"]
    assert candidate["technical_claims_allowed"] is True
    assert "器件类型匹配：CAN transceiver" in candidate["hard_constraint_matches"]
    with SessionLocal() as db:
        assert db.query(StockMovement).count() == movement_count_before
        assert db.get(Material, material_id).quantity == Decimal("12")


def test_component_intelligence_uses_server_context_for_candidate_followups(
    client, admin, monkeypatch
):
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        location = Location(
            code=f"CI-CONTEXT-LOC-{suffix}",
            name="CI context bin",
            full_path=f"CI Context / {suffix}",
        )
        db.add(location)
        db.flush()
        materials = []
        for index in range(2):
            material = Material(
                code=f"CI-CONTEXT-{index}-{suffix}",
                name="Context CAN Transceiver",
                mpn=f"CI-CONTEXT-MPN-{index}-{suffix}",
                specification="5V CAN transceiver",
                package="SOIC-8",
                manufacturer="Texas Instruments",
                tags=["CAN"],
                attributes={
                    "component_type": "CAN transceiver",
                    "interfaces": ["CAN"],
                    "supply_voltage": "5V",
                },
                location_id=location.id,
                quantity=Decimal(str(10 + index)),
            )
            db.add(material)
            db.flush()
            db.add(
                InventoryLot(
                    material_id=material.id,
                    location_id=location.id,
                    quantity=material.quantity,
                )
            )
            materials.append(material)
        db.commit()

    monkeypatch.setattr(settings, "component_intelligence_enabled", True)
    first = client.post(
        "/api/v1/component-intelligence/search",
        json={"requirement": "找 CAN transceiver", "limit": 20},
    )
    assert first.status_code == 200
    first_payload = first.json()
    candidate_ids = [
        item["material_id"]
        for item in first_payload["candidates"]
        if item["material_id"] in {material.id for material in materials}
    ]
    assert len(candidate_ids) == 2

    second = client.post(
        "/api/v1/component-intelligence/search",
        json={
            "requirement": "第二个",
            "conversation_id": first_payload["conversation_id"],
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["selected_material_id"] == first_payload["candidates"][1]["material_id"]
    assert len(second_payload["candidates"]) == 1

    followup = client.post(
        "/api/v1/component-intelligence/search",
        json={
            "requirement": "它在哪？",
            "conversation_id": first_payload["conversation_id"],
        },
    )
    assert followup.status_code == 200
    assert followup.json()["selected_material_id"] == second_payload["selected_material_id"]
    assert followup.json()["candidates"][0]["locations"]["locations"]


def test_component_core_sorts_verified_candidate_scope_by_available_inventory(admin):
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        low = Material(
            code=f"CI-SORT-LOW-{suffix}",
            name="Inventory sort low",
            quantity=Decimal("24"),
            reserved_quantity=Decimal("0"),
        )
        high = Material(
            code=f"CI-SORT-HIGH-{suffix}",
            name="Inventory sort high",
            quantity=Decimal("76"),
            reserved_quantity=Decimal("0"),
        )
        db.add_all([low, high])
        db.commit()

        result = ComponentSearchCore(db, user, f"sort-{suffix}").search(
            "库存多的放前面。",
            candidate_scope_ids=[low.id, high.id],
        )

        assert [item.material_id for item in result.candidates] == [high.id, low.id]


def test_exact_identity_never_overrides_a_hard_engineering_violation(client, admin, monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    code = f"CI-CAN-3V3-{suffix}"
    with SessionLocal() as db:
        db.add(
            Material(
                code=code,
                name="3.3V-only CAN Transceiver",
                mpn=f"CI-CAN-3V3-MPN-{suffix}",
                attributes={
                    "component_type": "CAN transceiver",
                    "interfaces": ["CAN"],
                    "supply_voltage": "3.3V",
                },
            )
        )
        db.commit()

    monkeypatch.setattr(settings, "component_intelligence_enabled", True)
    response = client.post(
        "/api/v1/component-intelligence/search",
        json={"requirement": f"找一个 5V CAN transceiver，参考编码 {code}"},
    )

    assert response.status_code == 200
    assert all(item["code"] != code for item in response.json()["candidates"])


def test_low_confidence_catalog_identity_never_becomes_a_technical_claim(
    client, admin, monkeypatch
):
    suffix = uuid.uuid4().hex[:8]
    code = f"CI-LOW-{suffix}"
    with SessionLocal() as db:
        material = Material(
            code=code,
            name="待分类 catalog identity",
            mpn=f"UNCERTAIN-{suffix}",
            specification="Catalog identity only; technical classification pending.",
            attributes={
                "portfolio_demo": {
                    "dataset_version": "v2",
                    "catalog_confidence": "low",
                    "stock_is_synthetic": True,
                }
            },
        )
        db.add(material)
        db.commit()

    monkeypatch.setattr(settings, "component_intelligence_enabled", True)
    generic = client.post(
        "/api/v1/component-intelligence/search",
        json={"requirement": "找一个 TFT display"},
    )
    assert generic.status_code == 200
    assert all(item["code"] != code for item in generic.json()["candidates"])

    exact = client.post(
        "/api/v1/component-intelligence/search",
        json={"requirement": f"查一下 {code}"},
    )
    assert exact.status_code == 200
    candidate = next(item for item in exact.json()["candidates"] if item["code"] == code)
    assert candidate["metadata_confidence"] == "low"
    assert candidate["technical_claims_allowed"] is False
    assert candidate["hard_constraint_matches"] == []
    assert "仅按 catalog identity" in exact.json()["engineering_caveat"]
