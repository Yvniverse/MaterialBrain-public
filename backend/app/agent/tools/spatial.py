"""Read-only spatial tools; execution commands remain authenticated mission APIs."""

from typing import Any, Literal

from pydantic import Field

from app.schemas.spatial import MissionRequest, SpatialContract, SpatialPose


class SpatialMapArgs(SpatialContract):
    map_id: str = "MB-EMB-LAB-03"


class SpatialQueryArgs(SpatialMapArgs):
    kind: Literal[
        "contains", "nearby", "nearest_dock", "intersects", "edge_zones", "affected_edges"
    ]
    entity: Literal["assets", "docks", "nodes", "edges", "zones", "overlays", "bindings"] = "zones"
    point: SpatialPose | None = None
    geometry: dict[str, Any] | None = None
    radius_m: float = Field(default=5, gt=0, le=1000)
    limit: int = Field(default=10, ge=1, le=100)
    edge_id: str | None = None
    overlay_id: str | None = None


def get_spatial_map(ctx, args):
    from app.spatial import SpatialMapService

    snapshot = SpatialMapService(ctx.db).snapshot(args.map_id)
    return {
        "map_id": snapshot["map_id"],
        "revision": snapshot["revision"],
        "frame": snapshot["frame"],
        "zones": [
            {key: zone.get(key) for key in ("id", "kind", "speed_limit_mps", "risk_level")}
            for zone in snapshot["zones"]
        ],
        "docks": snapshot["docks"],
        "affordances": snapshot["affordances"],
        "graph_nodes": len(snapshot["route_graph"]["nodes"]),
        "graph_edges": len(snapshot["route_graph"]["edges"]),
        "inventory_written": False,
    }


def query_spatial_context(ctx, args):
    from app.spatial import SpatialMapService

    body = args.model_dump(mode="json", exclude_none=True)
    body.pop("map_id")
    return SpatialMapService(ctx.db).query(args.map_id, body)


def plan_spatial_mission(ctx, args: MissionRequest):
    from app.services.spatial_mission import plan_mission
    from app.spatial import SpatialMapService

    result = plan_mission(
        SpatialMapService(ctx.db).snapshot(args.map_id), args.model_dump(mode="json")
    )
    return {key: value for key, value in result.items() if key != "segments"} | {
        "segments_count": len(result["segments"]),
        "execution_started": False,
        "ui_path": "/warehouse-twin?workspace=robot-lab",
    }
