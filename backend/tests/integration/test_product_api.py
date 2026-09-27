import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import AgentActionProposal, Material, StockMovement


def _inventory_snapshot(material_id: int) -> tuple[Decimal, Decimal, int, int]:
    with SessionLocal() as db:
        material = db.get(Material, material_id)
        return (
            material.quantity,
            material.reserved_quantity,
            int(db.scalar(select(func.count()).select_from(StockMovement)) or 0),
            int(db.scalar(select(func.count()).select_from(AgentActionProposal)) or 0),
        )


def test_product_revision_bom_api_and_readiness_are_audited_and_inventory_safe(
    client, admin, material
):
    code = f"TEST-PROD-{uuid.uuid4().hex[:8]}"
    product_response = client.post(
        "/api/v1/products",
        json={"code": code, "name": "测试产品", "description": "单台 BOM API 测试"},
    )
    assert product_response.status_code == 201, product_response.text
    product = product_response.json()

    invalid_default = client.post(
        f"/api/v1/products/{product['id']}/revisions",
        json={"revision": "DRAFT-R1", "status": "draft", "is_default": True},
    )
    assert invalid_default.status_code == 409
    assert invalid_default.json()["code"] == "PRODUCT_DEFAULT_REVISION_INVALID"

    revision_response = client.post(
        f"/api/v1/products/{product['id']}/revisions",
        json={"revision": "EVT-R1", "status": "draft", "is_default": False},
    )
    assert revision_response.status_code == 201, revision_response.text
    revision = revision_response.json()
    bom_response = client.post(
        f"/api/v1/product-revisions/{revision['id']}/bom",
        json={"material_id": material["id"], "quantity_per_unit": "2.5000"},
    )
    assert bom_response.status_code == 201, bom_response.text

    release_response = client.post(
        f"/api/v1/product-revisions/{revision['id']}/release",
        json={"make_default": True},
    )
    assert release_response.status_code == 200, release_response.text
    released = release_response.json()
    assert released["status"] == "released"
    assert released["is_default"] is True
    assert len(released["bom_hash"]) == 64

    immutable_add = client.post(
        f"/api/v1/product-revisions/{revision['id']}/bom",
        json={"material_id": material["id"], "quantity_per_unit": "1"},
    )
    assert immutable_add.status_code == 409
    assert immutable_add.json()["code"] == "PRODUCT_REVISION_IMMUTABLE"
    immutable_rename = client.put(
        f"/api/v1/product-revisions/{revision['id']}",
        json={"revision": "EVT-R1-EDITED"},
    )
    assert immutable_rename.status_code == 409
    assert immutable_rename.json()["code"] == "PRODUCT_REVISION_IMMUTABLE"

    clone_response = client.post(
        f"/api/v1/product-revisions/{revision['id']}/clone",
        json={"new_revision": "EVT-R2"},
    )
    assert clone_response.status_code == 201, clone_response.text
    clone = clone_response.json()
    assert clone["status"] == "draft"
    assert clone["is_default"] is False
    assert clone["bom_hash"] is None
    clone_bom = client.get(f"/api/v1/product-revisions/{clone['id']}/bom")
    assert clone_bom.status_code == 200
    assert clone_bom.json()["count"] == 1

    bom = client.get(f"/api/v1/product-revisions/{revision['id']}/bom")
    assert bom.status_code == 200
    assert bom.json()["items"][0]["quantity_per_unit"] == "2.5000"
    assert bom.json()["quantity_semantics"] == "单台用量；不是 Project BOM 总需求"

    before = _inventory_snapshot(material["id"])
    readiness = client.post(
        f"/api/v1/product-revisions/{revision['id']}/build-readiness",
        json={"build_quantity": 2, "project_id": None},
    )
    assert readiness.status_code == 200, readiness.text
    payload = readiness.json()
    assert payload["build_quantity"] == 2
    assert payload["items"][0]["required_total"] == "5.0000"
    assert payload["read_only"] is True
    assert _inventory_snapshot(material["id"]) == before

    products = client.get("/api/v1/products")
    assert products.status_code == 200
    listed = next(item for item in products.json() if item["id"] == product["id"])
    assert listed["default_revision"]["revision"] == "EVT-R1"
