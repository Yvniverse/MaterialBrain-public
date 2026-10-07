from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.exceptions import BusinessError
from app.models import Location, Role, User, WarehouseMap
from app.services.warehouse_maps import WarehouseMapService
from app.services.warehouse_routing import load_warehouse_map

FIXTURE = (
    Path(__file__).resolve().parents[2] / "sample_data" / "v2" / "warehouse_map_v1.json"
)


def _seed_map_admin_db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'warehouse-map-admin.db').as_posix()}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    role = Role(name="地图管理员", permissions=["location:view", "location:manage"])
    db.add(role)
    db.flush()
    user = User(
        username="mapadmin",
        full_name="Map Admin",
        password_hash="x",
        role_id=role.id,
        must_change_password=False,
    )
    db.add(user)
    warehouse = Location(code="WH-RD", name="研发仓库", type="warehouse", full_path="研发仓库")
    db.add(warehouse)
    db.flush()
    definition = load_warehouse_map(FIXTURE)
    for binding in definition.bindings:
        db.add(
            Location(
                parent_id=warehouse.id,
                code=binding.location_code,
                name=binding.location_code,
                type="box",
                full_path=f"研发仓库 / {binding.location_code}",
                organizer_style=binding.local_geometry_kind,
            )
        )
    db.flush()
    return db, user, warehouse, definition


def test_map_admin_clones_edits_verifies_and_activates_version(tmp_path):
    db, user, warehouse, definition = _seed_map_admin_db(tmp_path)
    try:
        service = WarehouseMapService(db)
        active = service.seed_definition(definition, activate=True)
        draft = service.clone_to_draft(
            active.id,
            code="WH-RD-MAP-V2",
            version="2.0.0",
            name="研发仓库地图 v2 草稿",
        )
        assert draft.status == "draft"
        assert draft.calibration_status == "demo_synthetic"

        edited = replace(
            service.definition(draft.id),
            width_m=12.5,
            geometry_note="现场测量草稿",
        )
        service.replace_draft_definition(draft.id, edited)
        service.set_calibration_status(
            draft.id,
            "measured",
            actor_id=user.id,
            geometry_note="已现场测量，待复核",
        )
        service.set_calibration_status(
            draft.id,
            "verified",
            actor_id=user.id,
            geometry_note="现场双人复核完成",
        )
        service.activate_verified(draft.id)
        db.commit()

        assert (
            db.scalar(select(WarehouseMap).where(WarehouseMap.code == active.code)).status
            == "archived"
        )
        activated = db.scalar(select(WarehouseMap).where(WarehouseMap.code == draft.code))
        assert activated.status == "active"
        assert activated.calibration_status == "verified"
        assert activated.verified_by_id == user.id
        assert activated.graph_hash == service.definition(activated.id).graph_hash
        assert len(service.list_for_warehouse(warehouse.id)) == 2
    finally:
        db.close()


def test_production_activation_rejects_unverified_draft(tmp_path):
    db, _user, _warehouse, definition = _seed_map_admin_db(tmp_path)
    try:
        service = WarehouseMapService(db)
        active = service.seed_definition(definition, activate=True)
        draft = service.clone_to_draft(
            active.id,
            code="WH-RD-MAP-V2",
            version="2.0.0",
            name="研发仓库地图 v2 草稿",
        )
        with pytest.raises(BusinessError) as error:
            service.activate_verified(draft.id)
        assert error.value.code == "WAREHOUSE_MAP_NOT_VERIFIED"
    finally:
        db.close()


def test_calibration_rejects_relabelled_demo_geometry(tmp_path):
    db, user, _, definition = _seed_map_admin_db(tmp_path)
    try:
        service = WarehouseMapService(db)
        active = service.seed_definition(definition, activate=True)
        draft = service.clone_to_draft(active.id, code="DEMO-COPY", version="2", name="复制")
        with pytest.raises(BusinessError) as error:
            service.set_calibration_status(
                draft.id, "measured", actor_id=user.id, geometry_note="只复制示例，未测量"
            )
        assert error.value.code == "WAREHOUSE_MAP_DEMO_NOT_MEASURED"
        assert draft.calibration_status == "demo_synthetic"
        with pytest.raises(BusinessError) as error:
            service.set_calibration_status(
                draft.id, "verified", actor_id=user.id, geometry_note="试图跳过测量"
            )
        assert error.value.code == "WAREHOUSE_MAP_NOT_MEASURED"
    finally:
        db.close()


def test_map_bootstrap_incomplete_draft_preserves_location_truth(tmp_path):
    from scripts.seed_warehouse_map import inspect_fixture

    db, _, warehouse, _ = _seed_map_admin_db(tmp_path)
    try:
        db.add(
            Location(
                code="LEGACY-RACK",
                name="Legacy",
                type="rack",
                parent_id=warehouse.id,
                full_path="warehouse/legacy",
            )
        )
        db.flush()
        assert inspect_fixture(db, FIXTURE)["warehouse_found"]
        with pytest.raises(BusinessError) as exc:
            inspect_fixture(db, FIXTURE, require_complete=True)
        assert exc.value.code == "WAREHOUSE_MAP_ORGANIZER_UNMAPPED"
    finally:
        db.close()


def test_map_bootstrap_draft_rejects_foreign_warehouse_binding(tmp_path):
    from scripts.seed_warehouse_map import inspect_fixture

    db, _, _, definition = _seed_map_admin_db(tmp_path)
    try:
        location = db.scalar(
            select(Location).where(Location.code == definition.bindings[0].location_code)
        )
        location.parent_id = None
        db.flush()
        with pytest.raises(BusinessError) as exc:
            inspect_fixture(db, FIXTURE)
        assert exc.value.code == "WAREHOUSE_MAP_LOCATION_OUTSIDE_WAREHOUSE"
    finally:
        db.close()
