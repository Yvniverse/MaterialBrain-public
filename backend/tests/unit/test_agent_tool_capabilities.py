from app.agent.tools.registry import TOOLS, ToolRegistry


def test_all_registered_tools_have_explicit_capability_metadata():
    assert len(TOOLS) == 24
    assert all(tool.capability.schema_version for tool in TOOLS)
    assert all(tool.capability.risk_level for tool in TOOLS)
    assert all(tool.capability.side_effect for tool in TOOLS)


def test_initial_mcp_profile_is_read_only_and_excludes_proposal_tools():
    registry = ToolRegistry(component_intelligence_enabled=True)
    matrix = {item["name"]: item for item in registry.capability_matrix()}

    assert len(matrix) == 24
    assert len(registry.mcp_exposed_names) == 22
    assert "propose_inventory_reservation" not in registry.mcp_exposed_names
    assert "propose_build_material_reservation" not in registry.mcp_exposed_names

    for name in registry.mcp_exposed_names:
        capability = matrix[name]
        assert capability["risk_level"] == "read"
        assert capability["side_effect"] == "none"
        assert capability["human_approval"] is False
        assert capability["mcp_exposed"] is True

    for name in {"propose_inventory_reservation", "propose_build_material_reservation"}:
        capability = matrix[name]
        assert capability["risk_level"] == "proposal"
        assert capability["side_effect"] == "proposal"
        assert capability["idempotency"] == "required"
        assert capability["human_approval"] is True
        assert capability["mcp_exposed"] is False


def test_component_feature_flag_changes_only_the_authoritative_registry():
    enabled = ToolRegistry(component_intelligence_enabled=True)
    disabled = ToolRegistry(component_intelligence_enabled=False)

    assert len(enabled.names) == 24
    assert len(enabled.mcp_exposed_names) == 22
    assert len(disabled.names) == 23
    assert len(disabled.mcp_exposed_names) == 21
    assert "search_components_by_requirement" not in disabled.names
    assert "search_components_by_requirement" in enabled.names


def test_schema_digest_is_deterministic_and_capability_export_is_complete():
    first = ToolRegistry(component_intelligence_enabled=True)
    second = ToolRegistry(component_intelligence_enabled=True)

    assert first.schema_digest() == second.schema_digest()
    assert all(
        set(item)
        == {
            "name",
            "args_model",
            "entity_key",
            "permissions",
            "risk_level",
            "side_effect",
            "idempotency",
            "human_approval",
            "mcp_exposed",
            "schema_version",
        }
        for item in first.capability_matrix()
    )
