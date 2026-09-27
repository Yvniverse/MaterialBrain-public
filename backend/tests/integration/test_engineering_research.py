from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agent.service import WarehouseAgentService
from app.core.config import Settings
from app.models import AgentEpisode, InventoryLot, Location, Material, Role, User
from app.portfolio_demo import PortfolioDemoV2Seeder
from app.seed.defaults import seed_defaults
from app.services.engineering_research import EngineeringResearchService


def _seed(db: Session) -> User:
    seed_defaults(db)
    role = db.scalar(select(Role).where(Role.name == "系统管理员"))
    assert role is not None
    user = User(
        username="engineering_research_operator",
        full_name="Engineering Research Operator",
        password_hash="not-a-login-secret",
        role_id=role.id,
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    PortfolioDemoV2Seeder(db, user, Settings(portfolio_demo_seed_enabled=True)).seed()
    db.refresh(user)
    return user


def test_phase32_engineering_research_is_multi_step_read_only_and_traceable(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'engineering-research.db').as_posix()}")
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        before = {
            material.code: (material.quantity, material.reserved_quantity)
            for material in db.scalars(select(Material)).all()
        }
        response = WarehouseAgentService(
            db,
            user,
            "engineering-research-test",
            config=Settings(
                agent_enabled=True,
                agent_model_policy="deterministic",
                agent_engineering_research_enabled=True,
            ),
            enforce_configuration=False,
        ).query(
            "帮我设计一套 12V 转 3.3V、800mA 的电源方案。比较 Buck 和 LDO，"
            "检查现有物料、库存、库位、外围要求和数据手册依据，给出需要人工审核的方案。"
        )

        assert response.intent == "engineering_research"
        assert response.execution_mode == "deterministic"
        assert response.model_call_count == 0
        assert response.entities["engineering_research"]["plan"]["read_only"] is True
        assert response.entities["engineering_research"]["plan"]["write_scope"] == "none"
        assert response.entities["engineering_research"]["draft"]["automatic_write"] is False
        tools = [event.tool for event in response.tool_events]
        assert tools[0] == "plan_power_design"
        assert "get_inventory_availability" in tools
        assert "find_material_locations" in tools
        assert "search_datasheet_evidence" in tools
        assert "不会自动修改 BOM、库存、预留或 Picking 结算" in response.answer
        assert "6.96" in response.answer
        assert "人工审核" in response.answer

        after = {
            material.code: (material.quantity, material.reserved_quantity)
            for material in db.scalars(select(Material)).all()
        }
        assert after == before
        episode = db.scalar(
            select(AgentEpisode).where(AgentEpisode.request_id == "engineering-research-test")
        )
        assert episode is not None
        assert len(episode.steps) >= len(tools)
        assert all(step["replayable"] is True for step in episode.steps)


def test_phase333_exact_selected_topology_composite_request_keeps_grounded_research(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase333-composite.db').as_posix()}")
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        response = WarehouseAgentService(
            db,
            user,
            "phase333-composite",
            config=Settings(
                agent_enabled=True,
                agent_model_policy="deterministic",
                agent_engineering_research_enabled=True,
            ),
            enforce_configuration=False,
        ).query(
            "12V到3.3V、100mA，我准备采用12V→5V Buck→3.3V LDO。"
            "把各级损耗、工程BOM完整度和LM5164候选库存库位一起给我。"
        )

        entity = response.entities["engineering_research"]
        assert response.intent == "engineering_research"
        assert entity["requirements"]["topology_choice"] == "buck_ldo"
        assert entity["requirements"]["intermediate_voltage_v"] == "5"
        assert "0.17W" in response.answer
        assert entity["completeness"]
        assert any(
            candidate["mpn"].startswith("LM5164")
            and (candidate.get("inventory") or {}).get("available_quantity") is not None
            and (candidate.get("locations") or {}).get("locations")
            for candidate in entity["draft"]["buck"]["candidates"]
        )


def test_phase332_peripheral_only_lm5164_case_keeps_live_facts_separate_from_selection(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase332-peripheral-only.db').as_posix()}")
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        before = {
            material.code: (material.quantity, material.reserved_quantity)
            for material in db.scalars(select(Material)).all()
        }
        response = WarehouseAgentService(
            db,
            user,
            "phase332-peripheral-only",
            config=Settings(
                agent_enabled=True,
                agent_model_policy="deterministic",
                agent_engineering_research_enabled=True,
            ),
            enforce_configuration=False,
        ).query(
            "For the selected LM5164 Buck stage, show the grounded engineering BOM roles, "
            "available inventory/location candidates and datasheet evidence without auto-selecting "
            "missing peripherals."
        )

        assert response.intent == "engineering_research"
        assert response.execution_mode == "deterministic"
        assert response.model_call_count == 0
        research = response.entities["engineering_research"]
        assert research["read_only"] is True
        assert research["automatic_write"] is False
        buck_candidates = research["draft"]["buck"]["candidates"]
        lm5164 = next(item for item in buck_candidates if item["mpn"] == "LM5164DDAR")
        assert lm5164["inventory"]["available_quantity"] == "14.0000"
        assert any("D03" in item["full_path"] for item in lm5164["locations"]["locations"])
        assert "输入/输出电压和负载未提供" in lm5164["electrical_thermal_judgment"]
        assert "满足当前输入/输出/负载约束" not in lm5164["electrical_thermal_judgment"]
        assert research["citations"]
        roles = {item["role"] for item in research["peripheral_requirements"]}
        assert {"input capacitor", "inductor", "bootstrap capacitor"} <= roles
        bootstrap = next(
            item
            for item in research["peripheral_requirements"]
            if item["role"] == "bootstrap capacitor"
        )
        assert bootstrap["source_anchor"]
        assert bootstrap["selection_status"] != "out_of_stock"
        assert research["engineering_bom_draft"]["read_only"] is True
        assert research["engineering_bom_draft"]["automatic_write"] is False
        after = {
            material.code: (material.quantity, material.reserved_quantity)
            for material in db.scalars(select(Material)).all()
        }
        assert after == before


def test_phase3341_standalone_bootstrap_comparison_is_structured_and_unselected(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'phase3341-standalone-comparison.db').as_posix()}"
    )
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        before = {
            material.code: (material.quantity, material.reserved_quantity)
            for material in db.scalars(select(Material)).all()
        }
        response = WarehouseAgentService(
            db,
            user,
            "phase3341-standalone-comparison",
            config=Settings(
                agent_enabled=True,
                agent_model_policy="deterministic",
                agent_engineering_research_enabled=True,
            ),
            enforce_configuration=False,
        ).query(
            "如果有多颗满足这项自举要求的候选，先按规格、库存、库位和证据给我比较，不要替我自动选。"
        )

        assert response.intent == "engineering_research"
        research = response.entities["engineering_research"]
        assert research["read_only"] is True
        assert research["automatic_write"] is False
        bootstrap = next(
            row
            for row in research["peripheral_requirements"]
            if row["role"] == "bootstrap capacitor"
        )
        assert bootstrap["selected_material_id"] is None
        assert bootstrap["selection_status"] != "selected"
        assert all(
            candidate.get("selected_material_id") is None for candidate in bootstrap["candidates"]
        )
        context = research["active_selection_context"]
        assert context is None or context["selected_material_id"] is None
        after = {
            material.code: (material.quantity, material.reserved_quantity)
            for material in db.scalars(select(Material)).all()
        }
        assert after == before


