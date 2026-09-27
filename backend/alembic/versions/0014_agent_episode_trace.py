"""Add durable structured AgentEpisode trace records.

Revision ID: 0014_agent_episode_trace
Revises: 0013_map_edge_precision
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_agent_episode_trace"
down_revision = "0013_map_edge_precision"
branch_labels = None
depends_on = None


def upgrade():
    # 0001_initial historically calls Base.metadata.create_all for its legacy
    # bootstrap.  On a fresh database running the current metadata that can
    # create this additive table before 0014.  Existing 0013 databases do not
    # have it, so keep the new revision responsible for creation there while
    # remaining compatible with the immutable bootstrap migration.
    if sa.inspect(op.get_bind()).has_table("agent_episodes"):
        return
    op.create_table(
        "agent_episodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("client_operation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("entry_surface", sa.String(length=32), nullable=False),
        sa.Column("execution_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("task_contract", sa.JSON(), nullable=False),
        sa.Column("task_contract_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("model_provider", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("model_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("tool_schema_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("grounded_facts", sa.JSON(), nullable=False),
        sa.Column("final_result", sa.JSON(), nullable=False),
        sa.Column("hard_failures", sa.JSON(), nullable=False),
        sa.Column("telemetry", sa.JSON(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("business_outcome", sa.JSON(), nullable=False),
        sa.Column("human_feedback", sa.JSON(), nullable=False),
        sa.Column("replay_parent_id", sa.String(length=36), nullable=True),
        sa.Column("trace_version", sa.String(length=16), nullable=False, server_default="1"),
        sa.Column("redaction_version", sa.String(length=16), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('success', 'error', 'blocked')",
            name="ck_agent_episode_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_episode_user_created", "agent_episodes", ["user_id", "created_at"]
    )
    op.create_index("ix_agent_episode_request", "agent_episodes", ["request_id"])
    op.create_index("ix_agent_episode_conversation", "agent_episodes", ["conversation_id"])
    op.create_index("ix_agent_episode_status", "agent_episodes", ["status"])
    op.create_index("ix_agent_episode_replay_parent", "agent_episodes", ["replay_parent_id"])


def downgrade():
    op.drop_table("agent_episodes")
