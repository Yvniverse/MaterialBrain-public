from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Callable

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionLocal
from app.models import AgentEpisode, User

logger = logging.getLogger(__name__)

TRACE_VERSION = "1"
REDACTION_VERSION = "1"
WAREHOUSE_AGENT_PROMPT_VERSION = "warehouse-agent-system-v1"

_SAFE_STEP_FIELDS = {
    "sequence",
    "call_id",
    "tool",
    "schema_version",
    "argument_hash",
    "authorization",
    "status",
    "error_code",
    "duration_ms",
    "result_hash",
    "entity_key",
    "replayable",
}
_SAFE_CONTRACT_FIELDS = {
    "entity_kind",
    "requested_facts",
    "write_intent",
    "capability_limitations",
    "requires_material_resolution",
    "requires_project_resolution",
    "requires_product_resolution",
    "deterministic_material_resolution",
    "build_quantity",
    "invalid_build_quantity",
    "route",
    "safety_blocked",
    "safety_intent",
    "tool_name",
}
_SAFE_FAILURE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,95}$")


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        jsonable_encoder(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def public_fact_refs(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = []
    for raw_item in facts:
        item = (
            raw_item.model_dump(mode="json") if hasattr(raw_item, "model_dump") else dict(raw_item)
        )
        refs.append(
            {
                "kind": item.get("kind"),
                "source_tool": item.get("source_tool"),
                "entity_id": item.get("entity_id"),
                "field": item.get("field"),
            }
        )
    return refs


def safe_task_contract(contract: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only the bounded server-owned contract, never the user message."""

    contract = contract or {}
    safe: dict[str, Any] = {}
    for key in _SAFE_CONTRACT_FIELDS:
        if key not in contract:
            continue
        value = contract[key]
        if isinstance(value, (set, tuple)):
            value = sorted(str(item) for item in value)
        if isinstance(value, (str, bool, int, float)) or value is None or isinstance(value, list):
            safe[key] = jsonable_encoder(value)
    return safe


def safe_trace_steps(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Persist the trace contract, not arguments, tool output, or model text."""

    safe_steps: list[dict[str, Any]] = []
    for position, item in enumerate(steps or []):
        safe = {key: jsonable_encoder(item.get(key)) for key in _SAFE_STEP_FIELDS if key in item}
        safe.setdefault("sequence", position)
        safe_steps.append(safe)
    return safe_steps


def safe_result_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    """Replace response text and entity values with stable, non-sensitive facts."""

    result = result or {}
    safe: dict[str, Any] = {}
    for key in (
        "intent",
        "execution_mode",
        "model_call_count",
        "tool_event_count",
        "proposal_count",
        "error_code",
    ):
        if key in result and isinstance(result[key], (str, int, bool)):
            safe[key] = result[key]
    answer = result.get("answer")
    if isinstance(answer, str):
        safe["answer_hash"] = canonical_hash(answer)
    elif result:
        safe["result_hash"] = canonical_hash(result)
    entities = result.get("entities")
    if isinstance(entities, dict):
        safe["entity_keys"] = sorted(str(key) for key in entities)
    return safe


def safe_failures(failures: list[str] | None) -> list[str]:
    return [
        item if _SAFE_FAILURE.fullmatch(item) else "REDACTED_FAILURE" for item in failures or []
    ]


def safe_telemetry(telemetry: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    allowed = {
        "provider",
        "model",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
        "tool_call_count",
        "attempts",
        "retries",
    }
    for item in telemetry or []:
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        row = {key: jsonable_encoder(item.get(key)) for key in allowed if key in item}
        safe.append(row)
    return safe


class AgentEpisodeRecorder:
    """Best-effort durable trace writer; never stores prompts or chain-of-thought."""

    def __init__(
        self,
        db: Session | None,
        user: User,
        *,
        session_factory: Callable[[], Session] | None = None,
    ):
        # The trace uses the caller's engine/bind but a fresh session.  This
        # keeps eval databases, isolated PostgreSQL databases and production
        # all aligned without allowing trace commit/rollback to touch the
        # authoritative request transaction.
        if session_factory is not None:
            self.session_factory = session_factory
        elif db is not None and hasattr(db, "get_bind"):
            bind = db.get_bind()
            # Read-only shadow/eval sessions are bound to a Connection with a
            # transaction-local READ ONLY setting.  Episode persistence is
            # telemetry, so use the underlying Engine for its independent
            # transaction rather than inheriting that connection state.
            bind = getattr(bind, "engine", bind)
            self.session_factory = sessionmaker(
                bind=bind,
                autoflush=False,
                expire_on_commit=False,
            )
        else:
            self.session_factory = SessionLocal
        self.user = user

    def record(
        self,
        *,
        request_id: str,
        conversation_id: str,
        client_operation_id: str,
        entry_surface: str,
        execution_mode: str,
        status: str,
        task_contract: dict[str, Any],
        tool_schema_version: str,
        steps: list[dict[str, Any]],
        grounded_facts: list[dict[str, Any]],
        final_result: dict[str, Any],
        hard_failures: list[str],
        telemetry: list[dict[str, Any]],
        latency_ms: int,
        replay_parent_id: str | None = None,
    ) -> str | None:
        # Deterministic read-only harnesses may use a synthetic actor instead
        # of a persisted User.  The trace is best effort and must not turn
        # that harness fact into a noisy FK failure.
        user_id = getattr(self.user, "id", None)
        if not isinstance(user_id, int) or user_id <= 0:
            return None
        episode_id = str(uuid.uuid4())
        safe_contract = safe_task_contract(task_contract)
        safe_steps = safe_trace_steps(steps)
        safe_facts = public_fact_refs(grounded_facts)
        safe_result = safe_result_summary(final_result)
        safe_failures_list = safe_failures(hard_failures)
        safe_telemetry_list = safe_telemetry(telemetry)
        provider = str(safe_telemetry_list[0].get("provider") or "") if safe_telemetry_list else ""
        model = str(safe_telemetry_list[0].get("model") or "") if safe_telemetry_list else ""
        input_tokens = sum(int(item.get("input_tokens") or 0) for item in safe_telemetry_list)
        output_tokens = sum(int(item.get("output_tokens") or 0) for item in safe_telemetry_list)
        total_tokens = sum(int(item.get("total_tokens") or 0) for item in safe_telemetry_list)
        row = AgentEpisode(
            id=episode_id,
            user_id=user_id,
            request_id=request_id,
            conversation_id=conversation_id,
            client_operation_id=client_operation_id,
            entry_surface=entry_surface,
            execution_mode=execution_mode,
            status=status,
            task_contract=safe_contract,
            task_contract_hash=canonical_hash(safe_contract),
            model_provider=provider,
            model_name=model,
            prompt_version=WAREHOUSE_AGENT_PROMPT_VERSION if telemetry else "",
            tool_schema_version=tool_schema_version,
            steps=safe_steps,
            grounded_facts=safe_facts,
            final_result=safe_result,
            hard_failures=safe_failures_list,
            telemetry=safe_telemetry_list,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=max(0, int(latency_ms)),
            business_outcome={},
            human_feedback={},
            replay_parent_id=replay_parent_id,
            trace_version=TRACE_VERSION,
            redaction_version=REDACTION_VERSION,
            finished_at=datetime.now(UTC),
        )
        try:
            with self.session_factory() as trace_db:
                trace_db.add(row)
                trace_db.commit()
            return episode_id
        except Exception:
            # Do not render driver/SQL details into logs: the trace is
            # best-effort telemetry and must never expose request content or
            # credentials when its independent write fails.
            logger.warning("agent_episode_record_failed request_id=%s", request_id)
            return None
