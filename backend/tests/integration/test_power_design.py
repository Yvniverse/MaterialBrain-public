from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agent.service import WarehouseAgentService
from app.agent.task_contract import classify_task_contract
from app.core.config import Settings
from app.core.database import Base
from app.models import Material, Role, User
from app.sample_data import SampleCableSeeder, SampleDataV2Seeder
from app.seed.defaults import seed_defaults
from app.services.power_design import PowerDesignService


def _seed_power_data(db: Session) -> User:
    seed_defaults(db)
    operator_role = db.scalar(select(Role).where(Role.name == "系统管理员"))
    operator = User(
        username="power_design_operator",
        full_name="Power Design Operator",
        password_hash="not-a-login-secret",
        role_id=operator_role.id,
        must_change_password=False,
    )
    db.add(operator)
    db.commit()
    SampleDataV2Seeder(
        db,
        operator,
        Settings(sample_data_seed_enabled=True),
    ).seed()
    SampleCableSeeder(
        db,
        operator,
        Settings(sample_cable_seed_enabled=True),
    ).seed()
    db.refresh(operator)
    return operator


def _branches(result: dict) -> dict[str, dict]:
    return {branch["topology"]: branch for branch in result["branches"]}


class _ControlledNarrativeProvider:
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
            "content": (
                "Buck 与 LDO 都可以作为候选；结合噪声目标、热余量和空间比较后，再按器件证据确认。"
            ),
            "_telemetry": {
                "provider": "controlled-test-provider",
                "model": "controlled-test-model",
                "finish_reason": "stop",
                "input_tokens": 20,
                "output_tokens": 10,
                "total_tokens": 30,
                "latency_ms": 1,
                "tool_call_count": 0,
                "attempts": 1,
                "retries": 0,
            },
        }


class _TruncatedPowerNarrativeProvider:
    def __init__(self):
        self.calls = 0
        self.max_tokens = 0
        self.messages = []

    def chat_with_max_tokens(self, messages, *, max_tokens, tools=None, tool_choice="auto"):
        self.calls += 1
        self.max_tokens = max_tokens
        self.messages = messages
        assert tools == []
        assert tool_choice == "auto"
        return {
            "role": "assistant",
            "content": "Buck+LDO 方案的后级 LDO 输入为 12V，100mA 时损耗为 0.87W。",
            "_telemetry": {
                "provider": "controlled-test-provider",
                "model": "truncated-test-model",
                "finish_reason": "length",
                "input_tokens": 120,
                "output_tokens": max_tokens,
                "total_tokens": 120 + max_tokens,
                "latency_ms": 1,
                "tool_call_count": 0,
                "attempts": 1,
                "retries": 0,
            },
        }


