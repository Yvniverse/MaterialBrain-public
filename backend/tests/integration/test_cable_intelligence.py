from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agent.service import WarehouseAgentService
from app.agent.task_contract import classify_task_contract
from app.core.config import Settings
from app.core.database import Base
from app.models import Material, Role, User
from app.portfolio_demo import PortfolioCableSeeder, PortfolioDemoV2Seeder
from app.schemas.agent import CableSearchArgs
from app.seed.defaults import seed_defaults
from app.services.cable_intelligence import CableSearchService


def _seed(db: Session) -> None:
    seed_defaults(db)
    role = db.scalar(select(Role).where(Role.name == "系统管理员"))
    operator = User(
        username="cable_intelligence_operator",
        full_name="Cable Intelligence Operator",
        password_hash="not-a-login-secret",
        role_id=role.id,
        must_change_password=False,
    )
    db.add(operator)
    db.commit()
    PortfolioDemoV2Seeder(
        db,
        operator,
        Settings(portfolio_demo_seed_enabled=True),
    ).seed()
    PortfolioCableSeeder(
        db,
        operator,
        Settings(portfolio_cable_seed_enabled=True),
    ).seed()


def test_cable_search_uses_hard_constraints_soft_length_and_real_lots(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-search.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        result = CableSearchService(db).search(
            CableSearchArgs(query="0.8mm 10Pin 反向 10cm 端子线")
        )

        assert result["count"] == 1
        item = result["items"][0]
        assert item["code"] == "CBL-PF-00080"
        assert item["direction"] == "reverse"
        assert item["available_quantity"] == "10.0000"
        assert item["locations"][0]["full_path"].endswith("100抽线缆柜 / E16")
        assert item["location_truth_source"] == "InventoryLot"
        assert result["automatic_substitution"] is False


def test_cable_search_asks_before_same_reverse_orientation_choice(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-direction.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        result = CableSearchService(db).search(CableSearchArgs(query="0.5mm 22Pin 极细同轴线"))

        assert result["count"] >= 2
        assert result["needs_direction_disambiguation"] is True
        assert result["material_candidates"]["selected_material_id"] is None
        assert result["clarification"] == "触点方向需要同向(A型)还是反向(B型)？"


def test_cable_length_is_ranking_not_a_compatibility_claim(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-length.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        result = CableSearchService(db).search(CableSearchArgs(query="IPEX 28cm"))

        assert result["items"][0]["length_cm"] == "30.0"
        assert result["constraints"]["length_is_soft"] is True
        assert Decimal(result["items"][0]["available_quantity"]) > 0


def test_cable_stock_priority_followup_is_deterministic(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-stock-priority.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        low_stock = db.scalar(select(Material).where(Material.code == "CBL-PF-00005"))
        assert low_stock is not None
        low_stock.quantity = 0
        low_stock.reserved_quantity = 0
        db.commit()

        result = CableSearchService(db).search(
            CableSearchArgs(query="0.8mm 10Pin 15cm 有现货的放前面")
        )

        assert result["count"] >= 2
        assert Decimal(result["items"][0]["available_quantity"]) > 0


def test_a1251_cable_location_followup_stays_in_cable_scope(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-a1251-followup.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        user = db.scalar(select(User).where(User.username == "cable_intelligence_operator"))
        first = WarehouseAgentService(
            db,
            user,
            "cable-a1251-followup-1",
            provider=NeverProvider(),
            config=Settings(agent_enabled=True),
            enforce_configuration=False,
        ).query("A1251 的 2P 双头线还有吗？15cm 那种。")
        second = WarehouseAgentService(
            db,
            user,
            "cable-a1251-followup-2",
            provider=NeverProvider(),
            config=Settings(agent_enabled=True),
            enforce_configuration=False,
        ).query("在哪？", conversation_id=first.conversation_id)

        assert first.entities["cable_search"]["count"] >= 1
        assert "search_cables" in {event.tool for event in first.tool_events}
        assert "find_material_locations" in {event.tool for event in second.tool_events}
        assert second.entities["locations"]["locations"]


@pytest.mark.parametrize(
    "query",
    [
        "我需要间距为0.8，3pin的接口端子有吗，具体在哪里？",
        "0.8 间距、3P 的端子线，有现货吗？具体放哪？",
        "0.8 间距 3P，10 到 20cm 都可以，先把现货多的给我看。",
        "间距 0.8mm，3pin 的端子线",
        "pitch=0.8, 3P terminal cable",
    ],
)
def test_natural_08mm_3pin_variants_keep_exact_pitch_and_cable_scope(query):
    requirement = CableSearchService._requirement(CableSearchArgs(query=query))
    contract = classify_task_contract(query)

    assert requirement.connector_pitch_mm == Decimal("0.8")
    assert requirement.pin_count == 3
    assert contract.entity_kind == "cable"
    assert "cable_search" in contract.requested_facts


def test_natural_length_range_becomes_center_and_exact_tolerance():
    requirement = CableSearchService._requirement(
        CableSearchArgs(query="0.8 间距 3P，10 到 20cm 都可以，先把现货多的给我看。")
    )

    assert requirement.length_cm == Decimal("15")
    assert requirement.length_tolerance_cm == Decimal("5")


def test_cable_queries_get_a_deterministic_task_contract():
    contract = classify_task_contract("找 0.5mm 30Pin FFC 排线")
    assert contract.entity_kind == "cable"
    assert contract.requested_facts == {"cable_search"}


def test_exact_cable_identifier_routes_to_rich_cable_result(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-identifier.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        result = CableSearchService(db).search(CableSearchArgs(query="帮我找 CBL-PF-00042"))

        assert result["result_state"] == "exact_match"
        assert result["count"] == 1
        assert result["items"][0]["available_quantity"]
        assert result["items"][0]["locations"]

    contract = classify_task_contract("帮我找 PORTFOLIO-CBL-PF-00042")
    assert contract.entity_kind == "cable"
    assert contract.requested_facts == {"cable_search"}


def test_intent_scope_precedence_routes_relations_and_scoped_cable_alternates():
    relation = classify_task_contract(
        "TCAN1044 和 MCP2562FD 既然 similar_to 已经验证了，那我是不是可以直接换？"
    )
    assert relation.entity_kind == "material"
    assert "component_relations" in relation.requested_facts

    alternate = classify_task_contract(
        "相机那根 15cm 的线不够了，仓库不是还有个长 3cm 的备选吗？先顶上行不行？"
    )
    assert alternate.entity_kind == "product"
    assert "product_alternates" in alternate.requested_facts


def test_purchase_quantity_question_returns_policy_denial_without_inventory_guess(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-purchase-policy.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        user = db.scalar(select(User).where(User.username == "cable_intelligence_operator"))
        result = WarehouseAgentService(
            db,
            user,
            "cable-purchase-policy",
            provider=NeverProvider(),
            config=Settings(agent_enabled=True),
            enforce_configuration=False,
        ).query("订单里买了 30 条，所以当前库存就是 30 吗？")

        assert "不是" in result.answer
        assert "不能作为当前库存" in result.answer
        assert [event.tool for event in result.tool_events] == ["search_cables"]


def test_specific_hc_catalog_identifier_is_not_collapsed_to_family(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-specific-hc.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        result = CableSearchService(db).search(CableSearchArgs(query="帮我找 HC-0.8-7PWT"))

        assert result["result_state"] == "exact_match"
        assert result["count"] == 3
        assert {item["connector_a"] for item in result["items"]} == {"HC-0.8-7PWT"}
        assert all(item["name"].startswith("HC-0.8-7PWT") for item in result["items"])
        assert {item["pin_count"] for item in result["items"]} == {7}
        assert {item["length_cm"] for item in result["items"]} == {"10.0", "20.0", "30.0"}


def test_missing_specific_hc_catalog_identifier_uses_near_match_not_false_exact(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-specific-hc-missing.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        result = CableSearchService(db).search(CableSearchArgs(query="帮我找 HC-0.8-99PWT"))

        assert result["result_state"] in {"near_match", "no_match"}
        assert all(item["mpn"] != "HC-0.8-99PWT" for item in result["items"])
        if result["items"]:
            assert all(item["match_state"] == "near_match" for item in result["items"])
            assert all(item.get("differences") for item in result["items"])


class NeverProvider:
    def chat(self, messages, tools=None, tool_choice="auto"):
        raise AssertionError("deterministic cable turns must not call an LLM")


def test_cable_multiturn_retains_structured_constraints_and_changes_only_length(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-context.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        user = db.scalar(select(User).where(User.username == "cable_intelligence_operator"))
        config = Settings(agent_enabled=True)

        first = WarehouseAgentService(
            db,
            user,
            "cable-context-1",
            provider=NeverProvider(),
            config=config,
            enforce_configuration=False,
        ).query("找 0.8mm 4Pin 30cm 的线")
        second = WarehouseAgentService(
            db,
            user,
            "cable-context-2",
            provider=NeverProvider(),
            config=config,
            enforce_configuration=False,
        ).query("20cm 的呢？", conversation_id=first.conversation_id)

        constraints = second.entities["cable_search"]["constraints"]
        assert constraints["connector_pitch_mm"] == "0.8"
        assert constraints["pin_count"] == 4
        assert constraints["length_cm"] == "20"
        assert second.entities["cable_search"]["count"] >= 2

        third = WarehouseAgentService(
            db,
            user,
            "cable-context-3",
            provider=NeverProvider(),
            config=config,
            enforce_configuration=False,
        ).query("第二条在哪？", conversation_id=first.conversation_id)
        tools = {event.tool for event in third.tool_events}
        assert {"search_cables", "find_material_locations"}.issubset(tools)
        assert third.entities["locations"]["locations"]


def test_s28_direction_clarification_merges_server_owned_cable_slots(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-s28.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        user = db.scalar(select(User).where(User.username == "cable_intelligence_operator"))
        config = Settings(agent_enabled=True)

        first = WarehouseAgentService(
            db,
            user,
            "cable-s28-1",
            provider=NeverProvider(),
            config=config,
            enforce_configuration=False,
        ).query("我想找一根给相机用的 0.5mm 30P 排线，15cm 左右。")
        assert first.entities["cable_search"]["needs_direction_disambiguation"] is True
        assert first.entities["cable_search"]["result_state"] == "awaiting_clarification"

        second = WarehouseAgentService(
            db,
            user,
            "cable-s28-2",
            provider=NeverProvider(),
            config=config,
            enforce_configuration=False,
        ).query("反向的。", conversation_id=first.conversation_id)

        constraints = second.entities["cable_search"]["constraints"]
        assert constraints["cable_kind"] == "flat_flex"
        assert constraints["connector_pitch_mm"] == "0.5"
        assert constraints["pin_count"] == 30
        assert constraints["length_cm"] == "15"
        assert constraints["direction"] == "reverse"
        assert [event.tool for event in second.tool_events] == ["search_cables"]
        assert second.entities["cable_search"]["result_state"] in {
            "exact_match",
            "near_match",
            "no_match",
        }
        assert not (
            second.entities["cable_search"]["result_state"] != "no_match"
            and not second.entities["cable_search"]["items"]
        )
        assert "pitch" not in second.answer.casefold()
        assert "pin count" not in second.answer.casefold()


def test_cable_pin_correction_updates_only_pin_slot(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-correction.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        user = db.scalar(select(User).where(User.username == "cable_intelligence_operator"))
        config = Settings(agent_enabled=True)
        first = WarehouseAgentService(
            db,
            user,
            "correction-1",
            provider=NeverProvider(),
            config=config,
            enforce_configuration=False,
        ).query("找 0.5mm 30P 15cm 的相机排线。")
        second = WarehouseAgentService(
            db,
            user,
            "correction-2",
            provider=NeverProvider(),
            config=config,
            enforce_configuration=False,
        ).query("反向。", conversation_id=first.conversation_id)
        third = WarehouseAgentService(
            db,
            user,
            "correction-3",
            provider=NeverProvider(),
            config=config,
            enforce_configuration=False,
        ).query("等等，是 20P，我记错了。", conversation_id=second.conversation_id)
        constraints = third.entities["cable_search"]["constraints"]
        assert constraints["connector_pitch_mm"] == "0.5"
        assert constraints["pin_count"] == 20
        assert constraints["length_cm"] == "15"
        assert constraints["direction"] == "reverse"


def test_new_low_stock_task_does_not_reuse_prior_cable_card(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'cable-task-isolation.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        _seed(db)
        user = db.scalar(select(User).where(User.username == "cable_intelligence_operator"))
        config = Settings(agent_enabled=True)
        first = WarehouseAgentService(
            db,
            user,
            "cable-isolation-1",
            provider=NeverProvider(),
            config=config,
            enforce_configuration=False,
        ).query("帮我找 CBL-PF-00042")
        second = WarehouseAgentService(
            db,
            user,
            "cable-isolation-2",
            provider=NeverProvider(),
            config=config,
            enforce_configuration=False,
        ).query("哪些物料低于安全库存？", conversation_id=first.conversation_id)

        assert "low_stock" in second.entities
        assert "cable_search" not in second.entities
        assert "material_candidates" not in second.entities
