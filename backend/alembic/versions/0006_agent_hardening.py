"""Add business idempotency fields for Warehouse Agent proposals."""

import hashlib
import json

import sqlalchemy as sa

from alembic import op

revision = "0006_agent_phase_1_5_hardening"
down_revision = "0005_agent_action_proposals"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "agent_action_proposals"
    if not inspector.has_table(table_name):
        raise RuntimeError("agent_action_proposals must exist before migration 0006")

    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "client_operation_id" not in columns:
        op.add_column(
            table_name,
            sa.Column("client_operation_id", sa.String(length=64), nullable=True),
        )
    if "payload_hash" not in columns:
        op.add_column(
            table_name,
            sa.Column("payload_hash", sa.String(length=64), nullable=True),
        )

    table = sa.table(
        table_name,
        sa.column("id", sa.Integer()),
        sa.column("payload", sa.JSON()),
        sa.column("client_operation_id", sa.String(length=64)),
        sa.column("payload_hash", sa.String(length=64)),
    )
    rows = bind.execute(
        sa.select(table.c.id, table.c.payload).where(
            sa.or_(
                table.c.client_operation_id.is_(None),
                table.c.payload_hash.is_(None),
            )
        )
    ).all()
    for proposal_id, payload in rows:
        canonical = json.dumps(
            payload or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        bind.execute(
            table.update()
            .where(table.c.id == proposal_id)
            .values(
                client_operation_id=f"legacy-{proposal_id}",
                payload_hash=hashlib.sha256(canonical).hexdigest(),
            )
        )

    inspector = sa.inspect(bind)
    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint.get("name")
    }
    with op.batch_alter_table(table_name) as batch:
        batch.alter_column(
            "client_operation_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch.alter_column(
            "payload_hash",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        if "uq_agent_proposal_business_operation" not in unique_names:
            batch.create_unique_constraint(
                "uq_agent_proposal_business_operation",
                ["created_by_id", "client_operation_id", "action_type"],
            )

    inspector = sa.inspect(bind)
    index_names = {index["name"] for index in inspector.get_indexes(table_name)}
    if "ix_agent_action_proposals_client_operation_id" not in index_names:
        op.create_index(
            "ix_agent_action_proposals_client_operation_id",
            table_name,
            ["client_operation_id"],
        )
    if "ix_agent_action_proposals_payload_hash" not in index_names:
        op.create_index(
            "ix_agent_action_proposals_payload_hash",
            table_name,
            ["payload_hash"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "agent_action_proposals"
    if not inspector.has_table(table_name):
        return
    index_names = {index["name"] for index in inspector.get_indexes(table_name)}
    for index_name in (
        "ix_agent_action_proposals_payload_hash",
        "ix_agent_action_proposals_client_operation_id",
    ):
        if index_name in index_names:
            op.drop_index(index_name, table_name=table_name)
    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint.get("name")
    }
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    with op.batch_alter_table(table_name) as batch:
        if "uq_agent_proposal_business_operation" in unique_names:
            batch.drop_constraint(
                "uq_agent_proposal_business_operation",
                type_="unique",
            )
        if "payload_hash" in columns:
            batch.drop_column("payload_hash")
        if "client_operation_id" in columns:
            batch.drop_column("client_operation_id")
