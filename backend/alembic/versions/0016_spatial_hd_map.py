"""Add local-metric PostGIS geometry, semantic policy, docks and TTL overlays.

Revision ID: 0016_spatial_hd_map
Revises: 0015_evidence_layout_index

PostgreSQL requires an extension-capable image before this migration. SQLite is
an explicitly labelled legacy-test compatibility store, not PostGIS evidence.
Historical WarehouseMap identity/scalar fields and business data are untouched.
"""

import json
import math

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import UserDefinedType

from alembic import op

revision = "0016_spatial_hd_map"
down_revision = "0015_evidence_layout_index"
branch_labels = None
depends_on = None


class _Geometry(UserDefinedType):
    cache_ok = True

    def __init__(self, kind="GEOMETRY"):
        self.kind = kind

    def get_col_spec(self, **kw):
        return f"geometry({self.kind},0)"


def _geometry(kind="GEOMETRY"):
    return _Geometry(kind) if op.get_bind().dialect.name == "postgresql" else sa.Text()


def _json():
    return JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def _columns():
    return {
        "warehouse_maps": [
            sa.Column("geom", _geometry("POLYGON")),
            sa.Column("spatial_revision", sa.String(64)),
            sa.Column("spatial_metadata", _json()),
        ],
        "warehouse_map_nodes": [
            sa.Column("geom", _geometry("POINT")),
            sa.Column("yaw_rad", sa.Float(), server_default="0", nullable=False),
        ],
        "warehouse_map_edges": [
            sa.Column("geom", _geometry("LINESTRING")),
            sa.Column("width_m", sa.Float()),
            sa.Column("speed_limit_mps", sa.Float()),
            sa.Column("risk_level", sa.Float(), server_default="0", nullable=False),
            sa.Column("allowed_robot_classes", _json(), server_default="[]", nullable=False),
            sa.Column("semantic_rules", _json(), server_default="{}", nullable=False),
        ],
        "location_map_bindings": [
            sa.Column("footprint", _geometry("POLYGON")),
            sa.Column("dock_yaw_rad", sa.Float()),
        ],
    }


def _map_fk():
    return sa.Column(
        "warehouse_map_id",
        sa.Integer(),
        sa.ForeignKey("warehouse_maps.id", ondelete="CASCADE"),
        nullable=False,
    )


