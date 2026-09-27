"""Create bounded Warehouse Agent conversation context."""

import sqlalchemy as sa

from alembic import op

revision = "0007_agent_conversation_context"
down_revision = "0006_agent_phase_1_5_hardening"
branch_labels = None
depends_on = None


def upgrade():
    # Migration 0001 creates the current metadata on a brand-new installation.
    # Existing databases upgraded from 0006 still need this table created here.
    if sa.inspect(op.get_bind()).has_table("agent_conversation_contexts"):
        return
    op.create_table(
        "agent_conversation_contexts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("context_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected_material_id", sa.Integer(), nullable=True),
        sa.Column("selected_project_id", sa.Integer(), nullable=True),
        sa.Column("selected_bom_version", sa.String(length=32), nullable=True),
        sa.Column("material_candidate_ids", sa.JSON(), nullable=False),
        sa.Column("project_candidate_ids", sa.JSON(), nullable=False),
        sa.Column("pending_disambiguation", sa.JSON(), nullable=False),
        sa.Column("last_entity_kind", sa.String(length=32), nullable=False),
        sa.Column("last_intent", sa.String(length=64), nullable=False),
        sa.Column("last_requested_facts", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "status IN ('active', 'closed')",
            name="ck_agent_conversation_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["selected_material_id"], ["materials.id"]),
        sa.ForeignKeyConstraint(["selected_project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_conversation_contexts_user_id",
        "agent_conversation_contexts",
        ["user_id"],
    )
    op.create_index(
        "ix_agent_conversation_contexts_status",
        "agent_conversation_contexts",
        ["status"],
    )
    op.create_index(
        "ix_agent_conversation_contexts_expires_at",
        "agent_conversation_contexts",
        ["expires_at"],
    )
    op.create_index(
        "ix_agent_conversation_contexts_selected_material_id",
        "agent_conversation_contexts",
        ["selected_material_id"],
    )
    op.create_index(
        "ix_agent_conversation_contexts_selected_project_id",
        "agent_conversation_contexts",
        ["selected_project_id"],
    )
    op.create_index(
        "ix_agent_conversation_owner_status",
        "agent_conversation_contexts",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_agent_conversation_owner_lookup",
        "agent_conversation_contexts",
        ["user_id", "id"],
    )


def downgrade():
    op.drop_index(
        "ix_agent_conversation_owner_lookup",
        table_name="agent_conversation_contexts",
    )
    op.drop_index(
        "ix_agent_conversation_owner_status",
        table_name="agent_conversation_contexts",
    )
    op.drop_index(
        "ix_agent_conversation_contexts_selected_project_id",
        table_name="agent_conversation_contexts",
    )
    op.drop_index(
        "ix_agent_conversation_contexts_selected_material_id",
        table_name="agent_conversation_contexts",
    )
    op.drop_index(
        "ix_agent_conversation_contexts_expires_at",
        table_name="agent_conversation_contexts",
    )
    op.drop_index(
        "ix_agent_conversation_contexts_status",
        table_name="agent_conversation_contexts",
    )
    op.drop_index(
        "ix_agent_conversation_contexts_user_id",
        table_name="agent_conversation_contexts",
    )
    op.drop_table("agent_conversation_contexts")
