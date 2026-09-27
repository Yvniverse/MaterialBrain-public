import logging
import time
import uuid

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from sqlalchemy.orm import Session

from app.agent.conversation import ConversationContextService
from app.agent.episode import AgentEpisodeRecorder, safe_result_summary
from app.agent.graph import WarehouseAgentGraph
from app.agent.public_result_contract import public_result_contract_violations
from app.agent.result_enrichment import ReadOnlyPublicResultEnricher
from app.agent.safety import inspect_user_message
from app.agent.state import WarehouseAgentState
from app.agent.tools import ToolContext, ToolRegistry
from app.core.config import Settings, settings
from app.core.exceptions import BusinessError
from app.llm.base import LLMProvider
from app.llm.errors import classify_provider_error
from app.llm.factory import create_llm_provider
from app.llm.model_pool import AIModelPoolExhausted, QwenModelPoolProvider
from app.models import User
from app.schemas.agent import AgentQueryResponse
from app.services.engineering_research import EngineeringResearchService
from app.services.product_bom_preview import ProductBomPreviewService

logger = logging.getLogger(__name__)


class _UnavailableLLMProvider:
    def chat(self, messages, tools=None, tool_choice="auto"):
        del messages, tools, tool_choice
        raise BusinessError(
            "AGENT_MODEL_NOT_CONFIGURED",
            "当前未启用模型辅助；确定性的物料、库存、库位、BOM 与证据查询仍可使用",
            503,
        )


