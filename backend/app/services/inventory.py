import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError
from app.models import (
    IdempotencyRecord,
    InventoryLot,
    Location,
    Material,
    Project,
    ProjectReservation,
    StockMovement,
    Stocktake,
)


class InventoryService:
    """The only application service allowed to mutate stock quantities."""

    def __init__(self, db: Session, user_id: int, request_id: str):
        self.db = db
        self.user_id = user_id
        self.request_id = request_id

    def _material(self, material_id: int) -> Material:
        material = self.db.scalar(
            select(Material)
            .where(Material.id == material_id, Material.is_deleted.is_(False))
            .with_for_update()
        )
        if not material:
            raise BusinessError("MATERIAL_NOT_FOUND", "物料不存在", 404)
        return material

    def _reservable_material(self, material_id: int) -> Material:
        material = self._material(material_id)
        if not material.is_active:
            raise BusinessError(
                "MATERIAL_INACTIVE",
                "物料已停用，不能新增预留。",
                409,
                details={"material_ids": [material.id]},
            )
        return material

    def _cached(self, endpoint: str, key: str) -> dict | None:
        record = self.db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == self.user_id,
                IdempotencyRecord.endpoint == endpoint,
                IdempotencyRecord.key == key,
            )
        )
        return record.response if record else None

    def _save_result(self, endpoint: str, key: str, result: dict) -> None:
        self.db.add(
            IdempotencyRecord(user_id=self.user_id, endpoint=endpoint, key=key, response=result)
        )

    def _movement(
        self,
        material: Material,
        operation_type: str,
        quantity_delta: Decimal,
        before_quantity: Decimal,
        before_reserved: Decimal,
        key: str,
        reason: str,
        notes: str = "",
        project_id: int | None = None,
        source_location_id: int | None = None,
        target_location_id: int | None = None,
        reversal_of_id: int | None = None,
    ) -> StockMovement:
        movement = StockMovement(
            movement_no=f"MV-{uuid.uuid4().hex[:18].upper()}",
            material_id=material.id,
            operation_type=operation_type,
            quantity_delta=quantity_delta,
            before_quantity=before_quantity,
            after_quantity=material.quantity,
            before_reserved=before_reserved,
            after_reserved=material.reserved_quantity,
            operator_id=self.user_id,
            project_id=project_id,
            source_location_id=source_location_id,
            target_location_id=target_location_id,
            reversal_of_id=reversal_of_id,
            reason=reason,
            notes=notes,
            request_id=self.request_id,
            idempotency_key=key,
        )
        self.db.add(movement)
        self.db.flush()
        return movement

    @staticmethod
    def _result(material: Material, movement: StockMovement, extra: dict | None = None) -> dict:
        result = {
            "movement_id": movement.id,
            "movement_no": movement.movement_no,
            "material_id": material.id,
            "quantity": str(material.quantity),
            "reserved_quantity": str(material.reserved_quantity),
            "available_quantity": str(material.available_quantity),
        }
        result.update(extra or {})
        return result

    def _execute(self, endpoint: str, key: str, callback, *, commit: bool = True) -> dict:
        cached = self._cached(endpoint, key)
        if cached is not None:
            return {**cached, "idempotent_replay": True}
        try:
            result = callback()
            self._save_result(endpoint, key, result)
            if commit:
                self.db.commit()
            else:
                self.db.flush()
            return result
        except IntegrityError:
            self.db.rollback()
            cached = self._cached(endpoint, key)
            if cached is not None:
                return {**cached, "idempotent_replay": True}
            raise
        except BusinessError:
            # A concurrent request with the same key can commit while this request
            # waits on a warehouse row lock. Re-check after rollback before exposing
            # a state-dependent validation error from the now-updated row.
            self.db.rollback()
            cached = self._cached(endpoint, key)
            if cached is not None:
                return {**cached, "idempotent_replay": True}
            raise
        except Exception:
            self.db.rollback()
            raise

    def inbound(
        self,
        material_id: int,
        quantity: Decimal,
        key: str,
        reason: str,
        notes="",
        operation_type="inbound",
    ) -> dict:
        def action():
            material = self._material(material_id)
            before, reserved = material.quantity, material.reserved_quantity
            material.quantity += quantity
            movement = self._movement(
                material, operation_type, quantity, before, reserved, key, reason, notes
            )
            return self._result(material, movement)

        return self._execute(operation_type, key, action)

    def inbound_batch(
        self,
        entries: list[dict],
        key: str,
        reason: str,
        result: dict,
        *,
        operation_type: str = "initial",
        endpoint: str = "cable_import",
    ) -> dict:
        """Apply one atomic import while retaining one movement per material."""

        def action():
            movements = []
            for index, entry in enumerate(entries):
                quantity = Decimal(str(entry["quantity"]))
                if quantity <= 0 or quantity != quantity.to_integral_value():
                    raise BusinessError(
                        "INVALID_IMPORT_QUANTITY",
                        "批量入库数量必须是大于 0 的整数",
                    )
                material = self._material(int(entry["material_id"]))
                before, reserved = material.quantity, material.reserved_quantity
                material.quantity += quantity
                movement_key = f"{key[:88]}-{index + 1}"
                movement = self._movement(
                    material,
                    operation_type,
                    quantity,
                    before,
                    reserved,
                    movement_key,
                    reason,
                    str(entry.get("notes") or ""),
                )
                movements.append(
                    {
                        "movement_id": movement.id,
                        "movement_no": movement.movement_no,
                        "material_id": material.id,
                        "quantity_delta": int(quantity),
                        "quantity": int(material.quantity),
                    }
                )
            return {**result, "movements": movements}

        return self._execute(endpoint, key, action)

    def initialize_location_allocations(
        self,
        material_id: int,
        allocations: list[dict],
        key: str,
        reason: str,
        notes: str = "",
    ) -> dict:
        """Create an immutable initial lot distribution through the stock service.

        This operation never changes the material's book quantity. It is intended
        for a newly created, empty-of-lots material and refuses to overwrite an
        existing distribution. The idempotency record makes seed retries safe.
        """

        def action():
            material = self._material(material_id)
            normalized: dict[int, Decimal] = {}
            for item in allocations:
                location_id = int(item["location_id"])
                quantity = Decimal(str(item["quantity"]))
                if quantity <= 0:
                    raise BusinessError(
                        "INVALID_LOCATION_ALLOCATION",
                        "库位分配数量必须大于 0",
                        details={"location_id": location_id},
                    )
                if location_id in normalized:
                    raise BusinessError(
                        "DUPLICATE_LOCATION_ALLOCATION",
                        "初始库位分配中不能重复库位",
                        details={"location_id": location_id},
                    )
                normalized[location_id] = quantity

            total = sum(normalized.values(), Decimal("0"))
            if total > material.quantity:
                raise BusinessError(
                    "LOCATION_ALLOCATION_EXCEEDS_STOCK",
                    "库位分配合计不能超过账面库存",
                    details={
                        "material_id": material.id,
                        "allocated": str(total),
                        "quantity": str(material.quantity),
                    },
                )

            existing = list(
                self.db.scalars(
                    select(InventoryLot)
                    .where(InventoryLot.material_id == material.id)
                    .order_by(InventoryLot.location_id)
                    .with_for_update()
                ).all()
            )
            existing_map = {item.location_id: item.quantity for item in existing}
            if existing_map:
                if existing_map == normalized:
                    return {
                        "material_id": material.id,
                        "quantity": str(material.quantity),
                        "allocated_quantity": str(total),
                        "allocations": [
                            {"location_id": location_id, "quantity": str(quantity)}
                            for location_id, quantity in sorted(normalized.items())
                        ],
                        "already_initialized": True,
                    }
                raise BusinessError(
                    "LOCATION_ALLOCATION_ALREADY_EXISTS",
                    "该物料已有库位分配，初始分配不会覆盖现有数据",
                    details={"material_id": material.id},
                )

            location_ids = sorted(normalized)
            existing_location_ids = set(
                self.db.scalars(select(Location.id).where(Location.id.in_(location_ids))).all()
            )
            missing = [item for item in location_ids if item not in existing_location_ids]
            if missing:
                raise BusinessError(
                    "LOCATION_NOT_FOUND",
                    "初始库位分配包含不存在的库位",
                    404,
                    details={"location_ids": missing},
                )

            movements = []
            for location_id in location_ids:
                quantity = normalized[location_id]
                self.db.add(
                    InventoryLot(
                        material_id=material.id,
                        location_id=location_id,
                        quantity=quantity,
                    )
                )
                movement = self._movement(
                    material,
                    "initial_location_allocation",
                    Decimal("0"),
                    material.quantity,
                    material.reserved_quantity,
                    f"{key[:76]}-{location_id}",
                    reason,
                    notes,
                    target_location_id=location_id,
                )
                movements.append(
                    {
                        "movement_id": movement.id,
                        "location_id": location_id,
                        "quantity": str(quantity),
                    }
                )
            return {
                "material_id": material.id,
                "quantity": str(material.quantity),
                "allocated_quantity": str(total),
                "allocations": movements,
                "already_initialized": False,
            }

        return self._execute("initial_location_allocation", key, action)

    def outbound(
        self,
        material_id: int,
        quantity: Decimal,
        key: str,
        reason: str,
        notes="",
        operation_type="outbound",
        project_id=None,
    ) -> dict:
        def action():
            material = self._material(material_id)
            if quantity > material.available_quantity:
                raise BusinessError(
                    "INSUFFICIENT_AVAILABLE_STOCK",
                    "可用库存不足",
                    details={
                        "material_id": material.id,
                        "requested": str(quantity),
                        "available": str(material.available_quantity),
                    },
                )
            before, reserved = material.quantity, material.reserved_quantity
            material.quantity -= quantity
            movement = self._movement(
                material,
                operation_type,
                -quantity,
                before,
                reserved,
                key,
                reason,
                notes,
                project_id=project_id,
            )
            return self._result(material, movement)

        return self._execute(operation_type, key, action)

    def scrap(self, material_id: int, quantity: Decimal, key: str, reason: str, notes="") -> dict:
        return self.outbound(material_id, quantity, key, reason, notes, "scrap")

    def refund(self, material_id: int, quantity: Decimal, key: str, reason: str, notes="") -> dict:
        return self.inbound(material_id, quantity, key, reason, notes, "refund")

    def reserve(
        self, material_id: int, project_id: int, quantity: Decimal, key: str, reason: str, notes=""
    ) -> dict:
        def action():
            material = self._reservable_material(material_id)
            if quantity > material.available_quantity:
                raise BusinessError("INSUFFICIENT_AVAILABLE_STOCK", "可用库存不足")
            before, reserved = material.quantity, material.reserved_quantity
            reservation = self.db.scalar(
                select(ProjectReservation)
                .where(
                    ProjectReservation.project_id == project_id,
                    ProjectReservation.material_id == material_id,
                )
                .with_for_update()
            )
            if not reservation:
                reservation = ProjectReservation(
                    project_id=project_id, material_id=material_id, quantity=0
                )
                self.db.add(reservation)
            reservation.quantity += quantity
            material.reserved_quantity += quantity
            movement = self._movement(
                material,
                "reserve",
                Decimal(0),
                before,
                reserved,
                key,
                reason,
                notes,
                project_id=project_id,
            )
            return self._result(
                material, movement, {"reservation_quantity": str(reservation.quantity)}
            )

        return self._execute("reserve", key, action)

    def reserve_batch(
        self,
        project_id: int,
        items: list[dict],
        key: str,
        reason: str,
        notes: str = "",
        *,
        commit: bool = True,
    ) -> dict:
        """Atomically reserve multiple materials for one project.

        The material rows are locked in stable ID order. Validation completes for
        the whole batch before any quantity is changed, so a failing item cannot
        leave a partial reservation behind.
        """

        def action():
            if not self.db.get(Project, project_id):
                raise BusinessError("PROJECT_NOT_FOUND", "项目不存在", 404)

            normalized: dict[int, Decimal] = {}
            for item in items:
                material_id = int(item["material_id"])
                quantity = Decimal(str(item["quantity"]))
                if quantity <= 0:
                    raise BusinessError(
                        "INVALID_RESERVATION_QUANTITY",
                        "预留数量必须大于 0",
                        details={"material_id": material_id},
                    )
                if material_id in normalized:
                    raise BusinessError(
                        "DUPLICATE_RESERVATION_MATERIAL",
                        "批量预留中不能重复物料",
                        details={"material_id": material_id},
                    )
                normalized[material_id] = quantity

            material_ids = sorted(normalized)
            materials = list(
                self.db.scalars(
                    select(Material)
                    .where(
                        Material.id.in_(material_ids),
                        Material.is_deleted.is_(False),
                    )
                    .order_by(Material.id)
                    .with_for_update()
                ).all()
            )
            materials_by_id = {material.id: material for material in materials}
            missing = [
                material_id
                for material_id in material_ids
                if material_id not in materials_by_id
            ]
            if missing:
                raise BusinessError(
                    "MATERIAL_NOT_FOUND",
                    "批量预留包含不存在的物料",
                    404,
                    details={"material_ids": missing},
                )
            inactive = [
                material_id
                for material_id in material_ids
                if not materials_by_id[material_id].is_active
            ]
            if inactive:
                raise BusinessError(
                    "MATERIAL_INACTIVE",
                    "物料已停用，不能新增预留。",
                    409,
                    details={"material_ids": inactive},
                )

            shortages = []
            for material_id in material_ids:
                material = materials_by_id[material_id]
                quantity = normalized[material_id]
                if quantity > material.available_quantity:
                    shortages.append(
                        {
                            "material_id": material.id,
                            "requested": str(quantity),
                            "available": str(material.available_quantity),
                        }
                    )
            if shortages:
                raise BusinessError(
                    "INSUFFICIENT_AVAILABLE_STOCK",
                    "批量预留中存在可用库存不足的物料",
                    details={"shortages": shortages},
                )

            reservations = list(
                self.db.scalars(
                    select(ProjectReservation)
                    .where(
                        ProjectReservation.project_id == project_id,
                        ProjectReservation.material_id.in_(material_ids),
                    )
                    .order_by(ProjectReservation.material_id)
                    .with_for_update()
                ).all()
            )
            reservations_by_material = {
                reservation.material_id: reservation for reservation in reservations
            }

            results = []
            for material_id in material_ids:
                material = materials_by_id[material_id]
                quantity = normalized[material_id]
                reservation = reservations_by_material.get(material_id)
                if reservation is None:
                    reservation = ProjectReservation(
                        project_id=project_id,
                        material_id=material_id,
                        quantity=Decimal("0"),
                    )
                    self.db.add(reservation)
                    reservations_by_material[material_id] = reservation

                before_quantity = material.quantity
                before_reserved = material.reserved_quantity
                reservation.quantity += quantity
                material.reserved_quantity += quantity
                movement = self._movement(
                    material,
                    "reserve",
                    Decimal("0"),
                    before_quantity,
                    before_reserved,
                    f"{key[:76]}-{material_id}",
                    reason,
                    notes,
                    project_id=project_id,
                )
                results.append(
                    {
                        **self._result(material, movement),
                        "requested_quantity": str(quantity),
                        "reservation_quantity": str(reservation.quantity),
                    }
                )

            return {"project_id": project_id, "items": results}

        return self._execute("reserve_batch", key, action, commit=commit)

    def cancel_reservation(
        self, material_id: int, project_id: int, quantity: Decimal, key: str, reason: str, notes=""
    ) -> dict:
        def action():
            material = self._material(material_id)
            reservation = self.db.scalar(
                select(ProjectReservation)
                .where(
                    ProjectReservation.project_id == project_id,
                    ProjectReservation.material_id == material_id,
                )
                .with_for_update()
            )
            if not reservation or quantity > reservation.quantity:
                raise BusinessError("INSUFFICIENT_PROJECT_RESERVATION", "项目预留数量不足")
            before, reserved = material.quantity, material.reserved_quantity
            reservation.quantity -= quantity
            material.reserved_quantity -= quantity
            movement = self._movement(
                material,
                "cancel_reservation",
                Decimal(0),
                before,
                reserved,
                key,
                reason,
                notes,
                project_id=project_id,
            )
            return self._result(
                material, movement, {"reservation_quantity": str(reservation.quantity)}
            )

        return self._execute("cancel_reservation", key, action)

    def reservation_to_outbound(
        self, material_id: int, project_id: int, quantity: Decimal, key: str, reason: str, notes=""
    ) -> dict:
        def action():
            material = self._material(material_id)
            reservation = self.db.scalar(
                select(ProjectReservation)
                .where(
                    ProjectReservation.project_id == project_id,
                    ProjectReservation.material_id == material_id,
                )
                .with_for_update()
            )
            if not reservation or quantity > reservation.quantity:
                raise BusinessError("INSUFFICIENT_PROJECT_RESERVATION", "项目预留数量不足")
            before, reserved = material.quantity, material.reserved_quantity
            reservation.quantity -= quantity
            reservation.consumed_quantity += quantity
            material.quantity -= quantity
            material.reserved_quantity -= quantity
            movement = self._movement(
                material,
                "reservation_to_outbound",
                -quantity,
                before,
                reserved,
                key,
                reason,
                notes,
                project_id=project_id,
            )
            return self._result(
                material, movement, {"reservation_quantity": str(reservation.quantity)}
            )

        return self._execute("reservation_to_outbound", key, action)

    def reservation_to_outbound_from_location(
        self,
        material_id: int,
        project_id: int,
        quantity: Decimal,
        source_location_id: int,
        key: str,
        reason: str,
        notes: str = "",
        *,
        operation_type: str = "pick_outbound",
        commit: bool = True,
    ) -> dict:
        """Consume project reservation and the exact physical source lot atomically.

        Picking must never decrement only Material.quantity while leaving the
        InventoryLot untouched.  This primitive keeps book stock, project
        reservation, reserved stock and physical-location stock in one
        transaction.  It is intended for PickTask confirmation and remains
        idempotent under the existing InventoryService key contract.
        """

        if not quantity.is_finite() or quantity <= 0:
            raise BusinessError("PICK_INVALID_QUANTITY", "拣货数量必须为正数", 400)

        def action():
            material = self._material(material_id)
            reservation = self.db.scalar(
                select(ProjectReservation)
                .where(
                    ProjectReservation.project_id == project_id,
                    ProjectReservation.material_id == material_id,
                )
                .with_for_update()
            )
            if reservation is None or quantity > reservation.quantity:
                raise BusinessError(
                    "INSUFFICIENT_PROJECT_RESERVATION",
                    "项目预留数量不足",
                    details={
                        "material_id": material_id,
                        "project_id": project_id,
                        "requested": str(quantity),
                        "reserved": str(reservation.quantity if reservation else 0),
                    },
                )
            if quantity > material.reserved_quantity:
                raise BusinessError(
                    "RESERVED_STOCK_INCONSISTENT",
                    "物料总预留数量不足，不能确认拣货",
                    409,
                    details={
                        "material_id": material_id,
                        "requested": str(quantity),
                        "reserved_quantity": str(material.reserved_quantity),
                    },
                )

            source_lot = self.db.scalar(
                select(InventoryLot)
                .where(
                    InventoryLot.material_id == material_id,
                    InventoryLot.location_id == source_location_id,
                )
                .with_for_update()
            )
            if source_lot is None:
                raise BusinessError(
                    "PICK_LOCATION_STOCK_NOT_FOUND",
                    "指定库位没有该物料的可拣库存",
                    409,
                    details={
                        "material_id": material_id,
                        "source_location_id": source_location_id,
                    },
                )
            if quantity > source_lot.quantity:
                raise BusinessError(
                    "PICK_LOCATION_STOCK_CHANGED",
                    "指定库位库存不足，请重新规划拣货任务",
                    409,
                    details={
                        "material_id": material_id,
                        "source_location_id": source_location_id,
                        "requested": str(quantity),
                        "location_quantity": str(source_lot.quantity),
                    },
                )
            if quantity > material.quantity:
                raise BusinessError(
                    "INSUFFICIENT_BOOK_STOCK",
                    "账面库存不足，不能确认拣货",
                    409,
                )

            before, reserved = material.quantity, material.reserved_quantity
            reservation.quantity -= quantity
            reservation.consumed_quantity += quantity
            material.quantity -= quantity
            material.reserved_quantity -= quantity
            source_lot.quantity -= quantity
            movement = self._movement(
                material,
                operation_type,
                -quantity,
                before,
                reserved,
                key,
                reason,
                notes,
                project_id=project_id,
                source_location_id=source_location_id,
            )
            return self._result(
                material,
                movement,
                {
                    "reservation_quantity": str(reservation.quantity),
                    "reservation_consumed_quantity": str(reservation.consumed_quantity),
                    "source_location_id": source_location_id,
                    "source_location_quantity": str(source_lot.quantity),
                },
            )

        return self._execute(
            "reservation_to_outbound_from_location",
            key,
            action,
            commit=commit,
        )

    def adjust(
        self, material_id: int, actual_quantity: Decimal, key: str, reason: str, notes=""
    ) -> dict:
        def action():
            material = self._material(material_id)
            if actual_quantity < material.reserved_quantity:
                raise BusinessError("ADJUSTMENT_BELOW_RESERVED", "盘点数量不能低于已预留数量")
            before, reserved = material.quantity, material.reserved_quantity
            delta = actual_quantity - before
            material.quantity = actual_quantity
            stocktake = Stocktake(
                stocktake_no=f"ST-{uuid.uuid4().hex[:14].upper()}",
                material_id=material.id,
                book_quantity=before,
                actual_quantity=actual_quantity,
                difference=delta,
                reason=reason,
                operator_id=self.user_id,
            )
            self.db.add(stocktake)
            movement = self._movement(
                material, "adjust", delta, before, reserved, key, reason, notes
            )
            return self._result(
                material,
                movement,
                {"difference": str(delta), "stocktake_no": stocktake.stocktake_no},
            )

        return self._execute("adjust", key, action)

    def transfer(
        self,
        material_id: int,
        quantity: Decimal,
        source_id: int,
        target_id: int,
        key: str,
        reason: str,
        notes="",
    ) -> dict:
        def action():
            if source_id == target_id:
                raise BusinessError("SAME_LOCATION", "来源和目标库位不能相同")
            material = self._material(material_id)
            source = self.db.scalar(
                select(InventoryLot)
                .where(
                    InventoryLot.material_id == material_id, InventoryLot.location_id == source_id
                )
                .with_for_update()
            )
            if not source or source.quantity < quantity:
                raise BusinessError("INSUFFICIENT_LOCATION_STOCK", "来源库位库存不足")
            target = self.db.scalar(
                select(InventoryLot)
                .where(
                    InventoryLot.material_id == material_id, InventoryLot.location_id == target_id
                )
                .with_for_update()
            )
            if not target:
                target = InventoryLot(material_id=material_id, location_id=target_id, quantity=0)
                self.db.add(target)
            source.quantity -= quantity
            target.quantity += quantity
            movement = self._movement(
                material,
                "transfer",
                Decimal(0),
                material.quantity,
                material.reserved_quantity,
                key,
                reason,
                notes,
                source_location_id=source_id,
                target_location_id=target_id,
            )
            return self._result(material, movement)

        return self._execute("transfer", key, action)

    def reverse(self, movement_id: int, key: str, reason: str, notes="") -> dict:
        def action():
            original = self.db.scalar(
                select(StockMovement).where(StockMovement.id == movement_id).with_for_update()
            )
            if not original:
                raise BusinessError("MOVEMENT_NOT_FOUND", "原流水不存在", 404)
            if original.operation_type in {
                "reserve",
                "cancel_reservation",
                "reservation_to_outbound",
                "transfer",
                "reverse",
            }:
                raise BusinessError(
                    "REVERSAL_REQUIRES_BUSINESS_OPERATION", "该类型流水必须通过对应业务操作纠正"
                )
            if self.db.scalar(
                select(StockMovement.id).where(StockMovement.reversal_of_id == original.id)
            ):
                raise BusinessError("ALREADY_REVERSED", "该流水已冲正")
            material = self._material(original.material_id)
            before, reserved = material.quantity, material.reserved_quantity
            new_quantity = material.quantity - original.quantity_delta
            if new_quantity < material.reserved_quantity:
                raise BusinessError("REVERSAL_VIOLATES_STOCK", "冲正会破坏库存约束")
            material.quantity = new_quantity
            movement = self._movement(
                material,
                "reverse",
                -original.quantity_delta,
                before,
                reserved,
                key,
                reason,
                notes,
                reversal_of_id=original.id,
            )
            return self._result(material, movement, {"reversal_of_id": original.id})

        return self._execute("reverse", key, action)
