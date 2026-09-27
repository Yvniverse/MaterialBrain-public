from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.agent.conversation import ConversationContextService
from app.agent.service import WarehouseAgentService
from app.core.config import Settings
from app.core.database import SessionLocal
from app.core.exceptions import BusinessError
from app.models import (
    AgentConversationContext,
    Location,
    Material,
    Product,
    ProductBomItem,
    ProductRevision,
    User,
)


class ScriptedProvider:
    def __init__(self, responses: list[dict]):
        self.responses = iter(responses)
        self.calls = 0

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.calls += 1
        return next(self.responses)


class NeverProvider:
    def chat(self, messages, tools=None, tool_choice="auto"):
        raise AssertionError("This turn must not call the model")


def _tool_call(name: str, arguments: str) -> dict:
    return {
        "id": f"conversation-{name}",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _service(db, user, provider, request_id="conversation-test"):
    return WarehouseAgentService(
        db,
        user,
        request_id,
        provider=provider,
        config=Settings(
            agent_max_tool_rounds=5,
            agent_deterministic_material_resolution_enabled=True,
        ),
        enforce_configuration=False,
    )


def test_exact_material_followup_reuses_server_context_without_model(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        material = Material(
            code="CTX-ENC-AS5047P",
            name="Context AS5047P",
            mpn="AS5047P-CTX",
            package="TSSOP14",
        )
        location = Location(
            code="CTX-ENC-BIN",
            name="Context encoder bin",
            type="bin",
            full_path="研发仓库 / 编码器区 / E01",
        )
        db.add_all([material, location])
        db.flush()
        material.location_id = location.id
        db.commit()

        provider = ScriptedProvider(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("search_materials", '{"query":"AS5047P-CTX"}')],
                }
            ]
        )
        first = _service(db, user, provider).query("帮我找 AS5047P-CTX")
        second = _service(db, user, NeverProvider()).query(
            "它在哪？",
            conversation_id=first.conversation_id,
        )

        assert second.conversation_id == first.conversation_id
        assert [event.tool for event in second.tool_events] == ["find_material_locations"]
        assert second.entities["material_candidates"]["selected_material_id"] == material.id
        context = db.get(AgentConversationContext, first.conversation_id)
        assert context.selected_material_id == material.id
        assert context.context_version == 2
        assert provider.calls == 0
        assert first.execution_mode == "deterministic"
        assert first.model_call_count == 0
        assert second.execution_mode == "deterministic"
        assert second.model_call_count == 0