def test_phase3341_production_bootstrap_match_builds_active_selection_context(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase3341-bootstrap-match.db').as_posix()}")
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        location = Location(
            code="P3341-CAP-01",
            name="Phase3341 capacitor bin",
            type="bin",
            full_path="Phase3341 / Capacitors / 01",
        )
        capacitor = Material(
            code="P3341-C-63-X7R",
            name="2.2nF 63V X7R bootstrap capacitor",
            mpn="P3341-CAP-2N2-63-X7R",
            specification="2.2nF 63V X7R",
            package="0603",
            manufacturer="Phase3341 fixture",
            quantity=5,
            reserved_quantity=0,
            attributes={
                "capacitance": "2.2nF",
                "rated_voltage": "63V",
                "dielectric": "X7R",
            },
            location=location,
            created_by_id=user.id,
        )
        db.add(capacitor)
        db.flush()
        db.add(InventoryLot(material_id=capacitor.id, location_id=location.id, quantity=5))
        db.commit()
        response = WarehouseAgentService(
            db,
            user,
            "phase3341-bootstrap-match",
            config=Settings(
                agent_enabled=True,
                agent_model_policy="deterministic",
                agent_engineering_research_enabled=True,
            ),
            enforce_configuration=False,
        ).query(
            "这套 Buck 继续按 LM5164。请按手册的自举电容要求，从现有物料里匹配，"
            "告诉我满足、信息不足和不满足的真实物料，并带库存和库位。"
        )

        assert response.intent == "engineering_research"
        research = response.entities["engineering_research"]
        bootstrap = next(
            row
            for row in research["peripheral_requirements"]
            if "bootstrap" in row["role"].casefold()
        )
        assert bootstrap["candidates"]
        assert bootstrap["selected_material_id"] is None
        assert research["active_selection_context"]["requirement_id"] == bootstrap["requirement_id"]
        assert research["active_selection_context"]["valid_candidate_ids"]
        assert "输入/输出电压和负载未提供" in response.answer


class _ControlledResearchNarrativeProvider:
    def __init__(self):
        self.calls = 0
        self.tools_seen: list[list[dict]] = []

    def chat(self, messages, tools=None, tool_choice="auto"):
        del messages
        self.calls += 1
        self.tools_seen.append(tools or [])
        assert tools == []
        assert tool_choice == "auto"
        return {
            "role": "assistant",
            "content": "候选、证据状态和可审阅草案已由服务端分别核验，精确外围值仍按页码补齐。",
            "_telemetry": {
                "provider": "controlled-research-provider",
                "model": "controlled-research-model",
                "finish_reason": "stop",
                "input_tokens": 40,
                "output_tokens": 18,
                "total_tokens": 58,
                "latency_ms": 1,
                "tool_call_count": 0,
                "attempts": 1,
                "retries": 0,
            },
        }


class _FailingResearchNarrativeProvider:
    model = "failing-research-model"

    def chat(self, messages, tools=None, tool_choice="auto"):
        del messages, tools, tool_choice
        raise RuntimeError("controlled provider failure")


class _InventoryWarningResearchNarrativeProvider:
    model = "inventory-warning-research-model"

    def chat(self, messages, tools=None, tool_choice="auto"):
        del messages, tools, tool_choice
        return {
            "role": "assistant",
            "content": "候选已找到；建议实物复核当前库存数量。",
            "_telemetry": {
                "provider": "controlled-research-provider",
                "model": self.model,
                "finish_reason": "stop",
                "input_tokens": 40,
                "output_tokens": 12,
                "total_tokens": 52,
                "latency_ms": 1,
                "tool_call_count": 0,
                "attempts": 1,
                "retries": 0,
            },
        }


class _ContradictoryCurrentResearchNarrativeProvider:
    model = "contradictory-current-research-model"

    def chat(self, messages, tools=None, tool_choice="auto"):
        del messages, tools, tool_choice
        return {
            "role": "assistant",
            "content": (
                "Buck 的整体效率不能排序；未提供本轮负载电流数据，所以不套用旧值。"
                "当前模拟支路电流未知，不能计算分轨损耗。"
            ),
            "_telemetry": {
                "provider": "controlled-research-provider",
                "model": self.model,
                "finish_reason": "stop",
                "input_tokens": 40,
                "output_tokens": 26,
                "total_tokens": 66,
                "latency_ms": 1,
                "tool_call_count": 0,
                "attempts": 1,
                "retries": 0,
            },
        }


def _research_service(db, user, request_id, *, provider=None, conversation=None):
    return WarehouseAgentService(
        db,
        user,
        request_id,
        provider=provider,
        config=Settings(
            agent_enabled=True,
            agent_model_policy="normal" if provider else "deterministic",
            agent_engineering_research_enabled=True,
            dashscope_enable_thinking=False,
        ),
        enforce_configuration=False,
        conversation_context=conversation,
    )


def test_long_engineering_requirement_keeps_datasheet_search_within_tool_contract(tmp_path):
    database_path = (tmp_path / "engineering-evidence-query-limit.db").as_posix()
    engine = create_engine(f"sqlite:///{database_path}")
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        message = (
            "请设计 12V 转 3.3V、100mA 的 MCU 电源，比较 Buck、Buck 加 LDO 和模拟分轨，"
            "检查 LM5164、TLV76133、TPS54560 的库存、库位、外围和数据手册页码。"
            + "请按当前物料对应的受管数据手册核实参数与页码。"
            * 24
        )
        assert len(message) > 500

        response = _research_service(db, user, "engineering-evidence-query-limit").query(message)

        evidence_events = [
            event for event in response.tool_events if event.tool == "search_datasheet_evidence"
        ]
        assert evidence_events
        assert all(event.status == "success" for event in evidence_events)
        assert all(event.error_code is None for event in evidence_events)


def test_phase321_engineering_research_preserves_four_round_context_and_separates_status_axes(
    tmp_path,
):
    engine = create_engine(f"sqlite:///{(tmp_path / 'engineering-research-rounds.db').as_posix()}")
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        service = _research_service(db, user, "engineering-research-rounds")
        first = service.query(
            "帮我设计一套 12V 转 3.3V、800mA 的电源方案。比较 Buck 和 LDO，"
            "检查现有物料、库存、库位、外围要求和数据手册依据，给出需要人工审核的方案。"
        )
        second = service.query(
            "LM5164 的外围具体要求是什么？请给出数据手册页码。",
            conversation_id=first.conversation_id,
        )
        third = service.query(
            "LM5164 的外围物料库存和库位够吗？",
            conversation_id=first.conversation_id,
        )
        fourth = service.query(
            "确认哪些是库存短缺，哪些只是证据缺口？",
            conversation_id=first.conversation_id,
        )

        assert first.entities["engineering_research"]["candidate_status"] == "found"
        assert first.entities["engineering_research"]["draft_status"] == "reviewable"
        assert "6.96" in first.answer
        assert "状态轴" in first.answer
        adaptive = first.entities["engineering_research"]["plan"]["adaptive"]
        assert adaptive["mode"] == "bounded_gap_planner"
        assert adaptive["max_additional_queries"] == 1
        assert adaptive["status"] == "executed"
        assert adaptive["candidate_id"] > 0
        assert adaptive["before"]["status"] in {"partial", "insufficient"}
        assert adaptive["after"]["status"] in {"partial", "sufficient", "insufficient"}
        assert second.entities["engineering_research"]["plan"]["continuation"] is True
        assert second.entities["engineering_research"]["plan"]["round"] == 2
        assert any(event.tool == "search_datasheet_evidence" for event in second.tool_events)
        second_rows = second.entities["engineering_research"]["peripheral_requirements"]
        bootstrap = next(row for row in second_rows if "bootstrap" in row["role"].casefold())
        assert bootstrap["value"] == "2.2 nF"
        assert bootstrap["rated_voltage_v"] == "50"
        assert bootstrap["dielectric"] == "X7R"
        assert bootstrap["source_anchor"]["page"] == 11
        cot = next(row for row in second_rows if "COT" in row["role"])
        assert cot["constraint_value"] == "at least 20 mV in-phase ripple"
        assert cot["source_anchor"]["page"] == 10
        assert third.entities["engineering_research"]["plan"]["round"] == 3
        assert "get_inventory_availability" in [event.tool for event in third.tool_events]
        assert "find_material_locations" in [event.tool for event in third.tool_events]
        third_entity = third.entities["engineering_research"]
        peripheral_rows = third_entity["peripheral_requirements"]
        assert peripheral_rows
        assert any("bootstrap" in row["role"].casefold() for row in peripheral_rows)
        assert any("COT" in row["role"] for row in peripheral_rows)
        peripheral_audit = third_entity["peripheral_tool_audit"]
        assert any(item["tool"] == "search_materials" for item in peripheral_audit)
        primary_ids = {
            candidate["material_id"]
            for branch in (third_entity["draft"]["buck"], third_entity["draft"]["ldo"])
            for candidate in branch["candidates"]
        }
        accessory_ids = {
            int(item["arguments"]["material_id"])
            for item in peripheral_audit
            if item["tool"] in {"get_inventory_availability", "find_material_locations"}
            and "material_id" in item["arguments"]
            and int(item["arguments"]["material_id"]) not in primary_ids
        }
        searched_accessory_ids = {
            int(item) for row in peripheral_rows for item in row.get("material_candidate_ids") or []
        }
        if searched_accessory_ids:
            assert accessory_ids == searched_accessory_ids
            assert any(
                item["tool"] == "get_inventory_availability"
                and int(item["arguments"]["material_id"]) in accessory_ids
                for item in peripheral_audit
            )
            assert any(
                item["tool"] == "find_material_locations"
                and int(item["arguments"]["material_id"]) in accessory_ids
                for item in peripheral_audit
            )
        assert "外围逐项结果" in third.answer
        assert fourth.entities["engineering_research"]["plan"]["round"] == 4
        assert "search_datasheet_evidence" in [event.tool for event in fourth.tool_events]
        fourth_entity = fourth.entities["engineering_research"]
        assert fourth_entity["focus_scope"] == "peripheral"
        assert fourth_entity["engineering_bom_draft"]["read_only"] is True
        assert fourth_entity["engineering_bom_draft"]["formal_product_bom_modified"] is False
        assert "外围逐项结果" in fourth.answer
        assert "不会自动修改 BOM、库存、预留或 Picking 结算" in fourth.answer


def test_power_followups_replan_current_topology_and_keep_inventory_fresh(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'research-power-followups.db').as_posix()}")
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        service = _research_service(db, user, "research-power-followups")
        first = service.query(
            "比较 12V→3.3V、总负载100mA 的 Buck 和 LDO 方案，核对现有物料、库存、库位和"
            "数据手册依据，形成需要人工审核的工程草案。"
        )
        first_entity = first.entities["engineering_research"]
        assert first_entity["requirements"]["load_current_a"] == "0.1"
        assert "0.87W" in first_entity["draft"]["conclusion"]
        assert "0.17W" in first_entity["draft"]["conclusion"]
        first_lm5164 = next(
            candidate
            for candidate in first_entity["draft"]["buck"]["candidates"]
            if candidate["mpn"].startswith("LM5164")
        )
        first_available = first_lm5164["inventory"]["available_quantity"]

        second = service.query(
            "把负载改成200mA，选择 Buck+LDO，重新计算后级损耗。",
            conversation_id=first.conversation_id,
        )
        second_entity = second.entities["engineering_research"]
        assert second_entity["plan"]["continuation"] is True
        assert second_entity["plan"]["round"] == 2
        assert second_entity["requirements"]["load_current_a"] == "0.2"
        second_buck_ldo = next(
            item for item in second_entity["draft"]["topologies"] if item["topology"] == "buck_ldo"
        )
        second_ldo = next(
            stage for stage in second_buck_ldo["stages"] if stage["topology"] == "ldo"
        )
        assert second_ldo["input_voltage_v"] == "5.0"
        assert second_ldo["load_current_a"] == "0.2"
        assert second_ldo["loss_w"] == "0.34"
        assert "0.34W" in second_entity["draft"]["conclusion"]
        assert "不是 12V" in second_entity["draft"]["conclusion"]

        third = service.query(
            "二级 LDO 输入改为4V，按当前负载重算损耗和压差。",
            conversation_id=first.conversation_id,
        )
        third_entity = third.entities["engineering_research"]
        assert third_entity["requirements"]["input_voltage_v"] == "12"
        assert third_entity["requirements"]["output_voltage_v"] == "3.3"
        assert third_entity["requirements"]["intermediate_voltage_v"] == "4"
        third_architecture = next(
            item for item in third_entity["draft"]["topologies"] if item["topology"] == "buck_ldo"
        )
        third_ldo = next(
            stage for stage in third_architecture["stages"] if stage["topology"] == "ldo"
        )
        assert third_ldo["input_voltage_v"] == "4"
        assert third_ldo["headroom_v"] == "0.7"
        assert third_ldo["loss_w"] == "0.14"

        fourth = service.query(
            "LM5164 库存和库位是多少？",
            conversation_id=first.conversation_id,
        )
        fourth_entity = fourth.entities["engineering_research"]
        fourth_lm5164 = next(
            candidate
            for candidate in fourth_entity["draft"]["buck"]["candidates"]
            if candidate["mpn"].startswith("LM5164")
        )
        assert fourth_lm5164["inventory"]["available_quantity"] == first_available
        assert fourth_lm5164["inventory"]["available_quantity"] != "0"
        assert "get_inventory_availability" in [event.tool for event in fourth.tool_events]

        separate = service.query(
            "另一个 12V→3.3V Buck/LDO 方案，请比较拓扑并核对数据手册，当前负载未提供。"
        )
        separate_requirements = separate.entities["engineering_research"]["requirements"]
        assert separate_requirements["load_current_a"] is None
        assert "未提供" in separate.entities["engineering_research"]["draft"]["conclusion"]
        assert "不代入" in separate.entities["engineering_research"]["draft"]["conclusion"]
        assert "6.96W" not in separate.entities["engineering_research"]["draft"]["conclusion"]
        switched = service.query(
            "改成 TPS54560 的外围查库存。",
            conversation_id=first.conversation_id,
        )
        switched_entity = switched.entities["engineering_research"]
        assert (
            switched_entity["selected_primary_material_id"]
            != fourth_entity["selected_primary_material_id"]
        )
        assert all(
            row["selected_primary_mpn"].startswith("TPS54560")
            for row in switched_entity["peripheral_requirements"]
        )
        assert not any(
            "bootstrap" in row["role"].casefold()
            and row["selected_primary_mpn"].startswith("LM5164")
            for row in switched_entity["peripheral_requirements"]
        )


