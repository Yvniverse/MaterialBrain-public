from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import DB, CurrentUser, require, require_any
from app.core.exceptions import BusinessError
from app.models import Location, WarehouseMap
from app.schemas.warehouse_map import (
    WarehouseMapActivateRequest,
    WarehouseMapCalibrationUpdate,
    WarehouseMapDraftCloneRequest,
    WarehouseMapDraftDefinitionUpdate,
    WarehouseRoutePreviewRequest,
)
from app.services.audit import add_audit
from app.services.warehouse_maps import WarehouseMapService, validate_definition
from app.services.warehouse_routing import (
    LocationMapBinding as RouteLocationBinding,
)
from app.services.warehouse_routing import (
    WarehouseMapDefinition,
)
from app.services.warehouse_routing import (
    WarehouseMapEdge as RouteEdge,
)
from app.services.warehouse_routing import (
    WarehouseMapNode as RouteNode,
)
from app.services.warehouse_twin import warehouse_twin_focus, warehouse_twin_snapshot

router = APIRouter(prefix="/warehouse-maps", tags=["仓库地图"])


@router.get(
    "/{map_id}/twin-snapshot",
    dependencies=[Depends(require_any("material:view", "location:manage", "picking:view"))],
)
def twin_snapshot(map_id: int, db: DB, user: CurrentUser):
    return warehouse_twin_snapshot(db, map_id)


@router.get(
    "/{map_id}/location-focus/{location_id}",
    dependencies=[Depends(require_any("material:view", "location:manage", "picking:view"))],
)
def twin_location_focus(map_id: int, location_id: int, db: DB, user: CurrentUser):
    return warehouse_twin_focus(db, map_id, location_id)



def _map_summary(item: WarehouseMap) -> dict:
    return {
        "id": item.id,
        "warehouse_location_id": item.warehouse_location_id,
        "code": item.code,
        "name": item.name,
        "version": item.version,
        "status": item.status,
        "calibration_status": item.calibration_status,
        "coordinate_unit": item.coordinate_unit,
        "width_m": str(item.width_m),
        "height_m": str(item.height_m),
        "default_start_node_code": item.default_start_node_code,
        "default_end_node_code": item.default_end_node_code,
        "graph_hash": item.graph_hash,
        "geometry_note": item.geometry_note,
        "verified_by_id": item.verified_by_id,
        "verified_at": item.verified_at.isoformat() if item.verified_at else None,
    }


def _definition_from_payload(
    map_row: WarehouseMap,
    warehouse_code: str,
    payload: WarehouseMapDraftDefinitionUpdate,
) -> WarehouseMapDefinition:
    return WarehouseMapDefinition(
        map_code=map_row.code,
        warehouse_code=warehouse_code,
        name=payload.name,
        version=map_row.version,
        calibration_status=map_row.calibration_status,
        coordinate_unit=map_row.coordinate_unit,
        width_m=payload.width_m,
        height_m=payload.height_m,
        default_start_node=payload.default_start_node,
        default_end_node=payload.default_end_node,
        nodes=tuple(
            RouteNode(
                code=item.code,
                node_type=item.node_type,
                x_m=item.x_m,
                y_m=item.y_m,
                label=item.label,
            )
            for item in payload.nodes
            if item.is_active
        ),
        edges=tuple(
            RouteEdge(
                code=item.code,
                from_node=item.from_node,
                to_node=item.to_node,
                distance_m=item.distance_m,
                bidirectional=item.bidirectional,
                enabled=item.enabled,
            )
            for item in payload.edges
        ),
        bindings=tuple(
            RouteLocationBinding(
                location_code=item.location_code,
                pick_node_code=item.pick_node_code,
                x_m=item.x_m,
                y_m=item.y_m,
                width_m=item.width_m,
                depth_m=item.depth_m,
                rotation_deg=item.rotation_deg,
                facing=item.facing,
                local_geometry_kind=item.local_geometry_kind,
            )
            for item in payload.organizer_bindings
        ),
        geometry_note=payload.geometry_note,
    )


@router.get(
    "", dependencies=[Depends(require_any("material:view", "location:manage", "picking:view"))]
)
def list_maps(warehouse_id: int, db: DB, user: CurrentUser):
    return [_map_summary(item) for item in WarehouseMapService(db).list_for_warehouse(warehouse_id)]


