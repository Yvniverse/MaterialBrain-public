"""Add Warehouse Agent action proposals."""

import sqlalchemy as sa

from alembic import op

revision = "0005_agent_action_proposals"
down_revision = "0004_user_soft_delete"
branch_labels = None
depends_on = None


def upgrade():
    # 0001 intentionally creates the then-current SQLAlchemy metadata. A brand-new
    # installation therefore already contains this table before Alembic reaches
    # 0005, while an existing installation upgraded from 0004 does not.
    if sa.inspect(op.get_bind()).has_table("agent_action_proposals"):
        return
    op.create_table(
        "agent_action_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proposal_no", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("approved_by_id", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("execution_result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
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
            "action_type IN ('reserve_inventory')",
            name="ck_agent_proposal_action_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executed', 'failed', 'expired')",
            name="ck_agent_proposal_status",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"]),
    )
    op.create_index(
        "ix_agent_action_proposals_proposal_no",
        "agent_action_proposals",
        ["proposal_no"],
        unique=True,
    )
    op.create_index(
        "ix_agent_action_proposals_action_type",
        "agent_action_proposals",
        ["action_type"],
    )
    op.create_index(
        "ix_agent_action_proposals_status",
        "agent_action_proposals",
        ["status"],
    )
    op.create_index(
        "ix_agent_action_proposals_created_by_id",
        "agent_action_proposals",
        ["created_by_id"],
    )
    op.create_index(
        "ix_agent_action_proposals_request_id",
        "agent_action_proposals",
        ["request_id"],
    )
    op.create_index(
        "ix_agent_action_proposals_expires_at",
        "agent_action_proposals",
        ["expires_at"],
    )


def downgrade():
    op.drop_index("ix_agent_action_proposals_expires_at", table_name="agent_action_proposals")
    op.drop_index("ix_agent_action_proposals_request_id", table_name="agent_action_proposals")
    op.drop_index("ix_agent_action_proposals_created_by_id", table_name="agent_action_proposals")
    op.drop_index("ix_agent_action_proposals_status", table_name="agent_action_proposals")
    op.drop_index("ix_agent_action_proposals_action_type", table_name="agent_action_proposals")
    op.drop_index("ix_agent_action_proposals_proposal_no", table_name="agent_action_proposals")
    op.drop_table("agent_action_proposals")