def test_power_design_is_topology_first_and_read_only(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-design.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        before = db.scalar(select(User).where(User.id == operator.id)).updated_at
        contract = classify_task_contract(
            "我需要一个5V转3.3V的降压芯片，以及其配套的物料，能在哪里找到？"
        )
        assert contract.entity_kind == "power"
        assert contract.requested_facts == {"power_design"}

        result = PowerDesignService(db, operator, "power-test").plan(
            "我需要一个5V转3.3V的降压芯片，以及其配套的物料，能在哪里找到？"
        )
        branches = _branches(result)
        assert result["workflow"] == "power_design"
        assert result["topology_first"] is True
        assert {"buck", "ldo"} == set(branches)
        assert any(item["mpn"] == "TPS54560DDAR" for item in branches["buck"]["candidates"])
        assert all(
            item["mpn"] in {"TPS7A7001DDA", "TLV76133DCYR"}
            for item in branches["ldo"]["candidates"]
        )
        assert result["missing_constraints"] == ["负载电流（或范围）"]
        assert result["read_only"] is True
        assert db.scalar(select(User).where(User.id == operator.id)).updated_at == before
        assert not db.new and not db.dirty and not db.deleted


def test_power_design_uses_deterministic_ldo_loss_and_reconciles_tps_minimum(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-loss.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        result = PowerDesignService(db, operator, "power-loss-test").plan(
            "请给我一套12V转3.3V的硬件设计方案，负载可能到800mA"
        )
        branches = _branches(result)
        ldo_candidates = branches["ldo"]["candidates"]
        assert [item["mpn"] for item in ldo_candidates] == ["TLV76133DCYR"]
        calculation = ldo_candidates[0]["calculations"][0]
        assert calculation["loss_w"] == Decimal("6.96")
        assert calculation["status"] == "calculated"
        buck = next(
            item for item in branches["buck"]["candidates"] if item["mpn"] == "TPS54560DDAR"
        )
        assert buck["evidence_reconciliation"][0]["local_value"] == "5.5"
        assert buck["evidence_reconciliation"][0]["official_value"] == "4.5"


def test_power_design_compares_post_buck_and_explicit_direct_ldo_at_one_load(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-ldo-comparison.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        query = "5V→3.3V LDO 带整个100mA，多少损耗？和12V直接LDO区别？"
        contract = classify_task_contract(query)
        result = PowerDesignService(db, operator, "power-ldo-comparison").plan(query)

        assert contract.requested_facts == {"power_design"}
        assert result["requirements"]["input_voltage_v"] == "12"
        assert result["requirements"]["intermediate_voltage_v"] == "5"
        assert result["requirements"]["output_voltage_v"] == "3.3"
        assert result["requirements"]["direct_ldo_comparison_input_voltage_v"] == "12"
        comparison = result["load_case_calculations"]
        assert len(comparison) == 1
        assert comparison[0]["load_current_a"] == Decimal("0.1")
        assert comparison[0]["direct_ldo_input_v"] == Decimal("12")
        assert comparison[0]["direct_ldo_loss_w"] == Decimal("0.87")
        assert comparison[0]["post_buck_intermediate_voltage_v"] == Decimal("5")
        assert comparison[0]["post_buck_ldo_loss_w"] == Decimal("0.17")
        architecture = next(item for item in result["topologies"] if item["topology"] == "buck_ldo")
        ldo_stage = next(stage for stage in architecture["stages"] if stage["topology"] == "ldo")
        assert ldo_stage["input_voltage_v"] == "5"
        assert Decimal(ldo_stage["loss_w"]) == Decimal("0.17")


def test_power_design_infers_direct_ldo_boundary_from_natural_language(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-natural-direct-ldo.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        daily = PowerDesignService(db, operator, "power-natural-direct-ldo-daily").plan(
            "12V输入、3.3V输出、100mA。只把关键损耗边界讲清楚："
            "如果直接用LDO是多少；如果先Buck到5V、再LDO到3.3V，后级LDO是多少。"
        )
        daily_case = daily["load_case_calculations"][0]
        assert daily["requirements"]["direct_ldo_comparison_input_voltage_v"] == "12"
        assert daily_case["direct_ldo_loss_w"] == Decimal("0.87")
        assert daily_case["post_buck_ldo_loss_w"] == Decimal("0.17")

        boundary = PowerDesignService(db, operator, "power-natural-direct-ldo-boundary").plan(
            "这是单独的高负载边界测试：12V转3.3V、800mA。比较直接LDO和"
            "12V→5V Buck→3.3V LDO的线性级损耗，并明确这不是日常默认负载。"
        )
        boundary_case = boundary["load_case_calculations"][0]
        assert boundary_case["direct_ldo_loss_w"] == Decimal("6.96")
        assert boundary_case["post_buck_ldo_loss_w"] == Decimal("1.36")


def test_power_followup_updates_current_only_within_its_conversation(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-followup-context.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        service = WarehouseAgentService(
            db,
            operator,
            "power-followup-context",
            config=Settings(
                agent_enabled=True,
                dashscope_api_key="",
                agent_deterministic_material_resolution_enabled=True,
            ),
            enforce_configuration=False,
        )
        first = service.query("12V→3.3V、100mA MCU，比较 Buck 与 Buck+LDO。")
        followup = service.query(
            "200mA 时两级后级 LDO 损耗？",
            conversation_id=first.conversation_id,
        )

        result = followup.entities["power_design"]
        assert result["requirements"]["input_voltage_v"] == "12"
        assert result["requirements"]["output_voltage_v"] == "3.3"
        assert result["requirements"]["load_current_a"] == "0.2"
        architecture = next(item for item in result["topologies"] if item["topology"] == "buck_ldo")
        ldo_stage = next(stage for stage in architecture["stages"] if stage["topology"] == "ldo")
        assert Decimal(ldo_stage["loss_w"]) == Decimal("0.34")
        assert "200.0mA" in followup.narrative
        assert "100mA" not in followup.narrative
        assert "800mA" not in followup.narrative

        fifty_ma = service.query(
            "再按50mA看一下两级方案。",
            conversation_id=followup.conversation_id,
        )
        fifty_result = fifty_ma.entities["power_design"]
        assert fifty_ma.intent == "plan_power_design"
        assert fifty_result["requirements"]["load_current_a"] == "0.05"
        fifty_architecture = next(
            item for item in fifty_result["topologies"] if item["topology"] == "buck_ldo"
        )
        fifty_ldo_stage = next(
            stage for stage in fifty_architecture["stages"] if stage["topology"] == "ldo"
        )
        assert Decimal(fifty_ldo_stage["loss_w"]) == Decimal("0.085")

        isolated = service.query("200mA 时两级后级 LDO 损耗？")
        assert "power_design" not in isolated.entities


def test_power_design_pwr02_arrow_input_uses_50ma_loss(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-pwr02.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        result = PowerDesignService(db, operator, "power-pwr02-test").plan(
            "请给我一套 12V→3.3V、50mA 的硬件方案，板子空间不大。"
        )
        branches = _branches(result)
        assert result["requirements"]["input_voltage_v"] == "12"
        assert result["requirements"]["output_voltage_v"] == "3.3"
        ldo = next(item for item in branches["ldo"]["candidates"] if item["mpn"] == "TLV76133DCYR")
        assert ldo["calculations"][0]["loss_w"] == Decimal("0.435")


def test_power_design_keeps_independent_current_cases_and_calculates_each(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-current-cases.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        query = "5V→3.3V 用 TLV76133，200mA 和 800mA 分别算。"
        contract = classify_task_contract(query)
        result = PowerDesignService(db, operator, "power-current-cases").plan(query)

        assert contract.entity_kind == "power"
        assert contract.requested_facts == {"power_design"}
        assert result["requirements"]["load_current_a"] == "0.2"
        assert result["requirements"]["load_current_cases_a"] == ["0.2", "0.8"]
        cases = result["load_case_calculations"]
        assert [item["load_current_a"] for item in cases] == [Decimal("0.2"), Decimal("0.8")]
        assert [item["direct_ldo_loss_w"] for item in cases] == [
            Decimal("0.34"),
            Decimal("1.36"),
        ]
        assert all(item["post_buck_ldo_loss_w"] is None for item in cases)
        architectures = {item["topology"]: item for item in result["topologies"]}
        assert "buck_ldo" not in architectures
        split_stages = architectures["split_rails"]["stages"]
        assert not any(
            stage["input_voltage_v"] == stage["output_voltage_v"] for stage in split_stages
        )


def test_power_followup_parses_full_buck_ldo_chain_and_ignores_historical_currents(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-staged-followup.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        query = (
            "将刚才 MCU 数字侧负载改为 200mA；方案指定 12V→5V Buck→3.3V LDO。"
            "重新计算后级 LDO 的损耗和需要复核的热条件，不要沿用 100mA 或历史 800mA。"
        )

        result = PowerDesignService(db, operator, "power-staged-followup").plan(query)
        requirements = result["requirements"]
        assert requirements["input_voltage_v"] == "12"
        assert requirements["output_voltage_v"] == "3.3"
        assert requirements["intermediate_voltage_v"] == "5"
        assert requirements["topology_choice"] == "buck_ldo"
        assert requirements["load_current_a"] == "0.2"
        assert requirements["load_current_cases_a"] == []

        architecture = next(item for item in result["topologies"] if item["topology"] == "buck_ldo")
        post_ldo = next(stage for stage in architecture["stages"] if stage["topology"] == "ldo")
        assert Decimal(post_ldo["input_voltage_v"]) == Decimal("5")
        assert Decimal(post_ldo["output_voltage_v"]) == Decimal("3.3")
        assert Decimal(post_ldo["load_current_a"]) == Decimal("0.2")
        assert Decimal(post_ldo["loss_w"]) == Decimal("0.34")
        assert post_ldo["thermal_screen"]["status"] != "unknown_load"


def test_split_rail_currents_are_allocations_not_separate_load_cases(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-split-current-cases.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        result = PowerDesignService(db, operator, "power-split-current-cases").plan(
            "本轮总负载 100mA，其中 MCU 数字支路 80mA、敏感模拟支路 20mA。"
            "比较数字与模拟分轨、12V→3.3V Buck 和 12V→5V Buck→3.3V LDO。"
        )

        requirements = result["requirements"]
        assert requirements["load_current_a"] == "0.1"
        assert requirements["load_current_cases_a"] == []
        assert requirements["digital_load_current_a"] == "0.08"
        assert requirements["analog_load_current_a"] == "0.02"


def test_power_architectures_compare_daily_loads_and_keep_800ma_as_boundary(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-architectures.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        service = PowerDesignService(db, operator, "power-architecture-values")
        expected = {
            50: (Decimal("0.435"), Decimal("0.085")),
            100: (Decimal("0.87"), Decimal("0.17")),
            200: (Decimal("1.74"), Decimal("0.34")),
            800: (Decimal("6.96"), Decimal("1.36")),
        }
        for milliamps, (direct_loss, second_stage_loss) in expected.items():
            result = service.plan(f"請比較 12V→3.3V、總負載{milliamps}mA 的 Buck+LDO 方案。")
            architectures = {item["topology"]: item for item in result["topologies"]}
            assert set(architectures) == {"direct_buck", "buck_ldo", "split_rails"}
            buck_ldo = architectures["buck_ldo"]
            ldo_stage = next(stage for stage in buck_ldo["stages"] if stage["topology"] == "ldo")
            assert ldo_stage["input_voltage_v"] == "5.0"
            assert ldo_stage["output_voltage_v"] == "3.3"
            assert ldo_stage["load_current_a"] == str(Decimal(milliamps) / Decimal("1000"))
            assert Decimal(ldo_stage["loss_w"]) == second_stage_loss
            assert Decimal(ldo_stage["ideal_efficiency"]) == Decimal("0.66")
            assert Decimal(ldo_stage["quiescent_current_a"]) == Decimal("0.00006")
            assert Decimal(ldo_stage["quiescent_input_power_w"]) == Decimal("0.000300")
            assert Decimal(
                ldo_stage["thermal_screen"]["estimated_delta_t_c"]
            ) == second_stage_loss * Decimal("95.4")
            assert ldo_stage["thermal_screen"]["status"] == "illustrative_reference_only"
            assert ldo_stage["thermal_screen"]["reference_device_mpn"] == "TLV76133DCYR"
            assert ldo_stage["thermal_screen"]["package"] == "SOT-223 (DCY), 4-pin"
            ldo_ref = ldo_stage["candidate_devices"][0]
            assert any(
                item["key"] == "psrr_electrical_table" and "IOUT=300mA" in item["conditions"]
                for item in ldo_ref["engineering_parameters"]
            )
            assert any(
                item["key"] == "dropout_at_1a" and "0.9–1.6" == item["value"]
                for item in ldo_ref["engineering_parameters"]
            )
            assert "不是板级结温预测" in ldo_stage["thermal_screen"]["limitations"]
            ldo_candidate = next(
                candidate
                for candidate in _branches(result)["ldo"]["candidates"]
                if candidate["mpn"] == "TLV76133DCYR"
            )
            assert Decimal(ldo_candidate["calculations"][0]["loss_w"]) == direct_loss
            assert result["rail_bom_draft"]["selected_topology"] == "buck_ldo"
            assert result["rail_bom_draft"]["read_only"] is True
            assert result["rail_bom_draft"]["automatic_write"] is False


def test_power_without_user_current_does_not_invent_load_or_thermal_estimate(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-no-current.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        result = PowerDesignService(db, operator, "power-no-current").plan(
            "12V→3.3V MCU 供电架构比较"
        )
        assert result["requirements"]["load_current_a"] is None
        buck_ldo = next(item for item in result["topologies"] if item["topology"] == "buck_ldo")
        ldo_stage = next(stage for stage in buck_ldo["stages"] if stage["topology"] == "ldo")
        assert ldo_stage["load_current_a"] is None
        assert ldo_stage["loss_w"] is None
        assert ldo_stage["thermal_screen"]["status"] == "unknown_load"


def test_split_rail_current_allocation_does_not_duplicate_total(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-split-rails.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        service = PowerDesignService(db, operator, "power-split-rails")
        result = service.plan(
            "12V→3.3V 系统总负载100mA，数字与敏感模拟电源分轨，模拟支路按50mA计算。"
        )
        split = next(item for item in result["topologies"] if item["topology"] == "split_rails")
        rails = {item["rail_id"]: item for item in split["rails"]}
        assert rails["analog-rail"]["load_current_a"] == "0.05"
        assert rails["digital-rail"]["load_current_a"] == "0.05"
        assert rails["digital-rail"]["current_basis"] == "derived_from_total"
        analog_ldo = next(
            stage for stage in split["stages"] if stage["stage_id"] == "split-analog-ldo"
        )
        assert Decimal(analog_ldo["loss_w"]) == Decimal("0.085")

        unknown = service.plan("12V→3.3V、总负载100mA，数字与敏感模拟电源分轨。")
        unknown_split = next(
            item for item in unknown["topologies"] if item["topology"] == "split_rails"
        )
        assert all(rail["load_current_a"] is None for rail in unknown_split["rails"])
        assert all(
            stage["loss_w"] is None
            for stage in unknown_split["stages"]
            if stage["topology"] == "ldo"
        )

        digital_only = service.plan(
            "本轮是全新对话。输入12V，MCU数字侧负载100mA；另有敏感模拟电源，"
            "但模拟支路电流和具体芯片还没给。先直接回答结论，再比较 "
            "12V→3.3V 直接Buck、12V→5V Buck→3.3V LDO、数字/敏感模拟分轨。"
        )
        digital_only_split = next(
            item for item in digital_only["topologies"] if item["topology"] == "split_rails"
        )
        digital_only_rails = {
            item["rail_id"]: item for item in digital_only_split["rails"]
        }
        assert digital_only_rails["digital-rail"]["load_current_a"] == "0.1"
        assert digital_only_rails["digital-rail"]["current_basis"] == "user_rail"
        assert digital_only_rails["analog-rail"]["load_current_a"] is None
        assert digital_only_rails["analog-rail"]["current_basis"] == "not_allocated"
        digital_only_analog_ldo = next(
            stage
            for stage in digital_only_split["stages"]
            if stage["stage_id"] == "split-analog-ldo"
        )
        assert digital_only_analog_ldo["load_current_a"] is None
        assert digital_only_analog_ldo["loss_w"] is None


def test_power_design_agent_path_is_deterministic_and_does_not_write_inventory(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-agent.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        before = {
            material.code: (
                material.quantity,
                material.reserved_quantity,
            )
            for material in db.scalars(select(Material)).all()
        }
        response = WarehouseAgentService(
            db,
            operator,
            "power-agent-test",
            config=Settings(
                agent_enabled=True,
                dashscope_api_key="",
                agent_deterministic_material_resolution_enabled=True,
            ),
            enforce_configuration=False,
        ).query("请给我一套12V转3.3V的硬件设计方案，负载可能到800mA")
        assert response.intent == "plan_power_design"
        assert response.model_call_count == 0
        assert response.entities["power_design"]["status"] == "supported"
        assert response.tool_events[0].tool == "plan_power_design"
        assert response.tool_events[0].status == "success"
        after = {
            material.code: (
                material.quantity,
                material.reserved_quantity,
            )
            for material in db.scalars(select(Material)).all()
        }
        assert after == before


def test_power_design_contract_precedes_optional_provider_narrative_and_stays_read_only(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-provider-contract.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        before = {
            material.code: (
                material.quantity,
                material.reserved_quantity,
                material.updated_at,
            )
            for material in db.scalars(select(Material)).all()
        }
        provider = _ControlledNarrativeProvider()
        response = WarehouseAgentService(
            db,
            operator,
            "power-provider-contract",
            provider=provider,
            config=Settings(
                agent_enabled=True,
                dashscope_enable_thinking=False,
                agent_deterministic_material_resolution_enabled=True,
            ),
            enforce_configuration=False,
        ).query("我需要一个 5V -> 3.3V 的电源，负载大概 100mA。现有库存能给我两套方案吗？")

        assert response.intent == "plan_power_design"
        assert response.entities["power_design"]["workflow"] == "power_design"
        assert [event.tool for event in response.tool_events][0] == "plan_power_design"
        assert response.model_call_count == provider.calls == 1
        assert provider.tools_seen == [[]]
        folded_answer = response.answer.casefold()
        assert "buck" in folded_answer and "ldo" in folded_answer
        assert response.narrative == (
            "Buck 与 LDO 都可以作为候选；结合噪声目标、热余量和空间比较后，再按器件证据确认。"
        )
        after = {
            material.code: (
                material.quantity,
                material.reserved_quantity,
                material.updated_at,
            )
            for material in db.scalars(select(Material)).all()
        }
        assert after == before


def test_power_design_discards_truncated_cross_topology_loss_and_uses_server_fallback(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'power-truncated-narrative.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        provider = _TruncatedPowerNarrativeProvider()
        response = WarehouseAgentService(
            db,
            operator,
            "power-truncated-narrative",
            provider=provider,
            config=Settings(
                agent_enabled=True,
                dashscope_enable_thinking=False,
                agent_deterministic_material_resolution_enabled=True,
            ),
            enforce_configuration=False,
        ).query("12V 转 3.3V，100mA 左右，想要纹波小一些，Buck 后接 LDO 是否有意义？")

        assert provider.calls == 1
        assert provider.max_tokens == 1024
        assert "available_quantity" not in str(provider.messages)
        assert "研发仓库" not in str(provider.messages)
        assert response.telemetry[0].finish_reason == "length"
        assert "12V，100mA 时损耗为 0.87W" not in response.narrative
        assert "5.0V" in response.narrative
        assert "0.17W" in response.narrative
        assert "16.218°C" in response.narrative
        assert "0.87W" not in response.narrative
        assert response.entities["power_design"]["selected_topology"] == "buck_ldo"


def test_phase332_smoke_expands_selected_buck_ldo_into_read_only_bom_requirements(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase332-smoke.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        result = PowerDesignService(db, operator, "phase332-smoke").plan(
            "选择 12V→5V Buck→3.3V LDO 架构，负载 100mA，展开只读多轨工程 BOM 草案。"
        )

        architecture = next(item for item in result["topologies"] if item["topology"] == "buck_ldo")
        assert result["rail_bom_draft"]["status"] != "not_supported"
        assert [stage["topology"] for stage in architecture["stages"]] == ["buck", "ldo"]
        ldo = architecture["stages"][1]
        assert Decimal(ldo["input_voltage_v"]) == Decimal("5")
        assert ldo["output_voltage_v"] == "3.3"
        assert ldo["load_current_a"] == "0.1"
        assert Decimal(ldo["loss_w"]) == Decimal("0.17")
        buck_roles = {
            item["role"]
            for item in architecture["stages"][0]["bom_requirements"]
        }
        assert {
            "primary regulator IC",
            "input capacitor",
            "inductor",
            "output capacitor",
            "feedback upper resistor",
            "feedback lower resistor",
            "bootstrap capacitor",
        } <= buck_roles
        assert {
            "primary LDO IC",
            "input capacitor",
            "output capacitor",
        } <= {item["role"] for item in ldo["bom_requirements"]}
        assert result["rail_bom_draft"]["read_only"] is True
        assert result["rail_bom_draft"]["automatic_write"] is False
        assert not db.new and not db.dirty and not db.deleted


def test_phase332_split_rail_bom_keeps_80_20_current_basis(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase332-split.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        result = PowerDesignService(db, operator, "phase332-split").plan(
            "12V→3.3V 总负载 100mA，数字侧 80mA，敏感模拟侧 20mA，展开分轨工程 BOM。"
        )
        split = next(item for item in result["topologies"] if item["topology"] == "split_rails")
        rails = {item["rail_id"]: item for item in split["rails"]}
        assert rails["digital-rail"]["load_current_a"] == "0.08"
        assert rails["analog-rail"]["load_current_a"] == "0.02"
        assert rails["digital-rail"]["current_basis"] == "user_rail"
        assert rails["analog-rail"]["current_basis"] == "user_rail"
        assert Decimal(rails["digital-rail"]["load_current_a"]) + Decimal(
            rails["analog-rail"]["load_current_a"]
        ) == Decimal("0.1")
        assert all(
            rail["load_current_a"] != "0.1"
            for rail in rails.values()
        )


def test_phase332_unknown_analog_is_needs_input_not_zero(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase332-unknown.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        result = PowerDesignService(db, operator, "phase332-unknown").plan(
            "12V→3.3V，数字侧 100mA，存在敏感模拟轨但没有提供模拟电流，展开分轨草案。"
        )
        split = next(item for item in result["topologies"] if item["topology"] == "split_rails")
        rails = {item["rail_id"]: item for item in split["rails"]}
        assert rails["digital-rail"]["load_current_a"] == "0.1"
        assert rails["analog-rail"]["load_current_a"] is None
        assert rails["analog-rail"]["current_basis"] == "not_allocated"
        assert rails["analog-rail"]["selection_status"] == "needs_input"
        analog_stage = next(
            stage
            for stage in split["stages"]
            if stage["stage_id"] == "split-analog-ldo"
        )
        assert analog_stage["load_current_a"] is None
        assert analog_stage["selection_status"] == "needs_input"
        assert all(item["status"] == "needs_input" for item in analog_stage["bom_requirements"])
        assert all(item["load_current_a"] != "0" for item in split["rails"])


def test_phase332_lm5164_bom_keeps_inventory_separate_from_selection(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase332-peripheral.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        operator = _seed_power_data(db)
        result = PowerDesignService(db, operator, "phase332-peripheral").plan(
            "12V→5V Buck→3.3V LDO、100mA，按 LM5164 选型上下文展开工程 BOM 外围、库存、库位和证据。"
        )
        architecture = next(item for item in result["topologies"] if item["topology"] == "buck_ldo")
        buck = architecture["stages"][0]
        primary = next(
            item for item in buck["bom_requirements"] if item["role"] == "primary regulator IC"
        )
        lm5164 = next(
            item for item in primary["candidates"] if item["mpn"] == "LM5164DDAR"
        )
        assert lm5164["inventory"]["available_quantity"] == "14.0000"
        assert any("D03" in item for item in lm5164["locations"])
        assert lm5164["selection_status"] == "candidate_found"
        assert primary["selected_material_id"] is None
        assert lm5164["citations"]
        bootstrap = next(
            item for item in buck["bom_requirements"] if item["role"] == "bootstrap capacitor"
        )
        assert bootstrap["exact_value"] == "2.2 nF, 50 V, X7R"
        assert bootstrap["status"] == "needs_selection"
        assert bootstrap["status"] != "out_of_stock"
        assert any(
            fact["role"] == "bootstrap capacitor" and fact["source_page"] == 11
            for fact in buck["engineering_facts"]
        )
        assert result["rail_bom_draft"]["automatic_write"] is False
