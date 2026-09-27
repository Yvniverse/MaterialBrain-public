import io
import uuid
from decimal import Decimal

from openpyxl import Workbook


def op(client, path, material_id, quantity, **extra):
    return client.post(
        f"/api/v1/inventory/{path}",
        json={
            "material_id": material_id,
            "quantity": quantity,
            "idempotency_key": str(uuid.uuid4()),
            "reason": "集成测试",
            **extra,
        },
    )


def test_login_me_and_logout(client, admin):
    assert client.get("/api/v1/auth/me").json()["username"] == "admin_test"


def test_material_edit_cannot_modify_stock(client, material):
    response = client.put(f"/api/v1/materials/{material['id']}", json={"quantity": 999})
    assert response.status_code == 422
    assert client.get(f"/api/v1/materials/{material['id']}").json()["quantity"] == "0.0000"


def test_material_delete_soft_deletes_zero_stock_and_blocks_stock(client, material):
    deleted = client.delete(f"/api/v1/materials/{material['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/materials/{material['id']}").status_code == 404
    assert client.delete(f"/api/v1/materials/{material['id']}").status_code == 404

    stocked_code = f"DELETE-STOCK-{uuid.uuid4().hex[:8]}"
    stocked = client.post(
        "/api/v1/materials",
        json={"code": stocked_code, "name": "有库存物料"},
    ).json()
    assert op(client, "inbound", stocked["id"], 1).status_code == 200
    blocked = client.delete(f"/api/v1/materials/{stocked['id']}")
    assert blocked.status_code == 400
    assert blocked.json()["code"] == "MATERIAL_HAS_STOCK"


def test_inventory_api_and_movement(client, material):
    assert op(client, "inbound", material["id"], 10).status_code == 200
    assert op(client, "outbound", material["id"], 4).json()["quantity"] == "6.0000"
    insufficient = op(client, "outbound", material["id"], 7)
    assert (
        insufficient.status_code == 400
        and insufficient.json()["code"] == "INSUFFICIENT_AVAILABLE_STOCK"
    )
    movements = client.get("/api/v1/stock-movements", params={"material_id": material["id"]}).json()
    assert movements["total"] == 2


def test_inventory_quantity_must_be_an_integer(client, material):
    response = op(client, "inbound", material["id"], 1.5)
    assert response.status_code == 422


def test_runtime_info_exposes_non_secret_build_identity(client):
    data = client.get("/api/v1/runtime-info").json()
    assert set(data) == {
        "backend_build_sha",
        "release_tag",
        "build_time_utc",
        "environment",
    }
    assert data["backend_build_sha"]
    assert "api_key" not in str(data).casefold()


def test_health_and_dashboard(client, admin):
    assert client.get("/api/v1/health").status_code == 200
    data = client.get("/api/v1/dashboard/summary").json()
    assert "material_count" in data and "trend" in data and len(data["trend"]) == 30


def test_dashboard_today_flows_follow_all_stock_quantity_changes(client, admin):
    before = client.get("/api/v1/dashboard/summary").json()
    code = f"DASH-{uuid.uuid4().hex[:8]}"
    imported = client.post(
        "/api/v1/imports/materials/commit",
        json={"rows": [{"code": code, "name": "仪表盘测试", "quantity": 7}]},
    )
    assert imported.status_code == 200
    material = client.get("/api/v1/materials", params={"q": code}).json()["items"][0]

    after_import = client.get("/api/v1/dashboard/summary").json()
    assert Decimal(after_import["today_inbound"]) - Decimal(before["today_inbound"]) == 7

    assert op(client, "outbound", material["id"], 2).status_code == 200
    after_outbound = client.get("/api/v1/dashboard/summary").json()
    assert Decimal(after_outbound["today_outbound"]) - Decimal(before["today_outbound"]) == 2


