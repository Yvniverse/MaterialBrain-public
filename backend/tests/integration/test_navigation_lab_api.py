from types import SimpleNamespace

import pytest

from app.agent.tools import ToolRegistry
from app.core.database import SessionLocal
from app.mcp.adapter import ReadOnlyMCPAdapter
from app.models import User
from app.services.embodied_navigation.service import world_snapshot


def test_navigation_world_auth_and_registered_revision(client, admin):
    result = client.get("/api/v1/navigation-lab/world")
    assert result.status_code == 200
    assert result.json()["revision_sha256"] == world_snapshot()["revision_sha256"]
    assert len(result.json()["assets"]) == 30
    assert result.json()["provenance"] == "synthetic_lab"


@pytest.mark.parametrize(
    "scenario,status",
    [
        ("baseline", "READY"),
        ("blocked-crossing", "READY"),
        ("isolated-dock", "BLOCKED"),
        ("low-battery", "NEEDS_CHARGE"),
    ],
)
def test_real_http_planning_uses_package_service(client, admin, scenario, status):
    w = world_snapshot()
    result = client.post(
        "/api/v1/navigation-lab/plan",
        json={
            "world_id": w["id"],
            "world_revision": w["revision_sha256"],
            "goal_ids": w["default_goal_ids"],
            "scenario_id": scenario,
        },
    )
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["status"] == status
    assert plan["world_revision"] == w["revision_sha256"]
    assert plan["mode"] == "simulation"
    assert plan["inventory_written"] is False
    if status == "READY":
        assert len(plan["segments"]) == 7
        assert plan["min_clearance_m"] > 0


def test_http_rejects_physical_mode_and_stale_world(client, admin):
    body = {"world_id": "MB-EMB-LAB-03", "goal_ids": ["P-IC"]}
    assert (
        client.post("/api/v1/navigation-lab/plan", json={**body, "mode": "physical"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/navigation-lab/plan", json={**body, "world_revision": "0" * 64}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/navigation-lab/plan", json={**body, "goal_ids": ["P-FAKE"]}
        ).status_code
        == 422
    )


def test_navigation_write_method_requires_existing_csrf(client, admin):
    token = client.headers.pop("X-CSRF-Token")
    try:
        result = client.post(
            "/api/v1/navigation-lab/plan", json={"world_id": "MB-EMB-LAB-03", "goal_ids": []}
        )
        assert result.status_code == 403
    finally:
        client.headers["X-CSRF-Token"] = token


def test_navigation_mcp_calls_are_real_readonly_and_permission_filtered(admin):
    registry = ToolRegistry()
    with SessionLocal() as db:
        adapter = ReadOnlyMCPAdapter(db, db.get(User, admin["id"]), registry)
        manifest = adapter.call_tool("get_navigation_lab", {})
        assert not manifest.is_error
        data = manifest.output["data"]
        result = adapter.call_tool(
            "plan_navigation_lab",
            {
                "world_id": data["world_id"],
                "world_revision": data["world_revision"],
                "goal_ids": [data["goals"][0]["id"]],
            },
        )
        assert not result.is_error
        assert result.output["data"]["status"] == "READY"
        assert result.output["data"]["inventory_written"] is False
        assert "segments" not in result.output["data"]
        restricted = SimpleNamespace(id=999, role=SimpleNamespace(permissions=["dashboard:view"]))
        blocked = ReadOnlyMCPAdapter(db, restricted, registry)
        assert "get_navigation_lab" not in {tool.name for tool in blocked.list_tools()}
        assert blocked.call_tool(
            "plan_navigation_lab", {"world_id": data["world_id"], "goal_ids": []}
        ).is_error
