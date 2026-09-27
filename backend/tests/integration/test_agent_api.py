import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent.proposals import ProposalService
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import BomItem, Location, Material, Project, Role, User
from app.schemas.agent import ProposeInventoryReservationArgs
from app.services.inventory import InventoryService


class NeverProvider:
    def chat(self, messages, tools=None, tool_choice="auto"):
        raise AssertionError("Pronoun clarification must not call the model")


def test_missing_dashscope_key_keeps_deterministic_queries_available(
    client, admin, material, monkeypatch
):
    monkeypatch.setattr(settings, "agent_enabled", True)
    monkeypatch.setattr(settings, "dashscope_api_key", "")
    monkeypatch.setattr(settings, "agent_deterministic_material_resolution_enabled", True)
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    response = client.post(
        "/api/v1/agent/query", json={"message": f"{material['code']} 在哪里？"}
    )
    assert response.status_code == 200
    assert response.json()["execution_mode"] == "deterministic"
    assert response.json()["model_call_count"] == 0

    narrative_optional = client.post(
        "/api/v1/agent/query",
        json={"message": "哪些物料低于安全库存？简短说原因。"},
    )
    assert narrative_optional.status_code == 200
    assert narrative_optional.json()["execution_mode"] == "deterministic"
    assert narrative_optional.json()["model_call_count"] == 0

    llm_required = client.post("/api/v1/agent/query", json={"message": "随便聊聊。"})
    assert llm_required.status_code == 503
    assert llm_required.json()["code"] == "AGENT_MODEL_NOT_CONFIGURED"


def test_agent_api_returns_and_reuses_server_conversation_id(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "agent_enabled", True)
    monkeypatch.setattr(settings, "dashscope_api_key", "configured-for-test")
    monkeypatch.setattr(
        "app.agent.service.create_llm_provider",
        lambda _settings: NeverProvider(),
    )

    first = client.post("/api/v1/agent/query", json={"message": "它在哪？"})
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]
    assert conversation_id

    second = client.post(
        "/api/v1/agent/query",
        json={"message": "它还有多少？", "conversation_id": conversation_id},
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id


def test_agent_suggestions_are_stable_database_questions_without_answers(
    client, admin, monkeypatch
):
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        admin_user = db.get(User, admin["id"])
        location = Location(
            code=f"SUG-LOC-{suffix}",
            name="Suggestion bin",
            full_path=f"Test / Suggestion / {suffix}",
        )
        db.add(location)
        db.flush()
        materials = [
            Material(
                code=f"SUG-MAT-A-{suffix}",
                name="Suggestion material A",
                mpn=f"SUG-MPN-A-{suffix}",
                location_id=location.id,
                quantity=Decimal("123"),
                reserved_quantity=Decimal("7"),
            ),
            Material(
                code=f"SUG-MAT-B-{suffix}",
                name="Suggestion material B",
                mpn=f"SUG-MPN-B-{suffix}",
            ),
        ]
        project = Project(
            code=f"SUG-PRJ-{suffix}",
            name="Suggestion project",
            manager_id=admin_user.id,
        )
        db.add_all([*materials, project])
        db.flush()
        db.add(
            BomItem(
                project_id=project.id,
                material_id=materials[0].id,
                required_quantity=Decimal("2"),
            )
        )
        db.commit()

    monkeypatch.setattr(
        "app.llm.factory.create_llm_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("suggestions must not create an LLM provider")
        ),
    )
    first = client.get("/api/v1/agent/suggestions")
    second = client.get("/api/v1/agent/suggestions")
    assert first.status_code == 200
    assert first.json()["source"] == "database"
    assert first.json()["items"] == second.json()["items"]
    assert 1 <= len(first.json()["items"]) <= 5

    with SessionLocal() as db:
        for item in first.json()["items"]:
            assert set(item) == {"type", "text", "material_id", "project_id"}
            assert "quantity" not in item and "location" not in item
            if item["material_id"]:
                material = db.get(Material, item["material_id"])
                assert material is not None
                if item["type"] == "material_location":
                    assert material.location_id is not None
            if item["project_id"]:
                assert db.scalar(select(BomItem).where(BomItem.project_id == item["project_id"]))


def test_agent_suggestions_hide_projects_without_project_permission(client, admin):
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        role = Role(
            name=f"SuggestionViewer-{suffix}",
            permissions=["material:view"],
        )
        db.add(role)
        db.flush()
        viewer = User(
            username=f"suggestion_viewer_{suffix}",
            full_name="Suggestion Viewer",
            password_hash=hash_password("ViewerTest123"),
            role_id=role.id,
            must_change_password=False,
        )
        db.add(viewer)
        db.commit()

    with TestClient(app) as viewer_client:
        login = viewer_client.post(
            "/api/v1/auth/login",
            json={
                "username": f"suggestion_viewer_{suffix}",
                "password": "ViewerTest123",
            },
        )
        assert login.status_code == 200
        response = viewer_client.get("/api/v1/agent/suggestions")
        assert response.status_code == 200
        assert all(item["type"] != "project_bom" for item in response.json()["items"])


def test_user_without_inventory_operate_cannot_approve(client, material, admin):
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        admin_user = db.get(User, admin["id"])
        project = Project(code=f"NOOP-{suffix}", name="无审批权限", manager_id=admin_user.id)
        role = Role(
            name=f"AgentViewer-{suffix}",
            description="Agent viewer",
            permissions=["material:view", "project:manage"],
        )
        db.add_all([project, role])
        db.flush()
        viewer = User(
            username=f"agent_viewer_{suffix}",
            full_name="Agent 查看者",
            password_hash=hash_password("ViewerTest123"),
            role_id=role.id,
            must_change_password=False,
        )
        db.add(viewer)
        db.commit()
        InventoryService(db, admin_user.id, "no-permission-stock").inbound(
            material["id"], Decimal("10"), f"stock-{suffix}", "初始库存"
        )
        proposal = ProposalService(db, admin_user, "no-permission-proposal").create_reservation(
            ProposeInventoryReservationArgs(
                project_id=project.id,
                items=[{"material_id": material["id"], "quantity": "3"}],
                reason="权限测试",
            )
        )
        proposal_id = proposal.id

    listed = client.get("/api/v1/agent/proposals")
    assert listed.status_code == 200
    listed_proposal = next(item for item in listed.json() if item["id"] == proposal_id)
    assert listed_proposal["display"]["project_code"] == f"NOOP-{suffix}"
    assert listed_proposal["display"]["items"][0]["code"] == material["code"]

    with TestClient(app) as viewer_client:
        login = viewer_client.post(
            "/api/v1/auth/login",
            json={"username": f"agent_viewer_{suffix}", "password": "ViewerTest123"},
        )
        assert login.status_code == 200
        response = viewer_client.post(
            f"/api/v1/agent/proposals/{proposal_id}/approve",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        )
        assert response.status_code == 403

    with SessionLocal() as db:
        assert db.get(Material, material["id"]).reserved_quantity == 0