def test_post_ldo_followup_overrides_prior_split_rail_selection_and_recomputes_current(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'research-post-ldo-followup.db').as_posix()}")
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        service = _research_service(db, user, "research-post-ldo-followup")
        first = service.query(
            "12V→3.3V、100mA MCU 加低噪声模拟传感器；比较直接 Buck、"
            "12V→5V Buck→3.3V LDO、数字和敏感模拟负载分轨，模拟电流未知；"
            "请核对 LM5164、TLV76133、TPS54560 的物料库存、库位和数据手册，形成只读工程草案。"
        )
        assert (
            first.entities["engineering_research"]["requirements"]["topology_choice"]
            == "split_rails"
        )

        second = service.query(
            "5V→3.3V LDO 带整个100mA，和12V直接LDO区别？",
            conversation_id=first.conversation_id,
        )
        second_data = second.entities["engineering_research"]
        assert second_data["requirements"]["topology_choice"] == "buck_ldo"
        second_architecture = next(
            item for item in second_data["draft"]["topologies"] if item["topology"] == "buck_ldo"
        )
        second_ldo = next(
            stage for stage in second_architecture["stages"] if stage["topology"] == "ldo"
        )
        assert second_ldo["input_voltage_v"] == "5"
        assert second_ldo["load_current_a"] == "0.1"
        assert second_ldo["loss_w"] == "0.17"

        third = service.query(
            "200mA 时两级供电的后级 LDO 输入 5V，损耗是多少？",
            conversation_id=first.conversation_id,
        )
        third_data = third.entities["engineering_research"]
        assert third_data["requirements"]["topology_choice"] == "buck_ldo"
        assert third_data["requirements"]["load_current_a"] == "0.2"
        assert third_data["requirements"]["input_voltage_v"] == "12"
        assert third_data["requirements"]["output_voltage_v"] == "3.3"
        assert third_data["requirements"]["intermediate_voltage_v"] == "5"
        assert "buck_ldo" in [item["topology"] for item in third_data["draft"]["topologies"]], (
            third_data["requirements"]
        )
        third_architecture = next(
            item for item in third_data["draft"]["topologies"] if item["topology"] == "buck_ldo"
        )
        third_ldo = next(
            stage for stage in third_architecture["stages"] if stage["topology"] == "ldo"
        )
        assert third_ldo["input_voltage_v"] == "5"
        assert third_ldo["load_current_a"] == "0.2"
        assert third_ldo["loss_w"] == "0.34"
        assert "0.34W" in third_data["draft"]["conclusion"]
        assert "不是 12V" in third_data["draft"]["conclusion"]


