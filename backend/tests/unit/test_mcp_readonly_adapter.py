from types import SimpleNamespace

from pydantic import BaseModel

from app.agent.tools.registry import (
    RegisteredTool,
    ToolCapability,
    ToolRegistry,
    _permission_granted,
)
from app.mcp import adapter as adapter_module
from app.mcp.adapter import MCPTraceFailureCounter, ReadOnlyMCPAdapter


class FakeRole:
    permissions = ["material:view", "project:view", "picking:view"]


class FakeUser:
    id = 999
    role = FakeRole()


class _TraceArgs(BaseModel):
    value: int


def _registry_for_trace_test() -> ToolRegistry:
    registry = ToolRegistry(component_intelligence_enabled=True)
    registry._tools["test_read"] = RegisteredTool(
        name="test_read",
        description="test",
        args_model=_TraceArgs,
        handler=lambda _ctx, args: {"value": args.value},
        entity_key="test",
        capability=ToolCapability(
            risk_level="read",
            side_effect="none",
            idempotency="none",
            human_approval=False,
            mcp_exposed=True,
            schema_version="1",
        ),
    )
    return registry


def test_adapter_lists_only_explicit_read_only_mcp_tools():
    registry = ToolRegistry(component_intelligence_enabled=True)
    adapter = ReadOnlyMCPAdapter(SimpleNamespace(), FakeUser(), registry)

    names = {tool.name for tool in adapter.list_tools()}
    assert names == registry.mcp_exposed_names
    assert "propose_inventory_reservation" not in names
    assert "propose_build_material_reservation" not in names


def test_adapter_fails_closed_for_non_exposed_tool():
    registry = ToolRegistry(component_intelligence_enabled=True)
    adapter = ReadOnlyMCPAdapter(SimpleNamespace(), FakeUser(), registry)

    result = adapter.call_tool("propose_inventory_reservation", {})
    assert result.is_error is True
    assert result.output["error"]["code"] == "MCP_TOOL_NOT_EXPOSED"


def test_adapter_discovery_uses_registry_permission_semantics():
    registry = ToolRegistry(component_intelligence_enabled=True)
    user = type(
        "MaterialOnlyUser",
        (),
        {"id": 999, "role": type("Role", (), {"permissions": ["material:view"]})()},
    )()
    adapter = ReadOnlyMCPAdapter(SimpleNamespace(), user, registry, record_episodes=False)

    names = {tool.name for tool in adapter.list_tools()}
    expected = {
        name
        for name in registry.mcp_exposed_names
        if all(
            _permission_granted(set(user.role.permissions), permission)
            for permission in registry.registered(name).permissions
        )
    }
    assert names == expected
    assert "search_materials" in names
    assert "search_projects" not in names
    assert "get_build_picking_readiness" not in names


def test_episode_trace_failure_is_observable_without_failing_read(monkeypatch):
    class BrokenRecorder:
        def __init__(self, *_args, **_kwargs):
            pass

        def record(self, **_kwargs):
            raise RuntimeError("sensitive database details must stay hidden")

    monkeypatch.setattr(adapter_module, "AgentEpisodeRecorder", BrokenRecorder)
    counter = MCPTraceFailureCounter(limit=1)
    adapter = ReadOnlyMCPAdapter(
        SimpleNamespace(),
        FakeUser(),
        _registry_for_trace_test(),
        trace_failure_counter=counter,
    )

    result = adapter.call_tool("test_read", {"value": 1})

    assert result.is_error is False
    assert counter.count == 1
    assert counter.suppressed == 0
