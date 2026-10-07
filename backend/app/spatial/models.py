"""Additive spatial records, all owned by the existing WarehouseMap identity."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.core.database import Base

SPATIAL_JSON = JSON().with_variant(JSONB(), "postgresql")


class LocalGeometry(UserDefinedType):
    """PostGIS geometry in the local metric frame; SQLite stores GeoJSON for tests.

    Production bind/read operations go through explicit spatial SQL in service.py,
    so no WKB assumptions or GeoAlchemy dependency leak into the existing ORM.
    """

    cache_ok = True

    def __init__(self, kind: str = "GEOMETRY"):
        self.kind = kind.upper()
        if self.kind not in {"GEOMETRY", "POINT", "LINESTRING", "POLYGON"}:
            raise ValueError("Unsupported local geometry kind")

    def get_col_spec(self, **kw):
        return f"geometry({self.kind},0)"


@compiles(LocalGeometry, "sqlite")
def _sqlite_local_geometry(type_, compiler, **kw):
    return "TEXT"


class SpatialAsset(Base):
    __tablename__ = "warehouse_spatial_assets"
    __table_args__ = (
        UniqueConstraint("warehouse_map_id", "code", name="uq_warehouse_spatial_asset_code"),
        Index("ix_wh_spatial_assets_geom_gist", "geom", postgresql_using="gist"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_map_id: Mapped[int] = mapped_column(
        ForeignKey("warehouse_maps.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    asset_type: Mapped[str] = mapped_column(String(32))
    geom: Mapped[str] = mapped_column(LocalGeometry("POLYGON"))
    properties: Mapped[dict[str, Any]] = mapped_column("metadata", SPATIAL_JSON, default=dict)


class SemanticZone(Base):
    __tablename__ = "warehouse_semantic_zones"
    __table_args__ = (
        UniqueConstraint("warehouse_map_id", "code", name="uq_warehouse_semantic_zone_code"),
        CheckConstraint("risk_level >= 0 AND risk_level <= 1", name="ck_wh_zone_risk"),
        CheckConstraint("speed_limit_mps IS NULL OR speed_limit_mps > 0", name="ck_wh_zone_speed"),
        Index("ix_wh_zones_geom_gist", "geom", postgresql_using="gist"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_map_id: Mapped[int] = mapped_column(
        ForeignKey("warehouse_maps.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    zone_type: Mapped[str] = mapped_column(String(32))
    geom: Mapped[str] = mapped_column(LocalGeometry("POLYGON"))
    speed_limit_mps: Mapped[float | None] = mapped_column(Float)
    risk_level: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    properties: Mapped[dict[str, Any]] = mapped_column("metadata", SPATIAL_JSON, default=dict)


class SpatialDock(Base):
    __tablename__ = "warehouse_spatial_docks"
    __table_args__ = (
        UniqueConstraint("warehouse_map_id", "code", name="uq_warehouse_spatial_dock_code"),
        Index("ix_wh_docks_geom_gist", "geom", postgresql_using="gist"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_map_id: Mapped[int] = mapped_column(
        ForeignKey("warehouse_maps.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64))
    node_id: Mapped[int] = mapped_column(ForeignKey("warehouse_map_nodes.id"), index=True)
    asset_code: Mapped[str | None] = mapped_column(String(64))
    geom: Mapped[str] = mapped_column(LocalGeometry("POINT"))
    yaw_rad: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    properties: Mapped[dict[str, Any]] = mapped_column("metadata", SPATIAL_JSON, default=dict)


class DynamicOverlay(Base):
    __tablename__ = "warehouse_dynamic_overlays"
    __table_args__ = (
        UniqueConstraint("warehouse_map_id", "code", name="uq_warehouse_dynamic_overlay_code"),
        CheckConstraint(
            "overlay_type IN ('closure','obstacle','speed_override','risk_override')",
            name="ck_wh_overlay_type",
        ),
        CheckConstraint("expires_at > active_from", name="ck_wh_overlay_expiry"),
        Index("ix_wh_overlays_geom_gist", "geom", postgresql_using="gist"),
        Index("ix_wh_overlay_active_expiry", "warehouse_map_id", "is_active", "expires_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_map_id: Mapped[int] = mapped_column(
        ForeignKey("warehouse_maps.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64))
    overlay_type: Mapped[str] = mapped_column(String(32))
    geom: Mapped[str] = mapped_column(LocalGeometry())
    payload: Mapped[dict[str, Any]] = mapped_column(SPATIAL_JSON, default=dict)
    active_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
