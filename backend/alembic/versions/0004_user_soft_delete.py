"""Add safe user deletion fields."""

import sqlalchemy as sa

from alembic import op

revision = "0004_user_soft_delete"
down_revision = "0003_bin_lightweight_content"
branch_labels = None
depends_on = None


def upgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "is_deleted" not in existing:
        op.add_column(
            "users",
            sa.Column(
                "is_deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        op.create_index("ix_users_is_deleted", "users", ["is_deleted"])
    if "deleted_at" not in existing:
        op.add_column(
            "users",
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "deleted_at" in existing:
        op.drop_column("users", "deleted_at")
    if "is_deleted" in existing:
        indexes = {
            index["name"] for index in sa.inspect(op.get_bind()).get_indexes("users")
        }
        if "ix_users_is_deleted" in indexes:
            op.drop_index("ix_users_is_deleted", table_name="users")
        op.drop_column("users", "is_deleted")
