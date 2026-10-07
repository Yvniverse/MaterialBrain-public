"""Metric spatial intelligence over the existing WarehouseMap authority.

Keep imports lazy so the canonical snapshot/export also runs in the ROS image
without loading SQLAlchemy, HTTP configuration or database credentials.
"""


def build_lab_snapshot() -> dict:
    from .snapshot import build_lab_snapshot as build

    return build()


def __getattr__(name):
    if name == "SpatialMapService":
        from .service import SpatialMapService

        return SpatialMapService
    raise AttributeError(name)