class WarehouseAgentService:
    def __init__(
        self,
        db: Session,
        user: User,
        request_id: str,
        *,
        provider: LLMProvider | None = None,
        config: Settings = settings,
        enforce_configuration: bool = True,
        registry: ToolRegistry | None = None,
        conversation_context: ConversationContextService | None = None,
    ):
        self.db = db
        self.user = user
        self.request_id = request_id
        self.config = config
        if enforce_configuration:
            self._validate_configuration()
        if config.agent_model_policy != "normal":
            # This policy is loaded from server configuration, not from a
            # browser label. It protects deterministic/Jev-only UAT even when
            # a production DashScope key is present in the process.
            self.provider = _UnavailableLLMProvider()
        else:
            self.provider = provider or (
                create_llm_provider(config)
                if config.dashscope_api_key.strip()
                else _UnavailableLLMProvider()
            )
        self.registry = registry or ToolRegistry(
            component_intelligence_enabled=config.component_intelligence_enabled
        )
        self.conversation_context = conversation_context

    def _validate_configuration(self) -> None:
        if not self.config.agent_enabled:
            raise BusinessError("AGENT_DISABLED", "Warehouse Agent 当前未启用", 503)

    def _record_episode(
        self,
        *,
        started: float,
        operation_id: str,
        conversation_id: str,
        execution_mode: str,
        status: str,
        task_contract: dict,
        steps: list[dict],
        grounded_facts: list[dict],
        final_result: dict,
        hard_failures: list[str],
        telemetry: list[dict],
    ) -> None:
        """Record trace data without joining or changing the request transaction."""

        try:
            AgentEpisodeRecorder(self.db, self.user).record(
                request_id=self.request_id,
                conversation_id=str(conversation_id),
                client_operation_id=operation_id,
                entry_surface="warehouse_agent",
                execution_mode=execution_mode,
                status=status,
                task_contract=task_contract,
                tool_schema_version=self.registry.schema_digest(),
                steps=steps,
                grounded_facts=grounded_facts,
                final_result=final_result,
                hard_failures=hard_failures,
                telemetry=telemetry,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        except Exception:
            # The recorder is deliberately non-authoritative.  A trace failure
            # must never change a successful business response.
            logger.exception("agent_episode_record_wrapper_failed request_id=%s", self.request_id)

    @staticmethod
    def _response_summary(response: AgentQueryResponse) -> dict:
        payload = response.model_dump(mode="json")
        payload["tool_event_count"] = len(response.tool_events)
        payload["proposal_count"] = len(response.proposal_ids)
        return safe_result_summary(payload)

    def query(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
        client_operation_id: str | None = None,
    ) -> AgentQueryResponse:
        started = time.perf_counter()
        operation_id = client_operation_id or uuid.uuid4().hex
        conversations = self.conversation_context or ConversationContextService(
            self.db, self.user, self.config
        )
        snapshot = conversations.open(conversation_id)
        safety = inspect_user_message(message)
        if safety.blocked:
            response = AgentQueryResponse(
                answer=safety.answer,
                narrative=safety.answer,
                intent=safety.intent,
                request_id=self.request_id,
                conversation_id=snapshot.id,
            )
            self._record_episode(
                started=started,
                operation_id=operation_id,
                conversation_id=snapshot.id,
                execution_mode="deterministic",
                status="blocked",
                task_contract={
                    "entity_kind": "unknown",
                    "route": "safety_block",
                    "safety_blocked": True,
                    "safety_intent": safety.intent,
                },
                steps=[],
                grounded_facts=[],
                final_result=self._response_summary(response),
                hard_failures=["SAFETY_BLOCKED"],
                telemetry=[],
            )
            return response
        prepared = conversations.prepare(snapshot, message)
        from app.agent.picking_queries import answer_picking_query

        picking_response = answer_picking_query(
            self.db, self.user, snapshot, message, self.request_id
        )
        if picking_response is not None:
            self._record_episode(
                started=started,
                operation_id=operation_id,
                conversation_id=picking_response.conversation_id,
                execution_mode="deterministic",
                status="success",
                task_contract={"entity_kind": "picking", "route": "picking_query"},
                steps=[],
                grounded_facts=picking_response.grounded_facts,
                final_result=self._response_summary(picking_response),
                hard_failures=[],
                telemetry=picking_response.telemetry,
            )
            return picking_response
        if (
            "product_bom_preview" in prepared.contract.requested_facts
            and not prepared.direct_answer
        ):
            preview = ProductBomPreviewService(self.db).build(
                product_id=int(prepared.snapshot.selected_product_id or 0),
                revision_id=int(prepared.snapshot.selected_product_revision_id or 0),
                engineering_context=prepared.entities.get("engineering_research_context"),
            )
            answer = self._product_bom_preview_answer(preview)
            entities = {
                key: value
                for key, value in prepared.entities.items()
                if key != "engineering_research_context"
            }
            entities["product_bom_preview"] = preview
            entities = ReadOnlyPublicResultEnricher(self.db).enrich(entities)
            conversations.persist(
                prepared,
                entities=entities,
                intent="product_bom_preview",
            )
            response = AgentQueryResponse(
                answer=answer,
                narrative=answer,
                intent="product_bom_preview",
                entities=entities,
                grounded_facts=[],
                tool_events=[],
                request_id=self.request_id,
                conversation_id=snapshot.id,
                telemetry=[],
                execution_mode="deterministic",
                model_call_count=0,
            )
            self._record_episode(
                started=started,
                operation_id=operation_id,
                conversation_id=snapshot.id,
                execution_mode="deterministic",
                status="success",
                task_contract=prepared.contract.model_dump(mode="json"),
                steps=[],
                grounded_facts=[],
                final_result=self._response_summary(response),
                hard_failures=[],
                telemetry=[],
            )
            return response
        if "engineering_research" in prepared.contract.requested_facts:
            trace_steps: list[dict] = []
            if not self.config.agent_engineering_research_enabled:
                answer = "当前工程研究 MVP 未启用；未执行任何物料、库存、库位或证据读取。"
                entities = {
                    **prepared.entities,
                    "engineering_research": {
                        "workflow": "engineering_research",
                        "status": "no_grounded_solution",
                        "read_only": True,
                        "automatic_write": False,
                        "requirements": {},
                        "plan": {"steps": [], "read_only": True, "write_scope": "none"},
                        "draft": {
                            "status": "no_grounded_solution",
                            "conclusion": answer,
                            "manual_review": [],
                            "unknowns": ["功能开关关闭。"],
                            "citations": [],
                        },
                        "citations": [],
                    },
                }
                tool_events: list = []
                grounded_facts: list = []
            else:
                research_provider = (
                    self.provider
                    if self.config.agent_model_policy == "normal"
                    and not isinstance(self.provider, _UnavailableLLMProvider)
                    else None
                )
                research = EngineeringResearchService(
                    self.db,
                    self.user,
                    self.request_id,
                    self.registry,
                    operation_id=operation_id,
                    trace_steps=trace_steps,
                    provider=research_provider,
                    prior_context=prepared.entities.get("engineering_research_context"),
                    conversation_id=snapshot.id,
                ).run(
                    prepared.effective_message or message,
                    prepared.contract,
                    prior_context=prepared.entities.get("engineering_research_context"),
                )
                answer = research["answer"]
                entities = {**prepared.entities, "engineering_research": research["entity"]}
                tool_events = research["tool_events"]
                grounded_facts = research["grounded_facts"]
                narrative = research.get("narrative") or answer
                research_execution_mode = research.get("execution_mode") or "deterministic"
                research_telemetry = research.get("telemetry") or []
                research_model_call_count = int(research.get("model_call_count") or 0)
            if "engineering_research" in entities:
                # The prepared turn contains the prior bounded context so the
                # service can resolve follow-ups. It must not be echoed back
                # after a mutation: otherwise a cleared selection remains
                # visible in engineering_research_context even though the
                # current engineering entity is cleared.
                entities["engineering_research_context"] = (
                    conversations._engineering_research_context(  # noqa: SLF001
                        entities["engineering_research"]
                    )
                )
            entities = ReadOnlyPublicResultEnricher(self.db).enrich(entities)
            conversations.persist(
                prepared,
                entities=entities,
                intent="engineering_research",
            )
            response = AgentQueryResponse(
                answer=answer,
                narrative=(narrative if "narrative" in locals() else answer),
                intent="engineering_research",
                entities=entities,
                grounded_facts=grounded_facts,
                tool_events=tool_events,
                request_id=self.request_id,
                conversation_id=snapshot.id,
                telemetry=(research_telemetry if "research_telemetry" in locals() else []),
                execution_mode=(
                    research_execution_mode
                    if "research_execution_mode" in locals()
                    else "deterministic"
                ),
                model_call_count=(
                    research_model_call_count if "research_model_call_count" in locals() else 0
                ),
            )
            hard_failures = [
                str(event.error_code)
                for event in response.tool_events
                if event.status == "error" and event.error_code
            ]
            self._record_episode(
                started=started,
                operation_id=operation_id,
                conversation_id=snapshot.id,
                execution_mode=response.execution_mode,
                status="error" if hard_failures else "success",
                task_contract=prepared.contract.model_dump(mode="json"),
                steps=trace_steps,
                grounded_facts=response.grounded_facts,
                final_result=self._response_summary(response),
                hard_failures=hard_failures,
                telemetry=response.telemetry,
            )
            return response
        if prepared.direct_answer:
            prepared_entities = ReadOnlyPublicResultEnricher(self.db).enrich(prepared.entities)
            conversations.persist(
                prepared,
                entities=prepared_entities,
                intent=prepared.direct_intent,
            )
            response = AgentQueryResponse(
                answer=prepared.direct_answer,
                narrative=prepared.direct_answer,
                intent=prepared.direct_intent,
                entities=prepared_entities,
                request_id=self.request_id,
                conversation_id=snapshot.id,
            )
            self._record_episode(
                started=started,
                operation_id=operation_id,
                conversation_id=snapshot.id,
                execution_mode="deterministic",
                status="success",
                task_contract=prepared.contract.model_dump(mode="json"),
                steps=[],
                grounded_facts=[],
                final_result=self._response_summary(response),
                hard_failures=[],
                telemetry=[],
            )
            return response
        initial: WarehouseAgentState = {
            "messages": [],
            "user_id": self.user.id,
            "request_id": self.request_id,
            "user_message": prepared.effective_message or message,
            "intent": None,
            "tool_round": 0,
            "tool_events": [],
            "pending_tool_calls": [],
            "entities": prepared.entities,
            "answer": None,
            "ui_actions": [],
            "proposal_ids": [],
            "grounded_facts": [],
            "narrative": "",
            "telemetry": [],
            "model_call_count": 0,
            "max_rounds_exceeded": False,
            "deadline_exceeded": False,
            "task_contract": prepared.contract.model_dump(mode="json"),
        }
        trace_steps: list[dict] = []
        graph = WarehouseAgentGraph(
            provider=(
                self.provider.primary_only()
                if isinstance(self.provider, QwenModelPoolProvider)
                and prepared.contract.write_intent != "none"
                else self.provider
            ),
            tool_context=ToolContext(
                self.db,
                self.user,
                self.request_id,
                client_operation_id=operation_id,
                trace_steps=trace_steps,
            ),
            max_tool_rounds=self.config.agent_max_tool_rounds,
            total_deadline_seconds=self.config.agent_total_deadline_seconds,
            force_fact_tool_calls=self.config.dashscope_enable_thinking is not True,
            deterministic_material_resolution_enabled=(
                self.config.agent_deterministic_material_resolution_enabled
                or self.config.agent_model_policy != "normal"
            ),
            narrative_synthesis_enabled=(
                self.config.agent_model_policy == "normal"
                and not isinstance(self.provider, _UnavailableLLMProvider)
            ),
            registry=self.registry,
        )
        try:
            result = graph.graph.invoke(initial)
        except AIModelPoolExhausted as exc:
            error = BusinessError(
                "AI_MODEL_POOL_EXHAUSTED",
                "AI 免费模型池当前不可用；物料搜索、库存、库位和项目/BOM 页面仍可使用",
                503,
                details={"provider_categories": exc.categories},
            )
            self._record_episode(
                started=started,
                operation_id=operation_id,
                conversation_id=snapshot.id,
                execution_mode="llm_assisted",
                status="error",
                task_contract=prepared.contract.model_dump(mode="json"),
                steps=trace_steps,
                grounded_facts=[],
                final_result={"error_code": error.code},
                hard_failures=[error.code],
                telemetry=[],
            )
            raise error from exc
        except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
            category = classify_provider_error(exc)
            error = BusinessError(
                "AGENT_SERVICE_UNAVAILABLE",
                "AI 服务暂不可用，请稍后重试；传统仓库功能不受影响",
                503,
                details={"provider_category": category},
            )
            self._record_episode(
                started=started,
                operation_id=operation_id,
                conversation_id=snapshot.id,
                execution_mode="llm_assisted",
                status="error",
                task_contract=prepared.contract.model_dump(mode="json"),
                steps=trace_steps,
                grounded_facts=[],
                final_result={"error_code": error.code},
                hard_failures=[error.code],
                telemetry=[],
            )
            raise error from exc
        except APIStatusError as exc:
            category = classify_provider_error(exc)
            error = BusinessError(
                "AGENT_PROVIDER_ERROR",
                "AI Provider 返回错误，请联系管理员检查百炼配置",
                503,
                details={
                    "status_code": exc.status_code,
                    "provider_category": category,
                },
            )
            self._record_episode(
                started=started,
                operation_id=operation_id,
                conversation_id=snapshot.id,
                execution_mode="llm_assisted",
                status="error",
                task_contract=prepared.contract.model_dump(mode="json"),
                steps=trace_steps,
                grounded_facts=[],
                final_result={"error_code": error.code},
                hard_failures=[error.code],
                telemetry=[],
            )
            raise error from exc
        except BusinessError as exc:
            self._record_episode(
                started=started,
                operation_id=operation_id,
                conversation_id=snapshot.id,
                execution_mode="llm_assisted",
                status="error",
                task_contract=prepared.contract.model_dump(mode="json"),
                steps=trace_steps,
                grounded_facts=[],
                final_result={"error_code": exc.code},
                hard_failures=[exc.code],
                telemetry=[],
            )
            raise
        result["entities"] = ReadOnlyPublicResultEnricher(self.db).enrich(
            result.get("entities") or {}
        )
        conversations.persist(
            prepared,
            entities=result.get("entities") or {},
            intent=result.get("intent"),
        )
        has_tool_error = any(
            event.get("status") == "error" for event in result.get("tool_events") or []
        )
        result_contract_failures = (
            []
            if has_tool_error
            else public_result_contract_violations(
                prepared.contract,
                entities=result.get("entities") or {},
                answer=result.get("answer") or "",
            )
        )
        if result_contract_failures:
            logger.error(
                "agent_public_result_contract_failed request_id=%s failures=%s",
                self.request_id,
                result_contract_failures,
            )
            result["answer"] = (
                "这次查询没有形成完整的业务结果，已停止展示不完整结论。请补充对象后重试。"
            )
            result["narrative"] = result["answer"]
            result["intent"] = "result_contract_incomplete"
        response = AgentQueryResponse(
            answer=result["answer"],
            narrative=result.get("narrative") or "",
            intent=result.get("intent"),
            entities=result.get("entities") or {},
            grounded_facts=result.get("grounded_facts") or [],
            tool_events=result.get("tool_events") or [],
            ui_actions=result.get("ui_actions") or [],
            proposal_ids=result.get("proposal_ids") or [],
            telemetry=result.get("telemetry") or [],
            execution_mode=(
                "llm_assisted" if int(result.get("model_call_count") or 0) > 0 else "deterministic"
            ),
            model_call_count=int(result.get("model_call_count") or 0),
            request_id=self.request_id,
            conversation_id=snapshot.id,
        )
        hard_failures = [
            str(event.get("error_code"))
            for event in result.get("tool_events") or []
            if event.get("status") == "error" and event.get("error_code")
        ]
        if result_contract_failures:
            hard_failures.append("RESULT_CONTRACT_INCOMPLETE")
        self._record_episode(
            started=started,
            operation_id=operation_id,
            conversation_id=snapshot.id,
            execution_mode=response.execution_mode,
            status="error" if hard_failures else "success",
            task_contract=prepared.contract.model_dump(mode="json"),
            steps=trace_steps,
            grounded_facts=response.grounded_facts,
            final_result=self._response_summary(response),
            hard_failures=hard_failures,
            telemetry=response.telemetry,
        )
        return response

    @staticmethod
    def _product_bom_preview_answer(preview: dict) -> str:
        summary = preview["summary"]
        target = preview["target_revision"]
        return (
            f"已生成 {preview['target_product']['code']} · {target['revision']} 的 "
            "Product BOM Preview："
            f"ADD {summary['add_count']}，UPDATE_QUANTITY {summary['update_quantity_count']}，"
            f"NO_CHANGE {summary['no_change_count']}，UNRESOLVED {summary['unresolved_count']}。"
            "本结果仅预览，未写入正式 Product BOM、库存、预留或 Picking。"
        )
