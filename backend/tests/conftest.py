import os
import tempfile
from pathlib import Path

if os.environ.get("PHASE3_MCP_STDIO_RUN") != "1":
    os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'test.db'}"
os.environ["ENVIRONMENT"] = "test"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import Role, User


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as value:
        yield value


@pytest.fixture(scope="session")
def admin(client):
    with SessionLocal() as db:
        role = db.scalar(select(Role).where(Role.name == "系统管理员"))
        user = User(
            username="admin_test",
            full_name="测试管理员",
            department="QA",
            password_hash=hash_password("AdminTest123"),
            role_id=role.id,
            must_change_password=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    response = client.post(
        "/api/v1/auth/login", json={"username": "admin_test", "password": "AdminTest123"}
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()["user"]


@pytest.fixture
def material(client, admin):
    import uuid

    code = f"TEST-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/materials",
        json={
            "code": code,
            "name": "测试物料",
            "unit": "pcs",
            "safety_stock": 3,
            "target_stock": 10,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
