from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.agent.episode import AgentEpisodeRecorder, safe_result_summary
from app.agent.tools import ToolContext, ToolRegistry
from app.agent.tools.registry import _permission_granted
from app.models import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    schema_version: str
    permissions: tuple[str, ...]


@dataclass(frozen=True)
class MCPCallResult:
    request_id: str
    tool_name: str
    output: dict[str, Any]
    is_error: bool
    schema_version: str


@dataclass
class MCPTraceFailureCounter:
    """Bounded, non-sensitive observability for best-effort trace failures."""

    limit: int = 100
    count: int = 0
    suppressed: int = 0

    def record(self) -> None:
        if self.count < self.limit:
            self.count += 1
            logger.warning("mcp_episode_trace_failure count=%d", self.count)
        elif self.suppressed < self.limit:
            self.suppressed += 1


class ReadOnlyMCPAdapter:
    """Small protocol-neutral adapter over the existing ToolRegistry.

    This class deliberately does not know about MCP transports. It enforces the
    MaterialBrain read-only MCP profile and then delegates schema validation,
    RBAC and business execution to the existing ToolRegistry/Services.
    """

    def __init__(
        self,
        db: Session,
        user: User,
        registry: ToolRegistry,
        *,
        record_episodes: bool = True,
        trace_failure_counter: MCPTraceFailureCounter | None = None,
    ):
        self.db = db
        self.user = user
        self.registry = registry
        self.record_episodes = record_episodes
        self.trace_failure_counter = trace_failure_counter

    @property
    def schema_digest(self) -> str:
        return self.registry.schema_digest()

    def list_tools(self) -> list[MCPToolSpec]:
        specs: list[MCPToolSpec] = []
        granted = set(getattr(getattr(self.user, "role", None), "permissions", None) or [])
        for name in sorted(self.registry.mcp_exposed_names):
            registered = self.registry.registered(name)
            if registered is None or not all(
                _permission_granted(granted, permission) for permission in registered.permissions
            ):
                continue
            specs.append(
                MCPToolSpec(
                    name=registered.name,
                    description=registered.description,
                    input_schema=registered.args_model.model_json_schema(),
                    schema_version=registered.capability.schema_version,
                    permissions=registered.permissions,
                )
            )
        return specs

    def call_tool(self, name: str, arguments: dict[str, Any] | None) -> MCPCallResult:
        request_id = f"mcp-{uuid.uuid4().hex}"
        registered = self.registry.registered(name)
        if (
            registered is None
            or name not in self.registry.mcp_exposed_names
            or registered.capability.risk_level != "read"
            or registered.capability.side_effect != "none"
        ):
            result = MCPCallResult(
                request_id=request_id,
                tool_name=name,
                output=self._normalized_error(
                    {
                        "ok": False,
                        "error": {
                            "code": "MCP_TOOL_NOT_EXPOSED",
                            "message": "该工具不属于 MaterialBrain MCP 只读能力面",
                            "details": {},
                        },
                    }
                ),
                is_error=True,
                schema_version=(registered.capability.schema_version if registered else "unknown"),
            )
            self._record_episode(result, [])
            return result

        trace_steps: list[dict] = []
        execution = self.registry.execute(
            ToolContext(
                self.db,
                self.user,
                request_id,
                client_operation_id=request_id,
                trace_steps=trace_steps,
            ),
            {
                "id": request_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(
                        jsonable_encoder(arguments or {}),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            },
        )
        output = self._normalized_error(jsonable_encoder(execution.output))
        result = MCPCallResult(
            request_id=request_id,
            tool_name=name,
            output=output,
            is_error=not bool(output.get("ok")),
            schema_version=registered.capability.schema_version,
        )
        self._record_episode(result, trace_steps)
        return result

    @staticmethod
    def _normalized_error(output: dict[str, Any]) -> dict[str, Any]:
        if output.get("ok") is True:
            return output
        error = output.get("error") if isinstance(output.get("error"), dict) else {}
        code = str(error.get("code") or "MCP_TOOL_ERROR")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.:-]{0,95}", code):
            code = "MCP_TOOL_ERROR"
        return {
            "ok": False,
            "error": {
                "code": code,
                "message": "MCP 只读工具调用失败",
                "details": {},
            },
        }

    def _record_episode(self, result: MCPCallResult, steps: list[dict]) -> None:
        if not self.record_episodes:
            return
        try:
            episode_id = AgentEpisodeRecorder(self.db, self.user).record(
                request_id=result.request_id,
                conversation_id="",
                client_operation_id=result.request_id,
                entry_surface="mcp_stdio",
                execution_mode="mcp_readonly",
                status="error" if result.is_error else "success",
                task_contract={
                    "entity_kind": "mcp",
                    "route": "mcp_readonly",
                    "tool_name": result.tool_name,
                },
                tool_schema_version=self.schema_digest,
                steps=steps,
                grounded_facts=[],
                final_result=safe_result_summary(result.output),
                hard_failures=[result.output["error"]["code"]] if result.is_error else [],
                telemetry=[],
                latency_ms=0,
            )
        except Exception:
            if self.trace_failure_counter is not None:
                self.trace_failure_counter.record()
            return
        # A persisted principal should produce an episode id.  A missing id is
        # therefore an observable trace failure, while synthetic test actors
        # remain intentionally non-persisted and are not counted here.
        if (
            episode_id is None
            and isinstance(getattr(self.user, "id", None), int)
            and self.user.id > 0
            and self.trace_failure_counter is not None
        ):
            self.trace_failure_counter.record()
