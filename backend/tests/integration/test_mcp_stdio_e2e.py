"""Real MCP stdio protocol closure against an isolated PostgreSQL database."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.database import SessionLocal, engine
from app.models import AgentEpisode, Role, User

pytestmark = pytest.mark.skipif(
    os.environ.get("PHASE3_MCP_STDIO_RUN") != "1",
    reason="BLOCKED_BY_ENVIRONMENT: set PHASE3_MCP_STDIO_RUN=1 with isolated PostgreSQL",
)

_BUSINESS_TABLES = (
    "materials",
    "inventory_lots",
    "stock_movements",
    "project_reservations",
    "build_plans",
    "build_plan_items",
    "pick_tasks",
    "pick_allocations",
)


def _database_name() -> str:
    with SessionLocal() as db:
        return str(db.execute(text("select current_database()")).scalar_one())


def _business_counts() -> dict[str, int]:
    with SessionLocal() as db:
        return {
            table: int(db.execute(text(f"select count(*) from {table}")).scalar_one())
            for table in _BUSINESS_TABLES
        }


def _episode_count() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(AgentEpisode)) or 0)


def _payload(result):
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    text_content = next(item.text for item in result.content if getattr(item, "type", "") == "text")
    return json.loads(text_content)


def _error_code(result) -> str:
    payload = _payload(result)
    return str((payload.get("error") or {}).get("code") or "")


def _child_environment() -> dict[str, str]:
    required = {"DATABASE_URL", "MATERIALBRAIN_BUILD_SHA"}
    missing = sorted(name for name in required if not os.environ.get(name))
    if missing:
        raise AssertionError(f"MCP stdio environment missing: {missing}")
    environment = {
        name: os.environ[name]
        for name in ("PATH", "PYTHONPATH", "DATABASE_URL", "MATERIALBRAIN_BUILD_SHA")
        if os.environ.get(name)
    }
    environment.update(
        {
            "MATERIALBRAIN_MCP_PROFILE": "readonly",
            "MATERIALBRAIN_MCP_USERNAME": os.environ["PHASE3_MCP_STDIO_USERNAME"],
            "COMPONENT_INTELLIGENCE_ENABLED": "true",
            "AGENT_ENABLED": "false",
            "DASHSCOPE_API_KEY": "",
        }
    )
    return environment


def _prepare_principal() -> tuple[int, int, str]:
    database = _database_name()
    if database in {"materialbrain_public", "materialbrain"}:
        raise AssertionError("refusing to mutate a default/production-like database")
    username = os.environ.get("PHASE3_MCP_STDIO_USERNAME") or (
        f"phase3_mcp_{uuid.uuid4().hex[:12]}"
    )
    role_name = f"{username}_role"
    with SessionLocal() as db:
        role = Role(
            name=role_name, description="isolated MCP stdio test", permissions=["material:view"]
        )
        user = User(
            username=username,
            full_name="isolated MCP stdio test",
            password_hash="not-a-login-secret",
            role=role,
            is_active=True,
            is_deleted=False,
            must_change_password=False,
        )
        db.add(user)
        db.commit()
        return user.id, role.id, username


def _set_principal(
    *, user_id: int, role_id: int, active: bool, deleted: bool, permissions: list[str]
):
    with SessionLocal() as db:
        user = db.get(User, user_id)
        role = db.get(Role, role_id)
        assert user is not None and role is not None
        user.is_active = active
        user.is_deleted = deleted
        role.permissions = permissions
        db.commit()


def _cleanup_principal(user_id: int, role_id: int):
    with SessionLocal() as db:
        db.execute(delete(AgentEpisode).where(AgentEpisode.user_id == user_id))
        user = db.get(User, user_id)
        role = db.get(Role, role_id)
        if user is not None:
            db.delete(user)
        if role is not None:
            db.delete(role)
        db.commit()


def _prove_postgres_read_only():
    if engine.dialect.name != "postgresql":
        raise AssertionError("MCP stdio closure requires PostgreSQL")
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        with pytest.raises(DBAPIError):
            connection.exec_driver_sql("UPDATE materials SET quantity = quantity WHERE false")
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()


async def _run_protocol() -> dict:
    from mcp.shared.exceptions import MCPError

    stderr = tempfile.TemporaryFile(mode="w+b")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        env=_child_environment(),
        cwd=Path.cwd(),
    )
    try:
        async with stdio_client(params, errlog=stderr) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                initial = await session.list_tools()
                initial_names = {tool.name for tool in initial.tools}
                assert initial_names
                assert "propose_inventory_reservation" not in initial_names
                assert "propose_build_material_reservation" not in initial_names
                assert "search_materials" in initial_names
                assert "search_projects" not in initial_names
                assert "get_build_picking_readiness" not in initial_names

                success = await session.call_tool("get_low_stock_materials", {"limit": 1})
                assert not success.is_error
                assert _payload(success)["ok"] is True

                invalid = await session.call_tool("search_materials", {"query": ""})
                assert invalid.is_error
                assert _error_code(invalid) == "TOOL_ARGUMENT_VALIDATION_ERROR"

                proposal = await session.call_tool("propose_inventory_reservation", {})
                assert proposal.is_error
                assert _error_code(proposal) == "MCP_TOOL_NOT_EXPOSED"

                unauthorized = await session.call_tool("search_projects", {"query": "x"})
                assert unauthorized.is_error
                assert _error_code(unauthorized) == "TOOL_PERMISSION_DENIED"

                user_id = int(os.environ["PHASE3_MCP_STDIO_USER_ID"])
                role_id = int(os.environ["PHASE3_MCP_STDIO_ROLE_ID"])
                _set_principal(
                    user_id=user_id,
                    role_id=role_id,
                    active=True,
                    deleted=False,
                    permissions=["project:view"],
                )
                revoked = await session.call_tool("search_materials", {"query": "x"})
                assert revoked.is_error
                assert _error_code(revoked) == "TOOL_PERMISSION_DENIED"
                after_revoke = await session.list_tools()
                after_revoke_names = {tool.name for tool in after_revoke.tools}
                assert "search_materials" not in after_revoke_names
                assert "search_projects" in after_revoke_names

                _set_principal(
                    user_id=user_id,
                    role_id=role_id,
                    active=False,
                    deleted=False,
                    permissions=["material:view"],
                )
                inactive = await session.call_tool("get_low_stock_materials", {"limit": 1})
                assert inactive.is_error
                assert _error_code(inactive) == "MCP_PRINCIPAL_UNAVAILABLE"
                try:
                    await session.list_tools()
                except MCPError as exc:
                    assert "password" not in str(exc).casefold()
                    assert "database_url" not in str(exc).casefold()
                else:
                    raise AssertionError("tools/list did not reject an inactive principal")

                _set_principal(
                    user_id=user_id,
                    role_id=role_id,
                    active=True,
                    deleted=False,
                    permissions=["material:view"],
                )
                recovered = await session.call_tool("get_low_stock_materials", {"limit": 1})
                assert not recovered.is_error

                _set_principal(
                    user_id=user_id,
                    role_id=role_id,
                    active=True,
                    deleted=True,
                    permissions=["material:view"],
                )
                deleted = await session.call_tool("get_low_stock_materials", {"limit": 1})
                assert deleted.is_error
                assert _error_code(deleted) == "MCP_PRINCIPAL_UNAVAILABLE"
    finally:
        stderr.seek(0)
        stderr_text = stderr.read().decode("utf-8", errors="replace")
        stderr.close()

    assert "not-a-login-secret" not in stderr_text
    assert "DASHSCOPE_API_KEY" not in stderr_text
    assert "postgresql+psycopg" not in stderr_text
    return {
        "protocol": "mcp_stdio",
        "initial_tool_count": len(initial_names),
        "after_revoke_tool_count": len(after_revoke_names),
        "provider_calls": 0,
        "stderr_bytes": len(stderr_text.encode("utf-8")),
    }


def test_real_mcp_stdio_protocol_and_dynamic_principal():
    user_id, role_id, username = _prepare_principal()
    os.environ["PHASE3_MCP_STDIO_USERNAME"] = username
    os.environ["PHASE3_MCP_STDIO_USER_ID"] = str(user_id)
    os.environ["PHASE3_MCP_STDIO_ROLE_ID"] = str(role_id)
    before = _business_counts()
    episodes_before = _episode_count()
    try:
        _prove_postgres_read_only()
        result = asyncio.run(_run_protocol())
        after = _business_counts()
        episodes_after = _episode_count()
        assert after == before
        assert episodes_after > episodes_before
        output_path = os.environ.get("PHASE3_MCP_STDIO_RESULT")
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        **result,
                        "status": "PASS",
                        "database": _database_name(),
                        "business_counts_before": before,
                        "business_counts_after": after,
                        "episodes_before": episodes_before,
                        "episodes_after": episodes_after,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    finally:
        _cleanup_principal(user_id, role_id)
