from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.agent.intent_scope import route_intent_scope
from app.agent.task_contract import (
    TaskContract,
    classify_task_contract,
    is_engineering_research_request,
    is_product_bom_preview_request,
)
from app.core.config import Settings
from app.core.exceptions import BusinessError
from app.models import (
    AgentConversationContext,
    Material,
    Product,
    ProductRevision,
    Project,
    User,
)
from app.power_design.requirements import extract_power_requirement
from app.services.cable_intelligence import CableSearchService

_SELECTION_WORDS = re.compile(r"(?:就要|我要|选择|选|那个|这个|这一款|这款|项目)", re.I)
_MATERIAL_TOKEN = re.compile(
    r"(?<![A-Z0-9])(?=[A-Z0-9._()+-]{3,})(?=[A-Z0-9._()+-]*[A-Z])"
    r"(?=[A-Z0-9._()+-]*\d)[A-Z0-9._()+-]+",
    re.I,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _fold(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _selection_needle(message: str) -> str:
    return _fold(_SELECTION_WORDS.sub("", message))


def _candidate_dict(material: Material) -> dict[str, Any]:
    return {
        "id": material.id,
        "code": material.code,
        "name": material.name,
        "mpn": material.mpn,
        "specification": material.specification,
        "package": material.package,
        "manufacturer": material.manufacturer,
        "unit": material.unit,
        "attributes": dict(material.attributes or {}),
    }


def _project_dict(project: Project, versions: list[str]) -> dict[str, Any]:
    return {
        "id": project.id,
        "code": project.code,
        "name": project.name,
        "status": project.status,
        "product_revision_id": project.product_revision_id,
        "available_versions": versions,
    }


def _revision_dict(revision: ProductRevision) -> dict[str, Any]:
    return {
        "id": revision.id,
        "revision": revision.revision,
        "status": revision.status,
        "is_default": revision.is_default,
    }


def _product_dict(product: Product, revisions: list[ProductRevision]) -> dict[str, Any]:
    return {
        "id": product.id,
        "code": product.code,
        "name": product.name,
        "description": product.description,
        "lifecycle_status": product.lifecycle_status,
        "released_revisions": [_revision_dict(item) for item in revisions],
        "default_revision": next(
            (_revision_dict(item) for item in revisions if item.is_default),
            None,
        ),
    }


@dataclass(frozen=True)
class ConversationSnapshot:
    id: str
    user_id: int
    context_version: int
    selected_material_id: int | None
    selected_project_id: int | None
    selected_bom_version: str | None
    selected_product_id: int | None
    selected_product_revision_id: int | None
    material_candidate_ids: tuple[int, ...]
    project_candidate_ids: tuple[int, ...]
    product_candidate_ids: tuple[int, ...]
    pending_disambiguation: dict[str, Any]
    last_entity_kind: str
    last_intent: str
    last_requested_facts: tuple[str, ...]
    last_build_quantity: int | None = None


@dataclass(frozen=True)
class PreparedConversationTurn:
    snapshot: ConversationSnapshot
    entities: dict[str, Any]
    contract: TaskContract
    direct_answer: str = ""
    direct_intent: str | None = None
    explicit_material_switch: bool = False
    explicit_project_switch: bool = False
    explicit_product_switch: bool = False
    isolate_previous_context: bool = False
    effective_message: str | None = None


class ConversationContextService:
    """Owns bounded semantic context; it never stores prompts or reasoning transcripts."""

    def __init__(self, db: Session, user: User, config: Settings):
        self.db = db
        self.user = user
        self.ttl = timedelta(minutes=config.agent_conversation_ttl_minutes)
        self.cleanup_grace = timedelta(days=config.agent_conversation_cleanup_grace_days)

    def open(self, conversation_id: str | None) -> ConversationSnapshot:
        if conversation_id is None:
            self._cleanup_expired_for_user()
            row = AgentConversationContext(
                id=str(uuid.uuid4()),
                user_id=self.user.id,
                status="active",
                context_version=0,
                selected_material_id=None,
                selected_project_id=None,
                selected_bom_version=None,
                selected_product_id=None,
                selected_product_revision_id=None,
                material_candidate_ids=[],
                project_candidate_ids=[],
                product_candidate_ids=[],
                pending_disambiguation={},
                last_entity_kind="unknown",
                last_intent="",
                last_requested_facts=[],
                last_build_quantity=None,
                expires_at=_now() + self.ttl,
            )
            self.db.add(row)
            self.db.commit()
            return self._snapshot(row)

        row = self.db.scalar(
            select(AgentConversationContext).where(
                AgentConversationContext.id == conversation_id,
                AgentConversationContext.user_id == self.user.id,
            )
        )
        if row is None:
            self.db.rollback()
            raise BusinessError(
                "AGENT_CONVERSATION_NOT_FOUND",
                "这段对话不存在或无法访问",
                404,
            )
        if row.status != "active":
            self.db.rollback()
            raise BusinessError(
                "AGENT_CONVERSATION_NOT_FOUND",
                "这段对话不存在或无法访问",
                404,
            )
        if _aware(row.expires_at) <= _now():
            self.db.rollback()
            raise BusinessError(
                "AGENT_CONVERSATION_EXPIRED",
                "这段对话的上下文已过期，请重新指定物料或项目。",
                409,
            )
        snapshot = self._validated_snapshot(row)
        self.db.commit()
        return snapshot

    def _cleanup_expired_for_user(self) -> None:
        """Bound metadata growth without hiding a newly expired conversation error."""

        self.db.execute(
            delete(AgentConversationContext).where(
                AgentConversationContext.user_id == self.user.id,
                AgentConversationContext.expires_at < _now() - self.cleanup_grace,
            ),
            execution_options={"synchronize_session": "fetch"},
        )

    def prepare(self, snapshot: ConversationSnapshot, message: str) -> PreparedConversationTurn:
        material_candidates = self._materials(snapshot.material_candidate_ids)
        project_candidates = self._projects(snapshot.project_candidate_ids)
        product_candidates = self._products(snapshot.product_candidate_ids)
        pending_kind = str(snapshot.pending_disambiguation.get("kind") or "")
        unsupported_write_contract = classify_task_contract(message)
        if unsupported_write_contract.write_intent == "unsupported_write":
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=unsupported_write_contract,
                direct_answer=(
                    "不能直接修改库存。库存变更必须走正常库存操作和审批流程；本次没有执行任何写入。"
                ),
                direct_intent="unsupported_write",
            )

        if is_product_bom_preview_request(message):
            return self._prepare_product_bom_preview(snapshot, message)

        # Engineering research is a bounded server-owned context, not a raw
        # transcript.  Once a research turn has selected a candidate, short
        # follow-ups such as “这些外围库存够不够？” must stay on that route
        # even though the follow-up has no voltage numbers of its own.
        research_context = snapshot.pending_disambiguation.get("research_context")
        current_power_requirement = extract_power_requirement(message)
        has_explicit_new_power_scope = (
            current_power_requirement.input_voltage_v is not None
            and current_power_requirement.output_voltage_v is not None
        )
        if (
            isinstance(research_context, dict)
            and (
                pending_kind == "engineering_research"
                or snapshot.last_entity_kind == "engineering_research"
            )
            and self._is_selection_followup(message, research_context)
        ):
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={"engineering_research_context": research_context},
                contract=TaskContract(
                    entity_kind="engineering_research",
                    requested_facts={"engineering_research"},
                ),
                effective_message=message,
            )
        if (
            isinstance(research_context, dict)
            and (
                pending_kind == "engineering_research"
                or snapshot.last_entity_kind == "engineering_research"
            )
            and (not is_engineering_research_request(message) or not has_explicit_new_power_scope)
        ):
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={"engineering_research_context": research_context},
                contract=TaskContract(
                    entity_kind="engineering_research",
                    requested_facts={"engineering_research"},
                ),
                effective_message=message,
            )

        power_context = snapshot.pending_disambiguation.get("power_design_context")
        if isinstance(power_context, dict) and snapshot.last_entity_kind == "power":
            effective_power_message = self._power_design_followup_message(message, power_context)
            if effective_power_message:
                power_contract = classify_task_contract(effective_power_message)
                if "power_design" in power_contract.requested_facts:
                    return PreparedConversationTurn(
                        snapshot=snapshot,
                        entities={},
                        contract=power_contract,
                        effective_message=effective_power_message,
                    )
        if pending_kind in {"cable", "component"}:
            material_candidates = self._materials(
                snapshot.pending_disambiguation.get("candidate_ids") or []
            )

        if "low_stock" in snapshot.last_requested_facts and any(
            marker in message.casefold()
            for marker in ("最缺", "缺口", "前三", "前3", "排序", "排一下")
        ):
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=TaskContract(
                    entity_kind="global",
                    requested_facts={"low_stock"},
                    requires_material_resolution=False,
                ),
                effective_message=f"低库存 {message}",
            )

        if pending_kind == "cable" or (
            pending_kind == "material" and snapshot.last_entity_kind == "cable"
        ):
            cable_turn = self._prepare_cable_continuation(
                snapshot,
                message,
                material_candidates,
            )
            if cable_turn is not None:
                return cable_turn

        if pending_kind == "component":
            component_turn = self._prepare_component_continuation(
                snapshot,
                message,
                material_candidates,
            )
            if component_turn is not None:
                return component_turn

        if pending_kind == "alternate_scope":
            effective_message = f"{message} 产品 BOM 位批准备选"
            if not self._mentions_known_product(message):
                return PreparedConversationTurn(
                    snapshot=snapshot,
                    entities={},
                    contract=classify_task_contract(effective_message),
                    effective_message=effective_message,
                    direct_answer="请提供具体产品编号和版本，才能核对该 BOM 位已批准的备选。",
                    direct_intent="clarify_product",
                )
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=classify_task_contract(effective_message),
                effective_message=effective_message,
            )

        scope_decision = route_intent_scope(message)
        if (
            pending_kind == "material"
            and "component_relations" in snapshot.last_requested_facts
            and scope_decision.mentions_product_scope
        ):
            effective_message = f"{message} 产品 BOM 位批准备选"
            return PreparedConversationTurn(
                snapshot=replace(
                    snapshot,
                    pending_disambiguation={
                        "kind": "alternate_scope",
                        "active_intent": "product_bom_alternate",
                        "task_status": "active",
                        "candidate_ids": list(snapshot.material_candidate_ids),
                    },
                ),
                entities={},
                contract=classify_task_contract(effective_message),
                effective_message=effective_message,
            )

        if (
            pending_kind == "material"
            and material_candidates
            and snapshot.last_entity_kind == "cable"
            and self._is_cable_constraint_refinement(message)
        ):
            effective_message = self._cable_refinement_query(message, material_candidates)
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=classify_task_contract(effective_message),
                effective_message=effective_message,
            )

        if (
            pending_kind == "material"
            and len(material_candidates) > 1
            and self._is_multi_material_followup(message, snapshot)
        ):
            contract = classify_task_contract(message)
            requested = set(contract.requested_facts)
            comparison_followup = any(
                marker in message.casefold()
                for marker in (
                    "具体差",
                    "差在哪",
                    "差异",
                    "比较",
                    "compare",
                    "为什么",
                    "依据",
                    "证据",
                )
            )
            if comparison_followup and "component_relations" in snapshot.last_requested_facts:
                requested.difference_update({"material_identity", "location"})
                requested.update({"component_relations", "component_evidence_comparison"})
            elif not requested and "component_relations" in snapshot.last_requested_facts:
                requested.add("component_relations")
            if (
                "component_evidence_comparison" in requested
                or "component_evidence_comparison" in snapshot.last_requested_facts
            ):
                requested.discard("engineering_evidence")
                requested.add("component_evidence_comparison")
            contract = contract.model_copy(
                update={
                    "entity_kind": "material",
                    "requested_facts": requested,
                    "requires_material_resolution": False,
                }
            )
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={
                    "material_candidates": self._material_entity(material_candidates, None),
                    "conversation_evidence_fields": list(
                        snapshot.pending_disambiguation.get("evidence_fields") or []
                    ),
                },
                contract=contract,
            )

        if pending_kind == "material" and material_candidates:
            selected = self._match_material(message, material_candidates)
            if selected is not None:
                entities = {"material_candidates": self._material_entity([selected], selected.id)}
                contract = classify_task_contract(message, selected_material=True)
                continue_query = bool(
                    {"location", "inventory"}.intersection(contract.requested_facts)
                )
                if snapshot.last_entity_kind == "cable" and continue_query:
                    contract = contract.model_copy(
                        update={
                            "entity_kind": "cable",
                            "requested_facts": set(contract.requested_facts) | {"cable_search"},
                            "requires_material_resolution": False,
                        }
                    )
                selection_only = not continue_query
                return PreparedConversationTurn(
                    snapshot=replace(
                        snapshot,
                        selected_material_id=selected.id,
                        material_candidate_ids=(),
                        pending_disambiguation={},
                    ),
                    entities=entities,
                    contract=contract,
                    direct_answer=(
                        f"已选择 {selected.mpn or selected.code}。你可以继续问库存或存放位置。"
                        if selection_only
                        else ""
                    ),
                    direct_intent="select_material" if selection_only else None,
                )
            entities = {"material_candidates": self._material_entity(material_candidates, None)}
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities=entities,
                contract=TaskContract(entity_kind="material"),
                direct_answer=self._material_choice_answer(material_candidates),
                direct_intent="search_material",
            )

        if pending_kind == "project" and project_candidates:
            selected = self._match_project(message, project_candidates)
            if selected is not None:
                entities = {"project_candidates": self._project_entity([selected], selected.id)}
                contract = classify_task_contract(message, selected_project=True)
                return PreparedConversationTurn(
                    snapshot=replace(
                        snapshot,
                        selected_project_id=selected.id,
                        project_candidate_ids=(),
                        pending_disambiguation={},
                    ),
                    entities=entities,
                    contract=contract,
                    direct_answer=f"已选择项目 {selected.name}。你可以继续问 BOM 或缺料情况。",
                    direct_intent="select_project",
                )
            entities = {"project_candidates": self._project_entity(project_candidates, None)}
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities=entities,
                contract=TaskContract(entity_kind="project"),
                direct_answer=self._project_choice_answer(project_candidates),
                direct_intent="search_project",
            )

        selected_project_for_version = self._project(snapshot.selected_project_id)
        if selected_project_for_version is not None and snapshot.selected_bom_version is None:
            available_versions = self._project_versions([selected_project_for_version.id]).get(
                selected_project_for_version.id,
                [],
            )
            normalized_version = message.casefold().strip(" ？?。！!")
            matched_version = next(
                (
                    version
                    for version in available_versions
                    if version.casefold() == normalized_version
                ),
                None,
            )
            if matched_version is not None:
                requested_facts = {
                    fact
                    for fact in snapshot.last_requested_facts
                    if fact in {"project_bom", "bom_stock"}
                }
                if not requested_facts:
                    requested_facts = {"project_bom"}
                return PreparedConversationTurn(
                    snapshot=replace(
                        snapshot,
                        selected_bom_version=matched_version,
                        pending_disambiguation={},
                    ),
                    entities={
                        "project_candidates": self._project_entity(
                            [selected_project_for_version],
                            selected_project_for_version.id,
                            selected_bom_version=matched_version,
                        )
                    },
                    contract=TaskContract(
                        entity_kind="project",
                        requested_facts=requested_facts,
                        requires_project_resolution=False,
                    ),
                )

        if pending_kind == "product" and product_candidates:
            selected = self._match_product(message, product_candidates)
            if selected is not None:
                default_revision = self._default_revision(selected.id)
                entities = {
                    "product_candidates": self._product_entity(
                        [selected],
                        selected.id,
                        selected_revision_id=(default_revision.id if default_revision else None),
                    )
                }
                contract = classify_task_contract(message, selected_product=True)
                return PreparedConversationTurn(
                    snapshot=replace(
                        snapshot,
                        selected_product_id=selected.id,
                        selected_product_revision_id=(
                            default_revision.id if default_revision else None
                        ),
                        product_candidate_ids=(),
                        pending_disambiguation={},
                    ),
                    entities=entities,
                    contract=contract,
                    direct_answer=(
                        f"已选择产品 {selected.name}。你可以继续问单台 BOM 或计划构建数量。"
                    ),
                    direct_intent="select_product",
                )
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={
                    "product_candidates": self._product_entity(
                        product_candidates,
                        None,
                    )
                },
                contract=TaskContract(entity_kind="product"),
                direct_answer=self._product_choice_answer(product_candidates),
                direct_intent="search_product",
            )

        selected_product = self._product(snapshot.selected_product_id)
        selected_product_revision = self._product_revision(
            snapshot.selected_product_revision_id,
            product_id=selected_product.id if selected_product else None,
        )
        if selected_product is not None:
            matched_revision = self._match_product_revision(message, selected_product.id)
            if matched_revision is not None:
                revised_snapshot = replace(
                    snapshot,
                    selected_product_revision_id=matched_revision.id,
                    pending_disambiguation={},
                )
                entities = {
                    "product_candidates": self._product_entity(
                        [selected_product],
                        selected_product.id,
                        selected_revision_id=matched_revision.id,
                    )
                }
                contract = classify_task_contract(message, selected_product=True)
                selection_only = _fold(message) == _fold(matched_revision.revision)
                return PreparedConversationTurn(
                    snapshot=revised_snapshot,
                    entities=entities,
                    contract=contract,
                    direct_answer=(
                        f"已选择产品版本 {matched_revision.revision}。"
                        "你可以继续问单台 BOM 或计划构建数量。"
                        if selection_only
                        else ""
                    ),
                    direct_intent="select_product_revision" if selection_only else None,
                )

        comparison_reference = self._explicit_material_reference(message)
        comparison_selected = self._material(snapshot.selected_material_id)
        if (
            comparison_selected is not None
            and comparison_reference is not None
            and comparison_reference.id != comparison_selected.id
            and any(marker in message.casefold() for marker in ("比呢", "比较", "差异", "compare"))
        ):
            pair = [comparison_selected, comparison_reference]
            return PreparedConversationTurn(
                snapshot=replace(
                    snapshot,
                    selected_material_id=None,
                    material_candidate_ids=tuple(item.id for item in pair),
                    pending_disambiguation={
                        "kind": "material",
                        "candidate_ids": [item.id for item in pair],
                    },
                ),
                entities={"material_candidates": self._material_entity(pair, None)},
                contract=TaskContract(
                    entity_kind="material",
                    requested_facts={"component_evidence_comparison"},
                    requires_material_resolution=False,
                ),
                effective_message=message,
            )

        known_product_mention = self._mentions_known_product(message)
        concrete_product_scope = known_product_mention or bool(
            re.search(
                r"(?<![A-Z0-9])(?:PROD-[A-Z0-9-]+|(?:EVT|DVT|PVT)-R?\d+)",
                message,
                re.I,
            )
        )
        named_robot_target = bool(
            re.search(
                r"(?<![A-Z0-9])(?:Robot\s+[A-Z0-9][A-Z0-9._-]*|"
                r"AMR(?:\s+[A-Z0-9][A-Z0-9._-]*)?)(?![A-Z0-9])",
                message,
                re.I,
            )
        )
        product_shaped = self._looks_like_product(message) or known_product_mention
        explicit_material = (
            bool(_MATERIAL_TOKEN.search(message))
            and not self._looks_like_project(message)
            and not product_shaped
            and not (snapshot.selected_material_id and self._is_relation_followup(message))
        )
        explicit_product = product_shaped and not (
            snapshot.selected_product_id and self._is_product_followup(message)
        )
        explicit_project = (
            self._looks_like_project(message)
            and not explicit_product
            and not (snapshot.selected_project_id and self._is_project_followup(message))
        )
        selected_material = (
            None if explicit_material else self._material(snapshot.selected_material_id)
        )
        selected_project = None if explicit_project else self._project(snapshot.selected_project_id)
        if explicit_product:
            selected_product = None
            selected_product_revision = None

        effective_message = (
            self._cable_refinement_query(message, [selected_material])
            if selected_material is not None
            and snapshot.last_entity_kind == "cable"
            and self._is_cable_constraint_refinement(message)
            else message
        )
        contract = classify_task_contract(
            effective_message,
            selected_material=selected_material is not None,
            selected_project=selected_project is not None,
            selected_product=selected_product is not None or explicit_product,
            previous_build_quantity=snapshot.last_build_quantity,
        )
        anaphoric = self._is_anaphoric_followup(message)
        isolate_previous_context = (
            not anaphoric
            and contract.entity_kind in {"global", "project", "product", "component", "cable"}
            and contract.entity_kind != snapshot.last_entity_kind
        )
        if (
            contract.entity_kind == "product"
            and contract.build_quantity is not None
            and not concrete_product_scope
            and snapshot.selected_product_id is None
            and not named_robot_target
            and not anaphoric
            and not self._is_product_followup(message)
        ):
            # A fresh build request such as “做 5 台这版产品” is not a
            # product selection. Do not inherit a previous ProductRevision or
            # fall through to a project search; the per-unit BOM scope must be
            # explicit before any readiness calculation is meaningful.
            selected_product = None
            selected_product_revision = None
        if isolate_previous_context:
            selected_material = None
            if contract.entity_kind not in {"project", "product"}:
                selected_project = None
                selected_product = None
                selected_product_revision = None

        if (
            contract.entity_kind == "product"
            and contract.build_quantity is not None
            and selected_product is None
            and not concrete_product_scope
            and snapshot.selected_product_id is None
            and not named_robot_target
            and not self._is_product_followup(message)
        ):
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=contract,
                direct_answer=(
                    "请先提供具体产品和版本（Revision），再核对该数量的单台 BOM、"
                    "库存与库位；当前未进行任何预留或写入。"
                ),
                direct_intent="clarify_product",
            )

        if (
            (
                scope_decision.intent == "product_bom_alternate"
                or (
                    scope_decision.mentions_alternate
                    and not explicit_material
                    and selected_material is None
                )
            )
            and (not scope_decision.mentions_product_scope or not concrete_product_scope)
            and (not explicit_product or not concrete_product_scope)
            and selected_product is None
        ):
            clarification_contract = TaskContract(
                entity_kind="product",
                requested_facts={"product_bom", "product_alternates"},
                requires_product_resolution=True,
            )
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=clarification_contract,
                direct_answer=(
                    "请告诉我具体的 CAN 芯片型号；如果要查某块板上已批准的备选，"
                    "也请说明产品或版本。"
                    if "can" in _fold(message)
                    else "请先说明具体产品、版本或 BOM 位；仅凭仓库里存在另一种规格，"
                    "不能据此认定为当前 BOM 物料的合格替代。"
                ),
                direct_intent="clarify_alternate_scope",
            )
        explicit_relation_materials = self._explicit_relation_materials(message, contract)
        if contract.invalid_build_quantity or (
            contract.entity_kind == "product"
            and contract.build_quantity is not None
            and contract.build_quantity <= 0
        ):
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=contract,
                direct_answer="构建数量必须是大于 0 的整数。",
                direct_intent="invalid_build_quantity",
            )
        if self._elliptical_product(message) and selected_product is None:
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=contract,
                direct_answer="请先指定要构建的产品或产品编号。",
                direct_intent="clarify_product",
            )
        if (
            not explicit_material
            and self._elliptical_material(message, contract)
            and selected_material is None
        ):
            evidence_followup = bool(
                {"engineering_evidence", "component_evidence_comparison"}.intersection(
                    contract.requested_facts
                )
            )
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=contract,
                direct_answer=(
                    "请先指定要查询的具体器件或物料。"
                    if evidence_followup
                    else "请先告诉我要查询的具体物料。"
                ),
                direct_intent="clarify_material",
                explicit_material_switch=explicit_material,
            )
        if (
            not explicit_project
            and self._elliptical_project(message, contract)
            and selected_project is None
        ):
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=contract,
                direct_answer="请先告诉我要查询的具体项目。",
                direct_intent="clarify_project",
                explicit_project_switch=explicit_project,
            )

        entities: dict[str, Any] = {}
        if explicit_relation_materials:
            entities["material_candidates"] = self._material_entity(
                explicit_relation_materials,
                None,
            )
            contract = contract.model_copy(update={"requires_material_resolution": False})
        if selected_material is not None:
            entities["material_candidates"] = self._material_entity(
                [selected_material], selected_material.id
            )
        if selected_project is not None:
            entities["project_candidates"] = self._project_entity(
                [selected_project],
                selected_project.id,
                selected_bom_version=snapshot.selected_bom_version,
            )
        if selected_product is not None:
            entities["product_candidates"] = self._product_entity(
                [selected_product],
                selected_product.id,
                selected_revision_id=(
                    selected_product_revision.id if selected_product_revision else None
                ),
            )
        return PreparedConversationTurn(
            snapshot=snapshot,
            entities=entities,
            contract=contract,
            explicit_material_switch=explicit_material,
            explicit_project_switch=explicit_project,
            explicit_product_switch=explicit_product,
            isolate_previous_context=isolate_previous_context,
            effective_message=(effective_message if effective_message != message else None),
        )

    @staticmethod
    def _is_selection_followup(message: str, research_context: dict[str, Any]) -> bool:
        """Keep explicit draft-selection language on the typed research route."""

        active = research_context.get("active_selection_context")
        if not isinstance(active, dict) or not active.get("requirement_id"):
            return False
        folded = str(message or "").casefold()
        if "不要替我自动选" in folded or "先比较" in folded or "不急着定" in folded:
            return False
        return any(
            marker in folded
            for marker in (
                "取消",
                "清除",
                "不要了",
                "就用",
                "选择",
                "选用",
                "定下",
                "作为这份工程草案",
                "换成",
                "换另一个",
                "换一个",
                "第二个",
                "第2个",
                "第一个",
                "第1个",
            )
        )

    def _prepare_product_bom_preview(
        self,
        snapshot: ConversationSnapshot,
        message: str,
    ) -> PreparedConversationTurn:
        """Resolve a preview target while retaining the persisted research draft."""

        contract = classify_task_contract(
            message,
            selected_product=snapshot.selected_product_id is not None,
        )
        research_context = snapshot.pending_disambiguation.get("research_context")
        if not isinstance(research_context, dict):
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=contract,
                direct_answer=(
                    "请先完成一次 Engineering Research，形成只读工程草案后再预览到 "
                    "ProductRevision。"
                ),
                direct_intent="clarify_engineering_research",
            )

        product = self._product(snapshot.selected_product_id)
        candidates: list[Product] = []
        if self._looks_like_product(message):
            candidates = self._product_matches(message)
            if len(candidates) == 1:
                product = candidates[0]
            elif len(candidates) > 1:
                return PreparedConversationTurn(
                    snapshot=snapshot,
                    entities={"product_candidates": self._product_entity(candidates, None)},
                    contract=contract,
                    direct_answer=self._product_choice_answer(candidates),
                    direct_intent="search_product",
                )
            elif product is None:
                return PreparedConversationTurn(
                    snapshot=snapshot,
                    entities={},
                    contract=contract,
                    direct_answer="没有找到明确的目标产品，请提供产品编号或名称。",
                    direct_intent="clarify_product",
                )
        if product is None:
            return PreparedConversationTurn(
                snapshot=snapshot,
                entities={},
                contract=contract,
                direct_answer="请先选择要预览的具体产品和 ProductRevision。",
                direct_intent="clarify_product",
            )

        revision = self._match_product_revision(message, product.id)
        if revision is None and product.id == snapshot.selected_product_id:
            revision = self._product_revision(
                snapshot.selected_product_revision_id,
                product_id=product.id,
            )
        if revision is None:
            revisions = self._all_product_revisions(product.id)
            defaults = [item for item in revisions if item.status == "released" and item.is_default]
            if len(defaults) == 1:
                revision = defaults[0]
            elif len(revisions) == 1:
                revision = revisions[0]
            else:
                labels = "、".join(item.revision for item in revisions) or "无可用版本"
                return PreparedConversationTurn(
                    snapshot=snapshot,
                    entities={
                        "product_candidates": self._product_entity(
                            candidates or [product], product.id
                        )
                    },
                    contract=contract,
                    direct_answer=f"请明确要预览的 ProductRevision：{labels}。",
                    direct_intent="clarify_product_revision",
                )

        next_snapshot = replace(
            snapshot,
            selected_product_id=product.id,
            selected_product_revision_id=revision.id,
            pending_disambiguation=dict(snapshot.pending_disambiguation),
        )
        contract = contract.model_copy(update={"requires_product_resolution": False})
        return PreparedConversationTurn(
            snapshot=next_snapshot,
            entities={
                "engineering_research_context": research_context,
                "product_candidates": self._product_entity(
                    [product], product.id, selected_revision_id=revision.id
                ),
            },
            contract=contract,
            effective_message=message,
        )

    def persist(
        self,
        prepared: PreparedConversationTurn,
        *,
        entities: dict[str, Any],
        intent: str | None,
    ) -> ConversationSnapshot:
        values, next_snapshot = self._derive_update(
            prepared,
            entities=entities,
            intent=intent,
        )
        snapshot = prepared.snapshot
        result = self.db.execute(
            update(AgentConversationContext)
            .where(
                AgentConversationContext.id == snapshot.id,
                AgentConversationContext.user_id == self.user.id,
                AgentConversationContext.context_version == snapshot.context_version,
                AgentConversationContext.status == "active",
            )
            .values(**values)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise BusinessError(
                "AGENT_CONVERSATION_CONFLICT",
                "这段对话刚刚收到另一条消息，请确认结果后重试。",
                409,
            )
        self.db.commit()
        return next_snapshot

    def _derive_update(
        self,
        prepared: PreparedConversationTurn,
        *,
        entities: dict[str, Any],
        intent: str | None,
    ) -> tuple[dict[str, Any], ConversationSnapshot]:
        snapshot = prepared.snapshot
        selected_material_id = snapshot.selected_material_id
        selected_project_id = snapshot.selected_project_id
        selected_bom_version = snapshot.selected_bom_version
        selected_product_id = snapshot.selected_product_id
        selected_product_revision_id = snapshot.selected_product_revision_id
        material_candidate_ids = list(snapshot.material_candidate_ids)
        project_candidate_ids = list(snapshot.project_candidate_ids)
        product_candidate_ids = list(snapshot.product_candidate_ids)
        pending = dict(snapshot.pending_disambiguation)
        last_build_quantity = snapshot.last_build_quantity

        if prepared.isolate_previous_context:
            selected_material_id = None
            selected_project_id = None
            selected_bom_version = None
            selected_product_id = None
            selected_product_revision_id = None
            material_candidate_ids = []
            project_candidate_ids = []
            product_candidate_ids = []
            pending = {}

        if prepared.explicit_material_switch:
            selected_material_id = None
            material_candidate_ids = []
            pending = {} if pending.get("kind") == "material" else pending
        if prepared.explicit_project_switch:
            selected_project_id = None
            selected_bom_version = None
            project_candidate_ids = []
            pending = {} if pending.get("kind") == "project" else pending
        if prepared.explicit_product_switch:
            selected_product_id = None
            selected_product_revision_id = None
            product_candidate_ids = []
            pending = {} if pending.get("kind") in {"product", "product_revision"} else pending

        material_entity = entities.get("material_candidates") or {}
        if "items" in material_entity:
            items = material_entity.get("items") or []
            selected = material_entity.get("selected_material_id")
            exact = material_entity.get("exact_match_ids") or []
            if selected or len(items) == 1 or len(exact) == 1:
                selected_material_id = int(selected or (exact[0] if exact else items[0]["id"]))
                material_candidate_ids = []
                if pending.get("kind") in {"material", "component"}:
                    pending = {}
            elif len(items) > 1:
                selected_material_id = None
                material_candidate_ids = [int(item["id"]) for item in items]
                pending = {"kind": "material", "candidate_ids": material_candidate_ids}
            else:
                selected_material_id = None
                material_candidate_ids = []
                if pending.get("kind") == "material":
                    pending = {}

        if (
            selected_material_id is not None
            and snapshot.pending_disambiguation.get("kind") == "component"
            and len(snapshot.pending_disambiguation.get("candidate_ids") or []) > 1
            and {"inventory", "location"}.intersection(prepared.contract.requested_facts)
        ):
            material_candidate_ids = list(
                snapshot.pending_disambiguation.get("candidate_ids") or []
            )
            pending = dict(snapshot.pending_disambiguation)

        project_entity = entities.get("project_candidates") or {}
        if "items" in project_entity:
            items = project_entity.get("items") or []
            selected = project_entity.get("selected_project_id")
            exact = project_entity.get("exact_match_ids") or []
            if selected or len(items) == 1 or len(exact) == 1:
                selected_project_id = int(selected or (exact[0] if exact else items[0]["id"]))
                project_candidate_ids = []
                if pending.get("kind") == "project":
                    pending = {}
            elif len(items) > 1:
                selected_project_id = None
                project_candidate_ids = [int(item["id"]) for item in items]
                pending = {"kind": "project", "candidate_ids": project_candidate_ids}
            else:
                selected_project_id = None
                project_candidate_ids = []
                if pending.get("kind") == "project":
                    pending = {}

        product_entity = entities.get("product_candidates") or {}
        if "items" in product_entity:
            items = product_entity.get("items") or []
            selected = product_entity.get("selected_product_id")
            selected_revision = product_entity.get("selected_product_revision_id")
            exact = product_entity.get("exact_match_ids") or []
            if selected or len(items) == 1 or len(exact) == 1:
                selected_product_id = int(selected or (exact[0] if exact else items[0]["id"]))
                product_candidate_ids = []
                if selected_revision:
                    selected_product_revision_id = int(selected_revision)
                else:
                    selected_item = next(
                        (item for item in items if int(item["id"]) == selected_product_id),
                        items[0] if items else {},
                    )
                    default_revision = selected_item.get("default_revision") or {}
                    selected_product_revision_id = (
                        int(default_revision["id"]) if default_revision.get("id") else None
                    )
                if pending.get("kind") in {"product", "product_revision"}:
                    pending = {}
            elif len(items) > 1:
                selected_product_id = None
                selected_product_revision_id = None
                product_candidate_ids = [int(item["id"]) for item in items]
                pending = {"kind": "product", "candidate_ids": product_candidate_ids}
            else:
                selected_product_id = None
                selected_product_revision_id = None
                product_candidate_ids = []
                if pending.get("kind") in {"product", "product_revision"}:
                    pending = {}

        build_entity = entities.get("build_readiness") or entities.get("product_bom") or {}
        if build_entity.get("product"):
            selected_product_id = int(build_entity["product"]["id"])
        if build_entity.get("revision"):
            selected_product_revision_id = int(build_entity["revision"]["id"])
        if prepared.contract.build_quantity is not None:
            last_build_quantity = prepared.contract.build_quantity

        alternate_entity = entities.get("product_bom_alternates") or {}
        alternate_items = alternate_entity.get("items") or []
        if alternate_items:
            scope = alternate_items[0]
            if scope.get("product"):
                selected_product_id = int(scope["product"]["id"])
            if scope.get("revision"):
                selected_product_revision_id = int(scope["revision"]["id"])
            selected_alternate = alternate_entity.get("selected_alternate_material_id")
            if selected_alternate:
                selected_material_id = int(selected_alternate)
                material_candidate_ids = []
                if pending.get("kind") == "material":
                    pending = {}

        comparison_entity = entities.get("component_evidence_comparison") or {}
        comparison_materials = comparison_entity.get("materials") or []
        if len(comparison_materials) >= 2:
            selected_material_id = None
            material_candidate_ids = [int(item["id"]) for item in comparison_materials]
            pending = {
                "kind": "material",
                "candidate_ids": material_candidate_ids,
                "evidence_fields": [
                    str(item["field"])
                    for item in comparison_entity.get("comparisons") or []
                    if item.get("field")
                ],
            }

        bom_entity = entities.get("bom_analysis") or entities.get("project_bom") or {}
        if bom_entity.get("version"):
            selected_bom_version = str(bom_entity["version"])

        cable_entity = entities.get("cable_search") or {}
        if cable_entity:
            constraints = {
                key: value
                for key, value in dict(cable_entity.get("constraints") or {}).items()
                if key != "length_is_soft"
            }
            cable_candidate_ids = [
                int(item["material_id"])
                for item in cable_entity.get("items") or []
                if item.get("material_id")
            ]
            material_candidate_ids = cable_candidate_ids
            if cable_entity.get("needs_direction_disambiguation"):
                selected_material_id = None
            pending = {
                "kind": "cable",
                "active_intent": "search_cables",
                "task_status": (
                    "awaiting_clarification"
                    if cable_entity.get("needs_direction_disambiguation")
                    else "active"
                ),
                "pending_slot": (
                    "direction" if cable_entity.get("needs_direction_disambiguation") else None
                ),
                "slots": constraints,
                "candidate_ids": cable_candidate_ids,
                "policy_flags": {
                    "purchase_quantity_not_inventory": any(
                        marker in str(cable_entity.get("query") or "").casefold()
                        for marker in ("买了", "订单", "采购数量")
                    )
                    or bool(
                        (snapshot.pending_disambiguation.get("policy_flags") or {}).get(
                            "purchase_quantity_not_inventory"
                        )
                    ),
                },
            }

        component_entity = entities.get("component_search") or {}
        if component_entity:
            component_candidate_ids = [
                int(item["material_id"])
                for item in component_entity.get("candidates") or []
                if item.get("material_id")
            ]
            material_candidate_ids = component_candidate_ids
            query_slots = dict(component_entity.get("query") or {})
            query_slots.pop("raw_text", None)
            pending = {
                "kind": "component",
                "active_intent": "search_components_by_requirement",
                "task_status": "active",
                "slots": query_slots,
                "candidate_ids": component_candidate_ids,
            }

        research_entity = entities.get("engineering_research") or {}
        if research_entity:
            context = self._engineering_research_context(research_entity)
            previous_context = snapshot.pending_disambiguation.get("research_context") or {}
            if not context.get("active_candidate_ids"):
                context["active_candidate_ids"] = list(
                    previous_context.get("active_candidate_ids") or []
                )
            pending = {
                "kind": "engineering_research",
                "active_intent": "engineering_research",
                "task_status": "active",
                "active_candidate_ids": list(context.get("active_candidate_ids") or []),
                "research_context": context,
            }

        power_entity = entities.get("power_design") or {}
        if power_entity:
            context = self._power_design_context(power_entity)
            if context:
                pending = {
                    "kind": "power_design",
                    "active_intent": "plan_power_design",
                    "task_status": "active",
                    "power_design_context": context,
                }

        if (
            "product_alternates" in prepared.contract.requested_facts
            and selected_product_id is None
        ):
            pending = {
                "kind": "alternate_scope",
                "active_intent": "product_bom_alternate",
                "task_status": "awaiting_scope",
                "candidate_ids": list(material_candidate_ids),
            }

        entity_kind = prepared.contract.entity_kind
        if selected_material_id and prepared.explicit_material_switch:
            selected_project_id = None
            selected_bom_version = None
            project_candidate_ids = []
        if selected_project_id and prepared.explicit_project_switch:
            selected_material_id = None
            material_candidate_ids = []
        if selected_product_id and prepared.explicit_product_switch:
            if not alternate_entity.get("selected_alternate_material_id"):
                selected_material_id = None
                material_candidate_ids = []
            selected_project_id = None
            selected_bom_version = None
            project_candidate_ids = []

        values = {
            "selected_material_id": selected_material_id,
            "selected_project_id": selected_project_id,
            "selected_bom_version": selected_bom_version,
            "selected_product_id": selected_product_id,
            "selected_product_revision_id": selected_product_revision_id,
            "material_candidate_ids": material_candidate_ids,
            "project_candidate_ids": project_candidate_ids,
            "product_candidate_ids": product_candidate_ids,
            "pending_disambiguation": pending,
            "last_entity_kind": entity_kind,
            "last_intent": intent or "",
            "last_requested_facts": sorted(prepared.contract.requested_facts),
            "last_build_quantity": last_build_quantity,
            "expires_at": _now() + self.ttl,
            "updated_at": _now(),
            "context_version": snapshot.context_version + 1,
        }
        next_snapshot = ConversationSnapshot(
            id=snapshot.id,
            user_id=snapshot.user_id,
            context_version=snapshot.context_version + 1,
            selected_material_id=selected_material_id,
            selected_project_id=selected_project_id,
            selected_bom_version=selected_bom_version,
            selected_product_id=selected_product_id,
            selected_product_revision_id=selected_product_revision_id,
            material_candidate_ids=tuple(material_candidate_ids),
            project_candidate_ids=tuple(project_candidate_ids),
            product_candidate_ids=tuple(product_candidate_ids),
            pending_disambiguation=pending,
            last_entity_kind=entity_kind,
            last_intent=intent or "",
            last_requested_facts=tuple(sorted(prepared.contract.requested_facts)),
            last_build_quantity=last_build_quantity,
        )
        return values, next_snapshot

    @staticmethod
    def _power_design_context(entity: dict[str, Any]) -> dict[str, Any]:
        """Retain only explicit electrical requirements for this conversation."""

        requirements = dict(entity.get("requirements") or {})
        if (
            requirements.get("input_voltage_v") is None
            or requirements.get("output_voltage_v") is None
        ):
            return {}
        keys = (
            "input_voltage_v",
            "output_voltage_v",
            "load_current_a",
            "load_current_min_a",
            "load_current_max_a",
            "load_current_range_a",
            "load_current_cases_a",
            "analog_load_current_a",
            "digital_load_current_a",
            "topology_choice",
            "intermediate_voltage_v",
            "topology_constraints",
        )
        return {"requirements": {key: requirements.get(key) for key in keys}}

    @staticmethod
    def _power_design_followup_message(message: str, context: dict[str, Any]) -> str | None:
        """Bind a short power follow-up to the same conversation's explicit rails."""

        folded = message.casefold()
        if any(marker in folded for marker in ("库存", "库位", "备料", "物料清单")):
            return None
        parsed = extract_power_requirement(message)
        if parsed.input_voltage_v is not None and parsed.output_voltage_v is not None:
            return None
        if not parsed.has_load_current and not any(
            marker in folded
            for marker in (
                "ldo",
                "buck",
                "损耗",
                "功耗",
                "温升",
                "压差",
                "dropout",
                "psrr",
                "纹波",
                "负载",
                "电流",
                "分轨",
                "模拟",
                "数字",
                "pdf",
                "数据手册",
                "datasheet",
            )
        ):
            return None
        requirements = context.get("requirements") or {}
        input_v = requirements.get("input_voltage_v")
        output_v = requirements.get("output_voltage_v")
        if input_v is None or output_v is None:
            return None

        prefix = f"{input_v}V→{output_v}V"
        intermediate = requirements.get("intermediate_voltage_v")
        if intermediate is not None and "ldo" not in folded:
            prefix += f"，Buck+LDO，后级 LDO 输入为{intermediate}V"
        elif intermediate is not None and not any(
            marker in folded for marker in ("后级 ldo 输入", "ldo 输入", "中间轨")
        ):
            prefix += f"，后级 LDO 输入为{intermediate}V"
        constraints = set(requirements.get("topology_constraints") or [])
        if {"buck", "ldo"}.issubset(constraints) and "buck" not in folded:
            prefix += "，Buck+LDO"

        current_present = parsed.has_load_current or any(
            value is not None
            for value in (parsed.analog_load_current_a, parsed.digital_load_current_a)
        )
        if not current_present:
            current = requirements.get("load_current_a") or requirements.get("load_current_max_a")
            if current is not None:
                try:
                    prefix += f"，本对话负载{Decimal(str(current)) * Decimal('1000')}mA"
                except (InvalidOperation, ValueError):
                    pass
        return f"{prefix}；{message}"

    @staticmethod
    def _engineering_research_context(entity: dict[str, Any]) -> dict[str, Any]:
        """Persist bounded research facts for the next turn, never prompts."""

        plan = entity.get("plan") or {}
        draft = entity.get("draft") or {}
        requirements = dict(entity.get("requirements") or plan.get("requirements") or {})
        requirements.pop("raw_text", None)
        candidates: list[dict[str, Any]] = []
        for topology in ("buck", "ldo"):
            branch = draft.get(topology) or {}
            for candidate in branch.get("candidates") or []:
                inventory = candidate.get("inventory") or {}
                locations = candidate.get("locations") or {}
                candidates.append(
                    {
                        "material_id": candidate.get("material_id"),
                        "code": candidate.get("code"),
                        "mpn": candidate.get("mpn") or candidate.get("code"),
                        "package": candidate.get("package"),
                        "topology": topology,
                        "component_class": candidate.get("component_class"),
                        "class_source": candidate.get("class_source"),
                        "class_match": candidate.get("class_match"),
                        "rejection_reason": candidate.get("rejection_reason"),
                        "provenance": candidate.get("provenance") or {},
                        "peripheral_roles": [
                            {
                                "role": item.get("role"),
                                "exact_value": item.get("exact_value"),
                                "value_status": item.get("value_status"),
                                "evidence": item.get("evidence"),
                                "connection": item.get("connection"),
                                "constraint_value": item.get("constraint_value"),
                                "source_document_revision": item.get("source_document_revision"),
                                "source_page": item.get("source_page"),
                            }
                            for item in (candidate.get("peripheral_roles") or [])
                        ][:8],
                        "evidence_coverage": candidate.get("evidence_coverage"),
                        "evidence_status": candidate.get("evidence_status"),
                        "evidence_gaps": list(candidate.get("evidence_gaps") or [])[:12],
                        "evidence_facts": [
                            {
                                key: fact.get(key)
                                for key in (
                                    "field",
                                    "value",
                                    "unit",
                                    "variant",
                                    "anchor_id",
                                    "conditions",
                                    "fact_type",
                                )
                                if fact.get(key) is not None
                            }
                            for fact in (candidate.get("evidence_facts") or [])
                        ][:16],
                        "thermal_analysis": candidate.get("thermal_analysis") or {},
                        "calculations": candidate.get("calculations") or [],
                        "electrical_thermal_judgment": candidate.get("electrical_thermal_judgment"),
                        "citations": [
                            {
                                key: citation.get(key)
                                for key in (
                                    "document_key",
                                    "document_revision",
                                    "page",
                                    "physical_page",
                                    "section",
                                    "anchor_id",
                                    "file_sha256",
                                    "synthetic_fixture",
                                )
                                if citation.get(key) is not None
                            }
                            for citation in (candidate.get("citations") or [])
                        ][:8],
                        "inventory": {
                            key: inventory.get(key)
                            for key in (
                                "material_id",
                                "code",
                                "mpn",
                                "name",
                                "unit",
                                "quantity",
                                "reserved_quantity",
                                "available_quantity",
                                "safety_stock",
                                "low_stock",
                            )
                            if inventory.get(key) is not None
                        },
                        "locations": {
                            "distribution_status": locations.get("distribution_status"),
                            "count": locations.get("count"),
                            "locations": [
                                {
                                    key: item.get(key)
                                    for key in (
                                        "location_id",
                                        "code",
                                        "name",
                                        "full_path",
                                        "quantity_at_location",
                                        "quantity_is_exact",
                                    )
                                    if item.get(key) is not None
                                }
                                for item in (locations.get("locations") or [])
                            ][:8],
                        },
                    }
                )
        return {
            "requirements": requirements,
            "candidates": candidates[:8],
            "candidate_context_pool": [
                {
                    key: item.get(key)
                    for key in (
                        "material_id",
                        "code",
                        "mpn",
                        "package",
                        "topology",
                        "component_class",
                        "class_source",
                        "class_match",
                        "rejection_reason",
                        "provenance",
                        "peripheral_roles",
                    )
                    if item.get(key) is not None
                }
                for item in (entity.get("candidate_context_pool") or [])
                if isinstance(item, dict) and item.get("material_id") is not None
            ][:8],
            "topologies": draft.get("topologies") or entity.get("topologies") or [],
            "rail_bom_draft": draft.get("rail_bom_draft") or entity.get("rail_bom_draft") or {},
            "focus_scope": entity.get("focus_scope") or plan.get("focus_scope") or "primary",
            "selected_primary_material_id": entity.get("selected_primary_material_id")
            or plan.get("selected_primary_material_id"),
            "peripheral_requirements": [
                {
                    key: item.get(key)
                    for key in (
                        "requirement_id",
                        "role",
                        "selected_primary_material_id",
                        "selected_primary_mpn",
                        "value",
                        "unit",
                        "capacitance_pf",
                        "rated_voltage_v",
                        "dielectric",
                        "tolerance",
                        "package",
                        "connection",
                        "constraint_value",
                        "required_quantity",
                        "specification_status",
                        "evidence_status",
                        "evidence_gap",
                        "component_class",
                        "expected_component_classes",
                        "component_class_gate",
                        "provenance",
                        "completeness_contribution",
                        "source_anchor",
                        "source_value",
                        "constraints",
                        "material_candidate_ids",
                        "candidates",
                        "selection_status",
                        "selected_material_id",
                        "selection_basis",
                        "selection_provenance",
                        "selection_conflict",
                        "selection_resolution",
                        "matched_material_id",
                        "matched_code",
                        "matched_mpn",
                        "available_quantity",
                        "shortage_quantity",
                        "location",
                        "location_status",
                        "unknowns",
                        "search_query",
                        "inventory_checked",
                    )
                    if item.get(key) is not None
                }
                for item in (entity.get("peripheral_requirements") or [])
                if isinstance(item, dict)
            ][:12],
            "engineering_bom_draft": dict(entity.get("engineering_bom_draft") or {}),
            "active_selection_context": dict(
                entity.get("active_selection_context")
                or (entity.get("engineering_bom_draft") or {}).get("active_selection_context")
                or {}
            ),
            "selection_action_result": dict(
                entity.get("selection_action_result")
                or (entity.get("engineering_bom_draft") or {}).get("selection_action_result")
                or {}
            ),
            "completeness": dict(
                entity.get("completeness")
                or (entity.get("engineering_bom_draft") or {}).get("completeness")
                or (draft.get("completeness") or {})
            ),
            "peripheral_tool_audit": [
                {
                    "tool": item.get("tool"),
                    "arguments": item.get("arguments") or {},
                    "status": item.get("status"),
                    "error_code": item.get("error_code"),
                }
                for item in (entity.get("peripheral_tool_audit") or [])
                if isinstance(item, dict)
            ][:30],
            "round": int((entity.get("plan") or {}).get("round") or 1),
            "candidate_status": draft.get("candidate_status"),
            "evidence_status": draft.get("evidence_status"),
            "draft_status": draft.get("draft_status"),
            "adaptive": dict(plan.get("adaptive") or {}),
            "active_candidate_ids": [
                int(item)
                for item in (entity.get("active_candidate_ids") or [])
                if str(item).isdigit()
            ][:4],
        }

    def _validated_snapshot(self, row: AgentConversationContext) -> ConversationSnapshot:
        material = self._material(row.selected_material_id)
        project = self._project(row.selected_project_id)
        product = self._product(row.selected_product_id)
        product_revision = self._product_revision(
            row.selected_product_revision_id,
            product_id=product.id if product else None,
        )
        material_ids = tuple(item.id for item in self._materials(row.material_candidate_ids or []))
        project_ids = tuple(item.id for item in self._projects(row.project_candidate_ids or []))
        product_ids = tuple(item.id for item in self._products(row.product_candidate_ids or []))
        pending = dict(row.pending_disambiguation or {})
        if pending.get("kind") == "material" and not material_ids:
            pending = {}
        if pending.get("kind") == "project" and not project_ids:
            pending = {}
        if pending.get("kind") == "product" and not product_ids:
            pending = {}
        return ConversationSnapshot(
            id=row.id,
            user_id=row.user_id,
            context_version=row.context_version,
            selected_material_id=material.id if material else None,
            selected_project_id=project.id if project else None,
            selected_bom_version=row.selected_bom_version if project else None,
            selected_product_id=product.id if product else None,
            selected_product_revision_id=(product_revision.id if product_revision else None),
            material_candidate_ids=material_ids,
            project_candidate_ids=project_ids,
            product_candidate_ids=product_ids,
            pending_disambiguation=pending,
            last_entity_kind=row.last_entity_kind,
            last_intent=row.last_intent,
            last_requested_facts=tuple(row.last_requested_facts or []),
            last_build_quantity=row.last_build_quantity,
        )

    @staticmethod
    def _snapshot(row: AgentConversationContext) -> ConversationSnapshot:
        return ConversationSnapshot(
            id=row.id,
            user_id=row.user_id,
            context_version=row.context_version,
            selected_material_id=row.selected_material_id,
            selected_project_id=row.selected_project_id,
            selected_bom_version=row.selected_bom_version,
            selected_product_id=row.selected_product_id,
            selected_product_revision_id=row.selected_product_revision_id,
            material_candidate_ids=tuple(row.material_candidate_ids or []),
            project_candidate_ids=tuple(row.project_candidate_ids or []),
            product_candidate_ids=tuple(row.product_candidate_ids or []),
            pending_disambiguation=dict(row.pending_disambiguation or {}),
            last_entity_kind=row.last_entity_kind,
            last_intent=row.last_intent,
            last_requested_facts=tuple(row.last_requested_facts or []),
            last_build_quantity=row.last_build_quantity,
        )

    def _material(self, material_id: int | None) -> Material | None:
        if not material_id:
            return None
        return self.db.scalar(
            select(Material).where(
                Material.id == material_id,
                Material.is_deleted.is_(False),
            )
        )

    def _materials(self, material_ids: Any) -> list[Material]:
        ids = [int(item) for item in material_ids or []]
        if not ids:
            return []
        rows = list(
            self.db.scalars(
                select(Material).where(
                    Material.id.in_(ids),
                    Material.is_deleted.is_(False),
                )
            ).all()
        )
        by_id = {row.id: row for row in rows}
        return [by_id[item] for item in ids if item in by_id]

    def _project(self, project_id: int | None) -> Project | None:
        return self.db.get(Project, project_id) if project_id else None

    def _projects(self, project_ids: Any) -> list[Project]:
        ids = [int(item) for item in project_ids or []]
        if not ids:
            return []
        rows = list(self.db.scalars(select(Project).where(Project.id.in_(ids))).all())
        by_id = {row.id: row for row in rows}
        return [by_id[item] for item in ids if item in by_id]

    def _product(self, product_id: int | None) -> Product | None:
        if not product_id:
            return None
        return self.db.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.lifecycle_status == "active",
            )
        )

    def _products(self, product_ids: Any) -> list[Product]:
        ids = [int(item) for item in product_ids or []]
        if not ids:
            return []
        rows = list(
            self.db.scalars(
                select(Product).where(
                    Product.id.in_(ids),
                    Product.lifecycle_status == "active",
                )
            ).all()
        )
        by_id = {row.id: row for row in rows}
        return [by_id[item] for item in ids if item in by_id]

    def _product_matches(self, message: str) -> list[Product]:
        folded_message = _fold(message)
        matches: list[Product] = []
        for product in self.db.scalars(
            select(Product).where(Product.lifecycle_status == "active").order_by(Product.code)
        ).all():
            code_parts = [
                part for part in re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", product.code or "") if part
            ]
            aliases = {_fold(product.code), _fold(product.name)}
            if code_parts and code_parts[0].casefold() in {"prod", "product"}:
                aliases.add(_fold("".join(code_parts[1:])))
            if any(alias and len(alias) >= 3 and alias in folded_message for alias in aliases):
                matches.append(product)
        return matches

    def _product_revision(
        self,
        revision_id: int | None,
        *,
        product_id: int | None = None,
    ) -> ProductRevision | None:
        if not revision_id:
            return None
        conditions = [
            ProductRevision.id == revision_id,
            ProductRevision.status != "obsolete",
        ]
        if product_id is not None:
            conditions.append(ProductRevision.product_id == product_id)
        return self.db.scalar(select(ProductRevision).where(*conditions))

    def _default_revision(self, product_id: int) -> ProductRevision | None:
        revisions = list(
            self.db.scalars(
                select(ProductRevision).where(
                    ProductRevision.product_id == product_id,
                    ProductRevision.status == "released",
                    ProductRevision.is_default.is_(True),
                )
            ).all()
        )
        return revisions[0] if len(revisions) == 1 else None

    def _product_revisions(self, product_ids: list[int]) -> dict[int, list[ProductRevision]]:
        result = {product_id: [] for product_id in product_ids}
        if not product_ids:
            return result
        rows = list(
            self.db.scalars(
                select(ProductRevision)
                .where(
                    ProductRevision.product_id.in_(product_ids),
                    ProductRevision.status == "released",
                )
                .order_by(ProductRevision.product_id, ProductRevision.revision)
            ).all()
        )
        for revision in rows:
            result[revision.product_id].append(revision)
        return result

    def _all_product_revisions(self, product_id: int) -> list[ProductRevision]:
        return list(
            self.db.scalars(
                select(ProductRevision)
                .where(
                    ProductRevision.product_id == product_id,
                    ProductRevision.status != "obsolete",
                )
                .order_by(ProductRevision.revision)
            ).all()
        )

    def _project_versions(self, project_ids: list[int]) -> dict[int, list[str]]:
        from app.models import BomItem

        result = {project_id: [] for project_id in project_ids}
        if not project_ids:
            return result
        rows = self.db.execute(
            select(BomItem.project_id, BomItem.version)
            .where(BomItem.project_id.in_(project_ids))
            .distinct()
            .order_by(BomItem.project_id, BomItem.version)
        ).all()
        for project_id, version in rows:
            result[project_id].append(version)
        return result

    @staticmethod
    def _ordinal_index(message: str) -> int | None:
        folded = _fold(message)
        markers = (
            ("第一个", "第1个", "第一条", "第1条", "first"),
            ("第二个", "第2个", "第二条", "第2条", "second"),
            ("第三个", "第3个", "第三条", "第3条", "third"),
        )
        for index, values in enumerate(markers):
            if any(_fold(value) in folded for value in values):
                return index
        return None

    def _prepare_cable_continuation(
        self,
        snapshot: ConversationSnapshot,
        message: str,
        candidates: list[Material],
    ) -> PreparedConversationTurn | None:
        task = dict(snapshot.pending_disambiguation)
        ordinal = self._ordinal_index(message)
        folded = message.casefold()
        if (
            any(
                marker in folded
                for marker in (
                    "哪些物料低于",
                    "低库存",
                    "安全库存以下",
                    "库存预警",
                )
            )
            or self._looks_like_project(message)
            or self._looks_like_product(message)
        ):
            return None
        explicit_material = self._explicit_material_reference(message)
        if explicit_material is not None and all(
            explicit_material.id != candidate.id for candidate in candidates
        ):
            return None
        wants_location = any(
            marker in folded for marker in ("在哪", "位置", "库位", "去哪拿", "去找")
        )
        wants_inventory = any(
            marker in folded for marker in ("库存", "现货", "还有", "多少", "可用", "剩")
        )
        select_longest = any(marker in folded for marker in ("最长", "longest"))
        availability_sort = any(
            marker in folded for marker in ("有现货", "现货优先", "库存多", "放前面")
        )
        if candidates and (
            ordinal is not None or wants_location or wants_inventory or select_longest
        ):
            selected = self._match_material(message, candidates)
            if selected is None and select_longest:
                selected = self._select_cable_by_length(candidates, longest=True)
            if (
                selected is None
                and wants_location
                and ordinal is None
                and explicit_material is None
            ):
                # The cable result is already a ranked, server-owned candidate
                # set.  A plain location follow-up resolves its first ranked
                # candidate instead of falling back to generic material search.
                selected = candidates[0]
            if selected is None and len(candidates) == 1:
                selected = candidates[0]
            if selected is not None:
                requested = {"cable_search"}
                if wants_location:
                    requested.add("location")
                if wants_inventory:
                    requested.add("inventory")
                return PreparedConversationTurn(
                    snapshot=replace(snapshot, selected_material_id=selected.id),
                    entities={
                        "material_candidates": self._material_entity([selected], selected.id)
                    },
                    contract=TaskContract(
                        entity_kind="cable",
                        requested_facts=requested,
                        requires_material_resolution=False,
                    ),
                    effective_message=f"{selected.code} 线缆 {message}",
                )

        parsed = CableSearchService.parse_constraints(message)
        refinement_fields = {
            key: value
            for key, value in parsed.items()
            if key != "length_is_soft" and value not in (None, "")
        }
        policy_continuation = bool(
            (task.get("policy_flags") or {}).get("purchase_quantity_not_inventory")
            and any(marker in folded for marker in ("现在", "还有", "库存", "对吧"))
        )
        is_refinement = (
            bool(refinement_fields)
            or self._is_cable_constraint_refinement(message)
            or policy_continuation
            or availability_sort
        )
        if not is_refinement:
            if task.get("task_status") == "awaiting_clarification":
                return PreparedConversationTurn(
                    snapshot=snapshot,
                    entities={},
                    contract=TaskContract(entity_kind="cable"),
                    direct_answer="请确认触点方向：同向（A 型）还是反向（B 型）？",
                    direct_intent="clarify_cable_direction",
                )
            return None

        slots = dict(task.get("slots") or {})
        slots.update(refinement_fields)
        effective_message = CableSearchService.query_from_constraints(slots)
        if availability_sort:
            effective_message = f"{effective_message} 有现货的放前面".strip()
        if (task.get("policy_flags") or {}).get("purchase_quantity_not_inventory"):
            effective_message += " 采购数量不能作为当前库存"
        return PreparedConversationTurn(
            snapshot=replace(
                snapshot,
                pending_disambiguation={
                    **task,
                    "task_status": "active",
                    "pending_slot": None,
                    "slots": slots,
                },
            ),
            entities={},
            contract=TaskContract(
                entity_kind="cable",
                requested_facts={"cable_search"},
                requires_material_resolution=False,
            ),
            effective_message=effective_message,
        )

    def _prepare_component_continuation(
        self,
        snapshot: ConversationSnapshot,
        message: str,
        candidates: list[Material],
    ) -> PreparedConversationTurn | None:
        folded = message.casefold()
        selected = self._match_material(message, candidates) if candidates else None
        select_highest_inventory = "库存多" in folded and any(
            marker in folded for marker in ("那个", "哪一个", "放哪", "在哪", "位置", "库位")
        )
        if selected is None and candidates and select_highest_inventory:
            selected = max(candidates, key=lambda item: item.available_quantity)
        if selected is None:
            selected = self._explicit_material_reference(message)
        classified = classify_task_contract(message, selected_material=selected is not None)
        if selected is not None and select_highest_inventory:
            classified = classified.model_copy(
                update={"requested_facts": set(classified.requested_facts) | {"inventory"}}
            )
        asks_fact = bool({"inventory", "location"}.intersection(classified.requested_facts))
        asks_engineering = bool(
            {
                "component_relations",
                "engineering_evidence",
                "component_evidence_comparison",
            }.intersection(classified.requested_facts)
        )
        if (
            selected is None
            and len(candidates) >= 2
            and asks_engineering
            and any(marker in folded for marker in ("另一个", "这两个", "它们"))
        ):
            return PreparedConversationTurn(
                snapshot=replace(
                    snapshot,
                    selected_material_id=None,
                    material_candidate_ids=tuple(item.id for item in candidates),
                    pending_disambiguation={
                        "kind": "material",
                        "candidate_ids": [item.id for item in candidates],
                    },
                ),
                entities={"material_candidates": self._material_entity(candidates, None)},
                contract=classified.model_copy(
                    update={
                        "entity_kind": "material",
                        "requires_material_resolution": False,
                    }
                ),
            )
        if selected is not None and (asks_fact or asks_engineering):
            return PreparedConversationTurn(
                snapshot=replace(snapshot, selected_material_id=selected.id),
                entities={"material_candidates": self._material_entity([selected], selected.id)},
                contract=classified.model_copy(
                    update={
                        "entity_kind": "material",
                        "requires_material_resolution": False,
                    }
                ),
            )
        if selected is not None:
            return PreparedConversationTurn(
                snapshot=replace(snapshot, selected_material_id=selected.id),
                entities={"material_candidates": self._material_entity([selected], selected.id)},
                contract=TaskContract(
                    entity_kind="material",
                    requires_material_resolution=False,
                ),
                direct_answer=(
                    f"已选择 {selected.mpn or selected.code}。你可以继续问库存或存放位置。"
                ),
                direct_intent="select_material",
            )

        refinement = any(
            marker in folded
            for marker in (
                "优先",
                "支持",
                "能跑",
                "can-fd",
                "can fd",
                "供电",
                "接口",
                "封装",
                "现货",
                "库存多",
                "放前面",
                "排序",
                "从高到低",
            )
        )
        if not refinement:
            return None
        slots = dict(snapshot.pending_disambiguation.get("slots") or {})
        parts: list[str] = []
        for value in slots.get("component_types") or []:
            parts.append(str(value))
        for value in slots.get("interfaces") or []:
            parts.append(str(value))
        supply = slots.get("supply_voltage_v")
        if supply is not None:
            parts.append(f"{supply}V 供电")
        for value in slots.get("package_preferences") or []:
            parts.append(str(value))
        parts.append(message)
        effective_message = " ".join(parts)
        return PreparedConversationTurn(
            snapshot=snapshot,
            entities={},
            contract=TaskContract(
                entity_kind="component",
                requested_facts={"component_search"},
            ),
            effective_message=effective_message,
        )

    def _match_material(self, message: str, candidates: list[Material]) -> Material | None:
        ordinal = self._ordinal_index(message)
        if ordinal is not None:
            return candidates[ordinal] if ordinal < len(candidates) else None
        needle = _selection_needle(message)
        folded_message = _fold(message)
        direct_matches = [
            item
            for item in candidates
            if any(
                value and value in folded_message for value in (_fold(item.code), _fold(item.mpn))
            )
        ]
        if len(direct_matches) == 1:
            return direct_matches[0]
        tokens = [_fold(token) for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", message)]
        partial_mpn_matches = [
            item
            for item in candidates
            if any(token and token in _fold(item.mpn) for token in tokens)
        ]
        if len(partial_mpn_matches) == 1:
            return partial_mpn_matches[0]
        partial_code_matches = [
            item
            for item in candidates
            if any(token and token in _fold(item.code) for token in tokens)
        ]
        if len(partial_code_matches) == 1:
            return partial_code_matches[0]
        matches = [
            item
            for item in candidates
            if needle
            and any(
                needle == value or needle in value
                for value in (_fold(item.code), _fold(item.name), _fold(item.mpn))
                if value
            )
        ]
        return matches[0] if len(matches) == 1 else None

    def _explicit_relation_materials(
        self,
        message: str,
        contract: TaskContract,
    ) -> list[Material]:
        if not {
            "component_relations",
            "component_evidence_comparison",
        }.intersection(contract.requested_facts):
            return []
        tokens = list(dict.fromkeys(match.group(0) for match in _MATERIAL_TOKEN.finditer(message)))
        if len(tokens) < 2:
            return []
        rows = list(
            self.db.scalars(
                select(Material).where(
                    Material.is_deleted.is_(False),
                    Material.is_active.is_(True),
                )
            ).all()
        )
        matches = [
            row
            for token in tokens
            for row in rows
            if any(_fold(token) in value for value in (_fold(row.code), _fold(row.mpn)) if value)
        ]
        unique = {row.id: row for row in matches}
        return list(unique.values()) if len(unique) >= 2 else []

    def _explicit_material_reference(self, message: str) -> Material | None:
        tokens = [_fold(token) for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", message)]
        if not tokens:
            return None
        rows = list(
            self.db.scalars(
                select(Material).where(
                    Material.is_deleted.is_(False),
                    Material.is_active.is_(True),
                )
            ).all()
        )
        mpn_matches = [row for row in rows if any(token in _fold(row.mpn) for token in tokens)]
        unique = {row.id: row for row in mpn_matches}
        if len(unique) == 1:
            return next(iter(unique.values()))
        code_matches = [row for row in rows if any(token in _fold(row.code) for token in tokens)]
        unique = {row.id: row for row in code_matches}
        return next(iter(unique.values())) if len(unique) == 1 else None

    def _mentions_known_product(self, message: str) -> bool:
        """Recognize an active Product name/code as scope, without selecting it."""

        folded_message = _fold(message)
        if not folded_message:
            return False
        rows = self.db.execute(
            select(Product.code, Product.name).where(Product.lifecycle_status == "active")
        ).all()
        for code, name in rows:
            aliases = {_fold(code), _fold(name)}
            code_parts = [
                part for part in re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", code or "") if part
            ]
            if code_parts and code_parts[0].casefold() in {"prod", "product"}:
                aliases.add(_fold("".join(code_parts[1:])))
            if len(code_parts) > 1 and len(code_parts[1]) >= 4:
                aliases.add(_fold(code_parts[1]))
            if any(alias and len(alias) >= 3 and alias in folded_message for alias in aliases):
                return True
        return False

    @staticmethod
    def _select_cable_by_length(
        candidates: list[Material],
        *,
        longest: bool,
    ) -> Material | None:
        scored: list[tuple[Decimal, Material]] = []
        for item in candidates:
            raw = (item.attributes or {}).get("length_cm")
            if raw in (None, ""):
                continue
            try:
                scored.append((Decimal(str(raw)), item))
            except (InvalidOperation, ValueError):
                continue
        if not scored:
            return None
        selector = max if longest else min
        return selector(scored, key=lambda pair: pair[0])[1]

    @staticmethod
    def _is_cable_constraint_refinement(message: str) -> bool:
        return bool(
            re.search(r"\d+(?:\.\d+)?\s*(?:cm|厘米|公分)", message, re.I)
            and any(marker in message.casefold() for marker in ("呢", "改", "换", "看看"))
        )

    @staticmethod
    def _cable_refinement_query(message: str, candidates: list[Material]) -> str:
        """Retain only common structured cable constraints, never prior prose."""

        attributes = [item.attributes or {} for item in candidates]

        def common(field: str):
            values = {item.get(field) for item in attributes if item.get(field) not in (None, "")}
            return next(iter(values)) if len(values) == 1 else None

        parts: list[str] = []
        pitch = common("connector_pitch_mm")
        if pitch is not None:
            parts.append(f"{pitch}mm")
        pins = common("pin_count")
        pins_b = common("pin_count_b")
        if pins:
            parts.append(f"{pins}Pin" + (f" 转 {pins_b}Pin" if pins_b else ""))
        cable_kind = common("cable_kind")
        kind_labels = {
            "terminal": "端子线",
            "flat_flex": "FFC 排线",
            "micro_coax": "极细同轴线",
            "rf_coax": "IPEX 射频同轴线",
        }
        if cable_kind in kind_labels:
            parts.append(kind_labels[cable_kind])
        direction = common("direction")
        if direction in {"same", "reverse"}:
            parts.append("同向" if direction == "same" else "反向")
        end_style = common("end_style")
        end_labels = {
            "double": "双头",
            "single": "单头",
            "single_tinned": "单头沾锡",
            "male_female_pair": "公母对接",
        }
        if end_style in end_labels:
            parts.append(end_labels[end_style])
        parts.append(message)
        return " ".join(str(item) for item in parts)

    def _match_project(self, message: str, candidates: list[Project]) -> Project | None:
        ordinal = self._ordinal_index(message)
        if ordinal is not None:
            return candidates[ordinal] if ordinal < len(candidates) else None
        needle = _selection_needle(message)
        matches = [
            item
            for item in candidates
            if needle
            and any(
                needle == value or needle in value
                for value in (_fold(item.code), _fold(item.name))
                if value
            )
        ]
        return matches[0] if len(matches) == 1 else None

    def _match_product(self, message: str, candidates: list[Product]) -> Product | None:
        ordinal = self._ordinal_index(message)
        if ordinal is not None:
            return candidates[ordinal] if ordinal < len(candidates) else None
        needle = _selection_needle(message)
        matches = [
            item
            for item in candidates
            if needle
            and any(
                needle == value or needle in value or value in needle
                for value in (_fold(item.code), _fold(item.name))
                if value
            )
        ]
        return matches[0] if len(matches) == 1 else None

    def _match_product_revision(
        self,
        message: str,
        product_id: int,
    ) -> ProductRevision | None:
        folded = _fold(message)
        revisions = list(
            self.db.scalars(
                select(ProductRevision).where(
                    ProductRevision.product_id == product_id,
                    ProductRevision.status != "obsolete",
                )
            ).all()
        )
        matches = [
            revision
            for revision in revisions
            if _fold(revision.revision) and _fold(revision.revision) in folded
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _looks_like_product(message: str) -> bool:
        folded = message.casefold()
        explicit = any(marker in folded for marker in ("产品", "单台", "product", "prod-")) or bool(
            re.search(
                r"(?<![A-Z0-9])(?:PROD-[A-Z0-9-]+|(?:EVT|DVT|PVT)-R?\d+)",
                message,
                re.I,
            )
        )
        build_shaped = bool(
            re.search(
                r"(?:生产|再生产|做|再做|按|计划构建)\s*-?\d+(?:\.\d+)?\s*(?:台|套|个)",
                message,
                re.I,
            )
        )
        explicit_project = any(marker in folded for marker in ("项目", "project", "prj-")) or bool(
            re.search(r"(?<![A-Z0-9])(?:RB|PRJ)-[A-Z0-9-]+", message, re.I)
        )
        return explicit or (build_shaped and not explicit_project)

    @staticmethod
    def _looks_like_project(message: str) -> bool:
        if ConversationContextService._looks_like_product(message):
            return False
        folded = message.casefold()
        return any(marker in folded for marker in ("项目", "project", "prj-", "bom")) or bool(
            re.search(r"(?<![A-Z0-9])(?:RB|PRJ)-[A-Z0-9-]+", message, re.I)
        )

    @staticmethod
    def _is_project_followup(message: str) -> bool:
        folded = message.casefold()
        return any(
            marker in folded
            for marker in (
                "缺什么料",
                "缺的料",
                "缺料",
                "库存够",
                "够不够",
                "bom",
                "带我去找",
                "带我去拿",
                "带我找",
                "去哪拿",
                "从哪拿",
                "开始找料",
                "开始拿料",
            )
        )

    @staticmethod
    def _is_product_followup(message: str) -> bool:
        folded = message.casefold()
        return any(
            marker in folded
            for marker in (
                "版本",
                "revision",
                "evt-",
                "dvt-",
                "够不够",
                "够吗",
                "备选",
                "批准",
                "can 芯片",
                "台呢",
                "套呢",
                "个呢",
            )
        )

    @staticmethod
    def _is_relation_followup(message: str) -> bool:
        folded = message.casefold()
        return any(
            marker in folded
            for marker in (
                "关系",
                "相似",
                "替代",
                "pin compatible",
                "pincompatible",
                "引脚兼容",
            )
        )

    @staticmethod
    def _is_anaphoric_followup(message: str) -> bool:
        folded = message.casefold().strip(" ？?。！!")
        return any(
            marker in folded
            for marker in (
                "它",
                "这个",
                "那个",
                "这条",
                "那条",
                "第二条",
                "同向",
                "反向",
                "那块板",
                "这个板",
                "我说的是",
                "那  ",
            )
        ) or bool(re.fullmatch(r"那\s*\d+(?:\.\d+)?\s*(?:台|套|个)(?:呢)?", folded))

    @staticmethod
    def _is_multi_material_followup(
        message: str,
        snapshot: ConversationSnapshot,
    ) -> bool:
        folded = message.casefold()
        markers = (
            "关系",
            "相似",
            "替代",
            "比较",
            "compare",
            "差异",
            "证据",
            "依据",
            "资料",
            "为什么",
            "规格书",
            "datasheet",
            "供电",
            "电压",
            "引脚",
            "pin",
            "接口",
            "封装",
            "分辨率",
            "输出电流",
        )
        return any(marker in folded for marker in markers) or bool(
            {"component_relations", "component_evidence_comparison"}.intersection(
                snapshot.last_requested_facts
            )
        )

    @staticmethod
    def _elliptical_product(message: str) -> bool:
        folded = message.casefold().strip(" ？?。！!")
        if any(marker in folded for marker in ("这个备选", "那个备选", "该备选", "这个替代料")):
            return True
        return bool(
            re.fullmatch(
                r"(?:那)?\s*(?:做)?\s*-?\d+(?:\.\d+)?\s*(?:台|套|个)(?:呢|够不够|够吗)?",
                folded,
            )
        )

    @staticmethod
    def _elliptical_material(message: str, contract: TaskContract) -> bool:
        folded = message.casefold().strip(" ？?。！!")
        pronoun = any(
            marker in folded for marker in ("它", "它们", "这个", "那个", "这两个", "那两个")
        ) or bool(re.match(r"^那\s*(?:pin|引脚|供电|电压|接口|封装|证据)", folded))
        standalone = folded in {
            "还有多少",
            "有多少",
            "多少",
            "在哪",
            "在哪里",
            "位置呢",
            "库存呢",
        }
        return contract.entity_kind == "material" and (pronoun or standalone)

    @staticmethod
    def _elliptical_project(message: str, contract: TaskContract) -> bool:
        folded = message.casefold()
        return contract.entity_kind == "project" and any(
            marker in folded
            for marker in (
                "缺什么料",
                "缺料",
                "bom 呢",
                "库存够",
                "够不够",
                "带我去找",
                "带我去拿",
                "带我找",
                "去哪拿",
                "从哪拿",
                "开始找料",
                "开始拿料",
            )
        )

    @staticmethod
    def _material_choice_answer(candidates: list[Material]) -> str:
        labels = "、".join(item.code for item in candidates)
        return f"找到多个候选（{labels}），请选择候选卡片。"

    @staticmethod
    def _project_choice_answer(candidates: list[Project]) -> str:
        labels = "、".join(item.code for item in candidates)
        return f"找到多个候选（{labels}），请选择候选列表。"

    @staticmethod
    def _product_choice_answer(candidates: list[Product]) -> str:
        labels = "、".join(item.code for item in candidates)
        return f"找到多个候选（{labels}），请选择候选列表。"

    @staticmethod
    def _material_entity(items: list[Material], selected_id: int | None) -> dict[str, Any]:
        return {
            "items": [_candidate_dict(item) for item in items],
            "count": len(items),
            "exact_match_ids": [selected_id] if selected_id else [],
            "selected_material_id": selected_id,
        }

    def _project_entity(
        self,
        items: list[Project],
        selected_id: int | None,
        *,
        selected_bom_version: str | None = None,
    ) -> dict[str, Any]:
        versions = self._project_versions([item.id for item in items])
        return {
            "items": [_project_dict(item, versions[item.id]) for item in items],
            "count": len(items),
            "exact_match_ids": [selected_id] if selected_id else [],
            "selected_project_id": selected_id,
            "selected_bom_version": selected_bom_version,
        }

    def _product_entity(
        self,
        items: list[Product],
        selected_id: int | None,
        *,
        selected_revision_id: int | None = None,
    ) -> dict[str, Any]:
        revisions = self._product_revisions([item.id for item in items])
        return {
            "items": [_product_dict(item, revisions[item.id]) for item in items],
            "count": len(items),
            "exact_match_ids": [selected_id] if selected_id else [],
            "selected_product_id": selected_id,
            "selected_product_revision_id": selected_revision_id,
        }


class InMemoryConversationContextService(ConversationContextService):
    """Structured context for read-only evaluators; never writes the source database."""

    def __init__(
        self,
        db: Session,
        user: User,
        config: Settings,
        store: dict[str, tuple[ConversationSnapshot, datetime]],
    ):
        super().__init__(db, user, config)
        self.store = store

    def open(self, conversation_id: str | None) -> ConversationSnapshot:
        if conversation_id is None:
            snapshot = ConversationSnapshot(
                id=str(uuid.uuid4()),
                user_id=self.user.id,
                context_version=0,
                selected_material_id=None,
                selected_project_id=None,
                selected_bom_version=None,
                selected_product_id=None,
                selected_product_revision_id=None,
                material_candidate_ids=(),
                project_candidate_ids=(),
                product_candidate_ids=(),
                pending_disambiguation={},
                last_entity_kind="unknown",
                last_intent="",
                last_requested_facts=(),
            )
            self.store[snapshot.id] = (snapshot, _now() + self.ttl)
            return snapshot

        stored = self.store.get(conversation_id)
        if stored is None or stored[0].user_id != self.user.id:
            raise BusinessError(
                "AGENT_CONVERSATION_NOT_FOUND",
                "这段对话不存在或无法访问",
                404,
            )
        snapshot, expires_at = stored
        if expires_at <= _now():
            raise BusinessError(
                "AGENT_CONVERSATION_EXPIRED",
                "这段对话的上下文已过期，请重新指定物料或项目。",
                409,
            )
        return self._revalidate(snapshot)

    def persist(
        self,
        prepared: PreparedConversationTurn,
        *,
        entities: dict[str, Any],
        intent: str | None,
    ) -> ConversationSnapshot:
        current = self.store.get(prepared.snapshot.id)
        if current is None or current[0].context_version != prepared.snapshot.context_version:
            raise BusinessError(
                "AGENT_CONVERSATION_CONFLICT",
                "这段对话刚刚收到另一条消息，请确认结果后重试。",
                409,
            )
        _values, next_snapshot = self._derive_update(
            prepared,
            entities=entities,
            intent=intent,
        )
        self.store[next_snapshot.id] = (next_snapshot, _now() + self.ttl)
        return next_snapshot

    def _revalidate(self, snapshot: ConversationSnapshot) -> ConversationSnapshot:
        material = self._material(snapshot.selected_material_id)
        project = self._project(snapshot.selected_project_id)
        product = self._product(snapshot.selected_product_id)
        product_revision = self._product_revision(
            snapshot.selected_product_revision_id,
            product_id=product.id if product else None,
        )
        material_ids = tuple(item.id for item in self._materials(snapshot.material_candidate_ids))
        project_ids = tuple(item.id for item in self._projects(snapshot.project_candidate_ids))
        product_ids = tuple(item.id for item in self._products(snapshot.product_candidate_ids))
        pending = dict(snapshot.pending_disambiguation)
        if pending.get("kind") == "material" and not material_ids:
            pending = {}
        if pending.get("kind") == "project" and not project_ids:
            pending = {}
        if pending.get("kind") == "product" and not product_ids:
            pending = {}
        return replace(
            snapshot,
            selected_material_id=material.id if material else None,
            selected_project_id=project.id if project else None,
            selected_bom_version=snapshot.selected_bom_version if project else None,
            selected_product_id=product.id if product else None,
            selected_product_revision_id=(product_revision.id if product_revision else None),
            material_candidate_ids=material_ids,
            project_candidate_ids=project_ids,
            product_candidate_ids=product_ids,
            pending_disambiguation=pending,
        )