def test_material_filter_can_include_nested_categories(client, admin):
    suffix = uuid.uuid4().hex[:8]
    parent = client.post(
        "/api/v1/categories",
        json={"code": f"P-{suffix}", "name": f"父分类-{suffix}"},
    ).json()
    child = client.post(
        "/api/v1/categories",
        json={"code": f"C-{suffix}", "name": f"子分类-{suffix}", "parent_id": parent["id"]},
    ).json()
    parent_code = f"MAT-P-{suffix}"
    child_code = f"MAT-C-{suffix}"
    for code, category_id in ((parent_code, parent["id"]), (child_code, child["id"])):
        response = client.post(
            "/api/v1/materials",
            json={"code": code, "name": code, "category_id": category_id},
        )
        assert response.status_code == 201

    exact = client.get(
        "/api/v1/materials",
        params={"category_id": parent["id"], "page_size": 200},
    ).json()
    nested = client.get(
        "/api/v1/materials",
        params={
            "category_id": parent["id"],
            "include_descendants": True,
            "page_size": 200,
        },
    ).json()

    assert {item["code"] for item in exact["items"]} == {parent_code}
    assert {item["code"] for item in nested["items"]} == {parent_code, child_code}
    assert nested["total"] == 2
    assert nested["summary"]["quantity"] == "0.0000"


def test_create_organizer_and_filter_materials_by_location(client, admin):
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/v1/locations/organizers",
        json={
            "code": f"BOX-{suffix}",
            "name": f"贴片元件盒-{suffix}",
            "notes": "电阻与电容",
        },
    )
    assert response.status_code == 201, response.text
    organizer = response.json()["organizer"]
    bins = response.json()["bins"]
    assert organizer["type"] == "box"
    assert organizer["organizer_style"] == "standard_56"
    assert len(bins) == 56
    assert bins[0]["name"] == "A01"
    assert bins[-1]["name"] == "G08"

    material_code = f"MAT-BOX-{suffix}"
    created = client.post(
        "/api/v1/materials",
        json={
            "code": material_code,
            "name": "盒内测试物料",
            "location_id": bins[0]["id"],
        },
    )
    assert created.status_code == 201
    nested = client.get(
        "/api/v1/materials",
        params={
            "location_id": organizer["id"],
            "include_location_descendants": True,
            "page_size": 200,
        },
    ).json()
    assert {item["code"] for item in nested["items"]} == {material_code}

    locations = client.get("/api/v1/locations").json()
    selected_bin = next(item for item in locations if item["id"] == bins[0]["id"])
    assert selected_bin["material_count"] == 1

    renamed = client.put(
        f"/api/v1/locations/{organizer['id']}",
        json={
            "code": organizer["code"],
            "name": f"更名后的元件盒-{suffix}",
            "type": "box",
            "notes": "电阻与电容",
        },
    )
    assert renamed.status_code == 200
    locations = client.get("/api/v1/locations").json()
    renamed_bin = next(item for item in locations if item["id"] == bins[0]["id"])
    assert renamed_bin["full_path"].startswith(f"更名后的元件盒-{suffix} / ")


