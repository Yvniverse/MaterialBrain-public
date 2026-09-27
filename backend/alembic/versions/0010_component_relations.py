"""Add reviewed component relations and product-scoped BOM alternates."""

import sqlalchemy as sa

from alembic import op

revision = "0010_component_relations"
down_revision = "0009_build_plan_governance"
branch_labels = None
depends_on = None

VALIDATOR_ROLE_NAMES = {"硬件工程师", "项目负责人"}
VALIDATE_PERMISSION = "component:validate"


def _backfill_validator_permission(*, remove: bool = False) -> None:
    bind = op.get_bind()
    roles = sa.Table("roles", sa.MetaData(), autoload_with=bind)
    rows = bind.execute(sa.select(roles.c.id, roles.c.name, roles.c.permissions)).mappings()
    for row in rows:
        permissions = list(row["permissions"] or [])
        if remove:
            updated = [item for item in permissions if item != VALIDATE_PERMISSION]
        elif row["name"] in VALIDATOR_ROLE_NAMES and "*" not in permissions:
            updated = [*permissions]
            if VALIDATE_PERMISSION not in updated:
                updated.append(VALIDATE_PERMISSION)
        else:
            continue
        if updated != permissions:
            bind.execute(
                roles.update().where(roles.c.id == row["id"]).values(permissions=updated)
            )


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("component_relations"):
        op.create_table(
            "component_relations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("source_material_id", sa.Integer(), nullable=False),
            sa.Column("target_material_id", sa.Integer(), nullable=False),
            sa.Column("relation_type", sa.String(length=32), nullable=False),
            sa.Column(
                "status",
                sa.String(length=24),
                nullable=False,
                server_default="candidate",
            ),
            sa.Column(
                "confidence_note", sa.Text(), nullable=False, server_default=""
            ),
            sa.Column(
                "evidence_summary", sa.Text(), nullable=False, server_default=""
            ),
            sa.Column(
                "evidence_refs",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
            sa.Column("created_by_id", sa.Integer(), nullable=False),
            sa.Column("validated_by_id", sa.Integer(), nullable=True),
            sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "rejected_reason", sa.Text(), nullable=False, server_default=""
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
                "source_material_id < target_material_id",
                name="ck_component_relation_canonical_materials",
            ),
            sa.CheckConstraint(
                "relation_type IN ('similar_to', 'electrical_compatible', "
                "'pin_compatible', 'same_footprint')",
                name="ck_component_relation_type",
            ),
            sa.CheckConstraint(
                "status IN ('candidate', 'validated', 'rejected')",
                name="ck_component_relation_status",
            ),
            sa.ForeignKeyConstraint(
                ["source_material_id"], ["materials.id"], name="fk_relation_source"
            ),
            sa.ForeignKeyConstraint(
                ["target_material_id"], ["materials.id"], name="fk_relation_target"
            ),
            sa.ForeignKeyConstraint(
                ["created_by_id"], ["users.id"], name="fk_relation_created_by"
            ),
            sa.ForeignKeyConstraint(
                ["validated_by_id"], ["users.id"], name="fk_relation_validated_by"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_material_id",
                "target_material_id",
                "relation_type",
                name="uq_component_relation",
            ),
        )
        op.create_index(
            "ix_component_relations_source_material_id",
            "component_relations",
            ["source_material_id"],
        )
        op.create_index(
            "ix_component_relations_target_material_id",
            "component_relations",
            ["target_material_id"],
        )
        op.create_index(
            "ix_component_relations_status", "component_relations", ["status"]
        )
        op.create_index(
            "ix_component_relations_created_by_id",
            "component_relations",
            ["created_by_id"],
        )
        op.create_index(
            "ix_component_relation_source_status",
            "component_relations",
            ["source_material_id", "status"],
        )
        op.create_index(
            "ix_component_relation_target_status",
            "component_relations",
            ["target_material_id", "status"],
        )

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("product_bom_alternates"):
        op.create_table(
            "product_bom_alternates",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("product_bom_item_id", sa.Integer(), nullable=False),
            sa.Column("alternate_material_id", sa.Integer(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=24),
                nullable=False,
                server_default="candidate",
            ),
            sa.Column(
                "priority", sa.Integer(), nullable=False, server_default="100"
            ),
            sa.Column(
                "usage_condition", sa.Text(), nullable=False, server_default=""
            ),
            sa.Column(
                "engineering_note", sa.Text(), nullable=False, server_default=""
            ),
            sa.Column(
                "evidence_refs",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
            sa.Column("source_component_relation_id", sa.Integer(), nullable=True),
            sa.Column("created_by_id", sa.Integer(), nullable=False),
            sa.Column("approved_by_id", sa.Integer(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "rejected_reason", sa.Text(), nullable=False, server_default=""
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
                "status IN ('candidate', 'approved', 'rejected')",
                name="ck_product_bom_alternate_status",
            ),
            sa.CheckConstraint(
                "priority > 0",
                name="ck_product_bom_alternate_priority_positive",
            ),
            sa.ForeignKeyConstraint(
                ["product_bom_item_id"],
                ["product_bom_items.id"],
                name="fk_alternate_bom_item",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["alternate_material_id"],
                ["materials.id"],
                name="fk_alternate_material",
            ),
            sa.ForeignKeyConstraint(
                ["source_component_relation_id"],
                ["component_relations.id"],
                name="fk_alternate_source_relation",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["created_by_id"], ["users.id"], name="fk_alternate_created_by"
            ),
            sa.ForeignKeyConstraint(
                ["approved_by_id"], ["users.id"], name="fk_alternate_approved_by"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "product_bom_item_id",
                "alternate_material_id",
                name="uq_product_bom_alternate",
            ),
        )
        op.create_index(
            "ix_product_bom_alternates_product_bom_item_id",
            "product_bom_alternates",
            ["product_bom_item_id"],
        )
        op.create_index(
            "ix_product_bom_alternates_alternate_material_id",
            "product_bom_alternates",
            ["alternate_material_id"],
        )
        op.create_index(
            "ix_product_bom_alternates_status",
            "product_bom_alternates",
            ["status"],
        )
        op.create_index(
            "ix_product_bom_alternates_source_component_relation_id",
            "product_bom_alternates",
            ["source_component_relation_id"],
        )
        op.create_index(
            "ix_product_bom_alternates_created_by_id",
            "product_bom_alternates",
            ["created_by_id"],
        )
        op.create_index(
            "ix_product_bom_alternate_item_status",
            "product_bom_alternates",
            ["product_bom_item_id", "status"],
        )

    _backfill_validator_permission()


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("product_bom_alternates"):
        for index in (
            "ix_product_bom_alternate_item_status",
            "ix_product_bom_alternates_created_by_id",
            "ix_product_bom_alternates_source_component_relation_id",
            "ix_product_bom_alternates_status",
            "ix_product_bom_alternates_alternate_material_id",
            "ix_product_bom_alternates_product_bom_item_id",
        ):
            op.drop_index(index, table_name="product_bom_alternates")
        op.drop_table("product_bom_alternates")

    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("component_relations"):
        for index in (
            "ix_component_relation_target_status",
            "ix_component_relation_source_status",
            "ix_component_relations_created_by_id",
            "ix_component_relations_status",
            "ix_component_relations_target_material_id",
            "ix_component_relations_source_material_id",
        ):
            op.drop_index(index, table_name="component_relations")
        op.drop_table("component_relations")

    _backfill_validator_permission(remove=True)
