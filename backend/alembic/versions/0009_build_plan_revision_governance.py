"""Add revision governance and immutable BuildPlan snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "0009_build_plan_governance"
down_revision = "0008_product_revision_bom"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    revision_columns = {
        column["name"] for column in inspector.get_columns("product_revisions")
    }
    conversation_columns = {
        column["name"]
        for column in inspector.get_columns("agent_conversation_contexts")
    }
    if (
        inspector.has_table("build_plans")
        and inspector.has_table("build_plan_items")
        and {"bom_hash", "released_by_id"}.issubset(revision_columns)
        and "last_build_quantity" in conversation_columns
    ):
        # Migration 0001 intentionally creates current metadata on fresh installs.
        return

    with op.batch_alter_table("agent_conversation_contexts") as batch:
        batch.add_column(sa.Column("last_build_quantity", sa.Integer(), nullable=True))

    with op.batch_alter_table("product_revisions") as batch:
        batch.add_column(sa.Column("bom_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("released_by_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_product_revision_released_by",
            "users",
            ["released_by_id"],
            ["id"],
        )
        batch.create_index("ix_product_revisions_bom_hash", ["bom_hash"])

    op.create_index(
        "uq_product_revisions_one_default",
        "product_revisions",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
        sqlite_where=sa.text("is_default = 1"),
    )

    op.create_table(
        "build_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_no", sa.String(length=64), nullable=False),
        sa.Column("product_revision_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("build_quantity", sa.Integer(), nullable=False),
        sa.Column("product_bom_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="ready",
        ),
        sa.Column("reservation_proposal_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_request_id",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
        sa.Column("client_operation_id", sa.String(length=128), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stale_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'reservation_pending', 'reserved', 'stale', 'cancelled')",
            name="ck_build_plan_status",
        ),
        sa.CheckConstraint(
            "build_quantity > 0",
            name="ck_build_plan_quantity_positive",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_build_plan_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["product_revision_id"],
            ["product_revisions.id"],
            name="fk_build_plan_product_revision",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_build_plan_project",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_proposal_id"],
            ["agent_action_proposals.id"],
            name="fk_build_plan_reservation_proposal",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_no", name="uq_build_plan_no"),
        sa.UniqueConstraint(
            "created_by_id",
            "client_operation_id",
            name="uq_build_plan_business_operation",
        ),
        sa.UniqueConstraint(
            "reservation_proposal_id",
            name="uq_build_plan_reservation_proposal",
        ),
    )
    op.create_index("ix_build_plans_plan_no", "build_plans", ["plan_no"])
    op.create_index(
        "ix_build_plans_product_revision_id",
        "build_plans",
        ["product_revision_id"],
    )
    op.create_index("ix_build_plans_project_id", "build_plans", ["project_id"])
    op.create_index("ix_build_plans_status", "build_plans", ["status"])
    op.create_index(
        "ix_build_plans_reservation_proposal_id",
        "build_plans",
        ["reservation_proposal_id"],
    )
    op.create_index("ix_build_plans_created_by_id", "build_plans", ["created_by_id"])
    op.create_index("ix_build_plans_snapshot_hash", "build_plans", ["snapshot_hash"])
    op.create_index(
        "ix_build_plan_project_status",
        "build_plans",
        ["project_id", "status"],
    )

    op.create_table(
        "build_plan_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("build_plan_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("quantity_per_unit", sa.Numeric(14, 4), nullable=False),
        sa.Column("required_total", sa.Numeric(14, 4), nullable=False),
        sa.Column("available_quantity_at_plan", sa.Numeric(14, 4), nullable=False),
        sa.Column(
            "reserved_for_project_at_plan",
            sa.Numeric(14, 4),
            nullable=False,
        ),
        sa.Column(
            "additional_reservation_required",
            sa.Numeric(14, 4),
            nullable=False,
        ),
        sa.Column(
            "projected_free_available_after_build",
            sa.Numeric(14, 4),
            nullable=False,
        ),
        sa.Column("safety_stock_at_plan", sa.Numeric(14, 4), nullable=False),
        sa.Column("below_safety_after_build", sa.Boolean(), nullable=False),
        sa.Column(
            "material_status_at_plan",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "quantity_per_unit > 0",
            name="ck_build_plan_item_qpu_positive",
        ),
        sa.CheckConstraint(
            "required_total > 0",
            name="ck_build_plan_item_required_positive",
        ),
        sa.CheckConstraint(
            "additional_reservation_required >= 0",
            name="ck_build_plan_item_additional_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["build_plan_id"],
            ["build_plans.id"],
            name="fk_build_plan_item_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["materials.id"],
            name="fk_build_plan_item_material",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "build_plan_id",
            "material_id",
            name="uq_build_plan_material",
        ),
    )
    op.create_index(
        "ix_build_plan_items_build_plan_id",
        "build_plan_items",
        ["build_plan_id"],
    )
    op.create_index(
        "ix_build_plan_items_material_id",
        "build_plan_items",
        ["material_id"],
    )


def downgrade():
    op.drop_index("ix_build_plan_items_material_id", table_name="build_plan_items")
    op.drop_index("ix_build_plan_items_build_plan_id", table_name="build_plan_items")
    op.drop_table("build_plan_items")

    op.drop_index("ix_build_plan_project_status", table_name="build_plans")
    op.drop_index("ix_build_plans_snapshot_hash", table_name="build_plans")
    op.drop_index("ix_build_plans_created_by_id", table_name="build_plans")
    op.drop_index(
        "ix_build_plans_reservation_proposal_id",
        table_name="build_plans",
    )
    op.drop_index("ix_build_plans_status", table_name="build_plans")
    op.drop_index("ix_build_plans_project_id", table_name="build_plans")
    op.drop_index("ix_build_plans_product_revision_id", table_name="build_plans")
    op.drop_index("ix_build_plans_plan_no", table_name="build_plans")
    op.drop_table("build_plans")

    op.drop_index(
        "uq_product_revisions_one_default",
        table_name="product_revisions",
    )
    released_by_fk = next(
        (
            foreign_key.get("name")
            for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(
                "product_revisions"
            )
            if foreign_key.get("constrained_columns") == ["released_by_id"]
        ),
        None,
    )
    with op.batch_alter_table("product_revisions") as batch:
        batch.drop_index("ix_product_revisions_bom_hash")
        if released_by_fk:
            batch.drop_constraint(released_by_fk, type_="foreignkey")
        batch.drop_column("released_by_id")
        batch.drop_column("bom_hash")

    with op.batch_alter_table("agent_conversation_contexts") as batch:
        batch.drop_column("last_build_quantity")
