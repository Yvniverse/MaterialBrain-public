"""Keep additive geometry synchronized in existing ORM map write workflows.

These four listeners derive only spatial geometry from the same persisted scalar
coordinates. They never infer widths, risk, calibration, inventory or bindings.
Raw migration/HD-map SQL retains its explicit geometry and bypasses the listeners.
"""

import json

from sqlalchemy import event, func, inspect, select

from .geometry import line_geometry, point_geometry, rectangle_polygon

_registered = False


def _needs(target, attribute, fields):
    return getattr(target, attribute) is None or any(
        inspect(target).attrs[field].history.has_changes() for field in fields
    )


def _value(connection, geometry):
    encoded = json.dumps(geometry, separators=(",", ":"), allow_nan=False)
    if connection.dialect.name == "postgresql":
        return func.ST_SetSRID(func.ST_GeomFromGeoJSON(encoded), 0)
    return encoded


def _map_geometry(mapper, connection, target):
    if target.width_m is None or target.height_m is None:
        return  # Preserve the existing NOT NULL constraint/error semantics.
    if _needs(target, "geom", ("width_m", "height_m")):
        width, height = float(target.width_m), float(target.height_m)
        target.geom = _value(
            connection,
            rectangle_polygon(
                {
                    "x": width / 2,
                    "y": height / 2,
                    "width": width,
                    "depth": height,
                }
            ),
        )


def _node_geometry(mapper, connection, target):
    if target.x_m is None or target.y_m is None:
        return
    if _needs(target, "geom", ("x_m", "y_m")):
        target.geom = _value(
            connection, point_geometry({"x": float(target.x_m), "y": float(target.y_m)})
        )


def _edge_geometry(mapper, connection, target):
    if not _needs(target, "geom", ("from_node_id", "to_node_id")):
        return
    from app.models.domain import WarehouseMapNode

    points = []
    for node_id in (target.from_node_id, target.to_node_id):
        row = connection.execute(
            select(
                WarehouseMapNode.x_m,
                WarehouseMapNode.y_m,
            ).where(WarehouseMapNode.id == node_id)
        ).first()
        if row is None:
            return  # Existing FK constraints remain responsible for invalid references.
        points.append({"x": float(row.x_m), "y": float(row.y_m)})
    target.geom = _value(connection, line_geometry(*points))


def _binding_geometry(mapper, connection, target):
    fields = ("x_m", "y_m", "width_m", "depth_m", "rotation_deg")
    if any(getattr(target, field) is None for field in fields[:-1]):
        return
    if _needs(target, "footprint", fields):
        target.footprint = _value(
            connection,
            rectangle_polygon(
                {
                    "x": float(target.x_m),
                    "y": float(target.y_m),
                    "width": float(target.width_m),
                    "depth": float(target.depth_m),
                    "yaw_deg": float(target.rotation_deg or 0),
                }
            ),
        )


def register_legacy_geometry_hooks():
    """Call once from the model barrel after domain classes finish loading."""
    global _registered
    if _registered:
        return
    from app.models.domain import (
        LocationMapBinding,
        WarehouseMap,
        WarehouseMapEdge,
        WarehouseMapNode,
    )

    for model, listener in (
        (WarehouseMap, _map_geometry),
        (WarehouseMapNode, _node_geometry),
        (WarehouseMapEdge, _edge_geometry),
        (LocationMapBinding, _binding_geometry),
    ):
        event.listen(model, "before_insert", listener)
        event.listen(model, "before_update", listener)
    _registered = True