def test_unverified_buck_efficiency_ranking_is_rewritten_without_dropping_other_explanation():
    content = (
        "直接 Buck 方案效率最高且热预算最小。"
        "Buck+LDO 可在其有效频段衰减部分纹波，仍要结合负载和 PSRR 曲线确认。"
    )

    guarded, rewritten = EngineeringResearchService._guard_unverified_efficiency_ranking(content)

    assert rewritten is True
    assert "不能断言各方案的总体效率或热预算排序" in guarded
    assert "Buck 方案效率最高" not in guarded
    assert "Buck+LDO 可在其有效频段衰减部分纹波" in guarded


def test_narrative_rewrites_unprovided_current_claim_without_losing_unknown_branch():
    service = object.__new__(EngineeringResearchService)
    service.provider = _ContradictoryCurrentResearchNarrativeProvider()
    requirement_text = (
        "12V→3.3V，MCU负载100mA，另有低噪声模拟传感器但其电流未知。"
        "比较直接Buck、12V→5V Buck→3.3V LDO和数字与敏感模拟分轨。"
    )
    entity = {
        "requirements": {"load_current_a": "0.1"},
        "draft": {
            "candidate_status": "found",
            "evidence_status": "partial",
            "draft_status": "reviewable",
            "conclusion": "主负载为100mA，模拟支路电流未知。",
            "topologies": [],
            "focus_scope": "primary",
            "peripheral_requirements": [],
            "unknowns": ["模拟支路电流未知"],
            "citations": [],
        },
    }

    narrative, telemetry = service._narrative(
        requirement_text,
        entity,
        round_number=1,
        continuation=False,
    )

    assert "本轮已明确提供负载电流为100mA" in narrative
    assert "未提供本轮负载电流数据" not in narrative
    assert "当前模拟支路电流未知" in narrative
    assert telemetry[0]["semantic_rewrites"] == ["unprovided_current_claim"]


