from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import anyio
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.agent.tools import ToolRegistry
from app.core.config import settings
from app.core.database import SessionLocal
from app.mcp.adapter import MCPTraceFailureCounter, ReadOnlyMCPAdapter
from app.models import User

logger = logging.getLogger(__name__)


class MCPRuntimeError(RuntimeError):
    """A sanitized MCP runtime failure safe to expose through the protocol."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.public_message = message


@dataclass
class MaterialBrainMCPRuntime:
    session_factory: Callable[[], Session]
    registry: ToolRegistry
    principal_username: str
    trace_failures: MCPTraceFailureCounter


def _configured_principal_username() -> str:
    profile = os.environ.get("MATERIALBRAIN_MCP_PROFILE", "").strip().casefold()
    username = os.environ.get("MATERIALBRAIN_MCP_USERNAME", "").strip()
    if profile != "readonly":
        raise MCPRuntimeError(
            "MCP_CONFIGURATION_INVALID",
            "MATERIALBRAIN_MCP_PROFILE must be explicitly set to readonly",
        )
    if not username:
        raise MCPRuntimeError(
            "MCP_CONFIGURATION_INVALID",
            "MATERIALBRAIN_MCP_USERNAME is required",
        )
    return username


def _load_read_only_principal(db: Session) -> User:
    username = _configured_principal_username()

    user = db.scalar(select(User).options(joinedload(User.role)).where(User.username == username))
    if user is None or user.is_deleted or not user.is_active:
        raise MCPRuntimeError(
            "MCP_PRINCIPAL_UNAVAILABLE",
            "configured MCP principal is missing or inactive",
        )

    role = getattr(user, "role", None)
    permissions = set(getattr(role, "permissions", None) or [])
    if role is None:
        raise MCPRuntimeError(
            "MCP_PRINCIPAL_UNAVAILABLE",
            "configured MCP principal role is missing",
        )
    forbidden = {
        permission
        for permission in permissions
        if permission == "*"
        or permission.endswith(":operate")
        or permission.endswith(":manage")
        or permission.endswith(":approve")
    }
    if forbidden:
        raise MCPRuntimeError(
            "MCP_PRINCIPAL_UNAVAILABLE",
            "MCP readonly principal contains write-capable permissions",
        )
    return user


def _rollback_and_close(db: Session) -> None:
    try:
        db.rollback()
    except Exception:
        logger.warning("mcp_readonly_session_rollback_failed")
    finally:
        try:
            db.close()
        except Exception:
            logger.warning("mcp_readonly_session_close_failed")


@contextmanager
def _read_only_session(session_factory: Callable[[], Session]) -> Iterator[Session]:
    """Open one PostgreSQL transaction and enforce database-level READ ONLY."""

    db = session_factory()
    try:
        bind = db.get_bind()
        dialect = getattr(getattr(bind, "dialect", None), "name", "")
        if dialect != "postgresql":
            raise MCPRuntimeError(
                "MCP_READ_ONLY_DATABASE_REQUIRED",
                "MCP read-only runtime requires PostgreSQL",
            )
        connection = db.connection()
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        yield db
    except MCPRuntimeError:
        _rollback_and_close(db)
        raise
    except Exception:
        _rollback_and_close(db)
        raise
    else:
        _rollback_and_close(db)


@asynccontextmanager
async def _lifespan(
    _server: Server[MaterialBrainMCPRuntime],
) -> AsyncIterator[MaterialBrainMCPRuntime]:
    username = _configured_principal_username()
    registry = ToolRegistry(component_intelligence_enabled=settings.component_intelligence_enabled)
    trace_failures = MCPTraceFailureCounter()
    runtime = MaterialBrainMCPRuntime(
        session_factory=SessionLocal,
        registry=registry,
        principal_username=username,
        trace_failures=trace_failures,
    )
    # Validate the configured principal at startup, but do not retain its
    # ORM object.  Every later request reloads it in a fresh session so role,
    # permission and active/deleted changes take effect without a restart.
    with _read_only_session(runtime.session_factory) as db:
        _load_read_only_principal(db)
    yield runtime


@contextmanager
def _request_adapter(runtime: MaterialBrainMCPRuntime) -> Iterator[ReadOnlyMCPAdapter]:
    try:
        with _read_only_session(runtime.session_factory) as db:
            user = _load_read_only_principal(db)
            yield ReadOnlyMCPAdapter(
                db,
                user,
                runtime.registry,
                trace_failure_counter=runtime.trace_failures,
            )
    except MCPRuntimeError:
        raise
    except Exception:
        # Never send SQLAlchemy/driver details through MCP.  The bounded
        # server log above is intentionally content-free.
        logger.warning("mcp_request_session_failed")
        raise MCPRuntimeError(
            "MCP_REQUEST_UNAVAILABLE",
            "MCP read-only request could not be completed",
        ) from None


def _error_result(runtime: MaterialBrainMCPRuntime, error: MCPRuntimeError) -> CallToolResult:
    output = {
        "ok": False,
        "error": {"code": error.code, "message": error.public_message, "details": {}},
    }
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(output, ensure_ascii=False, separators=(",", ":")),
            )
        ],
        structured_content=output,
        is_error=True,
        _meta={
            "materialbrain/requestId": "mcp-runtime-error",
            "materialbrain/toolSchemaDigest": runtime.registry.schema_digest(),
        },
    )


async def _list_tools(
    ctx: ServerRequestContext[MaterialBrainMCPRuntime],
    _params: PaginatedRequestParams | None,
) -> ListToolsResult:
    runtime = ctx.lifespan_context
    try:
        with _request_adapter(runtime) as adapter:
            specs = adapter.list_tools()
    except MCPRuntimeError as exc:
        # tools/list has no isError field; raising a sanitized exception lets
        # the MCP server return a protocol error without leaking DB details.
        raise RuntimeError(exc.public_message) from None
    return ListToolsResult(
        tools=[
            Tool(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
                annotations=ToolAnnotations(
                    read_only_hint=True,
                    destructive_hint=False,
                    idempotent_hint=True,
                ),
                _meta={
                    "materialbrain/toolSchemaVersion": spec.schema_version,
                    "materialbrain/permissions": list(spec.permissions),
                    "materialbrain/toolSchemaDigest": runtime.registry.schema_digest(),
                },
            )
            for spec in specs
        ]
    )


async def _call_tool(
    ctx: ServerRequestContext[MaterialBrainMCPRuntime],
    params: CallToolRequestParams,
) -> CallToolResult:
    runtime = ctx.lifespan_context
    try:
        with _request_adapter(runtime) as adapter:
            result = adapter.call_tool(params.name, params.arguments or {})
    except MCPRuntimeError as exc:
        return _error_result(runtime, exc)
    text = json.dumps(result.output, ensure_ascii=False, separators=(",", ":"))
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=result.output,
        is_error=result.is_error,
        _meta={
            "materialbrain/requestId": result.request_id,
            "materialbrain/toolSchemaVersion": result.schema_version,
            "materialbrain/toolSchemaDigest": runtime.registry.schema_digest(),
        },
    )


def build_server() -> Server[MaterialBrainMCPRuntime]:
    return Server(
        "materialbrain-readonly",
        version=settings.materialbrain_build_sha,
        description="MaterialBrain read-only engineering and warehouse capability surface",
        lifespan=_lifespan,
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )


async def _run_stdio() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    anyio.run(_run_stdio)