def test_pronoun_without_context_clarifies_without_tool_or_model(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        response = _service(db, user, NeverProvider()).query("它还有多少？")

        assert "具体物料" in response.answer
        assert response.intent == "clarify_material"
        assert response.tool_events == []
        context = db.get(AgentConversationContext, response.conversation_id)
        assert context.selected_material_id is None


def test_unsupported_inventory_write_is_refused_without_tools(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        response = _service(db, user, NeverProvider()).query("把这个料库存直接改成 100。")

        assert response.intent == "unsupported_write"
        assert "不能直接修改库存" in response.answer
        assert response.tool_events == []
        assert response.model_call_count == 0


def test_product_build_followup_reuses_revision_and_recomputes_without_model(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        material = Material(
            code="CTX-PRODUCT-BUILD-MAT",
            name="Product build material",
            quantity=Decimal("3"),
            safety_stock=Decimal("1"),
        )
        product = Product(
            code="CTX-PROD-ATLAS",
            name="CTX Atlas Product",
        )
        db.add_all([material, product])
        db.flush()
        revision_r1 = ProductRevision(
            product_id=product.id,
            revision="EVT-R1",
            status="released",
            is_default=False,
        )
        revision_r2 = ProductRevision(
            product_id=product.id,
            revision="EVT-R2",
            status="released",
            is_default=True,
        )
        db.add_all([revision_r1, revision_r2])
        db.flush()
        db.add_all(
            [
                ProductBomItem(
                    product_revision_id=revision_r1.id,
                    material_id=material.id,
                    quantity_per_unit=Decimal("1"),
                ),
                ProductBomItem(
                    product_revision_id=revision_r2.id,
                    material_id=material.id,
                    quantity_per_unit=Decimal("1"),
                ),
            ]
        )
        db.commit()

        first = _service(db, user, NeverProvider()).query("找 CTX Atlas 产品")
        second = _service(db, user, NeverProvider()).query(
            "做 3 台够不够？",
            conversation_id=first.conversation_id,
        )
        third = _service(db, user, NeverProvider()).query(
            "那 4 台呢？",
            conversation_id=first.conversation_id,
        )

        assert [event.tool for event in first.tool_events] == ["search_products"]
        assert [event.tool for event in second.tool_events] == ["analyze_product_build_readiness"]
        assert second.entities["build_readiness"]["sufficient"] is True
        assert second.entities["build_readiness"]["build_quantity"] == 3
        assert third.entities["build_readiness"]["sufficient"] is False
        assert third.entities["build_readiness"]["build_quantity"] == 4
        assert Decimal(third.entities["build_readiness"]["items"][0]["shortage"]) == 1
        context = db.get(AgentConversationContext, first.conversation_id)
        assert context.selected_product_id == product.id
        assert context.selected_product_revision_id == revision_r2.id

        reset = _service(db, user, NeverProvider()).query("那 2 台呢？")
        assert reset.intent == "clarify_product"
        assert reset.tool_events == []
        reset_context = db.get(AgentConversationContext, reset.conversation_id)
        assert reset_context.selected_product_id is None
        assert reset_context.selected_product_revision_id is None

        selected_r1 = _service(db, user, NeverProvider()).query(
            "EVT-R1",
            conversation_id=first.conversation_id,
        )
        assert selected_r1.intent == "select_product_revision"
        assert (
            db.get(AgentConversationContext, first.conversation_id).selected_product_revision_id
            == revision_r1.id
        )


def test_rb_project_code_is_an_explicit_project_switch():
    assert ConversationContextService._looks_like_project("RB-TOF-EVT 缺什么料？")


def test_can_alternate_without_referent_returns_minimal_business_clarification(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        response = _service(db, user, NeverProvider()).query(
            "这个 CAN 芯片没货了，有没有能替它的？"
        )

        assert response.intent == "clarify_alternate_scope"
        assert "具体的 CAN 芯片型号" in response.answer
        assert "产品或版本" in response.answer
        assert response.tool_events == []
        assert response.entities == {}


def test_generic_product_alternate_requires_product_revision_bom_scope(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        response = _service(db, user, NeverProvider()).query(
            "主料缺了，帮我把已经批准的产品备选和只是全局相似/已验证关系的候选分开，"
            "我只想评审，不要自动换料。"
        )

        assert response.intent == "clarify_alternate_scope"
        assert "具体产品、版本或 BOM 位" in response.answer
        assert response.tool_events == []
        assert response.entities == {}


def test_fresh_generic_product_build_requires_product_revision_scope(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        response = _service(db, user, NeverProvider()).query(
            "我准备做 5 台这版产品。请用工程师能直接行动的方式总结：哪些料够、哪些缺、"
            "哪些虽然账面有但库位不完整，以及我下一步应该先处理什么。"
        )

        assert response.intent == "clarify_product"
        assert "具体产品" in response.answer
        assert "版本" in response.answer
        assert "项目编号" not in response.answer
        assert response.tool_events == []
        assert response.entities == {}
        assert response.model_call_count == 0


def test_cable_alternate_without_product_scope_asks_for_product_and_never_approves(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        first = _service(db, user, NeverProvider()).query(
            "相机那根 15cm 的线不够了，仓库不是还有个长 3cm 的备选吗？先顶上行不行？"
        )
        second = _service(db, user, NeverProvider()).query(
            "我说的是当前产品 BOM 里那根相机线。",
            conversation_id=first.conversation_id,
        )

        assert first.intent == "clarify_alternate_scope"
        assert "不能据此认定" in first.answer
        assert first.tool_events == []
        assert second.intent == "clarify_product"
        assert "具体产品编号和版本" in second.answer
        assert second.tool_events == []
        assert "已批准替换" not in first.answer + second.answer


def test_known_product_name_keeps_alternate_product_bom_scoped(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        product = Product(code="PROD-DEXGRIP", name="灵巧夹爪")
        material = Material(
            code="CTX-CAN-TCAN1044",
            name="Context CAN transceiver",
            mpn="TCAN1044",
        )
        db.add_all([product, material])
        db.commit()

        manager = ConversationContextService(db, user, Settings())
        prepared = manager.prepare(
            manager.open(None),
            "DexGrip 的 TCAN1044 有已批准备选吗？",
        )

        assert prepared.contract.entity_kind == "product"
        assert prepared.contract.requested_facts == {
            "product_bom",
            "product_alternates",
        }
        assert prepared.explicit_product_switch is True


def test_explicit_build_task_does_not_expose_prior_material_entity(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        material = Material(
            code="CTX-ISOLATION-MAT-1",
            name="Isolation material",
            mpn="CTXISO-MAT-1",
            quantity=Decimal("10"),
        )
        location = Location(
            code="CTX-ISO-BIN",
            name="Isolation bin",
            type="bin",
            full_path="研发仓库 / 隔离测试 / I01",
        )
        product = Product(code="PROD-CTX-ISOLATION", name="Isolation Product")
        db.add_all([material, location, product])
        db.flush()
        material.location_id = location.id
        revision = ProductRevision(
            product_id=product.id,
            revision="EVT-R1",
            status="released",
            is_default=True,
        )
        db.add(revision)
        db.flush()
        db.add(
            ProductBomItem(
                product_revision_id=revision.id,
                material_id=material.id,
                quantity_per_unit=Decimal("1"),
            )
        )
        db.commit()

        first = _service(db, user, NeverProvider()).query("CTXISO-MAT-1 在哪里？")
        second = _service(db, user, NeverProvider()).query(
            "PROD-CTX-ISOLATION 做 2 台够不够？",
            conversation_id=first.conversation_id,
        )

        assert "build_readiness" in second.entities
        assert "material_candidates" not in second.entities
        assert second.entities["build_readiness"]["product"]["id"] == product.id


def test_candidate_selection_is_limited_to_pending_server_candidates(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        candidates = [
            Material(
                code="CTX-F405-RGT6",
                name="Context STM32F405",
                mpn="CTXSTM32F405RGT6",
                package="LQFP64",
            ),
            Material(
                code="CTX-F405-VGT6",
                name="Context STM32F405",
                mpn="CTXSTM32F405VGT6",
                package="LQFP100",
            ),
        ]
        db.add_all(candidates)
        db.commit()
        provider = ScriptedProvider(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("search_materials", '{"query":"CTXSTM32F405"}')],
                }
            ]
        )

        first = _service(db, user, provider).query("帮我找 CTXSTM32F405")
        context = db.get(AgentConversationContext, first.conversation_id)
        assert set(context.material_candidate_ids) == {item.id for item in candidates}
        assert context.pending_disambiguation["kind"] == "material"

        invalid = _service(db, user, NeverProvider()).query(
            "完全不存在的那个",
            conversation_id=first.conversation_id,
        )
        assert "请选择" in invalid.answer
        assert db.get(AgentConversationContext, first.conversation_id).selected_material_id is None

        selected = _service(db, user, NeverProvider()).query(
            "VGT6 那个",
            conversation_id=first.conversation_id,
        )
        assert selected.intent == "select_material"
        context = db.get(AgentConversationContext, first.conversation_id)
        assert context.selected_material_id == candidates[1].id
        assert context.material_candidate_ids == []
        assert context.pending_disambiguation == {}


def test_relation_review_followup_preserves_evidence_search_intent(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        materials = [
            Material(
                code="CTX-EVIDENCE-CAN-A",
                name="Context evidence CAN A",
                mpn="CTX-CAN-A",
            ),
            Material(
                code="CTX-EVIDENCE-CAN-B",
                name="Context evidence CAN B",
                mpn="CTX-CAN-B",
            ),
        ]
        db.add_all(materials)
        db.commit()

        manager = ConversationContextService(db, user, Settings())
        created = manager.open(None)
        context = db.get(AgentConversationContext, created.id)
        candidate_ids = [item.id for item in materials]
        context.material_candidate_ids = candidate_ids
        context.pending_disambiguation = {
            "kind": "material",
            "candidate_ids": candidate_ids,
        }
        context.last_requested_facts = ["component_relations"]
        db.commit()

        prepared = manager.prepare(
            manager.open(created.id),
            "用 datasheet 帮我自动批准这个替代关系",
        )

        assert prepared.contract.requested_facts == {
            "component_relations",
            "engineering_evidence",
        }
        assert "component_evidence_comparison" not in (prepared.contract.requested_facts)
        assert {item["id"] for item in prepared.entities["material_candidates"]["items"]} == set(
            candidate_ids
        )


def test_relation_difference_followup_preserves_pair_and_adds_evidence_comparison(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        materials = [
            Material(code="CTX-DIFF-CAN-A", name="Context CAN A", mpn="CTX-DIFF-A"),
            Material(code="CTX-DIFF-CAN-B", name="Context CAN B", mpn="CTX-DIFF-B"),
        ]
        db.add_all(materials)
        db.commit()

        manager = ConversationContextService(db, user, Settings())
        created = manager.open(None)
        context = db.get(AgentConversationContext, created.id)
        candidate_ids = [item.id for item in materials]
        context.material_candidate_ids = candidate_ids
        context.pending_disambiguation = {
            "kind": "material",
            "candidate_ids": candidate_ids,
        }
        context.last_requested_facts = ["component_relations"]
        db.commit()

        prepared = manager.prepare(manager.open(created.id), "具体差在哪？")

        assert prepared.contract.requested_facts == {
            "component_relations",
            "component_evidence_comparison",
        }
        assert prepared.contract.requires_material_resolution is False
        assert {item["id"] for item in prepared.entities["material_candidates"]["items"]} == set(
            candidate_ids
        )


def test_selected_material_comparison_followup_adds_the_new_explicit_material(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        first = Material(code="CTX-SN65", name="Classic CAN", mpn="SN65HVD230DR")
        second = Material(code="CTX-TCAN", name="CAN-FD", mpn="QA-TCAN1044-Z9")
        db.add_all([first, second])
        db.commit()

        manager = ConversationContextService(db, user, Settings())
        created = manager.open(None)
        context = db.get(AgentConversationContext, created.id)
        context.selected_material_id = first.id
        context.last_entity_kind = "component"
        context.last_requested_facts = ["component_search"]
        db.commit()

        prepared = manager.prepare(manager.open(created.id), "那和 QA-TCAN1044-Z9 比呢？")

        assert prepared.contract.requested_facts == {"component_evidence_comparison"}
        assert prepared.contract.requires_material_resolution is False
        assert {item["id"] for item in prepared.entities["material_candidates"]["items"]} == {
            first.id,
            second.id,
        }


def test_component_candidate_selection_is_server_owned_and_never_calls_model(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        candidates = [
            Material(
                code="CTX-COMPONENT-CAN-A",
                name="Context component CAN A",
                mpn="CTX-CAN-A",
            ),
            Material(
                code="CTX-COMPONENT-CAN-B",
                name="Context component CAN B",
                mpn="CTX-CAN-B",
            ),
        ]
        db.add_all(candidates)
        db.commit()

        manager = ConversationContextService(db, user, Settings())
        created = manager.open(None)
        context = db.get(AgentConversationContext, created.id)
        candidate_ids = [item.id for item in candidates]
        context.material_candidate_ids = candidate_ids
        context.pending_disambiguation = {
            "kind": "component",
            "active_intent": "search_components_by_requirement",
            "task_status": "active",
            "slots": {"component_types": ["CAN transceiver"]},
            "candidate_ids": candidate_ids,
        }
        context.last_entity_kind = "component"
        context.last_requested_facts = ["component_search"]
        db.commit()

        selected = _service(db, user, NeverProvider()).query(
            "第二个",
            conversation_id=created.id,
        )

        assert selected.intent == "select_material"
        assert selected.entities["material_candidates"]["selected_material_id"] == candidates[1].id
        persisted = db.get(AgentConversationContext, created.id)
        assert persisted.selected_material_id == candidates[1].id
        assert persisted.material_candidate_ids == []
        assert persisted.pending_disambiguation == {}


def test_component_inventory_sort_followup_remains_in_candidate_scope(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        candidates = [
            Material(code="CTX-SORT-CAN-A", name="Sort CAN A", mpn="CTX-SORT-A"),
            Material(code="CTX-SORT-CAN-B", name="Sort CAN B", mpn="CTX-SORT-B"),
        ]
        db.add_all(candidates)
        db.commit()

        manager = ConversationContextService(db, user, Settings())
        created = manager.open(None)
        context = db.get(AgentConversationContext, created.id)
        candidate_ids = [item.id for item in candidates]
        context.material_candidate_ids = candidate_ids
        context.pending_disambiguation = {
            "kind": "component",
            "active_intent": "search_components_by_requirement",
            "task_status": "active",
            "slots": {
                "component_types": ["CAN transceiver"],
                "interfaces": ["CAN-FD"],
                "supply_voltage_v": "5",
            },
            "candidate_ids": candidate_ids,
        }
        context.last_entity_kind = "component"
        context.last_requested_facts = ["component_search"]
        db.commit()

        prepared = manager.prepare(manager.open(created.id), "库存多的放前面。")

        assert prepared.contract.entity_kind == "component"
        assert prepared.contract.requested_facts == {"component_search"}
        assert "CAN transceiver" in prepared.effective_message
        assert "CAN-FD" in prepared.effective_message
        assert "库存多的放前面" in prepared.effective_message


def test_component_location_followup_selects_highest_available_and_keeps_pair(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        lower = Material(
            code="CTX-CAN-LOW",
            name="Low stock CAN",
            mpn="CTX-CAN-LOW",
            quantity=Decimal("10"),
        )
        higher = Material(
            code="CTX-CAN-HIGH",
            name="High stock CAN",
            mpn="CTX-CAN-HIGH",
            quantity=Decimal("30"),
        )
        db.add_all([lower, higher])
        db.commit()

        manager = ConversationContextService(db, user, Settings())
        created = manager.open(None)
        context = db.get(AgentConversationContext, created.id)
        context.material_candidate_ids = [lower.id, higher.id]
        context.pending_disambiguation = {
            "kind": "component",
            "candidate_ids": [lower.id, higher.id],
            "slots": {"component_types": ["CAN transceiver"]},
        }
        db.commit()

        prepared = manager.prepare(manager.open(created.id), "库存多的那个放哪？")

        assert prepared.snapshot.selected_material_id == higher.id
        assert prepared.contract.requested_facts == {"inventory", "location"}
        assert prepared.entities["material_candidates"]["selected_material_id"] == higher.id


def test_component_pronoun_relation_followup_keeps_both_candidates(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        pair = [
            Material(code="CTX-PAIR-A", name="Pair A", mpn="CTX-PAIR-A"),
            Material(code="CTX-PAIR-B", name="Pair B", mpn="CTX-PAIR-B"),
        ]
        db.add_all(pair)
        db.commit()

        manager = ConversationContextService(db, user, Settings())
        created = manager.open(None)
        context = db.get(AgentConversationContext, created.id)
        context.selected_material_id = pair[0].id
        context.material_candidate_ids = [item.id for item in pair]
        context.pending_disambiguation = {
            "kind": "component",
            "candidate_ids": [item.id for item in pair],
            "slots": {"component_types": ["CAN transceiver"]},
        }
        db.commit()

        prepared = manager.prepare(manager.open(created.id), "它和另一个能直接互换吗？")

        assert prepared.contract.requested_facts == {"component_relations"}
        assert {item["id"] for item in prepared.entities["material_candidates"]["items"]} == {
            item.id for item in pair
        }


def test_low_stock_followup_limits_and_sorts_the_same_result_scope(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        db.add_all(
            [
                Material(
                    code=f"CTX-LOW-{index}",
                    name=f"Low stock {index}",
                    quantity=Decimal(str(index)),
                    safety_stock=Decimal(str(10 + (index * 2))),
                    target_stock=Decimal("20"),
                )
                for index in range(4)
            ]
        )
        db.commit()
        provider = NeverProvider()
        first = _service(db, user, provider).query("哪些物料已经低于安全库存了？")
        second = _service(db, user, provider).query(
            "最缺的三个给我按缺口排一下。",
            conversation_id=first.conversation_id,
        )

        items = second.entities["low_stock"]["items"]
        gaps = [
            Decimal(item["safety_stock"]) - Decimal(item["available_quantity"])
            for item in items
        ]
        assert len(items) == 3
        assert gaps == sorted(gaps, reverse=True)
        assert [event.tool for event in second.tool_events] == ["get_low_stock_materials"]


def test_digit_prefixed_material_code_switches_context_explicitly(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        first_material = Material(
            code="CTX-SWITCH-001",
            name="Context switch first",
            mpn="CTX-SWITCH-FIRST",
        )
        second_material = Material(
            code="3P-CTX-SWITCH-002",
            name="Context switch second",
            mpn="CTX-SWITCH-SECOND",
        )
        db.add_all([first_material, second_material])
        db.commit()
        provider = NeverProvider()

        first = _service(db, user, provider).query("帮我找 CTX-SWITCH-001")
        second = _service(db, user, provider).query(
            "再查 3P-CTX-SWITCH-002",
            conversation_id=first.conversation_id,
        )

        context = db.get(AgentConversationContext, second.conversation_id)
        assert [event.tool for event in second.tool_events] == ["search_materials"]
        assert context.selected_material_id == second_material.id
        assert context.selected_material_id != first_material.id


def test_explicit_material_switch_then_pronoun_inventory_is_fully_deterministic(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        first_material = Material(code="CBL-CTX-000069", name="First cable")
        second_material = Material(code="C272976-CTX", name="Second connector")
        db.add_all([first_material, second_material])
        db.commit()

        first = _service(db, user, NeverProvider()).query("帮我找 CBL-CTX-000069")
        second = _service(db, user, NeverProvider()).query(
            "再查 C272976-CTX",
            conversation_id=first.conversation_id,
        )
        third = _service(db, user, NeverProvider()).query(
            "它还有多少？",
            conversation_id=first.conversation_id,
        )

        assert [event.tool for event in first.tool_events] == ["search_materials"]
        assert [event.tool for event in second.tool_events] == ["search_materials"]
        assert [event.tool for event in third.tool_events] == ["get_inventory_availability"]
        context = db.get(AgentConversationContext, first.conversation_id)
        assert context.selected_material_id == second_material.id


def test_conversation_ownership_and_expiry_do_not_leak_context(admin):
    with SessionLocal() as db:
        owner = db.get(User, admin["id"])
        created = _service(db, owner, NeverProvider()).query("它在哪？")
        other = db.scalar(select(User).where(User.id != owner.id))
        if other is None:
            other = User(
                username="conversation_other",
                full_name="Other conversation user",
                department="QA",
                password_hash="not-used",
                role_id=owner.role_id,
                must_change_password=False,
            )
            db.add(other)
            db.commit()

        with pytest.raises(BusinessError) as hidden:
            _service(db, other, NeverProvider()).query(
                "它在哪？",
                conversation_id=created.conversation_id,
            )
        assert hidden.value.code == "AGENT_CONVERSATION_NOT_FOUND"
        assert hidden.value.status_code == 404

        context = db.get(AgentConversationContext, created.conversation_id)
        context.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        with pytest.raises(BusinessError) as expired:
            _service(db, owner, NeverProvider()).query(
                "它在哪？",
                conversation_id=created.conversation_id,
            )
        assert expired.value.code == "AGENT_CONVERSATION_EXPIRED"
        assert expired.value.status_code == 409


def test_new_conversation_cleans_only_contexts_beyond_expiry_grace(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        old = _service(db, user, NeverProvider()).query("它在哪？")
        recent = _service(db, user, NeverProvider()).query("它在哪？")
        old_context = db.get(AgentConversationContext, old.conversation_id)
        recent_context = db.get(AgentConversationContext, recent.conversation_id)
        old_context.expires_at = datetime.now(UTC) - timedelta(days=8)
        recent_context.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        db.expire_all()
        # SQLite returns a naive datetime after the round trip. Keeping the row
        # loaded reproduces the ORM synchronization path used by Golden v1.2.
        assert db.get(AgentConversationContext, old.conversation_id) is not None

        created = _service(db, user, NeverProvider()).query("它在哪？")

        assert db.get(AgentConversationContext, old.conversation_id) is None
        assert db.get(AgentConversationContext, recent.conversation_id) is not None
        assert db.get(AgentConversationContext, created.conversation_id) is not None


def test_optimistic_context_version_rejects_concurrent_overwrite(admin):
    with SessionLocal() as setup_db:
        user = setup_db.get(User, admin["id"])
        original = ConversationContextService(
            setup_db,
            user,
            Settings(),
        ).open(None)

    first_db = SessionLocal()
    second_db = SessionLocal()
    try:
        first_user = first_db.get(User, admin["id"])
        second_user = second_db.get(User, admin["id"])
        first_manager = ConversationContextService(first_db, first_user, Settings())
        second_manager = ConversationContextService(second_db, second_user, Settings())
        first_snapshot = first_manager.open(original.id)
        second_snapshot = second_manager.open(original.id)
        first_turn = first_manager.prepare(first_snapshot, "它在哪？")
        second_turn = second_manager.prepare(second_snapshot, "它还有多少？")

        first_manager.persist(first_turn, entities={}, intent="clarify_material")
        with pytest.raises(BusinessError) as conflict:
            second_manager.persist(second_turn, entities={}, intent="clarify_material")
        assert conflict.value.code == "AGENT_CONVERSATION_CONFLICT"
        assert conflict.value.status_code == 409
    finally:
        first_db.close()
        second_db.close()


def test_cable_superlative_location_followup_selects_longest_candidate(admin):
    with SessionLocal() as db:
        user = db.get(User, admin["id"])
        short = Material(
            code="CBL-LONGEST-30",
            name="RF coax 30 cm",
            mpn="RF-30",
            attributes={"cable_kind": "rf_coax", "length_cm": "30"},
        )
        longest = Material(
            code="CBL-LONGEST-60",
            name="RF coax 60 cm",
            mpn="RF-60",
            attributes={"cable_kind": "rf_coax", "length_cm": "60"},
        )
        db.add_all([short, longest])
        db.commit()

        context = ConversationContextService(db, user, Settings())
        snapshot = context.open(None)
        prepared = context._prepare_cable_continuation(
            snapshot,
            "最长的那条在哪？",
            [short, longest],
        )

        assert prepared is not None
        assert prepared.entities["material_candidates"]["selected_material_id"] == longest.id
        assert prepared.contract.requested_facts == {"cable_search", "location"}
        assert "CBL-LONGEST-60" in prepared.effective_message
