"""Persistence adapter for versioned warehouse geometry and routing graphs."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models import (
    Location,
    LocationMapBinding,
    WarehouseMap,
    WarehouseMapEdge,
    WarehouseMapNode,
)
from app.services.warehouse_routing import (
    LocationMapBinding as RouteLocationBinding,
)
from app.services.warehouse_routing import (
    WarehouseMapDefinition,
    load_warehouse_map,
    optimize_route,
)
from app.services.warehouse_routing import (
    WarehouseMapEdge as RouteEdge,
)
from app.services.warehouse_routing import (
    WarehouseMapNode as RouteNode,
)


def validate_definition(definition):
    from app.services.warehouse_routing import validate_warehouse_map

    try:
        validate_warehouse_map(definition)
    except ValueError as exc:
        raise BusinessError("WAREHOUSE_MAP_INVALID", str(exc), 422) from exc


class WarehouseMapService:
    def __init__(self, db: Session):
        self.db = db

    def _lock_map(self, map_id: int) -> WarehouseMap:
        row = self.db.get(WarehouseMap, map_id)
        if row is None:
            raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "仓库地图不存在。", 404)
        self.db.scalar(
            select(Location).where(Location.id == row.warehouse_location_id).with_for_update()
        )
        self.db.refresh(row, with_for_update=True)
        return row

    def seed_from_file(
        self,
        path: str,
        *,
        verified_by_id: int | None = None,
        activate: bool = True,
    ) -> WarehouseMap:
        return self.seed_definition(
            load_warehouse_map(path),
            verified_by_id=verified_by_id,
            activate=activate,
        )

    def seed_definition(
        self,
        definition: WarehouseMapDefinition,
        *,
        verified_by_id: int | None = None,
        activate: bool = True,
    ) -> WarehouseMap:
        warehouse = self.db.scalar(
            select(Location).where(Location.code == definition.warehouse_code)
        )
        if warehouse is None or warehouse.type != "warehouse":
            raise BusinessError(
                "WAREHOUSE_MAP_ROOT_NOT_FOUND",
                "仓库地图对应的仓库根库位不存在。",
                409,
                details={"warehouse_code": definition.warehouse_code},
            )
        existing = self.db.scalar(
            select(WarehouseMap).where(WarehouseMap.code == definition.map_code)
        )
        if existing is not None:
            if existing.graph_hash != definition.graph_hash:
                raise BusinessError(
                    "WAREHOUSE_MAP_VERSION_CONFLICT",
                    "同一地图编码已存在但图版本哈希不同，请创建新版本。",
                    409,
                    details={
                        "map_code": definition.map_code,
                        "existing_graph_hash": existing.graph_hash,
                        "incoming_graph_hash": definition.graph_hash,
                    },
                )
            return existing

        if activate:
            active_maps = self.db.scalars(
                select(WarehouseMap).where(
                    WarehouseMap.warehouse_location_id == warehouse.id,
                    WarehouseMap.status == "active",
                )
            ).all()
            for item in active_maps:
                item.status = "archived"

        map_row = WarehouseMap(
            warehouse_location_id=warehouse.id,
            code=definition.map_code,
            name=definition.name,
            version=definition.version,
            status="active" if activate else "draft",
            calibration_status=definition.calibration_status,
            coordinate_unit=definition.coordinate_unit,
            width_m=Decimal(str(definition.width_m)),
            height_m=Decimal(str(definition.height_m)),
            default_start_node_code=definition.default_start_node,
            default_end_node_code=definition.default_end_node or "",
            graph_hash=definition.graph_hash,
            geometry_note=definition.geometry_note,
            verified_by_id=(
                verified_by_id if definition.calibration_status == "verified" else None
            ),
        )
        self.db.add(map_row)
        self.db.flush()

        node_rows: dict[str, WarehouseMapNode] = {}
        for node in definition.nodes:
            row = WarehouseMapNode(
                warehouse_map_id=map_row.id,
                code=node.code,
                node_type=node.node_type,
                label=node.label,
                x_m=Decimal(str(node.x_m)),
                y_m=Decimal(str(node.y_m)),
                is_active=True,
            )
            self.db.add(row)
            self.db.flush()
            node_rows[node.code] = row

        for edge in definition.edges:
            self.db.add(
                WarehouseMapEdge(
                    warehouse_map_id=map_row.id,
                    code=edge.code,
                    from_node_id=node_rows[edge.from_node].id,
                    to_node_id=node_rows[edge.to_node].id,
                    distance_m=Decimal(str(edge.distance_m)),
                    bidirectional=edge.bidirectional,
                    is_active=edge.enabled,
                )
            )

        location_by_code: dict[str, Location] = {}
        for binding in definition.bindings:
            location = self.db.scalar(
                select(Location).where(Location.code == binding.location_code)
            )
            if location is None:
                raise BusinessError(
                    "WAREHOUSE_MAP_LOCATION_NOT_FOUND",
                    "地图绑定的物理库位不存在。",
                    409,
                    details={"location_code": binding.location_code},
                )
            location_by_code[binding.location_code] = location
            self.db.add(
                LocationMapBinding(
                    warehouse_map_id=map_row.id,
                    location_id=location.id,
                    pick_node_id=node_rows[binding.pick_node_code].id,
                    x_m=Decimal(str(binding.x_m)),
                    y_m=Decimal(str(binding.y_m)),
                    width_m=Decimal(str(binding.width_m)),
                    depth_m=Decimal(str(binding.depth_m)),
                    rotation_deg=Decimal(str(binding.rotation_deg)),
                    facing=binding.facing,
                    local_geometry_kind=binding.local_geometry_kind,
                )
            )
        self.db.flush()
        return map_row

    def list_for_warehouse(self, warehouse_location_id: int) -> list[WarehouseMap]:
        return list(
            self.db.scalars(
                select(WarehouseMap)
                .where(WarehouseMap.warehouse_location_id == warehouse_location_id)
                .order_by(WarehouseMap.created_at.desc(), WarehouseMap.id.desc())
            ).all()
        )

    def clone_to_draft(
        self,
        source_map_id: int,
        *,
        code: str,
        version: str,
        name: str,
    ) -> WarehouseMap:
        source = self.db.get(WarehouseMap, source_map_id)
        if source is None:
            raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "仓库地图不存在。", 404)
        if self.db.scalar(select(WarehouseMap.id).where(WarehouseMap.code == code)) is not None:
            raise BusinessError("WAREHOUSE_MAP_CODE_EXISTS", "仓库地图编码已存在。", 409)
        if (
            self.db.scalar(
                select(WarehouseMap.id).where(
                    WarehouseMap.warehouse_location_id == source.warehouse_location_id,
                    WarehouseMap.version == version,
                )
            )
            is not None
        ):
            raise BusinessError("WAREHOUSE_MAP_VERSION_EXISTS", "该仓库的地图版本已存在。", 409)
        definition = self.definition(source_map_id)
        # Editing a verified map invalidates verification.  A clone must be re-measured
        # or re-verified before production activation.  Synthetic demo maps remain
        # explicitly synthetic.
        next_calibration = (
            "demo_synthetic" if definition.calibration_status == "demo_synthetic" else "measured"
        )
        draft = replace(
            definition,
            map_code=code,
            version=version,
            name=name,
            calibration_status=next_calibration,
            geometry_note=(
                f"基于 {source.code}@{source.version} 复制的草稿。修改后必须重新验证。 "
                + (definition.geometry_note or "")
            ).strip(),
        )
        return self.seed_definition(draft, activate=False)

    def replace_draft_definition(
        self,
        map_id: int,
        definition: WarehouseMapDefinition,
    ) -> WarehouseMap:
        map_row = self._lock_map(map_id)
        if map_row is None:
            raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "仓库地图不存在。", 404)
        if map_row.status != "draft":
            raise BusinessError(
                "WAREHOUSE_MAP_IMMUTABLE",
                "已启用或归档的地图不可直接修改，请复制为新草稿版本。",
                409,
            )
        warehouse = self.db.get(Location, map_row.warehouse_location_id)
        if warehouse is None or warehouse.code != definition.warehouse_code:
            raise BusinessError(
                "WAREHOUSE_MAP_WAREHOUSE_MISMATCH",
                "草稿地图与仓库根库位不一致。",
                409,
            )
        if definition.map_code != map_row.code or definition.version != map_row.version:
            raise BusinessError(
                "WAREHOUSE_MAP_IDENTITY_IMMUTABLE",
                "草稿地图编码和版本不可在编辑时改变，请另建新版本。",
                409,
            )

        # Validate the graph before deleting any existing draft children.

        validate_definition(definition)
        self._validate_location_bindings(map_row.warehouse_location_id, definition)

        self.db.execute(
            delete(LocationMapBinding).where(LocationMapBinding.warehouse_map_id == map_id)
        )
        self.db.execute(delete(WarehouseMapEdge).where(WarehouseMapEdge.warehouse_map_id == map_id))
        self.db.execute(delete(WarehouseMapNode).where(WarehouseMapNode.warehouse_map_id == map_id))
        self.db.flush()

        map_row.name = definition.name
        map_row.width_m = Decimal(str(definition.width_m))
        map_row.height_m = Decimal(str(definition.height_m))
        map_row.default_start_node_code = definition.default_start_node
        map_row.default_end_node_code = definition.default_end_node or ""
        map_row.graph_hash = definition.graph_hash
        map_row.geometry_note = definition.geometry_note
        # Any geometry edit invalidates a previous verified state.
        if map_row.calibration_status == "verified":
            map_row.calibration_status = "measured"
            map_row.verified_by_id = None
            map_row.verified_at = None

        node_rows: dict[str, WarehouseMapNode] = {}
        for node in definition.nodes:
            row = WarehouseMapNode(
                warehouse_map_id=map_id,
                code=node.code,
                node_type=node.node_type,
                label=node.label,
                x_m=Decimal(str(node.x_m)),
                y_m=Decimal(str(node.y_m)),
                is_active=True,
            )
            self.db.add(row)
            self.db.flush()
            node_rows[node.code] = row

        for edge in definition.edges:
            self.db.add(
                WarehouseMapEdge(
                    warehouse_map_id=map_id,
                    code=edge.code,
                    from_node_id=node_rows[edge.from_node].id,
                    to_node_id=node_rows[edge.to_node].id,
                    distance_m=Decimal(str(edge.distance_m)),
                    bidirectional=edge.bidirectional,
                    is_active=edge.enabled,
                )
            )
        for binding in definition.bindings:
            location = self.db.scalar(
                select(Location).where(Location.code == binding.location_code)
            )
            assert location is not None  # checked by _validate_location_bindings
            self.db.add(
                LocationMapBinding(
                    warehouse_map_id=map_id,
                    location_id=location.id,
                    pick_node_id=node_rows[binding.pick_node_code].id,
                    x_m=Decimal(str(binding.x_m)),
                    y_m=Decimal(str(binding.y_m)),
                    width_m=Decimal(str(binding.width_m)),
                    depth_m=Decimal(str(binding.depth_m)),
                    rotation_deg=Decimal(str(binding.rotation_deg)),
                    facing=binding.facing,
                    local_geometry_kind=binding.local_geometry_kind,
                )
            )
        self.db.flush()
        return map_row

    def _validate_location_bindings(
        self,
        warehouse_location_id: int,
        definition: WarehouseMapDefinition,
    ) -> None:
        bound = {binding.location_code for binding in definition.bindings}
        for organizer in self.db.scalars(
            select(Location).where(Location.is_active.is_(True), Location.type.in_(("box", "rack")))
        ):
            current, visited = organizer, set()
            while current and current.id not in visited:
                if current.id == warehouse_location_id:
                    if organizer.code not in bound:
                        raise BusinessError(
                            "WAREHOUSE_MAP_ORGANIZER_UNMAPPED",
                            "当前仓库仍有未绑定地图的设备",
                            409,
                            details={"location_code": organizer.code},
                        )
                    break
                visited.add(current.id)
                current = self.db.get(Location, current.parent_id) if current.parent_id else None
        for binding in definition.bindings:
            location = self.db.scalar(
                select(Location).where(Location.code == binding.location_code)
            )
            if location is None:
                raise BusinessError(
                    "WAREHOUSE_MAP_LOCATION_NOT_FOUND",
                    "地图绑定的物理库位不存在。",
                    409,
                    details={"location_code": binding.location_code},
                )
            current = location
            visited: set[int] = set()
            belongs = False
            while current is not None and current.id not in visited:
                if current.id == warehouse_location_id:
                    belongs = True
                    break
                visited.add(current.id)
                current = self.db.get(Location, current.parent_id) if current.parent_id else None
            if not belongs:
                raise BusinessError(
                    "WAREHOUSE_MAP_LOCATION_OUTSIDE_WAREHOUSE",
                    "地图只能绑定当前仓库树中的库位。",
                    409,
                    details={"location_code": binding.location_code},
                )

    def set_calibration_status(
        self,
        map_id: int,
        calibration_status: str,
        *,
        actor_id: int,
        geometry_note: str | None = None,
    ) -> WarehouseMap:
        map_row = self._lock_map(map_id)
        if map_row is None:
            raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "仓库地图不存在。", 404)
        if map_row.status != "draft":
            raise BusinessError(
                "WAREHOUSE_MAP_IMMUTABLE",
                "只有草稿地图可以修改校准状态。",
                409,
            )
        if calibration_status not in {"demo_synthetic", "measured", "verified"}:
            raise BusinessError("WAREHOUSE_MAP_CALIBRATION_INVALID", "未知的地图校准状态。")
        if calibration_status in {"measured", "verified"} and not (geometry_note or "").strip():
            raise BusinessError(
                "WAREHOUSE_MAP_CALIBRATION_NOTE_REQUIRED", "请记录测量或复核依据", 422
            )
        if calibration_status == "measured":
            definition = self.definition(map_id)
            for source in self.list_for_warehouse(map_row.warehouse_location_id):
                if source.id == map_id or source.calibration_status != "demo_synthetic":
                    continue
                demo = replace(
                    self.definition(source.id),
                    map_code=definition.map_code,
                    version=definition.version,
                )
                if (
                    demo.graph_hash == definition.graph_hash
                    and demo.width_m == definition.width_m
                    and demo.height_m == definition.height_m
                ):
                    raise BusinessError(
                        "WAREHOUSE_MAP_DEMO_NOT_MEASURED",
                        "复制示例坐标不能作为实测记录，请先录入现场测量几何与复核依据",
                        409,
                    )
        if calibration_status == "verified":
            if map_row.calibration_status != "measured":
                raise BusinessError("WAREHOUSE_MAP_NOT_MEASURED", "必须先完成实测记录", 409)
            # Reconstructing the definition also proves node/edge/binding references.
            definition = self.definition(map_id)

            validate_definition(definition)
            self._validate_location_bindings(map_row.warehouse_location_id, definition)
            map_row.verified_by_id = actor_id
            map_row.verified_at = datetime.now(timezone.utc)
        else:
            map_row.verified_by_id = None
            map_row.verified_at = None
        map_row.calibration_status = calibration_status
        if geometry_note is not None:
            map_row.geometry_note = geometry_note
        self.db.flush()
        return map_row

    def activate_verified(self, map_id: int) -> WarehouseMap:
        map_row = self._lock_map(map_id)
        if map_row is None:
            raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "仓库地图不存在。", 404)
        if map_row.status != "draft":
            raise BusinessError("WAREHOUSE_MAP_NOT_DRAFT", "只有草稿地图可以被启用。", 409)
        if map_row.calibration_status != "verified":
            raise BusinessError(
                "WAREHOUSE_MAP_NOT_VERIFIED",
                "生产路线地图必须先完成测量和验证，再启用。",
                409,
            )
        definition = self.definition(map_id)

        validate_definition(definition)
        self._validate_location_bindings(map_row.warehouse_location_id, definition)
        for item in self.list_for_warehouse(map_row.warehouse_location_id):
            if item.id != map_row.id and item.status == "active":
                item.status = "archived"
        map_row.status = "active"
        self.db.flush()
        return map_row

    def delete_draft(self, map_id: int) -> None:
        map_row = self._lock_map(map_id)
        if map_row is None:
            raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "仓库地图不存在。", 404)
        if map_row.status != "draft":
            raise BusinessError(
                "WAREHOUSE_MAP_IMMUTABLE",
                "已启用或归档的地图不能删除。",
                409,
            )
        self.db.delete(map_row)
        self.db.flush()

    def active_for_warehouse(self, warehouse_location_id: int) -> WarehouseMap | None:
        return self.db.scalar(
            select(WarehouseMap).where(
                WarehouseMap.warehouse_location_id == warehouse_location_id,
                WarehouseMap.status == "active",
            )
        )

    def definition(self, warehouse_map_id: int) -> WarehouseMapDefinition:
        map_row = self.db.get(WarehouseMap, warehouse_map_id)
        if map_row is None:
            raise BusinessError("WAREHOUSE_MAP_NOT_FOUND", "仓库地图不存在。", 404)
        warehouse = self.db.get(Location, map_row.warehouse_location_id)
        node_rows = self.db.scalars(
            select(WarehouseMapNode).where(WarehouseMapNode.warehouse_map_id == map_row.id)
        ).all()
        node_by_id = {item.id: item for item in node_rows}
        edges = self.db.scalars(
            select(WarehouseMapEdge).where(WarehouseMapEdge.warehouse_map_id == map_row.id)
        ).all()
        bindings = self.db.scalars(
            select(LocationMapBinding).where(LocationMapBinding.warehouse_map_id == map_row.id)
        ).all()
        return WarehouseMapDefinition(
            map_code=map_row.code,
            warehouse_code=warehouse.code if warehouse else str(map_row.warehouse_location_id),
            name=map_row.name,
            version=map_row.version,
            calibration_status=map_row.calibration_status,
            coordinate_unit=map_row.coordinate_unit,
            width_m=float(map_row.width_m),
            height_m=float(map_row.height_m),
            default_start_node=map_row.default_start_node_code,
            default_end_node=map_row.default_end_node_code or None,
            nodes=tuple(
                RouteNode(
                    code=item.code,
                    node_type=item.node_type,
                    x_m=float(item.x_m),
                    y_m=float(item.y_m),
                    label=item.label,
                )
                for item in node_rows
                if item.is_active
            ),
            edges=tuple(
                RouteEdge(
                    code=item.code,
                    from_node=node_by_id[item.from_node_id].code,
                    to_node=node_by_id[item.to_node_id].code,
                    distance_m=float(item.distance_m),
                    bidirectional=item.bidirectional,
                    enabled=item.is_active,
                )
                for item in edges
            ),
            bindings=tuple(
                RouteLocationBinding(
                    location_code=(self.db.get(Location, item.location_id).code),
                    pick_node_code=node_by_id[item.pick_node_id].code,
                    x_m=float(item.x_m),
                    y_m=float(item.y_m),
                    width_m=float(item.width_m),
                    depth_m=float(item.depth_m),
                    rotation_deg=float(item.rotation_deg),
                    facing=item.facing,
                    local_geometry_kind=item.local_geometry_kind,
                )
                for item in bindings
            ),
            geometry_note=map_row.geometry_note,
        )

    def resolve_pick_node(self, warehouse_map_id: int, location_id: int) -> WarehouseMapNode:
        """Resolve a bin/shelf to the nearest bound ancestor organizer pick face."""

        current = self.db.get(Location, location_id)
        visited: set[int] = set()
        while current is not None and current.id not in visited:
            visited.add(current.id)
            binding = self.db.scalar(
                select(LocationMapBinding).where(
                    LocationMapBinding.warehouse_map_id == warehouse_map_id,
                    LocationMapBinding.location_id == current.id,
                )
            )
            if binding is not None:
                node = self.db.get(WarehouseMapNode, binding.pick_node_id)
                if node is None or not node.is_active:
                    break
                return node
            current = self.db.get(Location, current.parent_id) if current.parent_id else None
        raise BusinessError(
            "LOCATION_NOT_MAPPED_FOR_PICKING",
            "该实际库位尚未绑定到仓库地图取料点。",
            409,
            details={"location_id": location_id, "warehouse_map_id": warehouse_map_id},
        )

    def route_for_locations(
        self,
        warehouse_map_id: int,
        location_ids: list[int],
        *,
        closed_edge_codes: set[str] | None = None,
    ):
        definition = self.definition(warehouse_map_id)
        stop_nodes = [
            self.resolve_pick_node(warehouse_map_id, location_id).code
            for location_id in location_ids
        ]
        try:
            return optimize_route(
                definition,
                stop_nodes,
                closed_edge_codes=closed_edge_codes or set(),
            )
        except ValueError as exc:
            raise BusinessError("WAREHOUSE_ROUTE_UNREACHABLE", str(exc), 409) from exc
