import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import User


def unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def test_existing_role_permissions_can_be_changed_immediately(client, admin):
    role_name = unique_name("仓库访客")
    created_role = client.post(
        "/api/v1/roles",
        json={
            "name": role_name,
            "description": "权限编辑测试",
            "permissions": ["material:view", "dashboard:view", "material:view"],
        },
    )
    assert created_role.status_code == 201, created_role.text
    role = created_role.json()
    assert role["permissions"] == ["dashboard:view", "material:view"]
    assert role["is_system"] is False

    username = unique_name("permission-user")
    created_user = client.post(
        "/api/v1/users",
        json={
            "username": username,
            "full_name": "权限测试用户",
            "department": "QA",
            "role_id": role["id"],
            "password": "123456",
        },
    )
    assert created_user.status_code == 201, created_user.text
    user = created_user.json()

    with TestClient(app) as limited_client:
        login = limited_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "123456"},
        )
        assert login.status_code == 200, login.text
        limited_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        assert limited_client.get("/api/v1/materials").status_code == 200
        assert limited_client.get("/api/v1/users").status_code == 403

        updated_role = client.put(
            f"/api/v1/roles/{role['id']}",
            json={
                "name": role_name,
                "description": "现在可以管理用户",
                "permissions": [
                    "dashboard:view",
                    "material:view",
                    "user:manage",
                ],
            },
        )
        assert updated_role.status_code == 200, updated_role.text
        assert updated_role.json()["permissions"] == [
            "dashboard:view",
            "material:view",
            "user:manage",
        ]
        assert limited_client.get("/api/v1/users").status_code == 200
        assert limited_client.get("/api/v1/roles").status_code == 200
        denied_update = limited_client.put(
            f"/api/v1/roles/{role['id']}",
            json={
                "name": role_name,
                "description": "",
                "permissions": ["dashboard:view"],
            },
        )
        assert denied_update.status_code == 403

    assert client.delete(f"/api/v1/users/{user['id']}").status_code == 200


def test_user_delete_revokes_sessions_and_preserves_a_soft_deleted_history_row(client, admin):
    roles = client.get("/api/v1/roles").json()
    basic_role = next(role for role in roles if "*" not in role["permissions"])
    username = unique_name("delete-user")
    created = client.post(
        "/api/v1/users",
        json={
            "username": username,
            "full_name": "待删除用户",
            "department": "研发部",
            "role_id": basic_role["id"],
            "password": "123456",
        },
    )
    assert created.status_code == 201, created.text
    target = created.json()

    with TestClient(app) as target_client:
        login = target_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "123456"},
        )
        assert login.status_code == 200, login.text
        target_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

        deleted = client.delete(f"/api/v1/users/{target['id']}")
        assert deleted.status_code == 200, deleted.text
        assert "历史业务记录已保留" in deleted.json()["message"]
        assert target_client.get("/api/v1/auth/me").status_code == 401

    visible_ids = {item["id"] for item in client.get("/api/v1/users").json()}
    assert target["id"] not in visible_ids
    assert client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "123456"},
    ).status_code == 401

    with SessionLocal() as db:
        stored = db.scalar(select(User).where(User.id == target["id"]))
        assert stored is not None
        assert stored.is_deleted is True
        assert stored.is_active is False
        assert stored.deleted_at is not None
        assert stored.username.startswith(f"deleted-{target['id']}-")

    recreated = client.post(
        "/api/v1/users",
        json={
            "username": username,
            "full_name": "重新创建用户",
            "department": "",
            "role_id": basic_role["id"],
            "password": "654321",
        },
    )
    assert recreated.status_code == 201, recreated.text
    assert client.delete(f"/api/v1/users/{recreated.json()['id']}").status_code == 200


def test_current_user_cannot_delete_or_disable_self(client, admin):
    delete_response = client.delete(f"/api/v1/users/{admin['id']}")
    assert delete_response.status_code == 409
    assert delete_response.json()["code"] == "CANNOT_DELETE_SELF"

    disable_response = client.put(
        f"/api/v1/users/{admin['id']}",
        json={"is_active": False},
    )
    assert disable_response.status_code == 409
    assert disable_response.json()["code"] == "CANNOT_DISABLE_SELF"


def test_unknown_role_permission_is_rejected(client, admin):
    response = client.post(
        "/api/v1/roles",
        json={
            "name": unique_name("非法权限角色"),
            "description": "",
            "permissions": ["unknown:permission"],
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_PERMISSIONS"
