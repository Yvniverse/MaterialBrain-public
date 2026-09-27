"""Persisted location claims and human-confirmed InventoryService settlement.

Create first locks its creator for business-key serialization. Stock lock order:
BuildPlan, ascending Material IDs, ProjectReservation, InventoryLot,
then task/allocation. Material locks serialize competing plans and inventory writes.
"""

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app.core.exceptions import BusinessError
from app.models import (
    AuditLog,
    BuildPlan,
    BuildPlanItem,
    IdempotencyRecord,
    InventoryLot,
    Location,
    Material,
    PickAllocation,
    PickTask,
    PickTaskItem,
    ProductRevision,
    ProjectReservation,
    User,
    WarehouseMap,
)
from app.services.audit import add_audit
from app.services.build_plans import build_plan_data
from app.services.inventory import InventoryService
from app.services.picking_planner import PickLotCandidate, plan_pick_allocations
from app.services.product_revisions import canonical_hash
from app.services.warehouse_maps import WarehouseMapService

ACTIVE = ("ready", "in_progress", "needs_replan")
CLAIMED = ("pending", "partial")
ZERO = Decimal("0")
COUNT_UNITS = frozenset({"pcs", "pc", "piece", "pieces", "个", "颗", "片", "只", "件", "套", "条"})


def _material_quantity_precision(material: Material) -> int:
    """Resolve authoritative pick precision without changing the inventory schema.

    A material may opt into a 0..4 decimal precision via attributes.quantity_precision.
    Count-like units default to integers; continuous units retain the existing 4-decimal
    storage precision.
    """

    configured = (material.attributes or {}).get("quantity_precision")
    if configured is not None:
        try:
            if isinstance(configured, bool):
                raise ValueError
            decimal_value = Decimal(str(configured))
            if not decimal_value.is_finite() or decimal_value != decimal_value.to_integral_value():
                raise ValueError
            value = int(decimal_value)
        except (InvalidOperation, TypeError, ValueError):
            value = -1
        if 0 <= value <= 4:
            return value
    return 0 if (material.unit or "").strip().lower() in COUNT_UNITS else 4


def _quantity_matches_precision(quantity: Decimal, precision: int) -> bool:
    quantum = Decimal(1).scaleb(-precision)
    return quantity == quantity.quantize(quantum)


