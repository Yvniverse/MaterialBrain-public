"""PostGIS queries and additive registration over WarehouseMap, never inventory.

Transactions belong to the caller, as for WarehouseMapService. Read methods do
not backfill or bootstrap. SQLite geometry helpers support legacy unit tests and
are explicitly labelled; PostgreSQL always executes indexed spatial predicates.
"""

from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models import Location, WarehouseMap
from app.services.embodied_navigation.planner import canonical_hash

from . import models as spatial_models  # noqa: F401 - registers additive metadata
from .geometry import (
    covers_point,
    geometry_distance,
    geometry_vertices,
    intersects,
    point_geometry,
    rectangle_polygon,
    validate_geometry,
)
from .snapshot import build_lab_snapshot, static_revision

ENTITY_TABLES = {
    "assets": ("warehouse_spatial_assets", "geom"),
    "docks": ("warehouse_spatial_docks", "geom"),
    "nodes": ("warehouse_map_nodes", "geom"),
    "edges": ("warehouse_map_edges", "geom"),
    "zones": ("warehouse_semantic_zones", "geom"),
    "overlays": ("warehouse_dynamic_overlays", "geom"),
    "bindings": ("location_map_bindings", "footprint"),
}
QUERY_PREDICATES = {
    "contains": "ST_Covers",
    "nearby": "ST_DWithin",
    "nearest_dock": "<->",
    "intersects": "ST_Intersects",
    "edge_zones": "ST_Intersects",
    "affected_edges": "ST_Intersects",
}
LOCAL_POINT_SQL = "ST_SetSRID(ST_MakePoint(:x,:y),0)"
LOCAL_GEOMETRY_SQL = "ST_SetSRID(ST_GeomFromGeoJSON(:geometry),0)"


def _json(value, default=None):
    if value is None:
        return copy.deepcopy(default)
    return json.loads(value) if isinstance(value, str) else copy.deepcopy(value)


def _geometry(value):
    result = _json(value)
    if result is None:
        return None

    def numbers(item):
        return [numbers(child) for child in item] if isinstance(item, list) else float(item)

    result["coordinates"] = numbers(result["coordinates"])
    return result


def _encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _iso(value):
    return _utc(value).isoformat().replace("+00:00", "Z")