def test_narrative_keeps_missing_current_unknown_for_a_new_unscoped_request():
    content = "未提供本轮负载电流数据，因此不代入旧值。"

    guarded, rewritten = EngineeringResearchService._guard_unprovided_current_claim(
        content,
        "另一个新设计：12V→3.3V MCU，请比较 Buck 和 LDO，当前没有提供负载电流。",
    )

    assert rewritten is False
    assert guarded == content


def test_narrative_does_not_turn_an_unknown_analog_branch_into_zero_current():
    content = "模拟侧因电流未知，暂按 0A 建模。"

    guarded, rewritten = EngineeringResearchService._guard_unprovided_current_claim(
        content,
        "12V→3.3V MCU 总负载100mA，另有敏感模拟支路但该支路电流未知。",
    )

    assert rewritten is True
    assert "不能按 0A 代入" in guarded
    assert "该支路损耗保持未知" in guarded
    assert "暂按 0A 建模" not in guarded


def test_phase323_peripheral_fixture_reads_accessory_id_inventory_and_shortage(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'peripheral-fixture.db').as_posix()}")
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        location = Location(
            code="FIXTURE-CAP-01",
            name="Fixture capacitor bin",
            type="bin",
            full_path="Fixture / Capacitors / 01",
        )
        capacitor = Material(
            code="FIX-C-2N2-50-X7R",
            name="2.2nF 50V X7R bootstrap capacitor",
            mpn="FIX-CAP-2N2-50-X7R",
            specification="2.2nF 50V X7R",
            package="0603",
            manufacturer="Fixture",
            quantity=1,
            reserved_quantity=0,
            attributes={
                "capacitance": "2.2nF",
                "rated_voltage": "50V",
                "dielectric": "X7R",
            },
            location=location,
            created_by_id=user.id,
        )
        wrong_capacitor = Material(
            code="FIX-C-2U2-16-X5R",
            name="2.2uF 16V X5R near-match",
            mpn="FIX-CAP-2U2-16-X5R",
            specification="2.2uF 16V X5R",
            package="0603",
            manufacturer="Fixture",
            quantity=4,
            reserved_quantity=0,
            attributes={
                "capacitance": "2.2uF",
                "rated_voltage": "16V",
                "dielectric": "X5R",
            },
            location=location,
            created_by_id=user.id,
        )
        db.add(capacitor)
        db.add(wrong_capacitor)
        db.flush()
        db.add(InventoryLot(material_id=capacitor.id, location_id=location.id, quantity=1))
        db.add(
            InventoryLot(
                material_id=wrong_capacitor.id,
                location_id=location.id,
                quantity=4,
            )
        )
        db.commit()

        service = _research_service(db, user, "phase323-peripheral-fixture")
        first = service.query(
            "帮我设计一套 12V 转 3.3V、800mA 的电源方案。比较 Buck 和 LDO，"
            "检查现有物料、库存、库位、外围要求和数据手册依据，并选 LM5164。"
        )
        service.query(
            "LM5164 具体需要哪些外围？给出 PDF 页码。",
            conversation_id=first.conversation_id,
        )
        third = service.query(
            "LM5164 自举电容按一个方案两颗，查外围库存与库位。",
            conversation_id=first.conversation_id,
        )

        entity = third.entities["engineering_research"]
        bootstrap = next(
            row
            for row in entity["peripheral_requirements"]
            if "bootstrap" in row["role"].casefold()
        )
        assert {capacitor.id, wrong_capacitor.id}.issubset(set(bootstrap["material_candidate_ids"]))
        assert bootstrap["matched_material_id"] == capacitor.id
        assert bootstrap["available_quantity"] == "1"
        assert bootstrap["required_quantity"] == "2"
        assert bootstrap["shortage_quantity"] == "1"
        assert bootstrap["selection_status"] == "shortage"
        assert bootstrap["location"][0]["full_path"] == "Fixture / Capacitors / 01"
        assert any(
            item["tool"] == "get_inventory_availability"
            and item["arguments"]["material_id"] == capacitor.id
            for item in entity["peripheral_tool_audit"]
        )
        assert any(
            item["tool"] == "find_material_locations"
            and item["arguments"]["material_id"] == capacitor.id
            for item in entity["peripheral_tool_audit"]
        )
        assert any(
            item["tool"] == "get_inventory_availability"
            and item["arguments"]["material_id"] == wrong_capacitor.id
            for item in entity["peripheral_tool_audit"]
        )
        assert any(
            item["tool"] == "find_material_locations"
            and item["arguments"]["material_id"] == wrong_capacitor.id
            for item in entity["peripheral_tool_audit"]
        )
        wrong = next(
            item for item in bootstrap["candidates"] if item["material_id"] == wrong_capacitor.id
        )
        assert wrong["match_status"] == "mismatch"
        assert "短缺数量：1" in third.answer