class PickingService:
    def __init__(self, db, user_id=0, request_id=""):
        self.db, self.user_id, self.request_id = db, user_id, request_id

    def plan(self, plan_id, *, lock=False):
        query = select(BuildPlan).where(BuildPlan.id == plan_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        plan = self.db.scalar(query)
        if plan is None:
            raise BusinessError("BUILD_PLAN_NOT_FOUND", "生产计划不存在", 404)
        if lock:
            ids = select(BuildPlanItem.material_id).where(BuildPlanItem.build_plan_id == plan_id)
            self.db.scalars(
                select(Material)
                .where(Material.id.in_(ids))
                .order_by(Material.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            self.db.scalars(
                select(ProjectReservation)
                .where(
                    ProjectReservation.project_id == plan.project_id,
                    ProjectReservation.material_id.in_(ids),
                )
                .order_by(ProjectReservation.material_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            self.db.scalars(
                select(InventoryLot)
                .where(InventoryLot.material_id.in_(ids))
                .order_by(InventoryLot.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        return plan

    def task(self, task_id, *, lock=False):
        task = self.db.get(PickTask, task_id)
        if task is None:
            raise BusinessError("PICK_TASK_NOT_FOUND", "拣货任务不存在", 404)
        if lock:
            self.plan(task.build_plan_id, lock=True)
            self.db.refresh(task, with_for_update=True)
        return task

    def allocations(self, task_id):
        return list(
            self.db.scalars(
                select(PickAllocation)
                .join(PickTaskItem)
                .where(PickTaskItem.pick_task_id == task_id)
                .order_by(
                    PickAllocation.route_sequence, PickAllocation.location_id, PickAllocation.id
                )
            ).all()
        )

    def readiness(self, plan_id, *, exclude_task_id=None):
        plan = self.plan(plan_id)
        revision = self.db.get(ProductRevision, plan.product_revision_id)
        identity_valid = bool(
            revision
            and revision.status == "released"
            and revision.bom_hash == plan.product_bom_hash
        )
        items = []
        for item in self.db.scalars(
            select(BuildPlanItem)
            .where(BuildPlanItem.build_plan_id == plan_id)
            .order_by(BuildPlanItem.material_id)
        ):
            material = self.db.get(Material, item.material_id)
            picked = (
                self.db.scalar(
                    select(func.coalesce(func.sum(PickTaskItem.picked_quantity), 0))
                    .join(PickTask)
                    .where(
                        PickTask.build_plan_id == plan_id,
                        PickTaskItem.material_id == item.material_id,
                    )
                )
                or ZERO
            )
            required = max(ZERO, item.required_total - picked)
            reservation = self.db.scalar(
                select(ProjectReservation).where(
                    ProjectReservation.project_id == plan.project_id,
                    ProjectReservation.material_id == item.material_id,
                )
            )
            reserved = reservation.quantity if reservation else ZERO
            claims = (
                select(PickAllocation)
                .join(PickTaskItem)
                .join(PickTask)
                .where(
                    PickTaskItem.material_id == item.material_id,
                    PickTask.status.in_(ACTIVE),
                    PickAllocation.status.in_(CLAIMED),
                )
            )
            if exclude_task_id:
                claims = claims.where(PickTask.id != exclude_task_id)
            active_by_lot, project_claim = {}, ZERO
            for allocation in self.db.scalars(claims):
                remaining = allocation.planned_quantity - allocation.picked_quantity
                active_by_lot[allocation.inventory_lot_id] = (
                    active_by_lot.get(allocation.inventory_lot_id, ZERO) + remaining
                )
                owner = self.db.get(PickTaskItem, allocation.pick_task_item_id)
                if self.db.get(PickTask, owner.pick_task_id).project_id == plan.project_id:
                    project_claim += remaining
            candidates = []
            for lot, location in self.db.execute(
                select(InventoryLot, Location)
                .join(Location, Location.id == InventoryLot.location_id)
                .where(InventoryLot.material_id == item.material_id, Location.is_active.is_(True))
            ):
                candidates.append(
                    PickLotCandidate(
                        lot.id,
                        location.id,
                        location.full_path,
                        lot.quantity,
                        active_by_lot.get(lot.id, ZERO),
                    )
                )
            locatable = sum((c.quantity for c in candidates), ZERO)
            free = sum((c.free_quantity for c in candidates), ZERO)
            allocation_plan = plan_pick_allocations(required, candidates) if required else None
            items.append(
                {
                    "build_plan_item_id": item.id,
                    "material_id": item.material_id,
                    "code": material.code,
                    "name": material.name,
                    "mpn": material.mpn,
                    "required_total": str(item.required_total),
                    "remaining_required": str(required),
                    "picked_quantity": str(picked),
                    "project_reserved": str(reserved),
                    "reservation_free": str(max(ZERO, reserved - project_claim)),
                    "book_quantity": str(material.quantity),
                    "locatable_quantity": str(locatable),
                    "unlocated_quantity": str(max(ZERO, material.quantity - locatable)),
                    "active_pick_allocated": str(sum(active_by_lot.values(), ZERO)),
                    "allocatable_now": str(free),
                    "location_shortage": str(max(ZERO, required - free)),
                    "reservation_shortage": str(max(ZERO, required - reserved + project_claim)),
                    "material_available": material.is_active and not material.is_deleted,
                    "allocations": [asdict(a) for a in allocation_plan.allocations]
                    if allocation_plan
                    else [],
                }
            )
        reserved_ok = all(Decimal(i["reservation_shortage"]) == 0 for i in items)
        located_ok = all(Decimal(i["location_shortage"]) == 0 for i in items)
        remaining = any(Decimal(i["remaining_required"]) > 0 for i in items)
        return {
            "build_plan_id": plan.id,
            "build_plan_status": plan.status,
            "snapshot_hash": plan.snapshot_hash,
            "identity_valid": identity_valid,
            "reservation_ready": reserved_ok,
            "fully_locatable": located_ok,
            "executable": bool(items)
            and remaining
            and identity_valid
            and reserved_ok
            and located_ok
            and plan.status == "reserved"
            and all(i["material_available"] for i in items),
            "preview_only": plan.status != "reserved",
            "items": items,
        }

    def preview_product(self, revision_id, quantity, project_id=None):
        """Read-only projection; never persists a plan, reservation or claim."""
        from app.services.build_readiness import BuildReadinessService

        build = BuildReadinessService(self.db).analyze(revision_id, quantity, project_id)
        items = []
        for row in build["items"]:
            claims = dict(
                self.db.execute(
                    select(
                        PickAllocation.inventory_lot_id,
                        func.sum(PickAllocation.planned_quantity - PickAllocation.picked_quantity),
                    )
                    .join(PickTaskItem)
                    .join(PickTask)
                    .where(
                        PickTaskItem.material_id == row["material_id"],
                        PickTask.status.in_(ACTIVE),
                        PickAllocation.status.in_(CLAIMED),
                    )
                    .group_by(PickAllocation.inventory_lot_id)
                ).all()
            )
            candidates = [
                PickLotCandidate(
                    lot.id, location.id, location.full_path, lot.quantity, claims.get(lot.id, ZERO)
                )
                for lot, location in self.db.execute(
                    select(InventoryLot, Location)
                    .join(Location, Location.id == InventoryLot.location_id)
                    .where(
                        InventoryLot.material_id == row["material_id"], Location.is_active.is_(True)
                    )
                )
            ]
            allocation = plan_pick_allocations(Decimal(row["required_total"]), candidates)
            material = self.db.get(Material, row["material_id"])
            locatable = sum((lot.quantity for lot in candidates), ZERO)
            free = sum((lot.free_quantity for lot in candidates), ZERO)
            items.append(
                {
                    **row,
                    "book_quantity": str(material.quantity),
                    "locatable_quantity": str(locatable),
                    "unlocated_quantity": str(max(ZERO, material.quantity - locatable)),
                    "allocatable_now": str(free),
                    "location_shortage": str(max(ZERO, Decimal(row["required_total"]) - free)),
                    "allocations": [asdict(a) for a in allocation.allocations],
                }
            )
        return {**build, "items": items, "preview_only": True, "executable": False}

    def _cached(self, endpoint, key, fingerprint):
        record = self.db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == self.user_id,
                IdempotencyRecord.endpoint == endpoint,
                IdempotencyRecord.key == key,
            )
        )
        if record:
            if record.response.get("fingerprint") != fingerprint:
                raise BusinessError("PICK_IDEMPOTENCY_CONFLICT", "操作标识已用于不同请求", 409)
            return {**record.response["result"], "idempotent_replay": True}
        return None

    def _save(self, endpoint, key, fingerprint, result):
        self.db.add(
            IdempotencyRecord(
                user_id=self.user_id,
                endpoint=endpoint,
                key=key,
                response={"fingerprint": fingerprint, "result": jsonable_encoder(result)},
            )
        )

    def _audit(self, action, task, data):
        add_audit(
            self.db,
            self.user_id,
            action,
            "pick_task",
            str(task.id),
            self.request_id,
            after=jsonable_encoder(data),
        )

    def _route(self, task, allocations, closed, *, preserve_task_map: bool = False):
        service = WarehouseMapService(self.db)
        if preserve_task_map:
            if task.warehouse_map_id is None:
                # A legacy hierarchy task has a deliberate no-map snapshot; a later
                # active map must not silently rewrite its historical route truth.
                maps = {None: None}
            else:
                map_row = self.db.get(WarehouseMap, task.warehouse_map_id)
                if map_row is None:
                    raise BusinessError(
                        "PICK_WAREHOUSE_MAP_SNAPSHOT_MISSING",
                        "拣货任务冻结的仓库地图不存在，不能重写历史路线",
                        409,
                    )
                maps = {map_row.id: map_row}
        else:
            maps = {}
            for allocation in allocations:
                location = self.db.get(Location, allocation.location_id)
                visited = set()
                while location and location.parent_id and location.id not in visited:
                    visited.add(location.id)
                    location = self.db.get(Location, location.parent_id)
                map_row = service.active_for_warehouse(location.id) if location else None
                if map_row:
                    maps[map_row.id] = map_row
                else:
                    maps[None] = None
        if maps and None not in maps and len(maps) == 1:
            map_row = next(iter(maps.values()))
            try:
                route = service.route_for_locations(
                    map_row.id, [a.location_id for a in allocations], closed_edge_codes=set(closed)
                )
            except ValueError as error:
                raise BusinessError("PICK_ROUTE_UNREACHABLE", str(error), 409) from error
            task.warehouse_map_id = map_row.id
            task.warehouse_graph_hash = route.graph_hash
            task.route_strategy = route.strategy
            task.route_distance_m = Decimal(str(route.total_distance_m))
            task.route_start_node_code, task.route_end_node_code = (
                route.start_node,
                route.end_node or "",
            )
            task.route_plan = jsonable_encoder(asdict(route))
            task.route_plan["map_version"] = map_row.version
            sequence = {node: n for n, node in enumerate(route.ordered_stop_nodes, 1)}
            arrival = {segment.to_node: segment.distance_m for segment in route.segments}
            visited_stops = set()
            for allocation in allocations:
                node = service.resolve_pick_node(map_row.id, allocation.location_id).code
                allocation.route_node_code = node
                allocation.route_sequence = sequence.get(node, 1)
                allocation.route_distance_from_previous_m = Decimal(
                    str(0 if node in visited_stops else arrival.get(node, 0))
                )
                visited_stops.add(node)
        elif len(maps) > 1:
            raise BusinessError("PICK_MULTIPLE_WAREHOUSE_MAPS", "请按仓库拆分生产计划", 409)
        else:
            if closed:
                raise BusinessError("PICK_ROUTE_MAP_REQUIRED", "无地图不能应用通道关闭条件", 409)
            task.route_strategy = "hierarchy_v1"
            task.warehouse_map_id, task.warehouse_graph_hash = None, ""
            task.route_distance_m = None
            task.route_plan = {
                "strategy": "hierarchy_v1",
                "optimization_note": "未配置仓库地图，按库位层级排序；不是物理最短路线",
            }
            locations = sorted(
                {a.location_id for a in allocations},
                key=lambda lid: (self.db.get(Location, lid).full_path, lid),
            )
            for a in allocations:
                a.route_sequence = locations.index(a.location_id) + 1
        task.route_constraints = {"closed_edge_codes": sorted(set(closed))}

    def create(self, payload):
        # Serialize the creator-scoped business key even when two requests name
        # different BuildPlans; the unique constraint remains the final backstop.
        self.db.scalar(select(User.id).where(User.id == self.user_id).with_for_update())
        plan = self.plan(payload.build_plan_id, lock=True)
        existing = self.db.scalar(
            select(PickTask).where(
                PickTask.created_by_id == self.user_id,
                PickTask.client_operation_id == payload.client_operation_id,
            )
        )
        if existing:
            if (
                existing.build_plan_id != plan.id
                or existing.build_plan_snapshot_hash != payload.build_plan_snapshot_hash
                or existing.notes != payload.notes
                or existing.route_constraints.get("closed_edge_codes")
                != sorted(set(payload.closed_edge_codes))
            ):
                raise BusinessError("PICK_IDEMPOTENCY_CONFLICT", "任务操作标识冲突", 409)
            return self.detail(existing.id)
        if plan.snapshot_hash != payload.build_plan_snapshot_hash:
            raise BusinessError("PICK_SNAPSHOT_CHANGED", "生产快照不匹配，请刷新", 409)
        if self.db.scalar(
            select(PickTask.id).where(
                PickTask.build_plan_id == plan.id, PickTask.status.in_(ACTIVE)
            )
        ):
            raise BusinessError("PICK_TASK_ALREADY_ACTIVE", "生产计划已有活动拣货任务", 409)
        readiness = self.readiness(plan.id)
        if not readiness["executable"]:
            raise BusinessError(
                "PICK_NOT_READY",
                "预留或实际库位不足，不能执行拣货",
                409,
                details=jsonable_encoder(readiness),
            )
        task = PickTask(
            pick_task_no=f"PK-{uuid.uuid4().hex[:18].upper()}",
            build_plan_id=plan.id,
            project_id=plan.project_id,
            product_revision_id=plan.product_revision_id,
            build_plan_snapshot_hash=plan.snapshot_hash,
            created_by_id=self.user_id,
            client_operation_id=payload.client_operation_id,
            notes=payload.notes,
            source_request_id=self.request_id,
        )
        self.db.add(task)
        self.db.flush()
        allocations = []
        for row in readiness["items"]:
            required = Decimal(row["remaining_required"])
            if required <= 0:
                continue
            item = PickTaskItem(
                pick_task_id=task.id,
                build_plan_item_id=row["build_plan_item_id"],
                material_id=row["material_id"],
                required_quantity=required,
                allocated_quantity=required,
                reservation_quantity_at_task=row["project_reserved"],
                locatable_quantity_at_task=row["allocatable_now"],
            )
            self.db.add(item)
            self.db.flush()
            for candidate in row["allocations"]:
                allocation = PickAllocation(
                    pick_task_item_id=item.id,
                    **{k: v for k, v in candidate.items() if k != "full_path"},
                )
                self.db.add(allocation)
                allocations.append(allocation)
        self._route(task, allocations, payload.closed_edge_codes)
        self.db.flush()
        self._audit("picking.create", task, {"readiness": readiness, "route": task.route_plan})
        self.db.commit()
        return self.detail(task.id)

    def operator_state(self, task_id: int) -> dict:
        """Read-only operator projection grouped by physical route stop.

        PickAllocation remains execution truth.  This projection only groups
        allocations that share a route node so an operator walks to one cabinet
        once and can finish several exact drawers/bins before advancing.
        """

        task = self.task(task_id)
        detail = self.detail(task_id)
        groups: list[dict] = []
        by_key: dict[tuple[object, ...], dict] = {}
        for allocation in detail["allocations"]:
            route_node_code = allocation.get("route_node_code") or ""
            # A completed allocation keeps its historical route sequence when a
            # later replan snapshots the remaining work.  The physical station
            # identity is the route node, so a stale sequence must not split
            # one organizer into multiple walking stations.  Legacy hierarchy
            # routes have no node code; retain a location/sequence fallback for
            # those tasks rather than collapsing unrelated locations together.
            key = (
                ("node", route_node_code)
                if route_node_code
                else (
                    "legacy",
                    int(allocation.get("route_sequence") or 0),
                    int(allocation.get("location_id") or 0),
                )
            )
            group = by_key.get(key)
            if group is None:
                group = {
                    "route_sequence": int(allocation.get("route_sequence") or 0),
                    "route_node_code": route_node_code,
                    "allocations": [],
                }
                by_key[key] = group
                groups.append(group)
            group["allocations"].append(allocation)

        # Station identity is physical (route_node_code), while station ordering must
        # follow the *remaining* route after a replan. Historical picked allocations
        # keep their old route_sequence and must not drag an active station forward.
        for group in groups:
            active_sequences = [
                int(row.get("route_sequence") or 0)
                for row in group["allocations"]
                if row["status"] in CLAIMED and Decimal(row["remaining_quantity"]) > 0
            ]
            all_sequences = [
                int(row.get("route_sequence") or 0) for row in group["allocations"]
            ]
            group["route_sequence"] = min(active_sequences or all_sequences)

        groups.sort(key=lambda row: (row["route_sequence"], row["route_node_code"]))
        current_allocation = None
        current_station = None
        completed_stations = 0
        completed_allocations = 0
        for index, group in enumerate(groups, 1):
            pending = [
                row
                for row in group["allocations"]
                if row["status"] in CLAIMED and Decimal(row["remaining_quantity"]) > 0
            ]
            completed = not pending
            group["station_index"] = index
            group["completed_allocations"] = len(group["allocations"]) - len(pending)
            group["remaining_allocations"] = len(pending)
            group["status"] = "completed" if completed else "pending"
            completed_allocations += group["completed_allocations"]
            if completed:
                completed_stations += 1
            elif current_allocation is None:
                current_station = group
                current_allocation = pending[0]
                group["status"] = "current"

        events = []
        for event in self.db.scalars(
            select(AuditLog)
            .where(
                AuditLog.resource_type == "pick_task",
                AuditLog.resource_id == str(task.id),
            )
            .order_by(AuditLog.id.desc())
            .limit(30)
        ):
            events.append(
                {
                    "id": event.id,
                    "action": event.action,
                    "success": event.success,
                    "created_at": event.created_at,
                    "after": event.after_data or {},
                }
            )

        return jsonable_encoder(
            {
                "task": detail,
                "station_groups": groups,
                "current_station": current_station,
                "current_allocation": current_allocation,
                "progress": {
                    "stations_total": len(groups),
                    "stations_completed": completed_stations,
                    "allocations_total": len(detail["allocations"]),
                    "allocations_completed": completed_allocations,
                },
                "events": events,
            }
        )

    def validate_scan(self, allocation_id: int, payload) -> dict:
        """Validate keyboard-wedge/barcode tokens without mutating stock."""

        allocation = self.db.get(PickAllocation, allocation_id)
        if allocation is None:
            raise BusinessError("PICK_ALLOCATION_NOT_FOUND", "拣货分配不存在", 404)
        item = self.db.get(PickTaskItem, allocation.pick_task_item_id)
        task = self.task(item.pick_task_id)
        location = self.db.get(Location, allocation.location_id)
        material = self.db.get(Material, item.material_id)
        location_token = (payload.location_token or "").strip()
        material_token = (payload.material_token or "").strip()
        location_match = bool(location_token) and location_token == location.code
        material_match = None
        if material_token:
            material_match = material_token in {material.code, material.barcode or material.code}
        executable = task.status in ("ready", "in_progress") and allocation.status in CLAIMED
        return {
            "pick_task_id": task.id,
            "allocation_id": allocation.id,
            "location_match": location_match,
            "material_match": material_match,
            "executable": executable,
            "ready": executable and location_match and material_match is True,
            "expected_location_code": location.code,
            "expected_material_code": material.code,
            "material_name": material.name,
            "unit": material.unit,
            "quantity_precision": _material_quantity_precision(material),
            "remaining_quantity": str(allocation.planned_quantity - allocation.picked_quantity),
        }

    def report_issue(self, task_id: int, payload) -> dict:
        """Record an operator exception without changing stock or silently replanning."""

        task = self.task(task_id)
        allocation = None
        if payload.allocation_id is not None:
            allocation = self.db.get(PickAllocation, payload.allocation_id)
            if allocation is None:
                raise BusinessError("PICK_ALLOCATION_NOT_FOUND", "拣货分配不存在", 404)
            owner = self.db.get(PickTaskItem, allocation.pick_task_item_id)
            if owner is None or owner.pick_task_id != task.id:
                raise BusinessError("PICK_ALLOCATION_TASK_MISMATCH", "分配不属于当前拣货任务", 409)
        recommendation = (
            "replan"
            if payload.issue_type in {"location_blocked", "stock_shortage"}
            else "review"
        )
        data = {
            "issue_type": payload.issue_type,
            "allocation_id": allocation.id if allocation else None,
            "notes": payload.notes,
            "recommended_action": recommendation,
        }
        self._audit("picking.issue", task, data)
        self.db.commit()
        return {
            "pick_task_id": task.id,
            **data,
            "recorded": True,
        }

    def detail(self, task_id):
        task = self.task(task_id)
        result = {c.name: getattr(task, c.name) for c in task.__table__.columns}
        result["build_plan"] = build_plan_data(self.db, self.plan(task.build_plan_id))
        result["allocations"] = []
        for allocation in self.allocations(task.id):
            item = self.db.get(PickTaskItem, allocation.pick_task_item_id)
            material, location = (
                self.db.get(Material, item.material_id),
                self.db.get(Location, allocation.location_id),
            )
            result["allocations"].append(
                {
                    **{c.name: getattr(allocation, c.name) for c in allocation.__table__.columns},
                    "material_id": material.id,
                    "material_code": material.code,
                    "material_name": material.name,
                    "mpn": material.mpn,
                    "unit": material.unit,
                    "quantity_precision": _material_quantity_precision(material),
                    "location_code": location.code,
                    "full_path": location.full_path,
                    "remaining_quantity": str(
                        allocation.planned_quantity - allocation.picked_quantity
                    ),
                }
            )
        result["next_stop"] = next(
            (
                a
                for a in result["allocations"]
                if a["status"] in CLAIMED and Decimal(a["remaining_quantity"]) > 0
            ),
            None,
        )
        return jsonable_encoder(result)

    def confirm(self, allocation_id, payload):
        allocation = self.db.get(PickAllocation, allocation_id)
        if allocation is None:
            raise BusinessError("PICK_ALLOCATION_NOT_FOUND", "拣货分配不存在", 404)
        item = self.db.get(PickTaskItem, allocation.pick_task_item_id)
        task = self.task(item.pick_task_id, lock=True)
        self.db.refresh(allocation)
        self.db.refresh(item)
        fingerprint = canonical_hash(
            {"allocation_id": allocation_id, **payload.model_dump(mode="json")}
        )
        cached = self._cached("picking.confirm", payload.idempotency_key, fingerprint)
        if cached:
            return cached
        if task.status not in ("ready", "in_progress") or allocation.status not in CLAIMED:
            raise BusinessError("PICK_NOT_EXECUTABLE", "当前任务或分配不可确认", 409)
        plan = self.plan(task.build_plan_id)
        revision = self.db.get(ProductRevision, plan.product_revision_id)
        if (
            plan.status != "reserved"
            or task.build_plan_snapshot_hash != plan.snapshot_hash
            or not revision
            or revision.bom_hash != plan.product_bom_hash
        ):
            task.status = "stale"
            self.db.commit()
            raise BusinessError("PICK_SNAPSHOT_CHANGED", "上游生产计划已失效，请重新计划", 409)
        location = self.db.get(Location, allocation.location_id)
        material = self.db.get(Material, item.material_id)
        if payload.location_token != location.code:
            raise BusinessError("PICK_WRONG_LOCATION", "库位不匹配，未扣库存", 409)
        if payload.material_token not in {material.code, material.barcode or material.code}:
            raise BusinessError("PICK_WRONG_MATERIAL", "物料不匹配，不允许替代取料", 409)
        manual_reason = payload.manual_override_reason.strip()
        if payload.confirmation_method == "manual" and len(manual_reason) < 2:
            raise BusinessError(
                "PICK_MANUAL_OVERRIDE_REASON_REQUIRED",
                "人工核对必须填写原因，未扣库存",
                400,
            )
        precision = _material_quantity_precision(material)
        if not _quantity_matches_precision(payload.quantity, precision):
            raise BusinessError(
                "PICK_QUANTITY_PRECISION_INVALID",
                f"{material.unit or '该物料'} 取料数量最多允许 {precision} 位小数，未扣库存",
                400,
                details={"unit": material.unit, "quantity_precision": precision},
            )
        remaining = allocation.planned_quantity - allocation.picked_quantity
        if payload.quantity > remaining:
            raise BusinessError("PICK_QUANTITY_EXCEEDED", "超过本分配剩余数量", 409)
        lot = self.db.get(InventoryLot, allocation.inventory_lot_id)
        claims = (
            self.db.scalar(
                select(func.sum(PickAllocation.planned_quantity - PickAllocation.picked_quantity))
                .join(PickTaskItem)
                .join(PickTask)
                .where(
                    PickAllocation.inventory_lot_id == lot.id,
                    PickAllocation.status.in_(CLAIMED),
                    PickTask.status.in_(ACTIVE),
                )
            )
            or ZERO
        )
        reservation = self.db.scalar(
            select(ProjectReservation).where(
                ProjectReservation.project_id == task.project_id,
                ProjectReservation.material_id == item.material_id,
            )
        )
        project_claims = (
            self.db.scalar(
                select(func.sum(PickAllocation.planned_quantity - PickAllocation.picked_quantity))
                .join(PickTaskItem)
                .join(PickTask)
                .where(
                    PickTask.project_id == task.project_id,
                    PickTaskItem.material_id == item.material_id,
                    PickTask.status.in_(ACTIVE),
                    PickAllocation.status.in_(CLAIMED),
                )
            )
            or ZERO
        )
        if (
            not location.is_active
            or lot.quantity < claims
            or not material.is_active
            or material.is_deleted
            or reservation is None
            or reservation.quantity < project_claims
        ):
            task.status = "needs_replan"
            self._audit("picking.needs_replan", task, {"allocation_id": allocation_id})
            self.db.commit()
            raise BusinessError(
                "PICK_LOCATION_STOCK_CHANGED",
                "实际库位已变化，请重新规划",
                409,
                details={"status": "needs_replan"},
            )
        result = InventoryService(
            self.db, self.user_id, self.request_id
        ).reservation_to_outbound_from_location(
            item.material_id,
            task.project_id,
            payload.quantity,
            allocation.location_id,
            "pick-" + canonical_hash({"user": self.user_id, "key": payload.idempotency_key}),
            "拣货人工确认",
            payload.notes,
            commit=False,
        )
        allocation.picked_quantity += payload.quantity
        item.picked_quantity += payload.quantity
        allocation.status = (
            "picked" if allocation.picked_quantity == allocation.planned_quantity else "partial"
        )
        item.status = "picked" if item.picked_quantity == item.required_quantity else "partial"
        allocation.confirmation_method = payload.confirmation_method
        allocation.confirmed_by_id, allocation.confirmed_at = self.user_id, datetime.now(UTC)
        task.started_at = task.started_at or datetime.now(UTC)
        self.db.flush()
        complete = all(
            a.picked_quantity == a.planned_quantity or a.status == "cancelled"
            for a in self.allocations(task.id)
        )
        task.status = "completed" if complete else "in_progress"
        if complete:
            task.completed_at = datetime.now(UTC)
        result.update(
            {
                "pick_task_id": task.id,
                "allocation_id": allocation.id,
                "status": task.status,
                "confirmed_quantity": str(payload.quantity),
            }
        )
        if payload.confirmation_method == "manual":
            self._audit(
                "picking.manual_override",
                task,
                {
                    "allocation_id": allocation.id,
                    "reason": manual_reason,
                    "quantity": str(payload.quantity),
                    "location_code": location.code,
                    "material_code": material.code,
                },
            )
        self._audit("picking.confirm", task, {**result, "method": payload.confirmation_method})
        self._save("picking.confirm", payload.idempotency_key, fingerprint, result)
        self.db.commit()
        return result

    def cancel(self, task_id, reason):
        task = self.task(task_id, lock=True)
        if task.status == "completed":
            raise BusinessError("PICK_ALREADY_COMPLETED", "已完成任务不能取消", 409)
        if task.status == "cancelled":
            return self.detail(task.id)
        for allocation in self.allocations(task.id):
            if allocation.status in CLAIMED or allocation.status == "stale":
                allocation.status = "cancelled"
        task.status, task.cancelled_at = "cancelled", datetime.now(UTC)
        self._audit("picking.cancel", task, {"reason": reason, "stock_reversed": False})
        self.db.commit()
        return self.detail(task.id)

    def replan(self, task_id, payload):
        task = self.task(task_id, lock=True)
        fingerprint = canonical_hash({"task_id": task_id, **payload.model_dump(mode="json")})
        cached = self._cached("picking.replan", payload.client_operation_id, fingerprint)
        if cached:
            return cached
        if task.status not in ACTIVE:
            raise BusinessError("PICK_NOT_EXECUTABLE", "任务不可重新规划", 409)
        readiness = self.readiness(task.build_plan_id, exclude_task_id=task.id)
        if not readiness["executable"]:
            task.status = "needs_replan"
            self.db.commit()
            raise BusinessError(
                "PICK_NOT_READY", "剩余需求尚不可拣", 409, details=jsonable_encoder(readiness)
            )
        before = self.detail(task.id)
        remaining_allocations = []
        for row in readiness["items"]:
            item = self.db.scalar(
                select(PickTaskItem).where(
                    PickTaskItem.pick_task_id == task.id,
                    PickTaskItem.material_id == row["material_id"],
                )
            )
            if not item:
                continue
            old = {
                a.inventory_lot_id: a
                for a in sorted(self.allocations(task.id), key=lambda entry: entry.generation)
                if a.pick_task_item_id == item.id
            }
            for a in old.values():
                if a.status != "picked":
                    a.status = "cancelled"
            for candidate in row["allocations"]:
                a = old.get(candidate["inventory_lot_id"])
                # A completed allocation is immutable history. A later claim on
                # the same physical lot receives its own allocation identity.
                if a is None or a.status == "picked":
                    generation = a.generation + 1 if a else 1
                    a = PickAllocation(
                        pick_task_item_id=item.id,
                        generation=generation,
                        picked_quantity=ZERO,
                        **{k: v for k, v in candidate.items() if k != "full_path"},
                    )
                    self.db.add(a)
                a.planned_quantity = a.picked_quantity + candidate["planned_quantity"]
                a.status = "partial" if a.picked_quantity else "pending"
                remaining_allocations.append(a)
            item.allocated_quantity = item.required_quantity
        try:
            self._route(
                task,
                remaining_allocations,
                payload.closed_edge_codes,
                preserve_task_map=True,
            )
        except BusinessError:
            self.db.rollback()
            task = self.task(task_id, lock=True)
            task.status = "needs_replan"
            self.db.commit()
            raise
        task.status = "in_progress" if task.started_at else "ready"
        self.db.flush()
        result = self.detail(task.id)
        self._audit(
            "picking.replan", task, {"before": before, "after": result, "reason": payload.reason}
        )
        self._save("picking.replan", payload.client_operation_id, fingerprint, result)
        self.db.commit()
        return result