def test_create_100_drawer_rack_with_20_rows_and_5_columns(client, admin):
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/v1/locations/organizers",
        json={
            "code": f"RACK-{suffix}",
            "name": f"100抽货架-{suffix}",
            "notes": "结构件与耗材",
            "organizer_style": "drawer_rack_100",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["organizer"]["organizer_style"] == "drawer_rack_100"
    assert len(body["bins"]) == 100
    assert [item["name"] for item in body["bins"][:6]] == [
        "A01",
        "B01",
        "C01",
        "D01",
        "E01",
        "A02",
    ]
    assert body["bins"][-1]["name"] == "E20"
    assert body["bins"][-1]["notes"] == "100抽货架 · 第 20 行 · E 列"

    content = client.put(
        f"/api/v1/locations/{body['bins'][-1]['id']}/content",
        json={"material_name": "M3 螺丝", "quantity": 50, "notes": "黑色"},
    )
    assert content.status_code == 200, content.text
    assert content.json()["bin_material_name"] == "M3 螺丝"
    assert content.json()["bin_quantity"] == 50


def test_create_six_level_shelf_rack_and_store_multiple_items_per_box(client, admin):
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/v1/locations/organizers",
        json={
            "code": f"SHELF-{suffix}",
            "name": f"六层物料货架-{suffix}",
            "organizer_style": "shelf_rack_6",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    rack = body["organizer"]
    shelves = body["bins"]
    assert rack["organizer_style"] == "shelf_rack_6"
    assert len(shelves) == 6
    assert [item["name"] for item in shelves] == [
        "第 1 层",
        "第 2 层",
        "第 3 层",
        "第 4 层",
        "第 5 层",
        "第 6 层",
    ]
    assert all(item["type"] == "shelf" for item in shelves)

    box_response = client.post(
        f"/api/v1/locations/shelf-racks/{rack['id']}/shelves/{shelves[0]['id']}/boxes",
        json={"name": "紧固件箱", "notes": "第一层左侧"},
    )
    assert box_response.status_code == 201, box_response.text
    storage_box = box_response.json()
    assert storage_box["type"] == "container"
    assert storage_box["parent_id"] == shelves[0]["id"]

    item_payloads = [
        {"material_name": "M3 螺丝", "quantity": 100, "notes": "黑色"},
        {"material_name": "M3 螺母", "quantity": 80, "notes": "镀锌"},
    ]
    item_responses = [
        client.post(
            f"/api/v1/locations/shelf-boxes/{storage_box['id']}/items",
            json=payload,
        )
        for payload in item_payloads
    ]
    assert all(item.status_code == 201 for item in item_responses)
    items = [item.json() for item in item_responses]
    assert {item["bin_material_name"] for item in items} == {"M3 螺丝", "M3 螺母"}
    assert all(item["parent_id"] == storage_box["id"] for item in items)

    blocked = client.delete(
        f"/api/v1/locations/shelf-racks/{rack['id']}/boxes/{storage_box['id']}"
    )
    assert blocked.status_code == 400
    assert blocked.json()["code"] == "SHELF_STORAGE_BOX_IN_USE"

    updated = client.put(
        f"/api/v1/locations/{items[0]['id']}/content",
        json={"material_name": "M3×8 螺丝", "quantity": 90, "notes": "黑色"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["bin_material_name"] == "M3×8 螺丝"
    assert updated.json()["name"] == "M3×8 螺丝"

    for item in items:
        deleted = client.delete(
            f"/api/v1/locations/shelf-boxes/{storage_box['id']}/items/{item['id']}"
        )
        assert deleted.status_code == 200, deleted.text

    deleted_box = client.delete(
        f"/api/v1/locations/shelf-racks/{rack['id']}/boxes/{storage_box['id']}"
    )
    assert deleted_box.status_code == 200, deleted_box.text


def test_clear_bin_content_detaches_legacy_material_and_preserves_inventory(client, admin):
    suffix = uuid.uuid4().hex[:8]
    organizer_response = client.post(
        "/api/v1/locations/organizers",
        json={
            "code": f"CLEAR-{suffix}",
            "name": f"清空测试盒-{suffix}",
        },
    )
    assert organizer_response.status_code == 201
    bin_location = organizer_response.json()["bins"][0]
    material_response = client.post(
        "/api/v1/materials",
        json={
            "code": f"CAP-{suffix}",
            "name": "测试电容",
            "location_id": bin_location["id"],
        },
    )
    assert material_response.status_code == 201
    material = material_response.json()
    assert op(client, "inbound", material["id"], 3).status_code == 200

    before = client.get("/api/v1/locations").json()
    occupied_bin = next(item for item in before if item["id"] == bin_location["id"])
    assert occupied_bin["material_count"] == 1

    cleared = client.delete(f"/api/v1/locations/{bin_location['id']}/content")
    assert cleared.status_code == 200
    assert cleared.json()["bin_material_name"] == ""
    assert cleared.json()["bin_quantity"] is None

    preserved_material = client.get(f"/api/v1/materials/{material['id']}").json()
    assert preserved_material["location_id"] is None
    assert preserved_material["quantity"] == "3.0000"

    after = client.get("/api/v1/locations").json()
    empty_bin = next(item for item in after if item["id"] == bin_location["id"])
    assert empty_bin["material_count"] == 0
    assert empty_bin["quantity"] == "0"


def test_delete_organizer_requires_empty_bins_and_removes_generated_locations(client, admin):
    suffix = uuid.uuid4().hex[:8]
    organizer_response = client.post(
        "/api/v1/locations/organizers",
        json={
            "code": f"DELETE-BOX-{suffix}",
            "name": f"待删除元件盒-{suffix}",
        },
    )
    assert organizer_response.status_code == 201
    organizer = organizer_response.json()["organizer"]
    bins = organizer_response.json()["bins"]
    nested_response = client.post(
        "/api/v1/locations",
        json={
            "code": f"DELETE-NESTED-{suffix}",
            "name": "嵌套空库位",
            "parent_id": bins[1]["id"],
            "type": "bin",
        },
    )
    assert nested_response.status_code == 201
    nested = nested_response.json()

    content = client.put(
        f"/api/v1/locations/{bins[0]['id']}/content",
        json={"material_name": "占用测试", "quantity": 1, "notes": ""},
    )
    assert content.status_code == 200
    blocked = client.delete(f"/api/v1/locations/organizers/{organizer['id']}")
    assert blocked.status_code == 400
    assert blocked.json()["code"] == "ORGANIZER_IN_USE"

    assert client.delete(f"/api/v1/locations/{bins[0]['id']}/content").status_code == 200
    deleted = client.delete(f"/api/v1/locations/organizers/{organizer['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted_bin_count"] == 57

    remaining_ids = {item["id"] for item in client.get("/api/v1/locations").json()}
    assert organizer["id"] not in remaining_ids
    assert nested["id"] not in remaining_ids
    assert not remaining_ids.intersection({item["id"] for item in bins})


def test_create_and_switch_configurable_split_organizer(client, admin):
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/v1/locations/organizers",
        json={
            "code": f"MIX-{suffix}",
            "name": f"混合元件盒-{suffix}",
            "organizer_style": "split_configurable",
            "organizer_left_module": "large",
            "organizer_right_module": "small",
        },
    )
    assert response.status_code == 201, response.text
    organizer = response.json()["organizer"]
    bins = response.json()["bins"]
    assert organizer["organizer_style"] == "split_configurable"
    assert organizer["organizer_left_module"] == "large"
    assert organizer["organizer_right_module"] == "small"
    assert len(bins) == 36
    assert bins[0]["name"] == "L-L01"
    assert bins[7]["name"] == "L-L08"
    assert bins[8]["name"] == "R-S01"
    assert bins[-1]["name"] == "R-S28"

    switched = client.put(
        f"/api/v1/locations/organizers/{organizer['id']}/layout",
        json={
            "organizer_left_module": "large",
            "organizer_right_module": "large",
        },
    )
    assert switched.status_code == 200, switched.text
    switched_body = switched.json()
    assert switched_body["organizer"]["organizer_right_module"] == "large"
    assert len(switched_body["bins"]) == 16
    assert switched_body["bins"][-1]["name"] == "R-L08"

    content = client.put(
        f"/api/v1/locations/{switched_body['bins'][0]['id']}/content",
        json={
            "material_name": "功率继电器",
            "quantity": 4,
            "notes": "左半区第一格",
        },
    )
    assert content.status_code == 200, content.text
    assert content.json()["bin_material_name"] == "功率继电器"
    assert content.json()["bin_quantity"] == 4
    assert content.json()["bin_content_notes"] == "左半区第一格"

    stored_locations = client.get("/api/v1/locations").json()
    stored_bin = next(
        item for item in stored_locations if item["id"] == switched_body["bins"][0]["id"]
    )
    assert stored_bin["bin_material_name"] == "功率继电器"
    assert stored_bin["bin_quantity"] == 4

    blocked = client.put(
        f"/api/v1/locations/organizers/{organizer['id']}/layout",
        json={
            "organizer_left_module": "small",
            "organizer_right_module": "large",
        },
    )
    assert blocked.status_code == 400
    assert blocked.json()["code"] == "ORGANIZER_HALF_IN_USE"
    assert "左半区已有物料" in blocked.json()["message"]

    cleared = client.delete(
        f"/api/v1/locations/{switched_body['bins'][0]['id']}/content"
    )
    assert cleared.status_code == 200
    assert cleared.json()["bin_material_name"] == ""
    assert cleared.json()["bin_quantity"] is None

    after_clear = client.put(
        f"/api/v1/locations/organizers/{organizer['id']}/layout",
        json={
            "organizer_left_module": "small",
            "organizer_right_module": "large",
        },
    )
    assert after_clear.status_code == 200, after_clear.text
    assert len(after_clear.json()["bins"]) == 36


def test_cable_management_filters_and_records_every_quantity_change(client, admin):
    created = client.post(
        "/api/v1/cables",
        json={
            "name": "",
            "model": "FFC-A",
            "connector_pitch_mm": 1.0,
            "direction": "reverse",
            "length_cm": 20,
            "pin_count": 30,
            "quantity": 5,
            "storage_location": "线缆抽屉 A",
            "notes": "屏幕排线",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert created.status_code == 201, created.text
    cable = created.json()
    assert cable["code"].startswith("CBL-")
    assert cable["name"] == "1.0mm 30Pin 20.0cm 反向线缆"
    assert cable["quantity"] == 5

    filtered = client.get(
        "/api/v1/cables",
        params={
            "q": "抽屉 A",
            "connector_pitch_mm": "1.0",
            "direction": "reverse",
            "length_cm": "20",
            "pin_count": 30,
        },
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 1
    assert filtered.json()["summary"]["quantity"] == 5
    assert "1.0" in filtered.json()["facets"]["connector_pitches"]

    decreased = client.post(
        f"/api/v1/cables/{cable['id']}/quantity",
        json={"delta": -2, "idempotency_key": str(uuid.uuid4())},
    )
    assert decreased.status_code == 200, decreased.text
    assert decreased.json()["quantity"] == 3

    update_payload = {
        "name": "摄像头定制排线",
        "model": "FFC-CAM-24",
        "connector_pitch_mm": 1.25,
        "direction": "same",
        "length_cm": 37.5,
        "pin_count": 24,
        "quantity": 7,
        "storage_location": "线缆抽屉 B",
        "notes": "自定义长度",
        "idempotency_key": str(uuid.uuid4()),
    }
    updated = client.put(f"/api/v1/cables/{cable['id']}", json=update_payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "摄像头定制排线"
    assert updated.json()["connector_pitch_mm"] == "1.25"
    assert updated.json()["length_cm"] == "37.5"
    assert updated.json()["quantity"] == 7

    blocked = client.delete(f"/api/v1/cables/{cable['id']}")
    assert blocked.status_code == 400
    assert blocked.json()["code"] == "CABLE_HAS_STOCK"

    update_payload["quantity"] = 0
    update_payload["idempotency_key"] = str(uuid.uuid4())
    cleared = client.put(f"/api/v1/cables/{cable['id']}", json=update_payload)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["quantity"] == 0

    movements = client.get(
        "/api/v1/stock-movements",
        params={"material_id": cable["id"], "page_size": 20},
    )
    assert movements.status_code == 200
    assert movements.json()["total"] == 4

    deleted = client.delete(f"/api/v1/cables/{cable['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/materials/{cable['id']}").status_code == 404


def test_cable_order_import_previews_commits_and_prevents_duplicate_stock(client, admin):
    suffix = uuid.uuid4().hex[:8]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "订单数据"
    sheet.append(
        [
            "订单号",
            "订单状态",
            "店铺名称",
            "商品名称",
            "商品链接",
            "型号款式",
            "商品数量",
            "商品金额",
        ]
    )
    sheet.append(
        [
            f"ORDER-{suffix}",
            "交易成功",
            "测试连接器店",
            "HC-0.8 端子线",
            f"https://example.test/{suffix}",
            f"HC-0.8-7PLT;150MM（15厘米）;双头同向-{suffix}",
            12,
            "￥2.50",
        ]
    )
    content = io.BytesIO()
    workbook.save(content)

    preview_response = client.post(
        "/api/v1/cables/import/preview",
        files={
            "file": (
                f"cables-{suffix}.xlsx",
                content.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["summary"]["source_rows"] == 1
    assert preview["summary"]["quantity"] == 12
    assert preview["rows"][0]["connector_pitch_mm"] == "0.8"
    assert preview["rows"][0]["pin_count"] == 7
    assert preview["rows"][0]["length_cm"] == "15.0"

    first_commit = client.post(
        "/api/v1/cables/import/commit",
        json={
            "rows": preview["rows"],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert first_commit.status_code == 200, first_commit.text
    assert first_commit.json()["created"] == 1
    assert first_commit.json()["quantity_added"] == 12

    second_commit = client.post(
        "/api/v1/cables/import/commit",
        json={
            "rows": preview["rows"],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert second_commit.status_code == 200, second_commit.text
    assert second_commit.json()["skipped"] == 1
    assert second_commit.json()["quantity_added"] == 0

    stored = client.get(
        "/api/v1/cables",
        params={"q": "HC-0.8-7PLT", "page_size": 200},
    ).json()
    imported = next(
        item
        for item in stored["items"]
        if item["notes"].startswith("淘宝订单导入")
    )
    assert imported["quantity"] == 12
    assert imported["unit_price"] == "2.5"


def test_loan_feature_is_not_exposed(client, admin):
    assert client.get("/api/v1/loans").status_code == 404
    assert client.post("/api/v1/inventory/loan", json={}).status_code == 404
    assert client.post("/api/v1/inventory/return", json={}).status_code == 404