def test_phase333_explicit_selection_is_conversation_scoped_and_reversible(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase333-selection.db').as_posix()}")
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        location = Location(
            code="P333-CAP-01",
            name="Phase333 capacitor bin",
            type="bin",
            full_path="Phase333 / Capacitors / 01",
        )
        candidates = [
            Material(
                code="P333-C-63-X7R",
                name="2.2nF 63V X7R bootstrap candidate",
                mpn="P333-CAP-2N2-63-X7R",
                specification="2.2nF 63V X7R",
                package="0603",
                manufacturer="Phase333",
                quantity=5,
                reserved_quantity=0,
                attributes={
                    "capacitance": "2.2nF",
                    "rated_voltage": "63V",
                    "dielectric": "X7R",
                },
                location=location,
                created_by_id=user.id,
            ),
            Material(
                code="P333-C-70-X7R",
                name="2.2nF 70V X7R bootstrap candidate",
                mpn="P333-CAP-2N2-70-X7R",
                specification="2.2nF 70V X7R",
                package="0603",
                manufacturer="Phase333",
                quantity=4,
                reserved_quantity=0,
                attributes={
                    "capacitance": "2.2nF",
                    "rated_voltage": "70V",
                    "dielectric": "X7R",
                },
                location=location,
                created_by_id=user.id,
            ),
        ]
        db.add_all(candidates)
        db.flush()
        db.add_all(
            [
                InventoryLot(material_id=item.id, location_id=location.id, quantity=item.quantity)
                for item in candidates
            ]
        )
        db.commit()

        service = _research_service(db, user, "phase333-selection")
        first = service.query(
            "帮我设计一套 12V 转 3.3V、800mA 的电源方案。比较 Buck 和 LDO，"
            "检查现有物料、库存、库位、外围要求和数据手册依据，并选 LM5164。"
        )
        service.query(
            "LM5164 具体需要哪些外围？给出 PDF 页码。",
            conversation_id=first.conversation_id,
        )
        matching = service.query(
            "这套 Buck 先按 LM5164 看。自举电容按手册要求帮我从现有物料里匹配，"
            "列出满足条件的候选、库存和库位。",
            conversation_id=first.conversation_id,
        )
        first_row = next(
            row
            for row in matching.entities["engineering_research"]["peripheral_requirements"]
            if "bootstrap" in row["role"].casefold()
        )
        assert first_row["selected_material_id"] is None
        assert first_row["match_summary"]["exact_or_compatible_candidates"] >= 2
        assert all(
            item["match_status"] in {"exact", "compatible"}
            for item in first_row["candidates"]
            if item["material_id"] in {candidate.id for candidate in candidates}
        )

        selected = service.query(
            "自举电容就用刚才满足条件、63V X7R 的那个候选作为这份工程草案的选择。",
            conversation_id=first.conversation_id,
        )
        selected_row = next(
            row
            for row in selected.entities["engineering_research"]["peripheral_requirements"]
            if "bootstrap" in row["role"].casefold()
        )
        assert selected_row["selected_material_id"] == candidates[0].id
        assert selected_row["selection_basis"] == "explicit_user"
        assert selected_row["selection_provenance"]["request_id"] == "phase333-selection"
        assert selected.model_call_count == 0
        assert (
            selected.entities["engineering_research"]["selection_action_result"][
                "selection_truth_source"
            ]
            == "server"
        )
        assert (
            selected.entities["engineering_research"]["selection_action_result"]["resolution"]
            == "selected"
        )
        assert (
            selected.entities["engineering_research"]["active_selection_context"][
                "selected_material_id"
            ]
            == candidates[0].id
        )
        assert (
            selected.entities["engineering_research"]["engineering_bom_draft"][
                "formal_product_bom_modified"
            ]
            is False
        )

        cleared = service.query(
            "刚才选的自举电容先取消，我想换另一个满足条件的候选。",
            conversation_id=first.conversation_id,
        )
        cleared_row = next(
            row
            for row in cleared.entities["engineering_research"]["peripheral_requirements"]
            if "bootstrap" in row["role"].casefold()
        )
        assert cleared_row["selected_material_id"] is None
        assert cleared.model_call_count == 0
        assert (
            cleared.entities["engineering_research"]["selection_action_result"]["resolution"]
            == "cleared"
        )
        assert (
            cleared.entities["engineering_research"]["selection_action_result"]["selection_action"]
            == "clear"
        )
        assert (
            cleared.entities["engineering_research_context"]["active_selection_context"][
                "selected_material_id"
            ]
            is None
        )

        changed = service.query(
            "就换成第二个。",
            conversation_id=first.conversation_id,
        )
        changed_row = next(
            row
            for row in changed.entities["engineering_research"]["peripheral_requirements"]
            if "bootstrap" in row["role"].casefold()
        )
        assert changed_row["selected_material_id"] == candidates[1].id
        assert changed_row["selection_basis"] == "explicit_user"
        assert changed.model_call_count == 0
        assert (
            changed.entities["engineering_research"]["selection_action_result"]["selection_action"]
            == "choose_ordinal"
        )
        assert "电源拓扑" not in changed.answer

        persisted = service.query(
            "请继续说明刚才选中的自举电容匹配理由和当前库存。",
            conversation_id=first.conversation_id,
        )
        persisted_row = next(
            row
            for row in persisted.entities["engineering_research"]["peripheral_requirements"]
            if "bootstrap" in row["role"].casefold()
        )
        assert persisted_row["selected_material_id"] == candidates[1].id
        assert persisted_row["selection_basis"] == "explicit_user"
        assert persisted_row["candidates"][0]["selected_material_id"] == candidates[1].id

        review = service.query(
            "再把这份草案还没定下来的项汇总一次。",
            conversation_id=first.conversation_id,
        )
        review_row = next(
            row
            for row in review.entities["engineering_research"]["peripheral_requirements"]
            if "bootstrap" in row["role"].casefold()
        )
        assert review_row["selected_material_id"] == candidates[1].id
        assert (
            review.entities["engineering_research"]["engineering_bom_draft"]["completeness"][
                "unresolved_roles"
            ]
            >= 1
        )


def test_phase3341_ordinal_no_second_is_typed_and_never_replans_topology():
    service = object.__new__(EngineeringResearchService)
    service.prior_context = {
        "active_selection_context": {
            "requirement_id": "bootstrap-capacitor",
            "role": "bootstrap capacitor",
        }
    }
    service.conversation_id = "phase3341-no-second"
    service.ctx = SimpleNamespace(request_id="phase3341-no-second")
    service._selection_action_result = None
    row = {
        "requirement_id": "bootstrap-capacitor",
        "role": "bootstrap capacitor",
        "selection_status": "candidate_found",
        "selected_material_id": None,
        "selection_basis": None,
        "selection_provenance": {},
        "selection_conflict": None,
        "candidates": [
            {
                "material_id": 84703,
                "code": "C84703",
                "mpn": "CL05B222KB5NNNC",
                "match_status": "exact",
            }
        ],
        "match_summary": {},
    }
    rows = service._apply_draft_selection([row], "就换成第二个。")
    assert rows[0]["selected_material_id"] is None
    assert rows[0]["selection_resolution"] == "ordinal_out_of_range"
    assert service._selection_action_result["resolution"] == "ordinal_out_of_range"
    assert "不切换电源拓扑" in service._selection_action_result["narrative"]


def test_phase321_engineering_research_provider_is_explanation_only_and_telemetry_is_recorded(
    tmp_path,
):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'engineering-research-provider.db').as_posix()}"
    )
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        provider = _ControlledResearchNarrativeProvider()
        response = _research_service(
            db,
            user,
            "engineering-research-provider",
            provider=provider,
        ).query("请比较 12V 转 3.3V、800mA 的电源方案 Buck 和 LDO，核对库存、库位和数据手册证据。")

        assert response.execution_mode == "llm_assisted"
        assert response.model_call_count == provider.calls == 1
        assert response.telemetry[0].input_tokens == 40
        assert provider.tools_seen == [[]]
        assert response.narrative in response.answer
        assert response.answer.count(response.narrative) == 1
        assert "model_explanation" not in response.entities["engineering_research"]
        assert response.entities["engineering_research"]["draft_status"] == "reviewable"
        episode = db.scalar(
            select(AgentEpisode).where(AgentEpisode.request_id == "engineering-research-provider")
        )
        assert episode is not None
        assert episode.telemetry[0]["model"] == "controlled-research-model"