class SpatialMapService:
    def __init__(self, session: Session, *, clock=None):
        self.db = session
        self.postgis = self.db.get_bind().dialect.name == "postgresql"
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def engine(self):
        return "postgis" if self.postgis else "sqlite_geometry_compat"

    def _geometry_select(self, column):
        return f"ST_AsGeoJSON({column},9,0)" if self.postgis else column

    def _geometry_bind(self, parameter="geometry"):
        return (
            f"ST_SetSRID(ST_GeomFromGeoJSON(:{parameter}),0)" if self.postgis else f":{parameter}"
        )

    def _json_bind(self, parameter):
        return f"CAST(:{parameter} AS jsonb)" if self.postgis else f":{parameter}"

    def _map(self, map_id):
        if not isinstance(map_id, str) or not map_id or len(map_id) > 64:
            raise BusinessError("SPATIAL_MAP_INVALID", "地图编码无效。", 422)
        row = (
            self.db.execute(
                text(
                    "SELECT id,code,name,status,calibration_status,graph_hash,"
                    "spatial_revision,spatial_metadata,"
                    "width_m,height_m,default_start_node_code,default_end_node_code,geometry_note,"
                    f"{self._geometry_select('geom')} AS geometry "
                    "FROM warehouse_maps WHERE code=:code"
                ),
                {"code": map_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("SPATIAL_MAP_NOT_FOUND", "空间地图未注册。", 404)
        return row

    def _collection(self, table, map_fk, *, geometry_column="geom", extra="", suffix=""):
        rows = list(
            self.db.execute(
                text(
                    f"SELECT *, {self._geometry_select(geometry_column)} AS geometry {extra} "
                    f"FROM {table} WHERE warehouse_map_id=:map_fk {suffix} ORDER BY code"
                ),
                {"map_fk": map_fk},
            ).mappings()
        )
        # Database locale can place Z-CHARGING after ZA. Snapshot identity uses
        # Python's canonical code ordering, independently of database collation.
        return sorted(rows, key=lambda row: row["code"])

    def register_lab(self) -> dict:
        snapshot = build_lab_snapshot()
        # Lock an existing root; lab registration creates no storage/inventory identities.
        active = self.db.scalar(select(WarehouseMap).where(WarehouseMap.code == "WH-RD-TWIN-V4"))
        root = self.db.scalar(
            select(Location)
            .where(
                Location.id == active.warehouse_location_id
                if active
                else Location.type == "warehouse"
            )
            .order_by(Location.id)
            .with_for_update()
        )
        if root is None or root.type != "warehouse":
            raise BusinessError("SPATIAL_WAREHOUSE_ROOT_REQUIRED", "先注册仓库根库位。", 409)
        existing = (
            self.db.execute(
                text(
                    "SELECT id,spatial_revision,graph_hash,status "
                    "FROM warehouse_maps WHERE code=:code"
                ),
                {"code": snapshot["map_id"]},
            )
            .mappings()
            .first()
        )
        if existing is not None:
            if (
                existing["spatial_revision"] != snapshot["revision"]
                or existing["graph_hash"] != snapshot["provenance"]["graph_hash"]
            ):
                raise BusinessError(
                    "SPATIAL_MAP_REVISION_CONFLICT", "地图编码已有不同修订，需注册新版本。", 409
                )
            self.snapshot(snapshot["map_id"])  # verify persisted graph, not only the hash column
            return self._registration(snapshot, existing["id"], False, existing["status"])
        metadata = {
            "frame": snapshot["frame"],
            "provenance": snapshot["provenance"],
            "affordances": snapshot["affordances"],
            "walls": snapshot["geometry"]["walls"],
        }
        self.db.execute(
            text(
                "INSERT INTO warehouse_maps (warehouse_location_id,code,name,version,status,"
                "calibration_status,"
                "coordinate_unit,width_m,height_m,default_start_node_code,default_end_node_code,graph_hash,"
                "geometry_note,geom,spatial_revision,spatial_metadata) VALUES "
                "(:root,:code,:name,:version,'draft','demo_synthetic','m',:width,:height,'HOME','HOME',:hash,:note,"
                f"{self._geometry_bind()},:revision,{self._json_bind('metadata')})"
            ),
            {
                "root": root.id,
                "code": snapshot["map_id"],
                "name": "研发物流实验仓 · 空间地图",
                "version": "lab-v4." + snapshot["revision"][:12],
                "width": 24,
                "height": 18,
                "hash": snapshot["provenance"]["graph_hash"],
                "note": (
                    "synthetic_lab; unchanged canonical V3 geometry; "
                    "local metric SRID 0; non-active lab map"
                ),
                "geometry": _encoded(snapshot["geometry"]["floor"]),
                "revision": snapshot["revision"],
                "metadata": _encoded(metadata),
            },
        )
        map_fk = self.db.execute(
            text("SELECT id FROM warehouse_maps WHERE code=:code"), {"code": snapshot["map_id"]}
        ).scalar_one()
        docks = {dock["node_id"]: dock for dock in snapshot["docks"]}
        self.db.execute(
            text(
                "INSERT INTO warehouse_map_nodes "
                "(warehouse_map_id,code,node_type,label,x_m,y_m,is_active,geom,yaw_rad) "
                f"VALUES (:map_fk,:code,:kind,:label,:x,:y,true,{self._geometry_bind()},:yaw)"
            ),
            [
                {
                    "map_fk": map_fk,
                    "code": n["id"],
                    "kind": "packing"
                    if n["id"] in ("HOME", "CHARGER")
                    else "pick_face"
                    if n["id"] in docks
                    else "intersection",
                    "label": docks.get(n["id"], {}).get("label", n["id"]),
                    "x": n["x"],
                    "y": n["y"],
                    "yaw": n["yaw"],
                    "geometry": _encoded(point_geometry(n)),
                }
                for n in snapshot["route_graph"]["nodes"]
            ],
        )
        node_ids = dict(
            self.db.execute(
                text("SELECT code,id FROM warehouse_map_nodes WHERE warehouse_map_id=:map_fk"),
                {"map_fk": map_fk},
            ).all()
        )
        self.db.execute(
            text(
                "INSERT INTO warehouse_map_edges "
                "(warehouse_map_id,code,from_node_id,to_node_id,distance_m,"
                "bidirectional,is_active,notes,geom,width_m,speed_limit_mps,risk_level,"
                "allowed_robot_classes,semantic_rules) "
                "VALUES (:map_fk,:code,:origin,:target,:length,false,true,"
                "'synthetic semantic lab graph',"
                f"{self._geometry_bind()},:width,:speed,:risk,{self._json_bind('classes')},{self._json_bind('rules')})"
            ),
            [
                {
                    "map_fk": map_fk,
                    "code": edge["id"],
                    "origin": node_ids[edge["from"]],
                    "target": node_ids[edge["to"]],
                    "length": edge["length_m"],
                    "width": edge["width_m"],
                    "speed": edge["speed_limit_mps"],
                    "risk": edge["risk_level"],
                    "classes": _encoded(edge["allowed_robot_classes"]),
                    "geometry": _encoded(edge["geometry"]),
                    "rules": _encoded(
                        {
                            key: edge[key]
                            for key in ("zone_ids", "rule_refs", "energy_wh", "min_clearance_m")
                        }
                    ),
                }
                for edge in snapshot["route_graph"]["edges"]
            ],
        )
        self.db.execute(
            text(
                "INSERT INTO warehouse_spatial_assets "
                "(warehouse_map_id,code,name,asset_type,geom,metadata) "
                f"VALUES (:map_fk,:code,:name,:kind,{self._geometry_bind()},"
                f"{self._json_bind('metadata')})"
            ),
            [
                {
                    "map_fk": map_fk,
                    "code": a["id"],
                    "name": a["name"],
                    "kind": a["kind"],
                    "geometry": _encoded(a["footprint"]),
                    "metadata": _encoded(
                        {k: v for k, v in a.items() if k not in ("id", "name", "kind", "footprint")}
                    ),
                }
                for a in snapshot["geometry"]["assets"]
            ],
        )
        self.db.execute(
            text(
                "INSERT INTO warehouse_semantic_zones "
                "(warehouse_map_id,code,name,zone_type,geom,speed_limit_mps,risk_level,metadata) "
                f"VALUES (:map_fk,:code,:name,:kind,{self._geometry_bind()},"
                f":speed,:risk,{self._json_bind('metadata')})"
            ),
            [
                {
                    "map_fk": map_fk,
                    "code": z["id"],
                    "name": z["name"],
                    "kind": z["kind"],
                    "speed": z["speed_limit_mps"],
                    "risk": z["risk_level"],
                    "geometry": _encoded(z["polygon"]),
                    "metadata": _encoded(
                        {
                            k: v
                            for k, v in z.items()
                            if k
                            not in (
                                "id",
                                "name",
                                "kind",
                                "polygon",
                                "speed_limit_mps",
                                "risk_level",
                            )
                        }
                    ),
                }
                for z in snapshot["zones"]
            ],
        )
        self.db.execute(
            text(
                "INSERT INTO warehouse_spatial_docks "
                "(warehouse_map_id,code,node_id,asset_code,geom,yaw_rad,metadata) "
                f"VALUES (:map_fk,:code,:node_id,:asset,{self._geometry_bind()},"
                f":yaw,{self._json_bind('metadata')})"
            ),
            [
                {
                    "map_fk": map_fk,
                    "code": d["id"],
                    "node_id": node_ids[d["node_id"]],
                    "asset": d["asset_id"],
                    "yaw": d["pose"]["yaw"],
                    "geometry": _encoded(point_geometry(d["pose"])),
                    "metadata": _encoded(
                        {
                            k: v
                            for k, v in d.items()
                            if k not in ("id", "node_id", "asset_id", "pose")
                        }
                    ),
                }
                for d in snapshot["docks"]
            ],
        )
        # Round-trip is a fail-closed provenance check, not a duplicate JSON map master.
        self.snapshot(snapshot["map_id"])
        return self._registration(snapshot, map_fk, True, "draft")

    @staticmethod
    def _registration(snapshot, map_fk, created, status):
        return {
            "map_id": snapshot["map_id"],
            "warehouse_map_id": map_fk,
            "revision": snapshot["revision"],
            "graph_hash": snapshot["provenance"]["graph_hash"],
            "status": status,
            "created": created,
            "node_count": len(snapshot["route_graph"]["nodes"]),
            "edge_count": len(snapshot["route_graph"]["edges"]),
            "inventory_written": False,
        }

    def snapshot(self, map_id: str) -> dict:
        row = self._map(map_id)
        map_fk = row["id"]
        metadata = _json(row["spatial_metadata"], {})
        raw_nodes = self._collection("warehouse_map_nodes", map_fk, suffix="AND is_active=true")
        raw_edges = self._collection("warehouse_map_edges", map_fk, suffix="AND is_active=true")
        assets = [
            {
                "id": a["code"],
                "name": a["name"],
                "kind": a["asset_type"],
                **_json(a["metadata"], {}),
                "footprint": _geometry(a["geometry"]),
            }
            for a in self._collection("warehouse_spatial_assets", map_fk)
        ]
        zones = [
            {
                "id": z["code"],
                "name": z["name"],
                "kind": z["zone_type"],
                "polygon": _geometry(z["geometry"]),
                "speed_limit_mps": z["speed_limit_mps"],
                "risk_level": float(z["risk_level"]),
                **_json(z["metadata"], {}),
            }
            for z in self._collection("warehouse_semantic_zones", map_fk)
        ]
        node_codes = {n["id"]: n["code"] for n in raw_nodes}
        for node in raw_nodes:
            geometry = _geometry(node["geometry"])
            if geometry and (
                geometry["type"] != "Point"
                or math.dist(geometry["coordinates"], [float(node["x_m"]), float(node["y_m"])])
                > 1e-7
            ):
                raise BusinessError(
                    "SPATIAL_GEOMETRY_INCONSISTENT", "节点几何与已注册坐标不一致。", 409
                )
            if row["spatial_revision"] and geometry is None:
                raise BusinessError(
                    "SPATIAL_GEOMETRY_INCONSISTENT", "已注册导航节点缺失几何。", 409
                )
        docks = []
        for dock in self._collection("warehouse_spatial_docks", map_fk):
            if dock["node_id"] not in node_codes:
                raise BusinessError("SPATIAL_TOPOLOGY_INVALID", "停靠点引用了无效导航节点。", 409)
            point = _geometry(dock["geometry"])["coordinates"]
            docks.append(
                {
                    "id": dock["code"],
                    "node_id": node_codes[dock["node_id"]],
                    "asset_id": dock["asset_code"],
                    "pose": {"x": point[0], "y": point[1], "yaw": float(dock["yaw_rad"])},
                    **_json(dock["metadata"], {}),
                }
            )
        dock_nodes = {dock["node_id"] for dock in docks}
        nodes = [
            {
                "id": n["code"],
                "x": float(n["x_m"]),
                "y": float(n["y_m"]),
                "yaw": float(n["yaw_rad"]),
                "kind": "dock" if n["code"] in dock_nodes else n["node_type"],
            }
            for n in raw_nodes
        ]
        edges = []
        for e in raw_edges:
            if e["from_node_id"] not in node_codes or e["to_node_id"] not in node_codes:
                raise BusinessError("SPATIAL_TOPOLOGY_INVALID", "导航边引用了无效节点。", 409)
            first, second = node_codes[e["from_node_id"]], node_codes[e["to_node_id"]]
            rules = _json(e["semantic_rules"], {})
            edge = {
                "id": e["code"],
                "from": first,
                "to": second,
                "length_m": float(e["distance_m"]),
                "width_m": e["width_m"],
                "speed_limit_mps": e["speed_limit_mps"],
                "risk_level": float(e["risk_level"]),
                "allowed_robot_classes": _json(e["allowed_robot_classes"], []),
                "bidirectional": False,
                "geometry": _geometry(e["geometry"]),
                **rules,
            }
            edge.setdefault("zone_ids", [])
            edge.setdefault("rule_refs", [])
            edge.setdefault("energy_wh", None)
            edge.setdefault("min_clearance_m", None)
            edges.append(edge)
            if e["bidirectional"]:
                reversed_edge = copy.deepcopy(edge)
                reversed_edge.update(id=edge["id"] + "::reverse", **{"from": second, "to": first})
                if reversed_edge["geometry"]:
                    reversed_edge["geometry"]["coordinates"].reverse()
                edges.append(reversed_edge)
        floor = _geometry(row["geometry"])
        if floor is None:
            width, height = float(row["width_m"]), float(row["height_m"])
            floor = rectangle_polygon(
                {"x": width / 2, "y": height / 2, "width": width, "depth": height}
            )
        snapshot = {
            "schema_version": 1,
            "map_id": map_id,
            "frame": metadata.get(
                "frame", {"name": "warehouse_map", "unit": "m", "srid": 0, "site_georef": None}
            ),
            "geometry": {
                "floor": floor,
                "assets": assets,
                "walls": metadata.get("walls", []),
                "columns": [a for a in assets if a["kind"] == "column"],
            },
            "zones": zones,
            "route_graph": {"nodes": nodes, "edges": sorted(edges, key=lambda e: e["id"])},
            "docks": docks,
            "affordances": metadata.get("affordances", []),
            "dynamic_overlays": self._active_overlays(map_fk),
            "provenance": metadata.get(
                "provenance",
                {
                    "source": "warehouse_map",
                    "geometry_note": row["geometry_note"],
                    "calibration_status": row["calibration_status"],
                    "map_status": row["status"],
                    "graph_hash": row["graph_hash"],
                    "inventory_written": False,
                },
            ),
        }
        snapshot["revision"] = static_revision(snapshot)
        if row["spatial_revision"] and (
            canonical_hash(snapshot["route_graph"]) != row["graph_hash"]
            or snapshot["provenance"].get("graph_hash") != row["graph_hash"]
        ):
            raise BusinessError(
                "SPATIAL_MAP_REVISION_MISMATCH", "地图图哈希与持久导航图不一致。", 409
            )
        if row["spatial_revision"] and row["spatial_revision"] != snapshot["revision"]:
            raise BusinessError(
                "SPATIAL_MAP_REVISION_MISMATCH",
                "持久地图与已注册修订不一致，停止导航并重新校验。",
                409,
            )
        return snapshot

    def _overlay(self, row):
        return {
            "id": row["code"],
            "kind": row["overlay_type"],
            "geometry": _geometry(row["geometry"]),
            **_json(row["payload"], {}),
            "created_at": _iso(row["active_from"]),
            "expires_at": _iso(row["expires_at"]),
        }

    def _active_overlays(self, map_fk):
        now = _utc(self.clock())
        if self.postgis:
            rows = self.db.execute(
                text(
                    f"SELECT *, {self._geometry_select('geom')} AS geometry "
                    "FROM warehouse_dynamic_overlays WHERE warehouse_map_id=:map_fk "
                    "AND is_active=true AND active_from<=:now AND expires_at>:now ORDER BY code"
                ),
                {"map_fk": map_fk, "now": now},
            ).mappings()
            return [self._overlay(row) for row in rows]
        rows = self._collection("warehouse_dynamic_overlays", map_fk, suffix="AND is_active=true")
        return [
            self._overlay(r) for r in rows if _utc(r["active_from"]) <= now < _utc(r["expires_at"])
        ]

    def put_overlay(self, map_id: str, overlay: dict) -> dict:
        row = self._map(map_id)
        if self.postgis:
            # Serialize metadata writes for this map; simultaneous retries must
            # observe the first committed overlay and preserve its expiry.
            self.db.execute(
                text("SELECT id FROM warehouse_maps WHERE id=:map_fk FOR UPDATE"),
                {"map_fk": row["id"]},
            ).scalar_one()
        if not isinstance(overlay, dict):
            raise BusinessError("SPATIAL_OVERLAY_INVALID", "动态限制参数无效。", 422)
        allowed = {
            "id",
            "kind",
            "geometry",
            "source",
            "reason",
            "ttl_s",
            "speed_limit_mps",
            "risk_level",
            "edge_ids",
            "node_ids",
        }
        if set(overlay) - allowed:
            raise BusinessError("SPATIAL_OVERLAY_INVALID", "时间与地图修订由服务端管理。", 422)
        code, kind = overlay.get("id"), overlay.get("kind")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", code):
            raise BusinessError("SPATIAL_OVERLAY_INVALID", "动态限制编码无效。", 422)
        if not isinstance(kind, str) or kind not in {
            "closure",
            "obstacle",
            "speed_override",
            "risk_override",
        }:
            raise BusinessError("SPATIAL_OVERLAY_INVALID", "动态限制类型无效。", 422)
        try:
            geometry = validate_geometry(overlay.get("geometry"))
            if geometry["type"] == "Point":
                raise ValueError("OVERLAY_REQUIRES_AREA_OR_LINE")
            if any(
                not 0 <= p[0] <= float(row["width_m"]) or not 0 <= p[1] <= float(row["height_m"])
                for p in geometry_vertices(geometry)
            ):
                raise ValueError("OVERLAY_OUTSIDE_MAP")
            ttl = overlay.get("ttl_s", 300)
            if (
                isinstance(ttl, bool)
                or not isinstance(ttl, (int, float))
                or not math.isfinite(ttl)
                or not 1 <= ttl <= 86400
            ):
                raise ValueError("INVALID_OVERLAY_TTL")
            for key, maximum in (("speed_limit_mps", 5), ("risk_level", 1)):
                if key in overlay:
                    value = overlay[key]
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or not 0 <= value <= maximum
                    ):
                        raise ValueError("INVALID_OVERLAY_POLICY")
                    if key == "speed_limit_mps" and value == 0:
                        raise ValueError("INVALID_OVERLAY_POLICY")
            if kind == "speed_override" and "speed_limit_mps" not in overlay:
                raise ValueError("OVERLAY_SPEED_REQUIRED")
            if kind == "risk_override" and "risk_level" not in overlay:
                raise ValueError("OVERLAY_RISK_REQUIRED")
            for key, maximum in (("source", 200), ("reason", 500)):
                if (
                    not isinstance(overlay.get(key), str)
                    or not overlay[key].strip()
                    or len(overlay[key]) > maximum
                ):
                    raise ValueError("OVERLAY_PROVENANCE_REQUIRED")
            for key, table in (
                ("edge_ids", "warehouse_map_edges"),
                ("node_ids", "warehouse_map_nodes"),
            ):
                if key not in overlay:
                    continue
                codes = overlay[key]
                if (
                    not isinstance(codes, list)
                    or len(codes) > 128
                    or any(not isinstance(c, str) for c in codes)
                ):
                    raise ValueError("INVALID_OVERLAY_REFERENCES")
                known = set(
                    self.db.execute(
                        text(
                            f"SELECT code FROM {table} "
                            "WHERE warehouse_map_id=:map_fk AND is_active=true"
                        ),
                        {"map_fk": row["id"]},
                    ).scalars()
                )
                if not set(codes) <= known:
                    raise ValueError("UNKNOWN_OVERLAY_REFERENCE")
        except (ValueError, TypeError, KeyError) as exc:
            raise BusinessError("SPATIAL_OVERLAY_INVALID", str(exc), 422) from exc
        if (
            self.postgis
            and not self.db.execute(
                text(f"SELECT ST_IsValid({self._geometry_bind()})"),
                {"geometry": _encoded(geometry)},
            ).scalar_one()
        ):
            raise BusinessError("SPATIAL_OVERLAY_INVALID", "动态限制几何无效。", 422)
        payload = {
            k: copy.deepcopy(v) for k, v in overlay.items() if k not in ("id", "kind", "geometry")
        }
        payload["ttl_s"] = ttl
        existing = (
            self.db.execute(
                text(
                    f"SELECT *, {self._geometry_select('geom')} AS geometry "
                    "FROM warehouse_dynamic_overlays WHERE warehouse_map_id=:map_fk AND code=:code"
                ),
                {"map_fk": row["id"], "code": code},
            )
            .mappings()
            .first()
        )
        if existing:
            if (
                existing["overlay_type"] != kind
                or _geometry(existing["geometry"]) != geometry
                or _json(existing["payload"], {}) != payload
            ):
                raise BusinessError(
                    "SPATIAL_OVERLAY_CONFLICT", "动态限制编码已有不同内容，请使用新编码。", 409
                )
            return {
                "map_id": map_id,
                "map_revision": self.snapshot(map_id)["revision"],
                "overlay": self._overlay(existing),
                "created": False,
                "inventory_written": False,
            }
        now = _utc(self.clock())
        expires = now + timedelta(seconds=ttl)
        self.db.execute(
            text(
                "INSERT INTO warehouse_dynamic_overlays "
                "(warehouse_map_id,code,overlay_type,geom,payload,"
                "active_from,expires_at,is_active) "
                f"VALUES (:map_fk,:code,:kind,{self._geometry_bind()},"
                f"{self._json_bind('payload')},:now,:expires,true)"
            ),
            {
                "map_fk": row["id"],
                "code": code,
                "kind": kind,
                "geometry": _encoded(geometry),
                "payload": _encoded(payload),
                "now": now if self.postgis else _iso(now),
                "expires": expires if self.postgis else _iso(expires),
            },
        )
        result = {
            "id": code,
            "kind": kind,
            "geometry": copy.deepcopy(geometry),
            **payload,
            "created_at": _iso(now),
            "expires_at": _iso(expires),
        }
        return {
            "map_id": map_id,
            "map_revision": self.snapshot(map_id)["revision"],
            "overlay": result,
            "created": True,
            "inventory_written": False,
        }

    def query(self, map_id: str, query: dict) -> dict:
        row = self._map(map_id)
        if not isinstance(query, dict):
            raise BusinessError("SPATIAL_QUERY_INVALID", "空间查询参数无效。", 422)
        kind = query.get("kind", query.get("type"))
        if not isinstance(kind, str):
            raise BusinessError("SPATIAL_QUERY_INVALID", "空间查询类型无效。", 422)
        kind = {
            "near": "nearby",
            "zone_membership": "contains",
            "edge_zone_overlap": "edge_zones",
        }.get(kind, kind)
        if kind not in QUERY_PREDICATES:
            raise BusinessError("SPATIAL_QUERY_INVALID", "空间查询类型无效。", 422)
        entity = (
            "docks"
            if kind == "nearest_dock"
            else query.get(
                "entity",
                "zones" if kind == "contains" else "edges" if kind == "intersects" else "docks",
            )
        )
        if not isinstance(entity, str) or entity not in ENTITY_TABLES:
            raise BusinessError("SPATIAL_QUERY_INVALID", "空间实体类型无效。", 422)
        try:
            limit = query.get("limit", 10)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                raise ValueError("INVALID_SPATIAL_LIMIT")
            parameters = {"map_fk": row["id"], "limit": limit}
            geometry = None
            if kind in {"contains", "nearby", "nearest_dock"}:
                p = query.get("point", query.get("pose"))
                if not isinstance(p, dict):
                    raise ValueError("SPATIAL_POINT_REQUIRED")
                geometry = validate_geometry(
                    {"type": "Point", "coordinates": [p.get("x"), p.get("y")]}
                )
                parameters.update(x=float(p["x"]), y=float(p["y"]))
            if kind == "nearby":
                radius = query.get("radius_m", 5)
                if (
                    isinstance(radius, bool)
                    or not isinstance(radius, (int, float))
                    or not math.isfinite(radius)
                    or not 0 < radius <= 1000
                ):
                    raise ValueError("INVALID_SPATIAL_RADIUS")
                parameters["radius"] = radius
            if kind == "intersects":
                geometry = validate_geometry(query.get("geometry"))
                parameters["geometry"] = _encoded(geometry)
            if kind == "edge_zones":
                if not isinstance(query.get("edge_id"), str):
                    raise ValueError("SPATIAL_EDGE_REQUIRED")
                parameters["edge_id"] = query["edge_id"]
            if kind == "affected_edges" and query.get("overlay_id"):
                if not isinstance(query["overlay_id"], str):
                    raise ValueError("INVALID_OVERLAY_REFERENCE")
                parameters["overlay_id"] = query["overlay_id"]
        except (ValueError, TypeError, KeyError) as exc:
            raise BusinessError("SPATIAL_QUERY_INVALID", str(exc), 422) from exc
        if self.postgis:
            results = self._postgis_query(kind, entity, parameters)
        else:
            results = self._sqlite_query(kind, entity, parameters, geometry)
        return {
            "map_id": map_id,
            "map_revision": row["spatial_revision"] or self.snapshot(map_id)["revision"],
            "query_kind": kind,
            "entity": "zones"
            if kind == "edge_zones"
            else "edges"
            if kind == "affected_edges"
            else entity,
            "engine": self.engine,
            "predicate": QUERY_PREDICATES[kind] if self.postgis else "local_geometry_compat",
            "results": results,
            "inventory_written": False,
        }

    def _postgis_query(self, kind, entity, parameters):
        if kind == "edge_zones":
            sql = """
                SELECT z.code AS id,z.zone_type AS kind,z.speed_limit_mps,z.risk_level
                FROM warehouse_semantic_zones z JOIN warehouse_map_edges e
                  ON z.warehouse_map_id=e.warehouse_map_id AND ST_Intersects(z.geom,e.geom)
                WHERE e.warehouse_map_id=:map_fk AND e.code=:edge_id AND e.is_active
                ORDER BY z.code LIMIT :limit
            """
        elif kind == "affected_edges":
            extra = " AND o.code=:overlay_id" if "overlay_id" in parameters else ""
            sql = (
                """
                SELECT e.code AS id,o.code AS overlay_id,o.overlay_type AS kind
                FROM warehouse_map_edges e JOIN warehouse_dynamic_overlays o
                  ON e.warehouse_map_id=o.warehouse_map_id AND ST_Intersects(e.geom,o.geom)
                WHERE e.warehouse_map_id=:map_fk AND e.is_active AND o.is_active
                  AND o.active_from<=:now AND o.expires_at>:now
            """
                + extra
                + " ORDER BY e.code,o.code LIMIT :limit"
            )
            parameters["now"] = _utc(self.clock())
        else:
            table, column = ENTITY_TABLES[entity]
            identity = "CAST(location_id AS text)" if entity == "bindings" else "code"
            point = LOCAL_POINT_SQL
            predicate = {
                "contains": f"ST_Covers({column},{point})",
                "nearby": f"ST_DWithin({column},{point},:radius)",
                "nearest_dock": "true",
                "intersects": f"ST_Intersects({column},{LOCAL_GEOMETRY_SQL})",
            }[kind]
            active = " AND is_active=true" if entity in {"nodes", "edges", "overlays"} else ""
            if entity == "overlays":
                active += " AND active_from<=:now AND expires_at>:now"
                parameters["now"] = _utc(self.clock())
            distance = (
                f",ST_Distance({column},{point}) AS distance_m"
                if kind in {"nearby", "nearest_dock"}
                else ""
            )
            order = (
                f"{column} <-> {point},{identity}"
                if kind in {"nearby", "nearest_dock"}
                else identity
            )
            sql = (
                f"SELECT {identity} AS id,ST_AsGeoJSON({column},9,0) AS geometry {distance} "
                f"FROM {table} WHERE warehouse_map_id=:map_fk "
                f"AND {column} IS NOT NULL AND {predicate}"
                f"{active} ORDER BY {order} LIMIT :limit"
            )
        rows = self.db.execute(text(sql), parameters).mappings()
        return [
            {
                key: _geometry(value)
                if key == "geometry"
                else float(value)
                if key in {"distance_m", "speed_limit_mps", "risk_level"} and value is not None
                else value
                for key, value in row.items()
            }
            for row in rows
        ]

    def _sqlite_query(self, kind, entity, parameters, geometry):
        map_fk, limit = parameters["map_fk"], parameters["limit"]
        if kind in {"edge_zones", "affected_edges"}:
            edges = self._collection("warehouse_map_edges", map_fk, suffix="AND is_active=true")
            targets = (
                self._collection("warehouse_semantic_zones", map_fk)
                if kind == "edge_zones"
                else self._active_overlays(map_fk)
            )
            results = []
            for edge in edges:
                if kind == "edge_zones" and edge["code"] != parameters["edge_id"]:
                    continue
                edge_geometry = _geometry(edge["geometry"])
                if not edge_geometry:
                    continue
                for target in targets:
                    code = target["code"] if kind == "edge_zones" else target["id"]
                    if "overlay_id" in parameters and parameters["overlay_id"] != code:
                        continue
                    target_geometry = (
                        _geometry(target["geometry"])
                        if kind == "edge_zones"
                        else target["geometry"]
                    )
                    if intersects(edge_geometry, target_geometry):
                        results.append(
                            {
                                "id": code,
                                "kind": target["zone_type"],
                                "speed_limit_mps": target["speed_limit_mps"],
                                "risk_level": target["risk_level"],
                            }
                            if kind == "edge_zones"
                            else {"id": edge["code"], "overlay_id": code, "kind": target["kind"]}
                        )
            return sorted(results, key=lambda r: (r["id"], r.get("overlay_id", "")))[:limit]
        table, column = ENTITY_TABLES[entity]
        if entity == "bindings":
            rows = list(
                self.db.execute(
                    text(
                        f"SELECT *,footprint AS geometry FROM {table} "
                        "WHERE warehouse_map_id=:map_fk ORDER BY location_id"
                    ),
                    {"map_fk": map_fk},
                ).mappings()
            )
        else:
            rows = self._collection(table, map_fk, geometry_column=column)
        results = []
        now = _utc(self.clock())
        for row in rows:
            candidate = _geometry(row["geometry"])
            if not candidate or (entity in {"nodes", "edges", "overlays"} and not row["is_active"]):
                continue
            if entity == "overlays" and not _utc(row["active_from"]) <= now < _utc(
                row["expires_at"]
            ):
                continue
            if kind == "contains" and not covers_point(candidate, geometry["coordinates"]):
                continue
            if kind == "intersects" and not intersects(candidate, geometry):
                continue
            distance = (
                geometry_distance(candidate, geometry)
                if kind in {"nearby", "nearest_dock"}
                else None
            )
            if kind == "nearby" and distance > parameters["radius"]:
                continue
            result = {
                "id": str(row["location_id"]) if entity == "bindings" else row["code"],
                "geometry": candidate,
            }
            if distance is not None:
                result["distance_m"] = distance
            results.append(result)
        return sorted(results, key=lambda r: (r.get("distance_m", 0), r["id"]))[:limit]
