"""Add Product revisions, per-unit BOMs, and product conversation context."""

import sqlalchemy as sa

from alembic import op

revision = "0008_product_revision_bom"
down_revision = "0007_agent_conversation_context"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("products"):
        op.create_table(
            "products",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "lifecycle_status",
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
                "lifecycle_status IN ('active', 'archived')",
                name="ck_product_lifecycle_status",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", name="uq_products_code"),
        )
        op.create_index("ix_products_code", "products", ["code"])
        op.create_index("ix_products_name", "products", ["name"])
        op.create_index(
            "ix_products_lifecycle_status",
            "products",
            ["lifecycle_status"],
        )

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("product_revisions"):
        op.create_table(
            "product_revisions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("revision", sa.String(length=64), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="draft",
            ),
            sa.Column(
                "is_default",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
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
                "status IN ('draft', 'released', 'obsolete')",
                name="ck_product_revision_status",
            ),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("product_id", "revision", name="uq_product_revision"),
        )
        op.create_index(
            "ix_product_revisions_product_id",
            "product_revisions",
            ["product_id"],
        )
        op.create_index(
            "ix_product_revisions_is_default",
            "product_revisions",
            ["is_default"],
        )
        op.create_index(
            "ix_product_revision_product_status",
            "product_revisions",
            ["product_id", "status"],
        )

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("product_bom_items"):
        op.create_table(
            "product_bom_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("product_revision_id", sa.Integer(), nullable=False),
            sa.Column("material_id", sa.Integer(), nullable=False),
            sa.Column("quantity_per_unit", sa.Numeric(14, 4), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
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
                name="ck_product_bom_qty_positive",
            ),
            sa.ForeignKeyConstraint(["material_id"], ["materials.id"]),
            sa.ForeignKeyConstraint(
                ["product_revision_id"],
                ["product_revisions.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "product_revision_id",
                "material_id",
                name="uq_product_bom_item",
            ),
        )
        op.create_index(
            "ix_product_bom_items_product_revision_id",
            "product_bom_items",
            ["product_revision_id"],
        )
        op.create_index(
            "ix_product_bom_items_material_id",
            "product_bom_items",
            ["material_id"],
        )

    if "product_revision_id" not in _columns("projects"):
        with op.batch_alter_table("projects") as batch:
            batch.add_column(sa.Column("product_revision_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_projects_product_revision",
                "product_revisions",
                ["product_revision_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index(
                "ix_projects_product_revision_id",
                ["product_revision_id"],
            )

    conversation_columns = _columns("agent_conversation_contexts")
    missing_conversation_columns = {
        "selected_product_id",
        "selected_product_revision_id",
        "product_candidate_ids",
    } - conversation_columns
    if missing_conversation_columns:
        with op.batch_alter_table("agent_conversation_contexts") as batch:
            if "selected_product_id" in missing_conversation_columns:
                batch.add_column(sa.Column("selected_product_id", sa.Integer(), nullable=True))
                batch.create_foreign_key(
                    "fk_agent_context_product",
                    "products",
                    ["selected_product_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
                batch.create_index(
                    "ix_agent_conversation_contexts_selected_product_id",
                    ["selected_product_id"],
                )
            if "selected_product_revision_id" in missing_conversation_columns:
                batch.add_column(
                    sa.Column("selected_product_revision_id", sa.Integer(), nullable=True)
                )
                batch.create_foreign_key(
                    "fk_agent_context_product_revision",
                    "product_revisions",
                    ["selected_product_revision_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
                batch.create_index(
                    "ix_agent_conversation_contexts_selected_product_revision_id",
                    ["selected_product_revision_id"],
                )
            if "product_candidate_ids" in missing_conversation_columns:
                batch.add_column(
                    sa.Column(
                        "product_candidate_ids",
                        sa.JSON(),
                        nullable=False,
                        server_default=sa.text("'[]'"),
                    )
                )


def downgrade():
    if sa.inspect(op.get_bind()).has_table("agent_conversation_contexts"):
        columns = _columns("agent_conversation_contexts")
        indexes = _indexes("agent_conversation_contexts")
        with op.batch_alter_table("agent_conversation_contexts") as batch:
            for index_name in (
                "ix_agent_conversation_contexts_selected_product_revision_id",
                "ix_agent_conversation_contexts_selected_product_id",
            ):
                if index_name in indexes:
                    batch.drop_index(index_name)
            for column_name in (
                "product_candidate_ids",
                "selected_product_revision_id",
                "selected_product_id",
            ):
                if column_name in columns:
                    batch.drop_column(column_name)

    if sa.inspect(op.get_bind()).has_table("projects"):
        columns = _columns("projects")
        indexes = _indexes("projects")
        if "product_revision_id" in columns:
            with op.batch_alter_table("projects") as batch:
                if "ix_projects_product_revision_id" in indexes:
                    batch.drop_index("ix_projects_product_revision_id")
                batch.drop_column("product_revision_id")

    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("product_bom_items"):
        op.drop_table("product_bom_items")
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("product_revisions"):
        op.drop_table("product_revisions")
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("products"):
        op.drop_table("products")
