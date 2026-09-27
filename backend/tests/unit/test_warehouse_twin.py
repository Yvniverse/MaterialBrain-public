"""Read-only projection tests. Can run directly without global pytest app fixtures."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.api.v1.warehouse_maps import router
from app.core.database import Base, get_db
from app.core.exceptions import BusinessError
from app.models import InventoryLot, Location, Material
from app.services.location_organizers import organizer_slot_names
from app.services.warehouse_maps import WarehouseMapService
from app.services.warehouse_routing import load_warehouse_map
from app.services.warehouse_twin import warehouse_twin_focus, warehouse_twin_snapshot

FIXTURE = Path(__file__).resolve().parents[2] / "portfolio_demo_data/v2/warehouse_map_v3.json"


class WarehouseTwinTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        wh = Location(code="WH-RD", name="研发仓库", type="warehouse", full_path="研发仓库")
        self.db.add(wh)
        self.db.flush()
        self.roots = {}
        self.slots = {}
        definition = load_warehouse_map(FIXTURE)
        for binding in definition.bindings:
            style = binding.local_geometry_kind
            root = Location(
                code=binding.location_code,
                name=binding.location_code,
                type="box",
                parent_id=wh.id,
                full_path="研发仓库 / " + binding.location_code,
                organizer_style=style,
                organizer_left_module="small",
                organizer_right_module="large",
            )
            self.db.add(root)
            self.db.flush()
            self.roots[root.code] = root
            names = organizer_slot_names(style, "small", "large")
            for name in names:
                if name not in {"A03", "G08", "L06", "L-S01", "R-L08"}:
                    continue
                loc = Location(
                    code=root.code + "-" + name,
                    name=name,
                    parent_id=root.id,
                    type="shelf" if style == "shelf_rack_6" else "bin",
                    full_path=root.full_path + " / " + name,
                )
                self.db.add(loc)
                self.db.flush()
                self.slots[root.code, name] = loc
        shelf = self.slots["PORT-PWR-6", "L06"]
        box = Location(
            code="PORT-PWR-6-L06-B01",
            name="电源模块箱",
            type="box",
            parent_id=shelf.id,
            full_path=shelf.full_path + " / 电源模块箱",
        )
        self.db.add(box)
        self.db.flush()
        self.box_id = box.id
        target = self.slots["PORT-IC-100", "A03"]
        for code, unit, lot_qty in [("M1", "pcs", "7"), ("M2", "m", "2.5")]:
            material = Material(code=code, name=code, unit=unit, quantity=Decimal("999"))
            self.db.add(material)
            self.db.flush()
            self.db.add(
                InventoryLot(
                    material_id=material.id, location_id=target.id, quantity=Decimal(lot_qty)
                )
            )
        self.db.flush()
        self.map = WarehouseMapService(self.db).seed_definition(definition, activate=False)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_snapshot_has_all_shapes_and_missing_slots_stay_unassigned(self):
        result = warehouse_twin_snapshot(self.db, self.map.id)
        self.assertEqual(len(result["assets"]), 12)
        self.assertEqual(sum((len(a["slots"]) for a in result["assets"])), 728)
        asset = next((a for a in result["assets"] if a["code"] == "PORT-IC-100"))
        empty = next((s for s in asset["slots"] if s["name"] == "A01"))
        self.assertIsNone(empty["location_id"])
        self.assertEqual(empty["materials"], [])
        self.assertEqual(result["metadata"]["source"], "database_snapshot")

    def test_quantities_come_from_lots_and_different_units_stay_separate(self):
        result = warehouse_twin_snapshot(self.db, self.map.id)
        a = next((a for a in result["assets"] if a["code"] == "PORT-IC-100"))
        self.assertEqual(Decimal(a["quantities_by_unit"]["pcs"]), Decimal("7"))
        self.assertEqual(Decimal(a["quantities_by_unit"]["m"]), Decimal("2.5"))
        self.assertEqual(a["material_kind_count"], 2)
        self.assertNotIn("1998", json.dumps(a))

    def test_focus_resolves_A03_G08_and_L06_from_server_geometry(self):
        for code, name in [
            ("PORT-IC-100", "A03"),
            ("PORT-SENSOR-56", "G08"),
            ("PORT-PWR-6", "L06"),
        ]:
            focus = warehouse_twin_focus(self.db, self.map.id, self.slots[code, name].id)
            self.assertEqual(focus["organizer_code"], code)
            self.assertEqual(focus["slot_name"], name)
            self.assertIsNotNone(focus["slot_geometry"])
        focus = warehouse_twin_focus(self.db, self.map.id, self.box_id)
        self.assertEqual(focus["slot_name"], "L06")

    def test_unmapped_location_is_an_error_not_a_fabricated_coordinate(self):
        with self.assertRaises(BusinessError):
            warehouse_twin_focus(self.db, self.map.id, 999999)

    def test_new_routes_require_authentication(self):
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: self.db
        with TestClient(app) as client:
            response = client.get(f"/api/v1/warehouse-maps/{self.map.id}/twin-snapshot")
            self.assertEqual(response.status_code, 401)

    def test_new_routes_respect_existing_view_permissions(self):
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: self.db
        permissions = ["picking:view"]
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            role=SimpleNamespace(permissions=permissions)
        )
        with TestClient(app) as client:
            response = client.get(f"/api/v1/warehouse-maps/{self.map.id}/twin-snapshot")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()["assets"]), 12)
            location = self.slots["PORT-IC-100", "A03"]
            focus = client.get(f"/api/v1/warehouse-maps/{self.map.id}/location-focus/{location.id}")
            self.assertEqual(focus.status_code, 200)
            self.assertEqual(focus.json()["slot_name"], "A03")
            permissions[:] = ["inventory:operate"]
            self.assertEqual(
                client.get(f"/api/v1/warehouse-maps/{self.map.id}/twin-snapshot").status_code, 403
            )

    def test_snapshot_performs_no_insert_update_delete(self):
        statements = []

        def capture(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement.strip().split()[0].upper())

        event.listen(self.engine, "before_cursor_execute", capture)
        before = [(x.id, x.quantity) for x in self.db.scalars(select(InventoryLot)).all()]
        warehouse_twin_snapshot(self.db, self.map.id)
        after = [(x.id, x.quantity) for x in self.db.scalars(select(InventoryLot)).all()]
        self.assertEqual(before, after)
        self.assertFalse(set(statements) & {"INSERT", "UPDATE", "DELETE"})
        self.assertFalse(self.db.new or self.db.dirty or self.db.deleted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