@router.post(
    "/{map_id}/clone-draft",
    status_code=201,
    dependencies=[Depends(require("location:manage"))],
)
def clone_draft(
    map_id: int,
    payload: WarehouseMapDraftCloneRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    service = WarehouseMapService(db)
    item = service.clone_to_draft(
        map_id, code=payload.code, version=payload.version, name=payload.name
    )
    add_audit(
        db,
        user.id,
        "warehouse_map.clone_draft",
        "warehouse_map",
        str(item.id),
        request.state.request_id,
        after=_map_summary(item),
    )
    db.commit()
    return _map_summary(item)


@router.put(
    "/{map_id}/definition",
    dependencies=[Depends(require("location:manage"))],
)
def update_draft_definition(
    map_id: int,
    payload: WarehouseMapDraftDefinitionUpdate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    map_row = db.get(WarehouseMap, map_id)
    if map_row is None:
        raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "仓库地图不存在。", 404)
    warehouse = db.get(Location, map_row.warehouse_location_id)
    if warehouse is None:
        raise BusinessError("WAREHOUSE_MAP_ROOT_NOT_FOUND", "仓库根库位不存在。", 409)
    before = _map_summary(map_row)
    definition = _definition_from_payload(map_row, warehouse.code, payload)
    item = WarehouseMapService(db).replace_draft_definition(map_id, definition)
    add_audit(
        db,
        user.id,
        "warehouse_map.definition.update",
        "warehouse_map",
        str(item.id),
        request.state.request_id,
        before=before,
        after=_map_summary(item),
    )
    db.commit()
    return map_detail(map_id, db, user)


@router.post(
    "/{map_id}/validate",
    dependencies=[Depends(require("location:manage"))],
)
def validate_map(map_id: int, db: DB, user: CurrentUser):
    service = WarehouseMapService(db)
    item = db.get(WarehouseMap, map_id)
    if item is None:
        raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "仓库地图不存在。", 404)
    definition = service.definition(map_id)
    validate_definition(definition)
    service._validate_location_bindings(item.warehouse_location_id, definition)
    return {
        "status": "PASS",
        "graph_hash": definition.graph_hash,
        "node_count": len(definition.nodes),
        "edge_count": len(definition.edges),
        "binding_count": len(definition.bindings),
    }


@router.put(
    "/{map_id}/calibration",
    dependencies=[Depends(require("location:manage"))],
)
def update_calibration(
    map_id: int,
    payload: WarehouseMapCalibrationUpdate,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    item = WarehouseMapService(db).set_calibration_status(
        map_id, payload.calibration_status, actor_id=user.id, geometry_note=payload.geometry_note
    )
    add_audit(
        db,
        user.id,
        "warehouse_map.calibration.update",
        "warehouse_map",
        str(item.id),
        request.state.request_id,
        after=_map_summary(item),
    )
    db.commit()
    return _map_summary(item)


@router.post(
    "/{map_id}/activate",
    dependencies=[Depends(require("location:manage"))],
)
def activate_map(
    map_id: int,
    payload: WarehouseMapActivateRequest,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    if not payload.confirm_verified_geometry:
        raise BusinessError(
            "WAREHOUSE_MAP_ACTIVATION_CONFIRMATION_REQUIRED",
            "启用生产路线地图前必须明确确认几何已完成现场验证。",
            409,
        )
    item = WarehouseMapService(db).activate_verified(map_id)
    add_audit(
        db,
        user.id,
        "warehouse_map.activate",
        "warehouse_map",
        str(item.id),
        request.state.request_id,
        after=_map_summary(item),
    )
    db.commit()
    return _map_summary(item)


@router.delete(
    "/{map_id}",
    dependencies=[Depends(require("location:manage"))],
)
def delete_draft_map(
    map_id: int,
    request: Request,
    db: DB,
    user: CurrentUser,
):
    WarehouseMapService(db).delete_draft(map_id)
    add_audit(
        db,
        user.id,
        "warehouse_map.draft.delete",
        "warehouse_map",
        str(map_id),
        request.state.request_id,
        after={"deleted": True},
    )
    db.commit()
    return {"message": "地图草稿已删除"}


@router.get(
    "/active",
    dependencies=[Depends(require_any("material:view", "location:manage", "picking:view"))],
)
def active_map(warehouse_id: int, db: DB, user: CurrentUser):
    item = WarehouseMapService(db).active_for_warehouse(warehouse_id)
    if item is None:
        raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "当前仓库没有启用的路线地图。", 404)
    return _map_summary(item)


@router.get(
    "/{map_id}",
    dependencies=[Depends(require_any("material:view", "location:manage", "picking:view"))],
)
def map_detail(map_id: int, db: DB, user: CurrentUser):
    service = WarehouseMapService(db)
    item = db.get(WarehouseMap, map_id)
    if item is None:
        raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "仓库地图不存在。", 404)
    definition = service.definition(map_id)
    return {
        **_map_summary(item),
        "nodes": [node.__dict__ for node in definition.nodes],
        "edges": [edge.__dict__ for edge in definition.edges],
        "organizer_bindings": [binding.__dict__ for binding in definition.bindings],
    }


@router.post(
    "/{map_id}/route-preview",
    dependencies=[Depends(require_any("material:view", "location:manage", "picking:view"))],
)
def route_preview(
    map_id: int,
    payload: WarehouseRoutePreviewRequest,
    db: DB,
    user: CurrentUser,
):
    service = WarehouseMapService(db)
    route = service.route_for_locations(
        map_id,
        payload.location_ids,
        closed_edge_codes=set(payload.closed_edge_codes),
    )
    location_nodes = [
        {
            "location_id": location_id,
            "pick_node_code": service.resolve_pick_node(map_id, location_id).code,
        }
        for location_id in payload.location_ids
    ]
    return {
        "map_code": route.map_code,
        "graph_hash": route.graph_hash,
        "calibration_status": route.calibration_status,
        "strategy": route.strategy,
        "start_node": route.start_node,
        "end_node": route.end_node,
        "ordered_stop_nodes": route.ordered_stop_nodes,
        "total_distance_m": route.total_distance_m,
        "segments": [segment.__dict__ for segment in route.segments],
        "location_nodes": location_nodes,
        "optimization_note": route.optimization_note,
    }