def _create_tables():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("warehouse_spatial_assets"):
        op.create_table(
            "warehouse_spatial_assets",
            sa.Column("id", sa.Integer(), primary_key=True),
            _map_fk(),
            sa.Column("code", sa.String(64), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("asset_type", sa.String(32), nullable=False),
            sa.Column("geom", _geometry("POLYGON"), nullable=False),
            sa.Column("metadata", _json(), nullable=False, server_default="{}"),
            sa.UniqueConstraint("warehouse_map_id", "code", name="uq_warehouse_spatial_asset_code"),
        )
    if not inspector.has_table("warehouse_semantic_zones"):
        op.create_table(
            "warehouse_semantic_zones",
            sa.Column("id", sa.Integer(), primary_key=True),
            _map_fk(),
            sa.Column("code", sa.String(64), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("zone_type", sa.String(32), nullable=False),
            sa.Column("geom", _geometry("POLYGON"), nullable=False),
            sa.Column("speed_limit_mps", sa.Float()),
            sa.Column("risk_level", sa.Float(), nullable=False, server_default="0"),
            sa.Column("metadata", _json(), nullable=False, server_default="{}"),
            sa.CheckConstraint("risk_level >= 0 AND risk_level <= 1", name="ck_wh_zone_risk"),
            sa.CheckConstraint(
                "speed_limit_mps IS NULL OR speed_limit_mps > 0", name="ck_wh_zone_speed"
            ),
            sa.UniqueConstraint("warehouse_map_id", "code", name="uq_warehouse_semantic_zone_code"),
        )
    if not inspector.has_table("warehouse_spatial_docks"):
        op.create_table(
            "warehouse_spatial_docks",
            sa.Column("id", sa.Integer(), primary_key=True),
            _map_fk(),
            sa.Column("code", sa.String(64), nullable=False),
            sa.Column(
                "node_id", sa.Integer(), sa.ForeignKey("warehouse_map_nodes.id"), nullable=False
            ),
            sa.Column("asset_code", sa.String(64)),
            sa.Column("geom", _geometry("POINT"), nullable=False),
            sa.Column("yaw_rad", sa.Float(), nullable=False, server_default="0"),
            sa.Column("metadata", _json(), nullable=False, server_default="{}"),
            sa.UniqueConstraint("warehouse_map_id", "code", name="uq_warehouse_spatial_dock_code"),
        )
    if not inspector.has_table("warehouse_dynamic_overlays"):
        op.create_table(
            "warehouse_dynamic_overlays",
            sa.Column("id", sa.Integer(), primary_key=True),
            _map_fk(),
            sa.Column("code", sa.String(64), nullable=False),
            sa.Column("overlay_type", sa.String(32), nullable=False),
            sa.Column("geom", _geometry(), nullable=False),
            sa.Column("payload", _json(), nullable=False, server_default="{}"),
            sa.Column(
                "active_from",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.CheckConstraint(
                "overlay_type IN ('closure','obstacle','speed_override','risk_override')",
                name="ck_wh_overlay_type",
            ),
            sa.CheckConstraint("expires_at > active_from", name="ck_wh_overlay_expiry"),
            sa.UniqueConstraint(
                "warehouse_map_id", "code", name="uq_warehouse_dynamic_overlay_code"
            ),
        )


GEOMETRY_INDEXES = (
    ("ix_wh_maps_geom_gist", "warehouse_maps", "geom"),
    ("ix_wh_nodes_geom_gist", "warehouse_map_nodes", "geom"),
    ("ix_wh_edges_geom_gist", "warehouse_map_edges", "geom"),
    ("ix_location_binding_footprint_gist", "location_map_bindings", "footprint"),
    ("ix_wh_spatial_assets_geom_gist", "warehouse_spatial_assets", "geom"),
    ("ix_wh_zones_geom_gist", "warehouse_semantic_zones", "geom"),
    ("ix_wh_docks_geom_gist", "warehouse_spatial_docks", "geom"),
    ("ix_wh_overlays_geom_gist", "warehouse_dynamic_overlays", "geom"),
)
NEW_TABLES = (
    "warehouse_dynamic_overlays",
    "warehouse_spatial_docks",
    "warehouse_semantic_zones",
    "warehouse_spatial_assets",
)


def _backfill_postgis():
    op.execute(
        "UPDATE warehouse_maps SET geom=ST_MakeEnvelope(0,0,width_m,height_m,0) WHERE geom IS NULL"
    )
    op.execute(
        "UPDATE warehouse_map_nodes SET geom=ST_SetSRID(ST_MakePoint(x_m,y_m),0) WHERE geom IS NULL"
    )
    op.execute("""
        UPDATE warehouse_map_edges e SET geom=ST_MakeLine(a.geom,b.geom)
        FROM warehouse_map_nodes a, warehouse_map_nodes b
        WHERE a.id=e.from_node_id AND b.id=e.to_node_id
          AND a.warehouse_map_id=e.warehouse_map_id AND b.warehouse_map_id=e.warehouse_map_id
          AND e.geom IS NULL
    """)
    op.execute("""
        UPDATE location_map_bindings SET footprint=ST_Rotate(
          ST_MakeEnvelope(x_m-width_m/2,y_m-depth_m/2,x_m+width_m/2,y_m+depth_m/2,0),
          radians(rotation_deg::double precision), ST_MakePoint(x_m,y_m))
        WHERE footprint IS NULL
    """)


def _backfill_sqlite():
    """Match geometry derivation in compatibility databases; do not infer policies."""
    bind = op.get_bind()

    def polygon(x, y, width, depth, rotation=0):
        angle = math.radians(rotation)
        c, s = math.cos(angle), math.sin(angle)
        ring = [
            [x + a * c - b * s, y + a * s + b * c]
            for a, b in (
                (-width / 2, -depth / 2),
                (width / 2, -depth / 2),
                (width / 2, depth / 2),
                (-width / 2, depth / 2),
            )
        ]
        return {"type": "Polygon", "coordinates": [[*ring, ring[0]]]}

    def update(table, column, identity, geometry):
        bind.execute(
            sa.text(f"UPDATE {table} SET {column}=:geom WHERE id=:id AND {column} IS NULL"),
            {"geom": json.dumps(geometry), "id": identity},
        )

    for row in bind.execute(sa.text("SELECT id,width_m,height_m FROM warehouse_maps")).mappings():
        w, h = float(row["width_m"]), float(row["height_m"])
        update("warehouse_maps", "geom", row["id"], polygon(w / 2, h / 2, w, h))
    nodes = {}
    for row in bind.execute(sa.text("SELECT id,x_m,y_m FROM warehouse_map_nodes")).mappings():
        point = [float(row["x_m"]), float(row["y_m"])]
        nodes[row["id"]] = point
        update("warehouse_map_nodes", "geom", row["id"], {"type": "Point", "coordinates": point})
    for row in bind.execute(
        sa.text("SELECT id,from_node_id,to_node_id FROM warehouse_map_edges")
    ).mappings():
        update(
            "warehouse_map_edges",
            "geom",
            row["id"],
            {
                "type": "LineString",
                "coordinates": [nodes[row["from_node_id"]], nodes[row["to_node_id"]]],
            },
        )
    for row in bind.execute(
        sa.text("SELECT id,x_m,y_m,width_m,depth_m,rotation_deg FROM location_map_bindings")
    ).mappings():
        update(
            "location_map_bindings",
            "footprint",
            row["id"],
            polygon(
                *(float(row[k]) for k in ("x_m", "y_m", "width_m", "depth_m", "rotation_deg")),
            ),
        )


def _align_legacy_foreign_keys(bind):
    """Normalize bootstrap FK names to the frozen evidence revision's names.

    The initial live-metadata bootstrap could create these columns before 0011,
    causing that revision to skip its explicitly named constraints. Keep the
    same references and data while making historical downgrade names available.
    """
    quote = bind.dialect.identifier_preparer.quote
    for table, prefix in (
        ("component_relations", "relation"),
        ("product_bom_alternates", "alternate"),
    ):
        foreign_keys = sa.inspect(bind).get_foreign_keys(table)
        for action in ("reviewed", "revoked"):
            column = f"{action}_by_id"
            expected = f"fk_{prefix}_{action}_by"
            matching = [
                item for item in foreign_keys
                if item["constrained_columns"] == [column]
                and item["referred_table"] == "users"
                and item["referred_columns"] == ["id"]
            ]
            if len(matching) != 1 or not matching[0]["name"]:
                raise RuntimeError(f"Unexpected legacy foreign key: {table}.{column}")
            if matching[0]["name"] != expected:
                op.execute(sa.text(
                    f"ALTER TABLE {quote(table)} RENAME CONSTRAINT "
                    f"{quote(matching[0]['name'])} TO {quote(expected)}"
                ))


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        _align_legacy_foreign_keys(bind)
    for table, columns in _columns().items():
        existing = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)
    _create_tables()
    for table in NEW_TABLES:
        names = {index["name"] for index in sa.inspect(bind).get_indexes(table)}
        name = f"ix_{table}_warehouse_map_id"
        if name not in names:
            op.create_index(name, table, ["warehouse_map_id"])
    names = {index["name"] for index in sa.inspect(bind).get_indexes("warehouse_dynamic_overlays")}
    if "ix_wh_overlay_active_expiry" not in names:
        op.create_index(
            "ix_wh_overlay_active_expiry",
            "warehouse_dynamic_overlays",
            ["warehouse_map_id", "is_active", "expires_at"],
        )
    if bind.dialect.name == "postgresql":
        for name, table, column in GEOMETRY_INDEXES:
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING GIST ({column})")
        _backfill_postgis()
    else:
        _backfill_sqlite()


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name, _, _ in reversed(GEOMETRY_INDEXES):
            op.execute(f"DROP INDEX IF EXISTS {name}")
    for table in NEW_TABLES:
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
    for table, columns in reversed(list(_columns().items())):
        existing = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        for column in reversed(columns):
            if column.name in existing:
                op.drop_column(table, column.name)
    # The extension can be shared by other maps/apps and is intentionally retained.