def test_phase321_provider_inventory_warning_is_suppressed_but_grounded_answer_remains(
    tmp_path,
):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'engineering-research-provider-boundary.db').as_posix()}"
    )
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        response = _research_service(
            db,
            user,
            "engineering-research-provider-boundary",
            provider=_InventoryWarningResearchNarrativeProvider(),
        ).query("请比较 12V 转 3.3V、800mA 的电源方案 Buck 和 LDO，核对库存、库位和数据手册证据。")

        assert response.model_call_count == 1
        assert response.telemetry[0].status == "success"
        assert "6.96" in response.answer
        assert "实物复核" not in response.answer
        assert "实物复核" not in response.narrative
        assert "model_explanation" not in response.entities["engineering_research"]


def test_phase321_provider_failure_keeps_deterministic_answer_and_error_telemetry(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'engineering-research-provider-error.db').as_posix()}"
    )
    from app.core.database import Base

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = _seed(db)
        response = _research_service(
            db,
            user,
            "engineering-research-provider-error",
            provider=_FailingResearchNarrativeProvider(),
        ).query("请比较 12V 转 3.3V、800mA 的电源方案 Buck 和 LDO，核对库存、库位和数据手册证据。")

        assert response.execution_mode == "llm_assisted"
        assert response.model_call_count == 1
        assert response.telemetry[0].status == "error"
        assert response.telemetry[0].model == "failing-research-model"
        assert "6.96" in response.answer
        assert response.entities["engineering_research"]["draft_status"] == "reviewable"
