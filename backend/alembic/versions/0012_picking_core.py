"""Add Production Picking Core tables.

Revision ID: 0012_picking_core
Revises: 0011_engineering_evidence

IMPORTANT for rebasing this candidate patch: the actual Phase 2.7 implementation
must first run `alembic heads` on the final Phase 2.6 source and change
`down_revision` if newer migrations exist.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_picking_core"
down_revision = "0011_engineering_evidence"
branch_labels = None
depends_on = None

PICKING_VIEW = "picking:view"
PICKING_OPERATE = "picking:operate"
PICKING_ROLE_PERMISSIONS = {
    "仓库管理员": (PICKING_VIEW, PICKING_OPERATE),
    "硬件工程师": (PICKING_VIEW,),
    "项目负责人": (PICKING_VIEW, PICKING_OPERATE),
}


def _backfill_picking_permissions(*, remove: bool = False) -> None:
    bind = op.get_bind()
    roles = sa.Table("roles", sa.MetaData(), autoload_with=bind)
    rows = bind.execute(sa.select(roles.c.id, roles.c.name, roles.c.permissions)).mappings()
    for row in rows:
        permissions = list(row["permissions"] or [])
        if "*" in permissions:
            continue
        managed = PICKING_ROLE_PERMISSIONS.get(row["name"], ())
        if not managed:
            continue
        if remove:
            updated = [item for item in permissions if item not in managed]
        else:
            updated = [*permissions]
            for permission in managed:
                if permission not in updated:
                    updated.append(permission)
        if updated != permissions:
            bind.execute(roles.update().where(roles.c.id == row["id"]).values(permissions=updated))


def upgrade() -> None:
    _backfill_picking_permissions()
    op.create_table(
        "warehouse_maps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("warehouse_location_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("calibration_status", sa.String(length=24), nullable=False, server_default="demo_synthetic"),
        sa.Column("coordinate_unit", sa.String(length=8), nullable=False, server_default="m"),
        sa.Column("width_m", sa.Numeric(10,3), nullable=False),
        sa.Column("height_m", sa.Numeric(10,3), nullable=False),
        sa.Column("default_start_node_code", sa.String(length=64), nullable=False, server_default="PACK"),
        sa.Column("default_end_node_code", sa.String(length=64), nullable=False, server_default="PACK"),
        sa.Column("graph_hash", sa.String(length=64), nullable=False),
        sa.Column("geometry_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("verified_by_id", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft','active','archived')", name="ck_warehouse_map_status"),
        sa.CheckConstraint(
            "calibration_status IN ('demo_synthetic','measured','verified')",
            name="ck_warehouse_map_calibration_status",
        ),
        sa.ForeignKeyConstraint(["warehouse_location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["verified_by_id"], ["users.id"]),
        sa.UniqueConstraint("code", name="uq_warehouse_map_code"),
        sa.UniqueConstraint("warehouse_location_id", "version", name="uq_warehouse_map_version"),
    )
    op.create_index("ix_warehouse_maps_warehouse_location_id", "warehouse_maps", ["warehouse_location_id"])
    op.create_index("ix_warehouse_maps_status", "warehouse_maps", ["status"])
    op.create_index("ix_warehouse_maps_calibration_status", "warehouse_maps", ["calibration_status"])
    op.create_index("ix_warehouse_maps_graph_hash", "warehouse_maps", ["graph_hash"])
    op.create_index("ix_warehouse_map_warehouse_status", "warehouse_maps", ["warehouse_location_id", "status"])

    op.create_table(
        "warehouse_map_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("warehouse_map_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("node_type", sa.String(length=24), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("x_m", sa.Numeric(10,3), nullable=False),
        sa.Column("y_m", sa.Numeric(10,3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "node_type IN ('packing','entrance','aisle','intersection','pick_face')",
            name="ck_warehouse_map_node_type",
        ),
        sa.ForeignKeyConstraint(["warehouse_map_id"], ["warehouse_maps.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("warehouse_map_id", "code", name="uq_warehouse_map_node_code"),
    )
    op.create_index("ix_warehouse_map_nodes_warehouse_map_id", "warehouse_map_nodes", ["warehouse_map_id"])
    op.create_index("ix_warehouse_map_nodes_node_type", "warehouse_map_nodes", ["node_type"])
    op.create_index("ix_warehouse_map_nodes_is_active", "warehouse_map_nodes", ["is_active"])

    op.create_table(
        "warehouse_map_edges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("warehouse_map_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("from_node_id", sa.Integer(), nullable=False),
        sa.Column("to_node_id", sa.Integer(), nullable=False),
        sa.Column("distance_m", sa.Numeric(10,3), nullable=False),
        sa.Column("bidirectional", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("distance_m > 0", name="ck_warehouse_map_edge_distance_positive"),
        sa.CheckConstraint("from_node_id <> to_node_id", name="ck_warehouse_map_edge_distinct_nodes"),
        sa.ForeignKeyConstraint(["warehouse_map_id"], ["warehouse_maps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["from_node_id"], ["warehouse_map_nodes.id"]),
        sa.ForeignKeyConstraint(["to_node_id"], ["warehouse_map_nodes.id"]),
        sa.UniqueConstraint("warehouse_map_id", "code", name="uq_warehouse_map_edge_code"),
    )
    for name,column in (
        ("ix_warehouse_map_edges_warehouse_map_id","warehouse_map_id"),
        ("ix_warehouse_map_edges_from_node_id","from_node_id"),
        ("ix_warehouse_map_edges_to_node_id","to_node_id"),
        ("ix_warehouse_map_edges_is_active","is_active"),
    ):
        op.create_index(name,"warehouse_map_edges",[column])
    op.create_index("ix_warehouse_map_edge_map_active","warehouse_map_edges",["warehouse_map_id","is_active"])

    op.create_table(
        "location_map_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("warehouse_map_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("pick_node_id", sa.Integer(), nullable=False),
        sa.Column("x_m", sa.Numeric(10,3), nullable=False),
        sa.Column("y_m", sa.Numeric(10,3), nullable=False),
        sa.Column("width_m", sa.Numeric(10,3), nullable=False),
        sa.Column("depth_m", sa.Numeric(10,3), nullable=False),
        sa.Column("rotation_deg", sa.Numeric(7,2), nullable=False, server_default="0"),
        sa.Column("facing", sa.String(length=16), nullable=False, server_default="aisle"),
        sa.Column("local_geometry_kind", sa.String(length=32), nullable=False, server_default="organizer"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("width_m > 0", name="ck_location_map_binding_width_positive"),
        sa.CheckConstraint("depth_m > 0", name="ck_location_map_binding_depth_positive"),
        sa.ForeignKeyConstraint(["warehouse_map_id"], ["warehouse_maps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["pick_node_id"], ["warehouse_map_nodes.id"]),
        sa.UniqueConstraint("warehouse_map_id", "location_id", name="uq_location_map_binding"),
    )
    for name,column in (
        ("ix_location_map_bindings_warehouse_map_id","warehouse_map_id"),
        ("ix_location_map_bindings_location_id","location_id"),
        ("ix_location_map_bindings_pick_node_id","pick_node_id"),
    ):
        op.create_index(name,"location_map_bindings",[column])

    op.create_table(
        "pick_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pick_task_no", sa.String(length=64), nullable=False),
        sa.Column("build_plan_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("product_revision_id", sa.Integer(), nullable=False),
        sa.Column("build_plan_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="ready"),
        sa.Column("route_strategy", sa.String(length=32), nullable=False, server_default="hierarchy_v1"),
        sa.Column("warehouse_map_id", sa.Integer(), nullable=True),
        sa.Column("warehouse_graph_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("route_distance_m", sa.Numeric(12,3), nullable=True),
        sa.Column("route_start_node_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("route_end_node_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("route_constraints", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("route_plan", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("client_operation_id", sa.String(length=128), nullable=False),
        sa.Column("source_request_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('ready','in_progress','needs_replan','completed','cancelled','stale')",
            name="ck_pick_task_status",
        ),
        sa.ForeignKeyConstraint(["build_plan_id"], ["build_plans.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["product_revision_id"], ["product_revisions.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["warehouse_map_id"], ["warehouse_maps.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("pick_task_no", name="uq_pick_task_no"),
        sa.UniqueConstraint("created_by_id", "client_operation_id", name="uq_pick_task_business_operation"),
    )
    op.create_index("ix_pick_tasks_build_plan_id", "pick_tasks", ["build_plan_id"])
    op.create_index("ix_pick_tasks_project_id", "pick_tasks", ["project_id"])
    op.create_index("ix_pick_tasks_product_revision_id", "pick_tasks", ["product_revision_id"])
    op.create_index("ix_pick_tasks_build_plan_snapshot_hash", "pick_tasks", ["build_plan_snapshot_hash"])
    op.create_index("ix_pick_tasks_status", "pick_tasks", ["status"])
    op.create_index("ix_pick_tasks_created_by_id", "pick_tasks", ["created_by_id"])
    op.create_index("ix_pick_tasks_warehouse_map_id", "pick_tasks", ["warehouse_map_id"])
    op.create_index("ix_pick_tasks_warehouse_graph_hash", "pick_tasks", ["warehouse_graph_hash"])
    op.create_index("ix_pick_task_build_plan_status", "pick_tasks", ["build_plan_id", "status"])
    op.create_index("ix_pick_task_project_status", "pick_tasks", ["project_id", "status"])

    op.create_table(
        "pick_task_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pick_task_id", sa.Integer(), nullable=False),
        sa.Column("build_plan_item_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("required_quantity", sa.Numeric(14,4), nullable=False),
        sa.Column("allocated_quantity", sa.Numeric(14,4), nullable=False),
        sa.Column("picked_quantity", sa.Numeric(14,4), nullable=False, server_default="0"),
        sa.Column("reservation_quantity_at_task", sa.Numeric(14,4), nullable=False),
        sa.Column("locatable_quantity_at_task", sa.Numeric(14,4), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("required_quantity > 0", name="ck_pick_item_required_positive"),
        sa.CheckConstraint("allocated_quantity >= 0", name="ck_pick_item_allocated_nonnegative"),
        sa.CheckConstraint("picked_quantity >= 0", name="ck_pick_item_picked_nonnegative"),
        sa.CheckConstraint("allocated_quantity <= required_quantity", name="ck_pick_item_allocated_lte_required"),
        sa.CheckConstraint("picked_quantity <= allocated_quantity", name="ck_pick_item_picked_lte_allocated"),
        sa.CheckConstraint("status IN ('pending','partial','picked','cancelled')", name="ck_pick_item_status"),
        sa.ForeignKeyConstraint(["pick_task_id"], ["pick_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["build_plan_item_id"], ["build_plan_items.id"]),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"]),
        sa.UniqueConstraint("pick_task_id", "material_id", name="uq_pick_task_material"),
    )
    for name,column in (
        ("ix_pick_task_items_pick_task_id","pick_task_id"),
        ("ix_pick_task_items_build_plan_item_id","build_plan_item_id"),
        ("ix_pick_task_items_material_id","material_id"),
        ("ix_pick_task_items_status","status"),
    ):
        op.create_index(name,"pick_task_items",[column])

    op.create_table(
        "pick_allocations",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("pick_task_item_id", "inventory_lot_id", "generation", name="uq_pick_item_lot"),
        sa.CheckConstraint("generation > 0", name="ck_pick_allocation_generation_positive"),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pick_task_item_id", sa.Integer(), nullable=False),
        sa.Column("inventory_lot_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("planned_quantity", sa.Numeric(14,4), nullable=False),
        sa.Column("picked_quantity", sa.Numeric(14,4), nullable=False, server_default="0"),
        sa.Column("route_sequence", sa.Integer(), nullable=False),
        sa.Column("route_key", sa.String(length=600), nullable=False, server_default=""),
        sa.Column("route_node_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("route_distance_from_previous_m", sa.Numeric(12,3), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("confirmation_method", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("confirmed_by_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("planned_quantity > 0", name="ck_pick_allocation_planned_positive"),
        sa.CheckConstraint("picked_quantity >= 0", name="ck_pick_allocation_picked_nonnegative"),
        sa.CheckConstraint("picked_quantity <= planned_quantity", name="ck_pick_allocation_picked_lte_planned"),
        sa.CheckConstraint("route_sequence > 0", name="ck_pick_allocation_route_positive"),
        sa.CheckConstraint("status IN ('pending','partial','picked','cancelled','stale')", name="ck_pick_allocation_status"),
        sa.CheckConstraint("confirmation_method IN ('none','manual','barcode','qr')", name="ck_pick_allocation_confirmation_method"),
        sa.ForeignKeyConstraint(["pick_task_item_id"], ["pick_task_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inventory_lot_id"], ["inventory_lots.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["confirmed_by_id"], ["users.id"]),
    )
    for name,column in (
        ("ix_pick_allocations_pick_task_item_id","pick_task_item_id"),
        ("ix_pick_allocations_inventory_lot_id","inventory_lot_id"),
        ("ix_pick_allocations_location_id","location_id"),
        ("ix_pick_allocations_status","status"),
        ("ix_pick_allocations_confirmed_by_id","confirmed_by_id"),
        ("ix_pick_allocations_route_sequence","route_sequence"),
    ):
        op.create_index(name,"pick_allocations",[column])
    op.create_index("ix_pick_allocation_lot_status","pick_allocations",["inventory_lot_id","status"])


def downgrade() -> None:
    _backfill_picking_permissions(remove=True)
    op.drop_table("pick_allocations")
    op.drop_table("pick_task_items")
    op.drop_table("pick_tasks")
    op.drop_table("location_map_bindings")
    op.drop_table("warehouse_map_edges")
    op.drop_table("warehouse_map_nodes")
    op.drop_table("warehouse_maps")
